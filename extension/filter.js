// Title/company filter for LinkedIn Beacons (feature: beacon-title-filter,
// odd/tasks/beacon-title-filter.md). Pure, side-effect-free module: no
// chrome.* APIs here, so it can be imported both by the background service
// worker (manifest.json declares "type": "module", so a plain ES module
// import works there) and by Node for tests (see
// tests/fixtures/extension_filter_harness.js and tests/test_extension_filter.js).
//
// Whole-word semantics: after normalize(), text and terms are sequences of
// alphanumeric tokens separated by single spaces. A term matches only when it
// appears as one or more WHOLE tokens bounded by the start/end of the string
// or another space — never as a substring inside a longer token. This is a
// deliberate choice: "Engineer" does NOT match "Engineering", "Senior" does
// NOT match "Seniority", "AI" does NOT match "maintain" or "said", and "IA"
// does NOT match "Media". Multi-word terms (e.g. "Full Stack") match as a
// contiguous phrase of whole tokens, so hyphens/spaces are interchangeable
// ("Full-Stack" and "Full Stack" both normalize to "full stack"). A
// concatenated variant like "Fullstack" is intentionally NOT matched by
// "Full Stack" — normalize() never merges or splits tokens, only strips
// punctuation/diacritics and re-splits on whitespace, so treating "fullstack"
// as equivalent would require an additional, separate term in the list.

/**
 * Lowercase, strip diacritics, and turn every non-alphanumeric run into a
 * single space. Collapses repeated whitespace and trims the ends.
 * @param {string} text
 * @returns {string}
 */
function normalize(text) {
  return String(text || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '') // strip combining diacritical marks
    // Punctuation-bearing tech names would otherwise degrade to ambiguous
    // tokens ("c#" -> "c", ".net" -> "net"). Map them to distinct tokens
    // first so a term like "C#" or ".NET" matches only the real thing:
    // "C#/.NET" and "ASP.NET" match, but plain "C", "C++" or "net zero" do not.
    .replace(/(^|[^a-z0-9])c#/g, '$1csharp ')
    .replace(/\.net(?![a-z0-9])/g, ' dotnet ')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Whole-word/phrase match of `term` inside `normalizedText`. `normalizedText`
 * must already be normalize()-d by the caller; `term` is normalized here.
 * @param {string} normalizedText
 * @param {string} term
 * @returns {boolean}
 */
function matchesTerm(normalizedText, term) {
  const normTerm = normalize(term);
  if (!normTerm) return false;
  const pattern = new RegExp(`(?:^|\\s)${escapeRegExp(normTerm)}(?:$|\\s)`);
  return pattern.test(normalizedText);
}

/**
 * @param {{title?: string, company?: string}} job
 * @param {{enabled?: boolean, includeGroups?: string[][], excludeTerms?: string[]}} filter
 * @returns {boolean}
 */
function passesFilter(job, filter) {
  if (!filter || !filter.enabled) return true;

  const title = normalize(job && job.title);
  const company = normalize(job && job.company);

  const includeGroups = (filter.includeGroups || []).filter(
    (group) => Array.isArray(group) && group.length > 0
  );
  for (const group of includeGroups) {
    const groupMatches = group.some((term) => matchesTerm(title, term));
    if (!groupMatches) return false;
  }

  const excludeTerms = filter.excludeTerms || [];
  for (const term of excludeTerms) {
    if (matchesTerm(title, term) || matchesTerm(company, term)) return false;
  }

  return true;
}

function stripQuotes(raw) {
  const t = String(raw).trim();
  if (t.length >= 2) {
    const first = t[0];
    const last = t[t.length - 1];
    if ((first === '"' && last === '"') || (first === "'" && last === "'")) {
      return t.slice(1, -1).trim();
    }
  }
  return t;
}

/**
 * Options-UI textarea format: one include group per line, terms separated by
 * commas. Blank lines and empty terms are dropped; surrounding quotes on a
 * term are stripped.
 * @param {string} text
 * @returns {string[][]}
 */
function parseIncludeGroups(text) {
  return String(text || '')
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .map((line) =>
      line
        .split(',')
        .map(stripQuotes)
        .filter((term) => term.length > 0)
    )
    .filter((group) => group.length > 0);
}

/**
 * Options-UI textarea format: exclude terms separated by commas and/or
 * newlines. Blank entries dropped; surrounding quotes stripped.
 * @param {string} text
 * @returns {string[]}
 */
function parseExcludeTerms(text) {
  return String(text || '')
    .split(/[,\n]/)
    .map(stripQuotes)
    .filter((term) => term.length > 0);
}

/** Inverse of parseIncludeGroups: one line per group, comma-joined terms. */
function formatIncludeGroups(groups) {
  return (groups || []).map((group) => (group || []).join(', ')).join('\n');
}

/** Inverse of parseExcludeTerms: comma-joined terms. */
function formatExcludeTerms(terms) {
  return (terms || []).join(', ');
}

// Reproduces the owner's current classic-search boolean filter (documented in
// odd/tasks/beacon-title-filter.md): include groups are ANDed, terms inside a
// group are ORed and checked against the TITLE only; exclude terms are
// checked against TITLE and COMPANY.
// Companies both profiles drop (checked against TITLE and COMPANY). Single
// source of truth: the ai and backend profiles spread it into excludeTerms.
const COMPANY_BLOCKLIST = [
  'Bending Spoons', 'Crossing Hurdles', 'Joinrs', 'Evolve', 'Hire Feed',
  'Quik Hire Staffing', 'micro1', 'BJAK',
];

const DEFAULT_TITLE_FILTER = {
  enabled: true,
  includeGroups: [
    [
      'AI', 'IA', 'Artificial Intelligence', 'Inteligencia Artificial', 'GenAI',
      'Generative AI', 'LLM', 'LLMOps', 'MLOps', 'RAG', 'Agentic', 'AI Agent',
      'Machine Learning', 'NLP', 'Prompt',
    ],
    ['Engineer', 'Developer', 'Ingeniero', 'Desarrollador', 'Programador', 'Scientist', 'Científico'],
  ],
  excludeTerms: [
    'Full Stack', 'Freelance', 'Java', 'JavaScript', 'TypeScript', 'Senior',
    'Lead', 'Staff', 'Principal', 'Architect', 'Arquitecto',
    ...COMPANY_BLOCKLIST,
  ],
};

// Backend / software developer profile (backend-profile T1,
// odd/tasks/backend-profile.md). One group of role terms (no AI term required);
// exclusions: seniority, other-stack/front roles, and the same company
// blocklist as the ai profile. ".NET" and "C#" rely on normalize()'s
// punctuation mapping above.
const BACKEND_TITLE_FILTER = {
  enabled: true,
  includeGroups: [
    [
      'Backend', 'Back-end', 'Back End', 'Software', 'Python', 'Django', 'FastAPI',
      'Developer', 'Engineer', 'Desarrollador', 'Programador', 'Ingeniero',
      'Desenvolupador', 'Enginyer',
    ],
  ],
  excludeTerms: [
    'Senior', 'Sr', 'Lead', 'Staff', 'Principal', 'Architect', 'Arquitecto', 'Head',
    'Manager', 'Director',
    'Java Developer', 'Java Engineer', 'Desarrollador Java', '.NET', 'C#', 'PHP',
    'Frontend', 'Front-end', 'Front End', 'React', 'Angular', 'Vue', 'iOS',
    'Android', 'Mobile', 'Salesforce', 'SAP', 'Golang', 'Ruby', 'Scala',
    ...COMPANY_BLOCKLIST, 'Freelance',
  ],
};

// Named profiles. A beacon picks one via `filterProfile`; missing/unknown -> 'ai'.
const DEFAULT_TITLE_FILTERS = { ai: DEFAULT_TITLE_FILTER, backend: BACKEND_TITLE_FILTER };

/**
 * Deep-copy a title filter. Mutating the returned object, or one of its
 * includeGroups sub-arrays, must never mutate `filter` itself — the single
 * caller that matters is DEFAULT_TITLE_FILTER, which every resolved/cleaned
 * filter is ultimately derived from.
 * @param {{enabled: boolean, includeGroups: string[][], excludeTerms: string[]}} filter
 * @returns {{enabled: boolean, includeGroups: string[][], excludeTerms: string[]}}
 */
function cloneTitleFilter(filter) {
  return {
    enabled: filter.enabled,
    includeGroups: filter.includeGroups.map((group) => [...group]),
    excludeTerms: [...filter.excludeTerms],
  };
}

/**
 * True when `raw` is object-shaped enough to attempt a repair via
 * cleanTitleFilter() — a non-null, non-array object. A missing/malformed
 * `includeGroups`/`excludeTerms` is NOT disqualifying here: cleanTitleFilter
 * repairs each field independently from DEFAULT_TITLE_FILTER, so a
 * partially-shaped stored filter (e.g. `enabled` + `excludeTerms` but no
 * `includeGroups`) is REPAIRED field by field rather than thrown away
 * wholesale. Only a value with nothing object-shaped to repair — `null`, a
 * string, a number, an array — is disqualifying.
 * @param {unknown} raw
 * @returns {boolean}
 */
function isTitleFilterLike(raw) {
  return raw !== null && typeof raw === 'object' && !Array.isArray(raw);
}

/**
 * Coerce a stored (hand-edited or import-sourced) title filter into a
 * well-shaped one, repairing missing/malformed pieces from
 * DEFAULT_TITLE_FILTER field by field instead of rejecting the whole value.
 * Options-UI single source of truth for save()/load()/importConfig().
 * `defaults` is the profile whose defaults repair missing pieces (ai by default).
 * @param {unknown} raw
 * @param {{enabled: boolean, includeGroups: string[][], excludeTerms: string[]}} [defaults]
 * @returns {{enabled: boolean, includeGroups: string[][], excludeTerms: string[]}}
 */
function cleanTitleFilter(raw, defaults = DEFAULT_TITLE_FILTER) {
  if (!raw || typeof raw !== 'object') return cloneTitleFilter(defaults);
  return {
    enabled: raw.enabled !== false,
    includeGroups: Array.isArray(raw.includeGroups)
      ? raw.includeGroups.map((g) => (Array.isArray(g) ? g.map(String) : []))
      : cloneTitleFilter(defaults).includeGroups,
    excludeTerms: Array.isArray(raw.excludeTerms)
      ? raw.excludeTerms.map(String)
      : cloneTitleFilter(defaults).excludeTerms,
  };
}

/**
 * Resolve the effective title filter from a `chrome.storage.local.get()`
 * result (relevant keys: `titleFilter`, `settings`).
 *
 * Storage contract: the options page persists the filter under its OWN
 * top-level `titleFilter` key (see options.js's save()/load()), never inside
 * `settings` — so the top-level key wins whenever it is present and
 * object-shaped, REPAIRED field by field via cleanTitleFilter() rather than
 * discarded wholesale when only part of it is malformed (robustness
 * follow-up: a top-level filter missing just `includeGroups`, say, used to
 * be thrown away entirely in favor of legacy/defaults). A filter nested
 * under `settings.titleFilter` (written by builds that predate the
 * top-level key) is used only as a fallback under the same repair rule, and
 * a value with nothing object-shaped in either place falls back to a fresh
 * copy of DEFAULT_TITLE_FILTER. The returned object is always a copy:
 * mutating it never mutates the stored input or DEFAULT_TITLE_FILTER.
 * @param {{titleFilter?: unknown, settings?: {titleFilter?: unknown}}} data
 * @returns {{enabled: boolean, includeGroups: string[][], excludeTerms: string[]}}
 */
function resolveTitleFilter(data) {
  const defaults = profileDefaults(data, 'ai');
  const topLevel = data && data.titleFilter;
  if (isTitleFilterLike(topLevel)) return cleanTitleFilter(topLevel, defaults);
  const legacy = data && data.settings && data.settings.titleFilter;
  if (isTitleFilterLike(legacy)) return cleanTitleFilter(legacy, defaults);
  return cloneTitleFilter(defaults);
}

/**
 * The defaults of a profile: the filter the bot's profile serves
 * (`data.profileTitleFilters`, stored by the options page after "Sugerir
 * búsquedas desde mi perfil") when present and object-shaped, else the
 * built-in. Always usable as `defaults` for cleanTitleFilter().
 * @param {{profileTitleFilters?: unknown}} data
 * @param {'ai'|'backend'} name
 */
function profileDefaults(data, name) {
  const builtin = name === 'backend' ? BACKEND_TITLE_FILTER : DEFAULT_TITLE_FILTER;
  const served = data && data.profileTitleFilters;
  const raw = isTitleFilterLike(served) ? served[name] : null;
  return isTitleFilterLike(raw) ? cleanTitleFilter(raw, builtin) : builtin;
}

/**
 * The title filters a bridge response carries (`titleFilters`, served only
 * when the profile defines its own), cleaned and ready to store as
 * `profileTitleFilters`; null when the response carries none.
 * @param {unknown} payload parsed JSON body of GET /searches/suggested
 * @returns {{ai?: object, backend?: object}|null}
 */
function parseProfileTitleFilters(payload) {
  const raw = payload && payload.titleFilters;
  if (!isTitleFilterLike(raw)) return null;
  const out = {};
  if (isTitleFilterLike(raw.ai)) out.ai = cleanTitleFilter(raw.ai);
  if (isTitleFilterLike(raw.backend)) {
    out.backend = cleanTitleFilter(raw.backend, BACKEND_TITLE_FILTER);
  }
  return Object.keys(out).length ? out : null;
}

const PROFILE_FILTER_KEYS = [
  'settings', 'titleFilter', 'titleFilterBackend', 'profileTitleFilters',
];

/**
 * Sync the stored `profileTitleFilters` with a bridge response: store the
 * served filters, or REMOVE the key when the response carries none (the owner
 * reverted to the built-in filters, or points at a default-profile bot), so a
 * stale profile never lingers as the defaults. Returns the storage data the
 * resolvers read (`resolveTitleFilters(data)`).
 * @param {{set: Function, remove: Function, get: Function}} area chrome.storage.local
 * @param {unknown} payload parsed JSON body of GET /searches/suggested
 */
async function adoptProfileTitleFilters(area, payload) {
  const served = parseProfileTitleFilters(payload);
  if (served) await area.set({ profileTitleFilters: served });
  else await area.remove('profileTitleFilters');
  return area.get(PROFILE_FILTER_KEYS);
}

/**
 * Profile name for a beacon-ish value: 'backend' only when explicitly so;
 * everything else (missing, null, unknown) is 'ai' so beacons stored before
 * profiles existed keep today's behavior.
 * @param {unknown} value
 * @returns {'ai'|'backend'}
 */
function normalizeProfile(value) {
  return value === 'backend' ? 'backend' : 'ai';
}

/**
 * Resolve both profiles from a `chrome.storage.local.get()` result. Storage
 * shape: the existing top-level `titleFilter` key IS the `ai` profile (so the
 * owner's stored filter is never lost); the backend profile lives under its
 * own top-level `titleFilterBackend` key. Each is repaired independently
 * from its own defaults.
 * @param {{titleFilter?: unknown, titleFilterBackend?: unknown, settings?: object}} data
 * @returns {{ai: object, backend: object}}
 */
function resolveTitleFilters(data) {
  const backendRaw = data && data.titleFilterBackend;
  const backendDefaults = profileDefaults(data, 'backend');
  return {
    ai: resolveTitleFilter(data),
    backend: isTitleFilterLike(backendRaw)
      ? cleanTitleFilter(backendRaw, backendDefaults)
      : cloneTitleFilter(backendDefaults),
  };
}

/**
 * The filter a beacon must be checked against.
 * @param {{ai: object, backend: object}} filters resolveTitleFilters() result
 * @param {{filterProfile?: unknown}|null|undefined} beacon
 */
function filterForBeacon(filters, beacon) {
  return filters[normalizeProfile(beacon && beacon.filterProfile)];
}

export {
  normalize,
  matchesTerm,
  passesFilter,
  parseIncludeGroups,
  parseExcludeTerms,
  formatIncludeGroups,
  formatExcludeTerms,
  DEFAULT_TITLE_FILTER,
  BACKEND_TITLE_FILTER,
  DEFAULT_TITLE_FILTERS,
  cloneTitleFilter,
  cleanTitleFilter,
  resolveTitleFilter,
  parseProfileTitleFilters,
  adoptProfileTitleFilters,
  normalizeProfile,
  resolveTitleFilters,
  filterForBeacon,
};
