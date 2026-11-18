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

// Role families (role-families T6, odd/tasks/role-families.md). The bot serves
// the profile's families with the suggested searches (`families`); the options
// page stores them as `profileFamilies` ([{id, label, default}], declared
// order). Without a stored list the built-in two apply, exactly as before.
// Ids mirror cva/families.py FAMILY_ID_RE + FAMILY_ID_MAX_LEN.
const FAMILY_ID_RE = /^[a-z][a-z0-9_]{0,31}$/;
const BUILTIN_FAMILIES = [
  { id: 'ai', label: 'AI', default: true },
  { id: 'backend', label: 'Backend', default: false },
];
// A declared family the bot sent no filter for: the bot's own default for a
// new family (no include group, the company blocklist excluded).
const FAMILY_TITLE_FILTER = { enabled: true, includeGroups: [], excludeTerms: COMPANY_BLOCKLIST.slice() };

const isBuiltinFamily = (id) => id === 'ai' || id === 'backend';
const hasOwn = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);

/**
 * A clean family list from untrusted data (a bridge response or storage):
 * valid unique ids only, a string label (the id when missing), exactly one
 * default (the first flagged one, else the first family). Null when nothing
 * usable is left.
 * @param {unknown} raw
 * @returns {{id: string, label: string, default: boolean}[]|null}
 */
function cleanFamilies(raw) {
  if (!Array.isArray(raw)) return null;
  const seen = new Set();
  const out = [];
  for (const item of raw) {
    if (!isTitleFilterLike(item)) continue;
    const id = item.id;
    if (typeof id !== 'string' || !FAMILY_ID_RE.test(id) || seen.has(id)) continue;
    seen.add(id);
    const label = typeof item.label === 'string' && item.label.trim() ? item.label.trim() : id;
    out.push({ id, label, default: item.default === true });
  }
  if (!out.length) return null;
  const first = out.findIndex((f) => f.default);
  return out.map((f, i) => ({ ...f, default: i === (first < 0 ? 0 : first) }));
}

/**
 * The family list of a `chrome.storage.local.get()` result (`profileFamilies`),
 * or a fresh copy of the built-in two.
 * @param {{profileFamilies?: unknown}} data
 */
function resolveFamilies(data) {
  return cleanFamilies(data && data.profileFamilies) || BUILTIN_FAMILIES.map((f) => ({ ...f }));
}

/** Id of the default family of a list (the built-in 'ai' for no list). */
function defaultFamilyId(families) {
  const list = Array.isArray(families) && families.length ? families : BUILTIN_FAMILIES;
  const found = list.find((f) => f && f.default);
  return (found || list[0]).id;
}

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
 * @param {string} name a family id ('ai', 'backend' or a declared one)
 */
function profileDefaults(data, name) {
  const builtin = name === 'backend' ? BACKEND_TITLE_FILTER
    : name === 'ai' ? DEFAULT_TITLE_FILTER : FAMILY_TITLE_FILTER;
  const served = data && data.profileTitleFilters;
  const raw = isTitleFilterLike(served) && hasOwn(served, name) ? served[name] : null;
  return isTitleFilterLike(raw) ? cleanTitleFilter(raw, builtin) : builtin;
}

/**
 * The title filters a bridge response carries (`titleFilters`, served only
 * when the profile defines its own), cleaned and ready to store as
 * `profileTitleFilters`; null when the response carries none.
 * Each served family's own `titleFilter` (role families) is added under its
 * id and wins over `titleFilters` for that id.
 * @param {unknown} payload parsed JSON body of GET /searches/suggested
 * @returns {Object<string, object>|null}
 */
function parseProfileTitleFilters(payload) {
  const raw = payload && payload.titleFilters;
  const out = {};
  if (isTitleFilterLike(raw)) {
    if (isTitleFilterLike(raw.ai)) out.ai = cleanTitleFilter(raw.ai);
    if (isTitleFilterLike(raw.backend)) {
      out.backend = cleanTitleFilter(raw.backend, BACKEND_TITLE_FILTER);
    }
  }
  const families = payload && Array.isArray(payload.families) ? payload.families : [];
  const ids = new Set((cleanFamilies(families) || []).map((f) => f.id));
  for (const item of families) {
    if (!isTitleFilterLike(item) || !ids.has(item.id) || !isTitleFilterLike(item.titleFilter)) continue;
    out[item.id] = cleanTitleFilter(item.titleFilter, profileDefaults({}, item.id));
  }
  return Object.keys(out).length ? out : null;
}

const PROFILE_FILTER_KEYS = [
  'settings', 'titleFilter', 'titleFilterBackend', 'profileTitleFilters',
  'profileFamilies', 'familyTitleFilters',
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
  // The family list rides on the same response (role families): stored when
  // the bot sends one, removed (built-ins again) when it does not.
  const families = cleanFamilies(payload && payload.families);
  if (families) await area.set({ profileFamilies: families });
  else await area.remove('profileFamilies');
  return area.get(PROFILE_FILTER_KEYS);
}

/**
 * Sync the stored family list with a GET /config response (read by every
 * round): store `profileFamilies` and add each family's served filter to the
 * `profileTitleFilters` defaults, or REMOVE `profileFamilies` (built-ins
 * again) when the response carries none. Other `profileTitleFilters` entries
 * (the suggested-searches `titleFilters`) and `familyTitleFilters` (the
 * owner's edits) are never touched.
 * @param {{set: Function, remove: Function, get: Function}} area chrome.storage.local
 * @param {unknown} payload parsed JSON body of GET /config
 */
async function adoptProfileFamilies(area, payload) {
  const families = cleanFamilies(payload && payload.families);
  if (!families) {
    const { profileFamilies } = await area.get('profileFamilies');
    if (profileFamilies !== undefined) await area.remove('profileFamilies');
    return;
  }
  const served = parseProfileTitleFilters({ families: payload.families }) || {};
  const { profileTitleFilters } = await area.get('profileTitleFilters');
  const defaults = isTitleFilterLike(profileTitleFilters) ? profileTitleFilters : {};
  await area.set({ profileFamilies: families, profileTitleFilters: { ...defaults, ...served } });
}

/**
 * Family id for a beacon-ish value. `families` is the profile's family list
 * (resolveFamilies()); without one the built-in two apply. A declared id is
 * kept; anything else (missing, null, unknown) is the default family, so
 * beacons stored before profiles existed keep today's behavior ('backend'
 * only when explicitly so, else 'ai').
 * @param {unknown} value
 * @param {{id: string, default?: boolean}[]} [families]
 * @returns {string}
 */
function normalizeProfile(value, families = BUILTIN_FAMILIES) {
  const list = Array.isArray(families) && families.length ? families : BUILTIN_FAMILIES;
  return list.some((f) => f && f.id === value) ? value : defaultFamilyId(list);
}

/**
 * Resolve every profile's filter from a `chrome.storage.local.get()` result.
 * Storage shape: the existing top-level `titleFilter` key IS the `ai` profile
 * (so the owner's stored filter is never lost); the backend profile lives
 * under its own top-level `titleFilterBackend` key; any other declared family
 * (`profileFamilies`) under `familyTitleFilters[<id>]`. Each is repaired
 * independently from its own defaults. `ai` and `backend` are always present.
 * @param {{titleFilter?: unknown, titleFilterBackend?: unknown, familyTitleFilters?: unknown,
 *   profileFamilies?: unknown, settings?: object}} data
 * @returns {Object<string, object>} {ai, backend, ...declared families}
 */
function resolveTitleFilters(data) {
  const backendRaw = data && data.titleFilterBackend;
  const backendDefaults = profileDefaults(data, 'backend');
  const out = {
    ai: resolveTitleFilter(data),
    backend: isTitleFilterLike(backendRaw)
      ? cleanTitleFilter(backendRaw, backendDefaults)
      : cloneTitleFilter(backendDefaults),
  };
  const stored = data && isTitleFilterLike(data.familyTitleFilters) ? data.familyTitleFilters : {};
  for (const { id } of cleanFamilies(data && data.profileFamilies) || []) {
    if (isBuiltinFamily(id)) continue;
    const defaults = profileDefaults(data, id);
    const raw = hasOwn(stored, id) ? stored[id] : null;
    out[id] = isTitleFilterLike(raw) ? cleanTitleFilter(raw, defaults) : cloneTitleFilter(defaults);
  }
  return out;
}

/**
 * The filter a beacon must be checked against: its family's when the family
 * is one of `filters` (resolveTitleFilters()), else the built-in rule
 * ('backend' or 'ai').
 * @param {Object<string, object>} filters resolveTitleFilters() result
 * @param {{filterProfile?: unknown}|null|undefined} beacon
 */
function filterForBeacon(filters, beacon) {
  const id = beacon && beacon.filterProfile;
  if (typeof id === 'string' && FAMILY_ID_RE.test(id) && hasOwn(filters, id)) return filters[id];
  return filters[normalizeProfile(id)];
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
  adoptProfileFamilies,
  normalizeProfile,
  resolveTitleFilters,
  filterForBeacon,
  FAMILY_ID_RE,
  BUILTIN_FAMILIES,
  cleanFamilies,
  resolveFamilies,
  defaultFamilyId,
  isBuiltinFamily,
};
