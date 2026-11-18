// foorilla (foorilla.com/hiring) portal adapter (add-portal skill). Contract:
// see portals/registry.js. Read in the owner's tab at low volume; robots.txt
// allows all, but the offer panel is filled by htmx so the SourceSpec has
// `server_fetch` off (a plain GET has no description). Global hiring feed:
// not suggested by default, the bot's location gate does the narrowing.

import { extractDetailText } from './detail.js';

// Cards of the latest-jobs list (verified on the owner's saved htmx fragment,
// 50 items): `li.list-group-item` holding `a.terminal-title` whose `href` is
// EMPTY; the offer path lives in `hx-get="/hiring/jobs/<slug>-<id>/"`. The id
// is the trailing digits of the slug. The card has no company (it is only on
// the detail page). The age ("55m ago", "1h ago", "2d ago", "3w ago") is in
// `.terminal-meta small`; cards older than 7 days are dropped, an unreadable
// age is kept. The level tag ([EN] entry level ...) has no field in the card
// schema and is ignored.
function extractFoorillaCards() {
  return new Promise((resolve) => {
    setTimeout(() => {
      const results = [];
      const seen = new Set();
      const text = (el) => (el ? (el.innerText || '').replace(/\s+/g, ' ').trim() : '');
      const UNIT_DAYS = { m: 0, h: 0, d: 1, w: 7, mo: 30, y: 365 };
      document.querySelectorAll('li.list-group-item').forEach((li) => {
        const a = li.querySelector('a.terminal-title');
        if (!a) return;
        const path = (a.getAttribute('hx-get') || a.getAttribute('href') || '')
          .split('#')[0].split('?')[0];
        const m = path.match(/^\/hiring\/jobs\/[^/]*?-(\d+)\/?$/);
        if (!m || seen.has(m[1])) return;
        const am = text(li.querySelector('.terminal-meta')).match(/(\d+)\s*(mo|m|h|d|w|y)\s+ago/i);
        if (am && Number(am[1]) * UNIT_DAYS[am[2].toLowerCase()] > 7) return;
        seen.add(m[1]);
        const where = li.querySelector('div.text-end');
        results.push({
          id: m[1],
          title: text(a).slice(0, 200),
          company: '',
          location: text(where).replace(/\s*\[R\]/g, ' (remote)').trim(),
          link: 'https://foorilla.com' + (path.endsWith('/') ? path : path + '/'),
        });
      });
      resolve(results);
    }, 1500);
  });
}

// UNVERIFIED: the saved fragment had results; this is a guess at the empty copy.
export const NO_RESULTS_MARKERS = ['no (?:new )?jobs found'];

function hostMatch(url) {
  try {
    const host = new URL(String(url).includes('://') ? url : 'https:' + url).hostname;
    return host === 'foorilla.com' || host.endsWith('.foorilla.com');
  } catch (_) {
    return false;
  }
}

// The list is global (no region or recency parameter was found in the saved
// fragment). The quick-search input is `name="job_search"` with
// `hx-get="/hiring/jobs/"`, so `?job_search=<kw>` is the request it issues
// (not verified live as a full page). Location is never sent: the bot's
// location gate and the title filter narrow the global feed.
function buildSearchUrl({ keywords = '', extra = {} } = {}) {
  const params = new URLSearchParams();
  const kw = String(keywords).trim();
  if (kw) params.set('job_search', kw);
  for (const [k, v] of Object.entries(extra || {})) {
    if (v === null || v === undefined || v === '') params.delete(k);
    else params.set(k, String(v));
  }
  const qs = params.toString();
  return 'https://foorilla.com/hiring/jobs/' + (qs ? '?' + qs : '');
}

export default {
  id: 'foorilla',
  label: 'foorilla',
  linkHost: 'foorilla.com',
  settleMs: 3000,
  noResultsMarkers: NO_RESULTS_MARKERS,
  hostMatch,
  buildSearchUrl,
  extractCards: extractFoorillaCards,
  // Opening an offer URL renders the job inside `#main2` (loaded by htmx);
  // shared generic extractor (portals/detail.js) pointed at that panel. The
  // detail pass waits for the tab to load plus a fixed 2s settle
  // (background.js enrichJob); no extra wait is built here.
  extractDetail: extractDetailText,
  detailArgs: ['#main2', true],
  // The slug part of an offer URL cannot be derived from the id.
  canonicalUrl: () => null,
};
