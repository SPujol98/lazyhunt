// Indeed (es.indeed.com) portal adapter (add-portal skill). Contract: see
// portals/registry.js. Verified against the owner's saved results page
// (2026-10): 16 cards.

import { extractDetailText } from './detail.js';

// Cards of an Indeed results page: every card is `div.job_seen_beacon`; the
// title link `a[data-jk]` carries the 16-hex job key (`data-jk`), the title is
// its `span[title]`, the company `[data-testid="company-name"]` and the location
// `[data-testid="text-location"]`. The card href is a tracking redirect
// (`/rc/clk?jk=...&bb=...`), so the link is rebuilt from the key as the
// canonical `https://es.indeed.com/viewjob?jk=<id>`.
function extractIndeedCards() {
  return new Promise((resolve) => {
    setTimeout(() => {
      const results = [];
      const seen = new Set();
      const text = (el) => (el ? (el.innerText || '').trim() : '');
      document.querySelectorAll('.job_seen_beacon').forEach((card) => {
        const a = card.querySelector('a[data-jk]');
        const id = a ? a.getAttribute('data-jk') || '' : '';
        if (!/^[0-9a-f]{16}$/.test(id) || seen.has(id)) return;
        seen.add(id);
        const titleEl = a.querySelector('span[title]');
        const title = (titleEl && titleEl.getAttribute('title')) || text(titleEl) || text(a);
        results.push({
          id,
          title: title.slice(0, 200),
          company: text(card.querySelector('[data-testid="company-name"]')),
          location: text(card.querySelector('[data-testid="text-location"]')),
          link: 'https://es.indeed.com/viewjob?jk=' + id,
        });
      });
      resolve(results);
    }, 1500);
  });
}

// Empty-state copy was not present in the saved (non-empty) page, so these
// Spanish markers are UNVERIFIED best guesses of Indeed's wording.
export const NO_RESULTS_MARKERS = [
  'no ha obtenido ning[uú]n resultado',
  'no se han encontrado (?:empleos|resultados)',
  'no hay (?:resultados|empleos) que coincidan',
];

function hostMatch(url) {
  try {
    const host = new URL(String(url).includes('://') ? url : 'https:' + url).hostname;
    return host === 'indeed.com' || host.endsWith('.indeed.com');
  } catch (_) {
    return false;
  }
}

// Search URL in the format of the owner's real search:
// `/jobs?q=<kw>&l=<place>&fromage=7&radius=25`. Barcelona uses the exact
// place string Indeed produced ("Barcelona, Barcelona provincia"); any other
// location is passed through as typed; empty or remote drops `l` (nationwide).
// `extra` overrides or adds params (an empty value drops one).
function buildSearchUrl({ keywords = '', location = '', extra = {} } = {}) {
  const params = new URLSearchParams();
  const kw = String(keywords).trim();
  if (kw) params.set('q', kw);
  const loc = String(location).trim();
  if (loc && !/^remot/i.test(loc)) {
    params.set('l', /^barcelona$/i.test(loc) ? 'Barcelona, Barcelona provincia' : loc);
    params.set('radius', '25');
  }
  params.set('fromage', '7');
  for (const [k, v] of Object.entries(extra || {})) {
    if (v === null || v === undefined || v === '') params.delete(k);
    else params.set(k, String(v));
  }
  return 'https://es.indeed.com/jobs?' + params.toString();
}

export default {
  id: 'indeed',
  label: 'Indeed',
  linkHost: 'es.indeed.com',
  settleMs: 2500,
  noResultsMarkers: NO_RESULTS_MARKERS,
  hostMatch,
  buildSearchUrl,
  extractCards: extractIndeedCards,
  // Per-offer detail pass: shared generic extractor (portals/detail.js);
  // the selector argument is optional and unverified (docs/portals.md).
  extractDetail: extractDetailText,
  detailArgs: ['#jobDescriptionText'],
  canonicalUrl: (id) => (/^[0-9a-f]{16}$/.test(String(id)) ? 'https://es.indeed.com/viewjob?jk=' + id : null),
};
