// JobFluent portal adapter (add-portal skill). Contract: see
// portals/registry.js. IN-TAB READER ONLY: the owner's Chrome reads the page
// at low volume; the bot never fetches JobFluent (its robots.txt disallows AI
// crawlers), so its SourceSpec has `server_fetch=False`.

import { extractDetailText } from './detail.js';

// Cards of a results page (recon sample, 2026-10): `div.panel-offer` with id
// `offer-<6 hex>`; the title link `.offer-title a[href]` is relative
// (`/jobs/<slug>-<6hex>?q=...`) and reads "Title at Company" (split on the
// LAST " at "); the company logo `a.offer-logo[href^="/company/"]` carries
// "View <Company> company details ..." as a fallback. The page has no location
// per card: it is the city of the search (`/jobs-<city>`) taken from the
// card's `data-offer-page`. The id is the 6-hex job key. There is no recency
// filter, so cards older than 7 days ("added N days ago", weeks, months) are
// dropped; "today", "yesterday", hours and a card without a date are kept.
function extractJobfluentCards() {
  return new Promise((resolve) => {
    setTimeout(() => {
      const results = [];
      const seen = new Set();
      const text = (el) => (el ? (el.innerText || '').trim() : '');
      const ageDays = (raw) => {
        const t = String(raw || '').toLowerCase();
        if (/today|hour|minute|just now/.test(t)) return 0;
        if (/yesterday/.test(t)) return 1;
        const m = t.match(/(\d+)\s*(day|week|month|year)/);
        if (!m) return null;
        const n = Number(m[1]);
        return n * { day: 1, week: 7, month: 30, year: 365 }[m[2]];
      };
      document.querySelectorAll('div.panel-offer').forEach((card) => {
        const idm = (card.getAttribute('id') || '').match(/^offer-([0-9a-f]{6})$/);
        if (!idm || seen.has(idm[1])) return;
        const age = ageDays(text(card.querySelector('.published-date')));
        if (age !== null && age > 7) return;
        const titleBox = card.querySelector('.offer-title');
        const a = titleBox ? titleBox.querySelector('a') : null;
        const href = a ? (a.getAttribute('href') || '').split('#')[0].split('?')[0] : '';
        if (!href) return;
        const full = text(a);
        const cut = full.lastIndexOf(' at ');
        let title = cut > 0 ? full.slice(0, cut).trim() : full;
        let company = cut > 0 ? full.slice(cut + 4).trim() : '';
        if (!company) {
          const logo = card.querySelector('a.offer-logo');
          const lt = logo ? logo.getAttribute('title') || '' : '';
          company = lt.replace(/^View\s+/, '').replace(/\s+company details.*$/, '');
        }
        const offerEl = card.querySelector('[data-offer-page]');
        const city = ((offerEl ? offerEl.getAttribute('data-offer-page') : '') || '')
          .match(/\/jobs-([a-z-]+)/);
        seen.add(idm[1]);
        results.push({
          id: idm[1],
          title: title.slice(0, 200),
          company,
          location: city ? city[1].charAt(0).toUpperCase() + city[1].slice(1) : '',
          link: href.startsWith('http') ? href : 'https://www.jobfluent.com' + href,
        });
      });
      resolve(results);
    }, 1500);
  });
}

// Recon: the no-results page says "No <kw> jobs found in <City>".
export const NO_RESULTS_MARKERS = ['no .{1,80} jobs found'];

function hostMatch(url) {
  try {
    const host = new URL(String(url).includes('://') ? url : 'https:' + url).hostname;
    return host === 'jobfluent.com' || host.endsWith('.jobfluent.com');
  } catch (_) {
    return false;
  }
}

// Search URL `/jobs-<city>?q=<kw>`. Cities observed: barcelona, madrid,
// valencia and remote. An unknown city falls back to Barcelona (the owner's
// base; a generic `/jobs?q=` was not observed so it is not used).
const CITIES = ['barcelona', 'madrid', 'valencia'];

function buildSearchUrl({ keywords = '', location = '', extra = {} } = {}) {
  const loc = String(location).split(',')[0].trim().toLowerCase()
    .normalize('NFD').replace(/[̀-ͯ]/g, '');
  const city = /^remot/.test(loc) ? 'remote' : CITIES.includes(loc) ? loc : 'barcelona';
  const params = new URLSearchParams();
  const kw = String(keywords).trim();
  if (kw) params.set('q', kw);
  for (const [k, v] of Object.entries(extra || {})) {
    if (v === null || v === undefined || v === '') params.delete(k);
    else params.set(k, String(v));
  }
  const qs = params.toString();
  return 'https://www.jobfluent.com/jobs-' + city + (qs ? '?' + qs : '');
}

export default {
  id: 'jobfluent',
  label: 'JobFluent',
  linkHost: 'www.jobfluent.com',
  settleMs: 2500,
  noResultsMarkers: NO_RESULTS_MARKERS,
  hostMatch,
  buildSearchUrl,
  extractCards: extractJobfluentCards,
  // Per-offer detail pass: shared generic extractor (portals/detail.js);
  // the selector argument is optional and unverified (docs/portals.md).
  extractDetail: extractDetailText,
  detailArgs: [null],
  // The slug part of an offer URL cannot be derived from the id.
  canonicalUrl: () => null,
};
