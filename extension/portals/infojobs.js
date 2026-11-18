// InfoJobs portal adapter (portal-adapters T1). The extraction logic moved
// here verbatim from background.js; the adapter contract is documented in
// portals/registry.js.

import { slugify } from '../slug.js';

// Cards of an InfoJobs results page. Verified against a saved real page
// (2026-10): every card is `.ij-OfferCard`; the title link is
// `.ij-OfferCardContent-description-link` with `href=".../<city>/<slug>/of-i<30 hex>?<tracking>"`,
// the company `.ij-OfferCardContent-description-subtitle-link` and the first
// `.ij-OfferCardContent-description-list-item-truncate` is the location. The
// same offer can appear twice (promoted + organic): the id dedupes. Older
// `/of-<32 chars>` links without the card markup fall back to the text lines
// of the enclosing block. The link is canonicalised: no query, no fragment.
function extractInfojobsCards() {
  return new Promise((resolve) => {
    setTimeout(() => {
      const results = [];
      const seen = new Set();
      const infojobsNoise =
        /^(nuevo|new|promocionada|destacada|featured|solicitud sencilla|incorporación inmediata|más de 30 días|\d+ (día|días|hora|horas|minuto|minutos)(\s+de publicación)?|hace .+|\d+)\.?$/i;
      const idRe = /\/of-(i[0-9a-f]{30}|[a-z0-9]{32})(?![a-z0-9])/;
      const text = (el) => (el ? (el.innerText || '').trim() : '');
      const anchors = document.querySelectorAll('a[href*="/of-"]');
      anchors.forEach((a) => {
        const raw = a.getAttribute('href') || '';
        const m = raw.match(idRe);
        if (!m) return;
        const id = m[1];
        if (seen.has(id)) return;
        const href = raw.split('#')[0].split('?')[0];
        const link = href.startsWith('//') ? 'https:' + href
          : href.startsWith('http') ? href
          : 'https://www.infojobs.net' + href;
        const structured = a.closest('.ij-OfferCard');
        let title;
        let company;
        let location;
        if (structured) {
          title = text(structured.querySelector('.ij-OfferCardContent-description-title-link'))
            || a.getAttribute('aria-label') || text(a);
          company = text(structured.querySelector('.ij-OfferCardContent-description-subtitle-link'));
          location = text(structured.querySelector('.ij-OfferCardContent-description-list-item-truncate'));
        } else {
          const card = a.closest('div') || a.parentElement;
          const lines = (card?.innerText || '')
            .split('\n')
            .map((l) => l.trim())
            .filter((l) => l && !infojobsNoise.test(l));
          [title = '', company = '', location = ''] = lines;
        }
        seen.add(id);
        results.push({
          id,
          title: title.slice(0, 200),
          company,
          location,
          link,
        });
      });
      resolve(results);
    }, 1500);
  });
}

function extractInfojobsDetail() {
  const NAV = [
    'Buscar empleo',
    'Formación',
    'Buscar empresas',
    'Salarios',
    'ACCESO EMPRESAS',
    'ACCESO CANDIDATOS',
  ];
  const raw = document.querySelector('main')?.innerText || '';
  const text = raw
    .split('\n')
    .map((s) => s.trim())
    .filter((s) => s && !NAV.includes(s))
    .join('\n');
  if (document.title === 'InfoJobs' && text.length < 200) return null;
  return text.slice(0, 20000);
}

export const NO_RESULTS_MARKERS = [
  'no se (?:han? )?encontrado (?:ning[uú]n[ao]? )?(?:empleos?|ofertas?|trabajos?|resultados)',
  'no se encontraron (?:empleos|ofertas|trabajos|resultados)',
  'no hemos encontrado (?:ning[uú]n[ao]? )?(?:empleos?|ofertas?|trabajos?|resultados)',
  'no hay ofertas (?:de trabajo )?que coincidan',
];

function hostMatch(url) {
  try {
    const host = new URL(String(url).includes('://') ? url : 'https:' + url).hostname;
    return host === 'infojobs.net' || host.endsWith('.infojobs.net');
  } catch (_) {
    return false;
  }
}

// Provinces whose InfoJobs `provinceIds` value was verified (the owner's saved
// search: Barcelona = 9). Anything else is NOT guessed: an unknown location
// drops `provinceIds`/`searchByType` and the search runs nationwide on the
// keywords alone (the profile's location is then only a hint for the bot).
const PROVINCE_IDS = { barcelona: '9' };

// Search URL from {keywords, location, extra}, in the format of the owner's
// real search: `/jobsearch/search-results/list.xhtml?keyword&provinceIds&
// searchByType=province&countryIds=17&sinceDate=_7_DAYS&sortBy&page=1`.
// `sortBy=RELEVANCE` is the value seen in the owner's URL; the date sort
// (`PUBLICATION_DATE`) is unverified, so it can be set through `extra`.
// `extra` params override or add to the defaults (an empty value drops one).
function buildSearchUrl({ keywords = '', location = '', extra = {} } = {}) {
  const params = new URLSearchParams();
  const kw = String(keywords).trim();
  if (kw) params.set('keyword', kw);
  const province = PROVINCE_IDS[slugify(String(location).split(',')[0])];
  if (province) {
    params.set('provinceIds', province);
    params.set('searchByType', 'province');
  }
  params.set('countryIds', '17');
  params.set('sinceDate', '_7_DAYS');
  params.set('sortBy', 'RELEVANCE');
  params.set('page', '1');
  for (const [k, v] of Object.entries(extra || {})) {
    if (v === null || v === undefined || v === '') params.delete(k);
    else params.set(k, String(v));
  }
  return 'https://www.infojobs.net/jobsearch/search-results/list.xhtml?' + params.toString();
}

export default {
  id: 'infojobs',
  label: 'InfoJobs',
  linkHost: 'www.infojobs.net',
  settleMs: 2500,
  noResultsMarkers: NO_RESULTS_MARKERS,
  hostMatch,
  buildSearchUrl,
  extractCards: extractInfojobsCards,
  // Per-offer detail pass (background loads each NEW offer's page and runs this).
  extractDetail: extractInfojobsDetail,
  // The city/slug part of an InfoJobs offer URL cannot be derived from the id:
  // callers keep the link the card carried.
  canonicalUrl: () => null,
};
