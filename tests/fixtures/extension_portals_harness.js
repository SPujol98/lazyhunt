'use strict';

// Node harness for extension/portals/* (portal-adapters T1). Adapters and the
// registry are ES modules loaded via dynamic import(). Card extraction runs
// over saved synthetic HTML snippets (tests/fixtures/portals/*.html) through
// the minimal DOM in minidom.js. Invoked by tests/test_extension_portals.py.

const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');
const { installDocument, uninstallDocument } = require('./minidom.js');

const failures = [];
function check(name, actual, expected) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) failures.push(`${name}: expected ${e}, got ${a}`);
}

// Page extractors wait (setTimeout) before reading the DOM: run them at once.
globalThis.setTimeout = (fn) => setImmediate(fn);

const fixture = (name) => fs.readFileSync(path.join(__dirname, 'portals', name), 'utf8');
const load = (rel) => import(pathToFileURL(path.resolve(__dirname, '..', '..', 'extension', rel)).href);

async function extract(adapter, html) {
  installDocument(html);
  try {
    return await adapter.extractCards();
  } finally {
    uninstallDocument();
  }
}

async function main() {
  const { default: linkedin } = await load('portals/linkedin.js');
  const { default: infojobs } = await load('portals/infojobs.js');
  const { PORTALS, portalFor, noResultsSource } = await load('portals/registry.js');

  // --- contract shape ---------------------------------------------------------
  for (const p of [linkedin, infojobs]) {
    check(`${p.id}: contract fields`, [
      typeof p.id, typeof p.label, typeof p.hostMatch,
      typeof p.extractCards, Array.isArray(p.noResultsMarkers), typeof p.canonicalUrl,
      typeof p.linkHost,
    ], ['string', 'string', 'function', 'function', true, 'function', 'string']);
    // executeScript serializes the function: it must be named and self-contained.
    check(`${p.id}: extractCards is a named function`, p.extractCards.name.length > 0, true);
  }
  check('extractCards names stay stable', [linkedin.extractCards.name, infojobs.extractCards.name],
    ['extractJobsFromPage', 'extractInfojobsCards']);

  // --- registry ----------------------------------------------------------------
  check('registry starts with linkedin and infojobs', PORTALS.map((p) => p.id).slice(0, 2), ['linkedin', 'infojobs']);
  check('portalFor linkedin', portalFor('https://www.linkedin.com/jobs/search/?keywords=ai').id, 'linkedin');
  check('portalFor infojobs host', portalFor('https://www.infojobs.net/jobsearch/search-results/list.xhtml').id, 'infojobs');
  check('portalFor infojobs subdomain', portalFor('https://m.infojobs.net/x').id, 'infojobs');
  check('portalFor bare infojobs.net', portalFor('https://infojobs.net/x').id, 'infojobs');
  check('portalFor protocol-relative', portalFor('//www.infojobs.net/x').id, 'infojobs');
  check('portalFor unknown host falls back to linkedin', portalFor('https://example.com/x').id, 'linkedin');
  check('portalFor garbage falls back to linkedin', portalFor('not a url').id, 'linkedin');
  check('portalFor empty falls back to linkedin', portalFor('').id, 'linkedin');
  check('lookalike host is not infojobs', portalFor('https://evilinfojobs.net/').id, 'linkedin');

  // --- hostMatch / canonical / link host -----------------------------------------
  check('linkedin hostMatch', [linkedin.hostMatch('https://www.linkedin.com/x'), linkedin.hostMatch('https://www.infojobs.net/x')], [true, false]);
  check('infojobs hostMatch', [infojobs.hostMatch('https://www.infojobs.net/x'), infojobs.hostMatch('https://www.linkedin.com/x')], [true, false]);
  check('linkedin canonicalUrl', linkedin.canonicalUrl('123'), 'https://www.linkedin.com/jobs/view/123/');
  check('infojobs canonicalUrl is not derivable from the id', infojobs.canonicalUrl('abc'), null);
  check('link hosts', [linkedin.linkHost, infojobs.linkHost], ['www.linkedin.com', 'www.infojobs.net']);

  // --- no-results markers ----------------------------------------------------------
  const probe = (portal, text) => new RegExp(noResultsSource(portal), 'i').test(text);
  check('linkedin EN marker', probe(linkedin, 'No matching jobs found.'), true);
  check('linkedin ES marker', probe(linkedin, 'No se han encontrado empleos que coincidan'), true);
  check('linkedin CA marker', probe(linkedin, 'No hem trobat cap feina'), true);
  check('infojobs ES marker', probe(infojobs, 'No hemos encontrado ofertas'), true);
  check('unrelated text is no marker', probe(linkedin, 'Something went wrong'), false);

  // --- LinkedIn card strategies ---------------------------------------------------------
  check('linkedin componentkey strategy', await extract(linkedin, fixture('linkedin_componentkey.html')), [
    { id: '1000000001', title: 'AI Engineer', company: 'Acme Robotics', location: 'Barcelona, Catalonia, Spain', link: 'https://www.linkedin.com/jobs/view/1000000001/', easyApply: true },
    { id: '1000000002', title: 'Backend Developer', company: 'Globex', location: 'Madrid, Spain', link: 'https://www.linkedin.com/jobs/view/1000000002/' },
  ]);
  check('linkedin legacy list strategy', await extract(linkedin, fixture('linkedin_legacy.html')), [
    { id: '2000000001', title: 'Data Engineer', company: 'Initech', location: 'Remote', link: 'https://www.linkedin.com/jobs/view/2000000001/' },
  ]);
  check('linkedin anchor strategy', await extract(linkedin, fixture('linkedin_anchors.html')), [
    { id: '3000000001', title: 'ML Engineer', company: 'ML Engineer', location: 'Umbrella', link: 'https://www.linkedin.com/jobs/view/3000000001/' },
    { id: '3000000002', title: 'Platform Engineer', company: 'Platform Engineer', location: 'Hooli', link: 'https://www.linkedin.com/jobs/view/3000000002/' },
  ]);
  check('linkedin empty page', await extract(linkedin, '<main><p>nothing</p></main>'), []);

  // --- InfoJobs cards -----------------------------------------------------------------------
  check('infojobs cards', await extract(infojobs, fixture('infojobs_cards.html')), [
    { id: '0f8d7e6c5b4a39281706f5e4d3c2b1a0', title: 'Ingeniero de IA', company: 'Acme Datos', location: 'Barcelona', link: 'https://www.infojobs.net/barcelona/ai-engineer/of-0f8d7e6c5b4a39281706f5e4d3c2b1a0/' },
    { id: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', title: 'Data Scientist', company: 'Globex', location: 'Madrid', link: 'https://www.infojobs.net/madrid/data/of-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/' },
  ]);
  check('infojobs empty page', await extract(infojobs, '<main></main>'), []);

  if (failures.length) {
    console.error(`FAIL: ${failures.length} assertion(s) failed:`);
    for (const f of failures) console.error(' - ' + f);
    process.exitCode = 1;
  } else {
    process.stdout.write('OK: all extension/portals assertions passed\n');
  }
}

main().catch((e) => {
  console.error('harness crashed:', (e && e.stack) || e);
  process.exitCode = 1;
});
