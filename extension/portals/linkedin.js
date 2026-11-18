// LinkedIn portal adapter (docs/extension-spec.md 5.2). The adapter contract
// is documented in portals/registry.js. No detail pass: offers are sent to the
// bot with the card fields only.

const VIEW_URL = 'https://www.linkedin.com/jobs/view/';

// Page function: cards of a LinkedIn job search results page, as a promise of
// [{ id, title, company, location, link }]. Injected with
// chrome.scripting.executeScript, so it must stay self-contained (no imports,
// no module-scope bindings) and keep its name.
async function extractJobsFromPage() {
  // Whole-line badges, dates and hints LinkedIn mixes into a card's text.
  const noise = [
    /^(promoted|promocionado|sponsored|patrocinado)$/,
    /^(viewed|visto|applied|aplicados?|solicitado|saved|guardado)$/,
    /^(new|nuevo|reposted|publicado de nuevo|easy apply|solicitud sencilla)$/,
    /^(today|yesterday|hoy|ayer)$/,
    /^(reposted |posted )?\d+\+? (second|minute|hour|day|week|month|year)s? ago$/,
    /^(publicado |volvió a publicarse )?hace (\d+|un|una|más de \d+) .+$/,
    /^\d+\+? (segundos?|minutos?|horas?|d[ií]as?|semanas?|mes|meses|años?)$/,
    /^.*\b(actively )?(evaluating|reviewing) applicants$/,
    /^.*\bevaluando solicitudes$/,
    /^.*\bmenos de \d+ solicitantes$/,
    /^.*\bcontactos? trabajan?\b.*$/,
    /^.*\bantiguos? alumnos?\b.*$/,
    /^(\d+|un|una|tu|tus)\b.*\bde contacto\b.*$/,
  ];
  const isNoise = (line) => noise.some((re) => re.test(line.toLowerCase()));
  const linesOf = (el) => String((el && el.innerText) || '').split('\n');
  const idFromLink = (a) => {
    const match = String((a && a.getAttribute('href')) || '').match(/\/jobs\/view\/(\d+)/);
    return match ? match[1] : null;
  };
  const jobLink = '[href*="/jobs/view/"]';

  // Lazily rendered cards appear once the list and the window are scrolled.
  const scrollToEnd = (target) => {
    try {
      target.scrollTo(0, Number(target.scrollHeight) || 1e6);
    } catch (e) {
      // not scrollable
    }
  };
  const list = document.querySelector('.jobs-search-results-list')
    || document.querySelector('.jobs-search__results-list')
    || document.querySelector('main');
  if (list) scrollToEnd(list);
  scrollToEnd(window);
  await new Promise((resolve) => setTimeout(resolve, 1500));

  const cards = [];
  const taken = new Set();
  const add = (id, lines) => {
    if (!id || taken.has(id)) return;
    taken.add(id);
    const kept = lines.map((line) => line.trim()).filter((line) => line && !isNoise(line));
    const [title = '', company = '', location = ''] = kept;
    cards.push({ id, title: title.slice(0, 200), company, location, link: `https://www.linkedin.com/jobs/view/${id}/` });
  };

  // 1. Current UI: the id is in the card's componentkey.
  document.querySelectorAll('[componentkey^="job-card-component-ref-"]').forEach((el) => {
    const match = String(el.getAttribute('componentkey')).match(/^job-card-component-ref-(\d+)$/);
    if (match) add(match[1], linesOf(el));
  });

  // 2. Legacy results list items that link to an offer.
  if (!cards.length) {
    document.querySelectorAll('li.scaffold-layout__list-item').forEach((item) => {
      const id = idFromLink(item.querySelector(`a${jobLink}`));
      if (id) add(id, linesOf(item));
    });
  }

  // 3. Last resort: any offer link, read with the block around it.
  if (!cards.length) {
    document.querySelectorAll(`a${jobLink}`).forEach((a) => {
      const id = idFromLink(a);
      if (!id) return;
      const block = a.closest('li') || a.closest('[data-job-id]') || a.parentElement;
      add(id, [linesOf(a)[0] || '', ...linesOf(block)]);
    });
  }
  return cards;
}

// LinkedIn's own empty-results copy (English, Spanish, Catalan).
const NO_RESULTS_MARKERS = [
  'no matching jobs found',
  'no se (?:ha|han) encontrado (?:ning[uú]n[oa]? )?(?:empleos?|ofertas?|trabajos?|resultados?)',
  'no se encontraron (?:ning[uú]n[oa]? )?(?:empleos?|ofertas?|trabajos?|resultados?)',
  'no hemos encontrado (?:ning[uú]n[oa]? )?(?:empleos?|ofertas?|trabajos?|resultados?)',
  'no hay ofertas (?:de trabajo )?que coincidan',
  'no hem trobat (?:cap )?(?:feina|feines|oferta|ofertes|resultats?)',
];

// linkedin.com or any subdomain; a URL without a scheme is read as https.
function hostMatch(url) {
  const text = String(url || '').trim();
  if (!text) return false;
  let withScheme = text;
  if (text.startsWith('//')) withScheme = `https:${text}`;
  else if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(text)) withScheme = `https://${text}`;
  try {
    const host = new URL(withScheme).hostname.toLowerCase();
    return host === 'linkedin.com' || host.endsWith('.linkedin.com');
  } catch {
    return false;
  }
}

// Past week, newest first; `extra` entries override or (empty) remove a
// parameter.
function buildSearchUrl({ keywords = '', location = '', extra = {} } = {}) {
  const query = new URLSearchParams();
  const words = String(keywords || '').trim();
  const place = String(location || '').trim();
  if (words) query.set('keywords', words);
  if (place) query.set('location', place);
  query.set('f_TPR', 'r604800');
  query.set('sortBy', 'DD');
  for (const [name, value] of Object.entries(extra || {})) {
    if (value === null || value === undefined || value === '') query.delete(name);
    else query.set(name, String(value));
  }
  return `https://www.linkedin.com/jobs/search/?${query}`;
}

function canonicalUrl(id) {
  return `${VIEW_URL}${id}/`;
}

// A login wall replaces the results page when the session is gone.
function isSessionExpired(tabUrl) {
  const url = String(tabUrl || '');
  return !url || ['/login', '/authwall', '/checkpoint'].some((part) => url.includes(part));
}

export default {
  id: 'linkedin',
  label: 'LinkedIn',
  linkHost: 'www.linkedin.com',
  settleMs: 2500,
  noResultsMarkers: NO_RESULTS_MARKERS,
  hostMatch,
  buildSearchUrl,
  extractCards: extractJobsFromPage,
  canonicalUrl,
  isSessionExpired,
};
