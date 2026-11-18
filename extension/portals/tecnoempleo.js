// Tecnoempleo portal adapter (add-portal skill). Contract: see
// portals/registry.js. IN-TAB READER ONLY: the owner's Chrome reads the page
// at low volume; the bot never fetches Tecnoempleo (its robots.txt disallows
// AI crawlers), so its SourceSpec is `inline_only`.

import { extractDetailText } from './detail.js';

// Cards of a results page (recon sample, 2026-10): `div.p-3.border.rounded.mb-3.bg-white`
// with the title in `h3 a` (title attr), company `a.link-muted`, location the
// first `<b>` and the date `dd/mm/yyyy`. The id is `rf-<20 hex>` taken from
// the offer link (the `a[name^=rf-]` anchor precedes the card, outside it).
// Tecnoempleo has no recency filter (newest first), so cards older than 7
// days by their card date are dropped; a card without a date is kept.
function extractTecnoempleoCards() {
  return new Promise((resolve) => {
    setTimeout(() => {
      const results = [];
      const seen = new Set();
      const text = (el) => (el ? (el.innerText || '').trim() : '');
      const now = Date.now();
      document.querySelectorAll('div.p-3.border.rounded.mb-3.bg-white').forEach((card) => {
        const h3 = card.querySelector('h3');
        const a = h3 ? h3.querySelector('a') : null;
        const href = a ? (a.getAttribute('href') || '').split('#')[0].split('?')[0] : '';
        const m = href.match(/\/(rf-[0-9a-f]{20})(?![0-9a-f])/);
        if (!m || seen.has(m[1])) return;
        const dm = text(card).match(/(?:^|\D)(\d{2})\/(\d{2})\/(\d{4})(?!\d)/);
        if (dm) {
          const posted = Date.UTC(Number(dm[3]), Number(dm[2]) - 1, Number(dm[1]));
          if (Math.floor((now - posted) / 86400000) > 7) return;
        }
        seen.add(m[1]);
        const link = href.startsWith('http') ? href : 'https://www.tecnoempleo.com' + href;
        results.push({
          id: m[1],
          title: (a.getAttribute('title') || text(a)).slice(0, 200),
          company: text(card.querySelector('a.link-muted')),
          location: text(card.querySelector('b')),
          link,
        });
      });
      resolve(results);
    }, 1500);
  });
}

export const NO_RESULTS_MARKERS = ['no hemos encontrado ofertas de empleo'];

function hostMatch(url) {
  try {
    const host = new URL(String(url).includes('://') ? url : 'https:' + url).hostname;
    return host === 'tecnoempleo.com' || host.endsWith('.tecnoempleo.com');
  } catch (_) {
    return false;
  }
}

// Province ids (`pr`) verified from the recon: Barcelona 240, Madrid 263. Any
// other location (or remote) omits `pr`: the search runs on the keywords alone.
const PROVINCE_IDS = { barcelona: '240', madrid: '263' };

function buildSearchUrl({ keywords = '', location = '', extra = {} } = {}) {
  const params = new URLSearchParams();
  const kw = String(keywords).trim();
  if (kw) params.set('te', kw);
  const loc = String(location).split(',')[0].trim().toLowerCase();
  if (PROVINCE_IDS[loc]) params.set('pr', PROVINCE_IDS[loc]);
  for (const [k, v] of Object.entries(extra || {})) {
    if (v === null || v === undefined || v === '') params.delete(k);
    else params.set(k, String(v));
  }
  return 'https://www.tecnoempleo.com/ofertas-trabajo/?' + params.toString();
}

export default {
  id: 'tecnoempleo',
  label: 'Tecnoempleo',
  linkHost: 'www.tecnoempleo.com',
  settleMs: 2500,
  noResultsMarkers: NO_RESULTS_MARKERS,
  hostMatch,
  buildSearchUrl,
  extractCards: extractTecnoempleoCards,
  // Per-offer detail pass: shared generic extractor (portals/detail.js);
  // the selector argument is optional and unverified (docs/portals.md).
  extractDetail: extractDetailText,
  detailArgs: [null],
  // The slug part of an offer URL cannot be derived from the id.
  canonicalUrl: () => null,
};
