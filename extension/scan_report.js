// Per-scan health report (offer-gates-and-health T5, T5b). Pure helpers, no
// chrome.* calls, so they can be loaded by the Node test harness. background.js
// POSTs the result to the bot's /scan-report endpoint after every beacon scan.

import { markersToSource, ALL_NO_RESULTS_MARKERS, portalFor, portalById } from './portals/registry.js';
import { beaconPortalId } from './searches.js';

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

// Why a scan failed (beta10 S2), sent as the optional `reason` of an `error`
// report so the bot can show it in /searches. Codes set at the throw site
// (`error.code`, e.g. every bounded timeout) win over the message, which may be
// localized; Chrome's own errors are matched by their (English) text.
export const SCAN_REASONS = ['timeout', 'tab_closed', 'offline', 'blocked', 'unknown'];
const OFFLINE = /chrome-error:|showing error page|net::ERR_|ERR_INTERNET_DISCONNECTED|failed to fetch/i;
const TAB_CLOSED = /no tab with id|tab was closed|tab .*(?:removed|discarded)|frame with id \d+ was removed/i;
const BLOCKED = /cannot access contents of|cannot access a chrome|cannot be scripted/i;
const TIMED_OUT = /timed out|timeout/i;

export function classifyScanReason(error) {
  const code = error && typeof error.code === 'string' ? error.code : '';
  if (SCAN_REASONS.includes(code)) return code;
  const text = String((error && error.message) || '');
  if (OFFLINE.test(text)) return 'offline';
  if (TAB_CLOSED.test(text)) return 'tab_closed';
  if (BLOCKED.test(text)) return 'blocked';
  if (TIMED_OUT.test(text)) return 'timeout';
  return 'unknown';
}

// `outcome` is { jobs, noResults } for a completed scan (the raw cards parsed,
// before any filtering; `noResults` says the page showed a genuine no-results
// marker) or { error } for a failed one. A completed scan with zero cards and
// no confirmed marker is `empty_unconfirmed`: the bot records it but never
// counts it as a healthy zero nor as breakage.
// A portal whose cards never show the company (foorilla) reports 0 empty
// companies, so the bot's "parser broken" health alert is not raised for it.
function cardsCarryCompany(beacon) {
  const portal = portalById(beaconPortalId(beacon, portalFor));
  return !portal || portal.cardsCarryCompany !== false;
}

export function buildScanReport(beacon, outcome, at) {
  const failed = Boolean(outcome && outcome.error);
  const jobs = failed ? [] : (outcome && outcome.jobs) || [];
  let status = 'ok';
  if (failed) status = classifyScanError(outcome.error.message);
  else if (jobs.length === 0 && !(outcome && outcome.noResults)) status = 'empty_unconfirmed';
  const report = {
    beacon_id: String(beacon.id),
    beacon_name: String(beacon.name || beacon.id),
    at: new Date(at).toISOString(),
    status,
    jobs_found: jobs.length,
    empty_company: cardsCarryCompany(beacon)
      ? jobs.filter((j) => !String(j.company || '').trim()).length
      : 0,
  };
  if (status === 'error') report.reason = classifyScanReason(outcome.error);
  return report;
}

// The scan endpoint lives next to the configured bridge URL (".../bridge").
export function scanReportUrl(bridgeUrl) {
  const url = String(bridgeUrl || '');
  return /\/bridge\/?$/.test(url) ? url.replace(/\/bridge\/?$/, '/scan-report') : null;
}
