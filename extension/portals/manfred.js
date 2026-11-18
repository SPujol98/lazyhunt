// Manfred (getmanfred.com) portal adapter (add-portal skill). Contract: see
// portals/registry.js. Kept deliberately minimal: the site is a JS-rendered
// SPA read in the owner's tab (verified against a saved rendered page, 3 cards).

import { extractDetailText } from './detail.js';

// Cards: `a[href*="/ofertas-empleo/<num>/<slug>"]` holding `p` (title), a logo
// `img[alt=<company>]` and a "Ubicación" label span followed by its value.
// The id is the number in the URL. When the card has no title/company the
// slug is used for the title.
function extractManfredCards() {
  return new Promise((resolve) => {
    setTimeout(() => {
      const results = [];
      const seen = new Set();
      const text = (el) => (el ? (el.innerText || '').trim() : '');
      document.querySelectorAll('a[href*="/ofertas-empleo/"]').forEach((a) => {
        const href = (a.getAttribute('href') || '').split('#')[0].split('?')[0];
        const m = href.match(/\/ofertas-empleo\/(\d+)\/([^/]+)/);
        if (!m || seen.has(m[1])) return;
        seen.add(m[1]);
        const spans = a.querySelectorAll('span');
        let location = '';
        spans.forEach((s, i) => {
          if (!location && text(s) === 'Ubicación') location = text(spans[i + 1]);
        });
        const alts = [...a.querySelectorAll('img[alt]')]
          .map((i) => i.getAttribute('alt') || '').filter(Boolean);
        const slugTitle = m[2].replace(/-/g, ' ');
        results.push({
          id: m[1],
          title: (text(a.querySelector('p')) || slugTitle).slice(0, 200),
          company: alts[0] || '',
          location,
          link: href.startsWith('http') ? href : 'https://www.getmanfred.com' + href,
        });
      });
      resolve(results);
    }, 2500);
  });
}

// UNVERIFIED: the saved page had results; this is a guess at the empty copy.
export const NO_RESULTS_MARKERS = ['no (?:hay|hemos encontrado) ofertas'];

function hostMatch(url) {
  try {
    const host = new URL(String(url).includes('://') ? url : 'https:' + url).hostname;
    return host === 'getmanfred.com' || host.endsWith('.getmanfred.com');
  } catch (_) {
    return false;
  }
}

// Manfred has no keyword parameter (keywords are ignored). Only the owner's
// Barcelona `locations` value is known; any other place applies no filter.
const BARCELONA = 'ChIJ5TCOcRaYpBIRCmZHTz37sEQ:Barcelona, España';

function buildSearchUrl({ location = '', extra = {} } = {}) {
  const params = new URLSearchParams();
  params.set('onlyActive', 'true');
  params.set('currency', '€');
  if (/^barcelona$/i.test(String(location).split(',')[0].trim())) {
    params.set('locations', BARCELONA);
  }
  for (const [k, v] of Object.entries(extra || {})) {
    if (v === null || v === undefined || v === '') params.delete(k);
    else params.set(k, String(v));
  }
  return 'https://www.getmanfred.com/ofertas-empleo?' + params.toString();
}

export default {
  id: 'manfred',
  label: 'Manfred',
  linkHost: 'www.getmanfred.com',
  settleMs: 4000,
  noResultsMarkers: NO_RESULTS_MARKERS,
  hostMatch,
  buildSearchUrl,
  extractCards: extractManfredCards,
  // Per-offer detail pass: shared generic extractor (portals/detail.js);
  // the selector argument is optional and unverified (docs/portals.md).
  extractDetail: extractDetailText,
  detailArgs: [null],
  // The slug part of an offer URL cannot be derived from the id.
  canonicalUrl: () => null,
};
