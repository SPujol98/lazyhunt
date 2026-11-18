// Per-scan health report (offer-gates-and-health T5, T5b). Pure helpers, no
// chrome.* calls, so they can be loaded by the Node test harness. background.js
// POSTs the result to the bot's /scan-report endpoint after every beacon scan.

import { markersToSource, ALL_NO_RESULTS_MARKERS } from './portals/registry.js';

const RATE_LIMITED = /\b429\b|too many requests|rate[\s-]?limit/i;

// The copy the portals show for a search that genuinely has no results lives
// in each portal adapter (portals/*.js: LinkedIn EN/ES/CA, InfoJobs ES; best
// effort, so an unrecognized wording degrades to `empty_unconfirmed`, which
// the bot ignores). A page that parsed zero cards WITHOUT one of these may be
// a soft throttle (LinkedIn serves an empty results page when it throttles),
// so it must not count as a zero.
// One RegExp source (flags: i) covering every portal.
export const NO_RESULTS_SOURCE = markersToSource(ALL_NO_RESULTS_MARKERS);

export function hasNoResultsMarker(text) {
  return new RegExp(NO_RESULTS_SOURCE, 'i').test(String(text || ''));
}

export function classifyScanError(message) {
  const text = String(message || '');
  if (text === 'SESSION_EXPIRED') return 'session_expired';
  if (RATE_LIMITED.test(text)) return 'rate_limited';
  return 'error';
}

// `outcome` is { jobs, noResults } for a completed scan (the raw cards parsed,
// before any filtering; `noResults` says the page showed a genuine no-results
// marker) or { error } for a failed one. A completed scan with zero cards and
// no confirmed marker is `empty_unconfirmed`: the bot records it but never
// counts it as a healthy zero nor as breakage.
export function buildScanReport(beacon, outcome, at) {
  const failed = Boolean(outcome && outcome.error);
  const jobs = failed ? [] : (outcome && outcome.jobs) || [];
  let status = 'ok';
  if (failed) status = classifyScanError(outcome.error.message);
  else if (jobs.length === 0 && !(outcome && outcome.noResults)) status = 'empty_unconfirmed';
  return {
    beacon_id: String(beacon.id),
    beacon_name: String(beacon.name || beacon.id),
    at: new Date(at).toISOString(),
    status,
    jobs_found: jobs.length,
    empty_company: jobs.filter((j) => !String(j.company || '').trim()).length,
  };
}

// The scan endpoint lives next to the configured bridge URL (".../bridge").
export function scanReportUrl(bridgeUrl) {
  const url = String(bridgeUrl || '');
  return /\/bridge\/?$/.test(url) ? url.replace(/\/bridge\/?$/, '/scan-report') : null;
}
