// Adzuna (adzuna.es) portal adapter (add-portal skill). Contract: see
// portals/registry.js. IN-TAB READER ONLY: the owner's Chrome reads the page at
// low volume; Cloudflare blocks plain HTTP clients, so the bot never fetches
// Adzuna (`server_fetch=False`). Adzuna is an aggregator: it is not suggested
// by default.

import { extractDetailText } from './detail.js';

// Cards (verified on the owner's saved page, 10 cards): `article[data-aid]`
// with the title in `h2`, company `.ui-company`, location `.ui-location`. The
// id is `data-aid` (the same number appears in `/details/<id>` and in the
// sponsored `/land/ad/<id>?...` links, whose query carries tracking), so the
// link is rebuilt as the canonical `https://www.adzuna.es/details/<id>`.
function extractAdzunaCards() {
  return new Promise((resolve) => {
    setTimeout(() => {
      const results = [];
      const seen = new Set();
      const text = (el) => (el ? (el.innerText || '').replace(/\s+/g, ' ').trim() : '');
      document.querySelectorAll('article[data-aid]').forEach((card) => {
        const id = card.getAttribute('data-aid') || '';
        if (!/^\d+$/.test(id) || seen.has(id)) return;
        seen.add(id);
        results.push({
          id,
          // Tidy spacing inside parentheses ("( Backend )" -> "(Backend)").
          title: text(card.querySelector('h2')).replace(/\(\s+/g, '(').replace(/\s+\)/g, ')').slice(0, 200),
          company: text(card.querySelector('.ui-company')),
          location: text(card.querySelector('.ui-location')),
          link: 'https://www.adzuna.es/details/' + id,
        });
      });
      resolve(results);
    }, 1500);
  });
}

// UNVERIFIED: the saved page had results; the results heading reads
// "<N> Backend empleos en Barcelona", so a zero count is the best guess.
export const NO_RESULTS_MARKERS = [
  '(?:^|\\s)0\\s+[^\\n]{0,60}empleos en\\b',
  'no hemos encontrado (?:ning[uú]n )?(?:empleo|oferta)',
];

function hostMatch(url) {
  try {
    const host = new URL(String(url).includes('://') ? url : 'https:' + url).hostname;
    return host === 'adzuna.es' || host.endsWith('.adzuna.es');
  } catch (_) {
    return false;
  }
}

// `loc` ids taken from the owner's URL: Barcelona = 206508. Any other city
// applies no `loc` (nationwide on the keywords). "remoto" uses the portal's own
// remote filter (`remote_only=1`, from its filter link) without a location.
const LOCATION_IDS = { barcelona: '206508' };

function buildSearchUrl({ keywords = '', location = '', extra = {} } = {}) {
  const params = new URLSearchParams();
  params.set('f', '7'); // published in the last 7 days
  const loc = String(location).split(',')[0].trim().toLowerCase();
  if (LOCATION_IDS[loc]) params.set('loc', LOCATION_IDS[loc]);
  else if (/^(remoto|remote|teletrabajo)$/.test(loc)) params.set('remote_only', '1');
  const kw = String(keywords).trim();
  if (kw) params.set('q', kw);
  for (const [k, v] of Object.entries(extra || {})) {
    if (v === null || v === undefined || v === '') params.delete(k);
    else params.set(k, String(v));
  }
  return 'https://www.adzuna.es/search?' + params.toString();
}

export default {
  id: 'adzuna',
  label: 'Adzuna',
  linkHost: 'www.adzuna.es',
  settleMs: 2500,
  noResultsMarkers: NO_RESULTS_MARKERS,
  hostMatch,
  buildSearchUrl,
  extractCards: extractAdzunaCards,
  // Per-offer detail pass: shared generic extractor (portals/detail.js); the
  // selector argument is optional and unverified (docs/portals.md).
  extractDetail: extractDetailText,
  detailArgs: [null],
  canonicalUrl: (id) => 'https://www.adzuna.es/details/' + id,
};
