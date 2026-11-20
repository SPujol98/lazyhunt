'use strict';

// Node harness for extension/scan_report.js (offer-gates-and-health T5).
// Loaded through dynamic import() like extension_filter_harness.js; invoked by
// tests/test_extension_scan_report.py. Exit 0 on success.

const path = require('path');
const { pathToFileURL } = require('url');

const failures = [];

function check(name, actual, expected) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) failures.push(`${name}: expected ${e}, got ${a}`);
}

async function main() {
  const modUrl = pathToFileURL(
    path.resolve(__dirname, '..', '..', 'extension', 'scan_report.js')
  ).href;
  const {
    buildScanReport,
    classifyScanError,
    scanReportUrl,
    hasNoResultsMarker,
    NO_RESULTS_SOURCE,
  } = await import(modUrl);

  const beacon = { id: 'b1', name: 'Barcelona AI' };
  const at = 1760788800000;

  check('ok report counts raw cards and empty companies', buildScanReport(beacon, {
    jobs: [
      { id: '1', company: 'Acme' },
      { id: '2', company: '' },
      { id: '3' },
      { id: '4', company: '  ' },
    ],
  }, at), {
    beacon_id: 'b1',
    beacon_name: 'Barcelona AI',
    at: '2025-10-18T12:00:00.000Z',
    status: 'ok',
    jobs_found: 4,
    empty_company: 3,
  });

  // foorilla cards never carry the company (it is only on the offer page),
  // so its scans must not count them as empty: the bot would raise the
  // "more than half without a company" alert on every scan of 4+ cards.
  const noCompany = { jobs: [{ id: '1', company: '' }, { id: '2' }, { id: '3' }, { id: '4' }] };
  check('foorilla auto beacon reports no empty companies',
    buildScanReport({ id: 'f1', name: 'AI · foorilla', auto: true, portal: 'foorilla' }, noCompany, at).empty_company, 0);
  check('foorilla manual beacon (by URL) reports no empty companies',
    buildScanReport({ id: 'f2', name: 'manual', url: 'https://foorilla.com/hiring/jobs/?job_search=ai' }, noCompany, at).empty_company, 0);
  check('other portals still count empty companies',
    buildScanReport({ id: 'a1', name: 'AI · Adzuna', auto: true, portal: 'adzuna' }, noCompany, at).empty_company, 4);

  check('ok report with zero cards', buildScanReport(beacon, { jobs: [] }, at).jobs_found, 0);

  // T5b: an empty page is only a healthy "0 results" when the portal says so;
  // otherwise it may be a soft throttle and the bot must not count it.
  check('zero cards, no marker checked: empty_unconfirmed', buildScanReport(beacon, { jobs: [] }, at).status, 'empty_unconfirmed');
  check('zero cards, marker absent: empty_unconfirmed', buildScanReport(beacon, { jobs: [], noResults: false }, at).status, 'empty_unconfirmed');
  check('zero cards, marker present: ok', buildScanReport(beacon, { jobs: [], noResults: true }, at).status, 'ok');
  check('cards found stay ok whatever the marker says', buildScanReport(beacon, { jobs: [{ id: '1', company: 'A' }], noResults: false }, at).status, 'ok');
  check('a failed scan keeps its error class', buildScanReport(beacon, { error: new Error('HTTP 429'), noResults: true }, at).status, 'rate_limited');
  check('unconfirmed empty scan report body', buildScanReport(beacon, { jobs: [], noResults: false }, at), {
    beacon_id: 'b1',
    beacon_name: 'Barcelona AI',
    at: '2025-10-18T12:00:00.000Z',
    status: 'empty_unconfirmed',
    jobs_found: 0,
    empty_company: 0,
  });

  const failed = buildScanReport(beacon, { error: new Error('SESSION_EXPIRED') }, at);
  check('session expired status', failed.status, 'session_expired');
  check('failed scan reports no cards', [failed.jobs_found, failed.empty_company], [0, 0]);
  check('rate limited by 429', buildScanReport(beacon, { error: new Error('HTTP 429') }, at).status, 'rate_limited');
  check('generic error', buildScanReport(beacon, { error: new Error('boom') }, at).status, 'error');

  check('classify too many requests', classifyScanError('Too Many Requests'), 'rate_limited');
  check('classify rate limit', classifyScanError('rate limit exceeded'), 'rate_limited');
  check('classify empty message', classifyScanError(undefined), 'error');

  // The marker list is handed to the injected page function as a RegExp source.
  check('marker source is a string', typeof NO_RESULTS_SOURCE, 'string');
  for (const text of [
    'No matching jobs found.',
    'NO MATCHING JOBS FOUND',
    'Resultados\nNo se han encontrado empleos que coincidan con tu búsqueda.',
    'No hemos encontrado ningún empleo que coincida con tu búsqueda',
    'No hem trobat cap feina que coincideixi amb la teva cerca',
    'No hemos encontrado ninguna oferta que coincida con tu búsqueda',
    'No hay ofertas de trabajo que coincidan con tu búsqueda',
  ]) {
    check(`no-results marker: ${text}`, hasNoResultsMarker(text), true);
    check(`marker source matches: ${text}`, new RegExp(NO_RESULTS_SOURCE, 'i').test(text), true);
  }
  for (const text of [
    '',
    undefined,
    'Skip to main content',
    'Something went wrong. Please try again later.',
    'Too many requests',
    '25 results',
    'Inicia sesión para ver más empleos',
    'Hemos encontrado 25 ofertas para ti',
  ]) {
    check(`not a no-results page: ${text}`, hasNoResultsMarker(text), false);
  }

  check('scan url from bridge url', scanReportUrl('http://127.0.0.1:8787/bridge'), 'http://127.0.0.1:8787/scan-report');
  check('scan url tolerates trailing slash', scanReportUrl('http://127.0.0.1:8787/bridge/'), 'http://127.0.0.1:8787/scan-report');
  check('scan url unknown shape is null', scanReportUrl('http://127.0.0.1:8787/other'), null);
  check('scan url empty is null', scanReportUrl(''), null);

  if (failures.length) {
    console.error(`FAIL: ${failures.length} assertion(s) failed:`);
    for (const f of failures) console.error(' - ' + f);
    process.exitCode = 1;
  } else {
    console.log('OK: all extension/scan_report.js assertions passed');
  }
}

main().catch((e) => {
  console.error('harness crashed:', (e && e.stack) || e);
  process.exitCode = 1;
});
