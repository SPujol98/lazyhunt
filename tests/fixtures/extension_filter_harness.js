'use strict';

// Node harness for extension/filter.js (feature: beacon-title-filter).
//
// filter.js is an ES module (matches manifest.json's background service
// worker "type": "module"), so it is loaded here through a dynamic import()
// from this plain CommonJS script — portable across Node versions, unlike
// relying on require(esm) support. Invoked by tests/test_extension_filter.py;
// exits 0 on success, non-zero (with a printed failure list) otherwise.

const path = require('path');
const { pathToFileURL } = require('url');

const failures = [];

function check(name, actual, expected) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) {
    failures.push(`${name}: expected ${e}, got ${a}`);
  }
}

function assertTrue(name, cond) {
  if (!cond) failures.push(`${name}: expected true, got false`);
}

function assertFalse(name, cond) {
  if (cond) failures.push(`${name}: expected false, got true`);
}

async function main() {
  const modUrl = pathToFileURL(
    path.resolve(__dirname, '..', '..', 'extension', 'filter.js')
  ).href;
  const {
    normalize,
    matchesTerm,
    passesFilter,
    parseIncludeGroups,
    parseExcludeTerms,
    DEFAULT_TITLE_FILTER,
    resolveTitleFilter,
    DEFAULT_TITLE_FILTERS,
    BACKEND_TITLE_FILTER,
    normalizeProfile,
    resolveTitleFilters,
    filterForBeacon,
    parseProfileTitleFilters,
    adoptProfileTitleFilters,
  } = await import(modUrl);

  // --- normalize -----------------------------------------------------------
  check('normalize lowercases', normalize('AI Engineer'), 'ai engineer');
  check(
    'normalize strips accents',
    normalize('Científico'),
    normalize('Cientifico')
  );
  check(
    'normalize turns hyphens/slashes into spaces',
    normalize('Full-Stack/Backend'),
    'full stack backend'
  );
  check('normalize collapses spaces', normalize('AI   Engineer  '), 'ai engineer');

  // --- matchesTerm: whole-word semantics ------------------------------------
  assertTrue('AI matches "AI Engineer"', matchesTerm(normalize('AI Engineer'), 'AI'));
  assertFalse('AI does not match "said"', matchesTerm(normalize('said'), 'AI'));
  assertFalse(
    'AI does not match "maintain"',
    matchesTerm(normalize('maintain the pipeline'), 'AI')
  );
  assertFalse('IA does not match "Media"', matchesTerm(normalize('Media Specialist'), 'IA'));
  assertTrue(
    'IA matches "Ingeniero de IA"',
    matchesTerm(normalize('Ingeniero de IA'), 'IA')
  );
  assertFalse(
    'Senior does not match "Seniority"',
    matchesTerm(normalize('Program Seniority Lead'), 'Senior')
  );
  assertTrue(
    'full stack matches "Full-Stack AI Engineer"',
    matchesTerm(normalize('Full-Stack AI Engineer'), 'full stack')
  );
  assertTrue(
    'full stack matches "Full Stack Engineer"',
    matchesTerm(normalize('Full Stack Engineer'), 'full stack')
  );
  assertTrue(
    'accent-insensitive: Cientifico matches Científico',
    matchesTerm(normalize('Científico de Datos'), 'Cientifico')
  );

  // --- parseIncludeGroups / parseExcludeTerms -------------------------------
  check(
    'parseIncludeGroups: one group per line, comma-separated terms',
    parseIncludeGroups('AI, IA, Machine Learning\nEngineer, Developer'),
    [
      ['AI', 'IA', 'Machine Learning'],
      ['Engineer', 'Developer'],
    ]
  );
  check(
    'parseIncludeGroups: drops empty lines and empty terms',
    parseIncludeGroups('AI,,\n\n\nEngineer,, ,'),
    [['AI'], ['Engineer']]
  );
  check(
    'parseIncludeGroups: strips surrounding quotes',
    parseIncludeGroups('"AI", \'IA\'\nEngineer'),
    [
      ['AI', 'IA'],
      ['Engineer'],
    ]
  );
  check(
    'parseExcludeTerms: comma or newline separated',
    parseExcludeTerms('Java, TypeScript\nSenior,,\nStaff'),
    ['Java', 'TypeScript', 'Senior', 'Staff']
  );
  check('parseIncludeGroups: empty text yields no groups', parseIncludeGroups(''), []);
  check('parseExcludeTerms: empty text yields no terms', parseExcludeTerms(''), []);

  // --- passesFilter: defaults reproduce the owner's current filter ----------
  check('DEFAULT_TITLE_FILTER matches the owner\'s current filter', DEFAULT_TITLE_FILTER, {
    enabled: true,
    includeGroups: [
      [
        'AI', 'IA', 'Artificial Intelligence', 'Inteligencia Artificial',
        'GenAI', 'Generative AI', 'LLM', 'LLMOps', 'MLOps', 'RAG', 'Agentic',
        'AI Agent', 'Machine Learning', 'NLP', 'Prompt',
      ],
      ['Engineer', 'Developer', 'Ingeniero', 'Desarrollador', 'Programador', 'Scientist', 'Científico'],
    ],
    excludeTerms: [
      'Full Stack', 'Freelance', 'Java', 'JavaScript', 'TypeScript', 'Senior',
      'Lead', 'Staff', 'Principal', 'Architect', 'Arquitecto', 'Bending Spoons',
      'Crossing Hurdles', 'Joinrs', 'Evolve', 'Hire Feed', 'Quik Hire Staffing',
      'micro1', 'BJAK',
    ],
  });

  // --- passesFilter: disabled / empty-list bypass ---------------------------
  assertTrue(
    'disabled filter passes everything',
    passesFilter(
      { title: 'Random Job Nobody Wants', company: 'Whatever Inc' },
      { enabled: false, includeGroups: [['nomatch']], excludeTerms: ['random'] }
    )
  );
  assertTrue(
    'empty include/exclude lists pass everything (when enabled)',
    passesFilter(
      { title: 'Anything At All', company: 'Anyone' },
      { enabled: true, includeGroups: [], excludeTerms: [] }
    )
  );

  // --- passesFilter: include groups are ANDed, terms inside ORed -----------
  assertTrue(
    'include OR: Machine Learning satisfies group 1',
    passesFilter(
      { title: 'Machine Learning Engineer', company: 'Acme' },
      DEFAULT_TITLE_FILTER
    )
  );
  assertFalse(
    'include AND: group 1 alone (no role term) is not enough',
    passesFilter({ title: 'AI Specialist', company: 'Acme' }, DEFAULT_TITLE_FILTER)
  );

  // --- passesFilter: include only checks the TITLE, never the company ------
  assertFalse(
    'company "AI Corp" alone does not satisfy the include groups',
    passesFilter({ title: 'Data Analyst', company: 'AI Corp' }, DEFAULT_TITLE_FILTER)
  );

  // --- passesFilter: exclude checks TITLE and COMPANY -----------------------
  assertFalse(
    'exclude on title (Senior)',
    passesFilter({ title: 'Senior AI Engineer', company: 'Acme' }, DEFAULT_TITLE_FILTER)
  );
  assertFalse(
    'exclude on company (micro1)',
    passesFilter({ title: 'AI Engineer', company: 'micro1' }, DEFAULT_TITLE_FILTER)
  );

  // --- passesFilter: case-insensitivity -------------------------------------
  assertTrue(
    'case-insensitive matching',
    passesFilter({ title: 'ai ENGINEER', company: '' }, DEFAULT_TITLE_FILTER)
  );

  // --- passesFilter: accents -------------------------------------------------
  assertTrue(
    'accent-insensitive include term (Científico / Cientifico)',
    passesFilter({ title: 'Cientifico de Datos IA', company: '' }, DEFAULT_TITLE_FILTER)
  );
  assertTrue(
    'accented title variant also passes',
    passesFilter({ title: 'Científico de Datos IA', company: '' }, DEFAULT_TITLE_FILTER)
  );

  // --- passesFilter: hyphen/space insensitivity ------------------------------
  assertFalse(
    'Full-Stack AI Engineer is excluded (hyphen == space)',
    passesFilter({ title: 'Full-Stack AI Engineer', company: '' }, DEFAULT_TITLE_FILTER)
  );

  // --- passesFilter: realistic titles from the owner's beacon feed ----------
  const realistic = [
    [{ title: 'AI Engineer', company: 'Acme Data' }, true],
    [{ title: 'Senior AI Engineer', company: '' }, false],
    [{ title: 'Junior AI Engineer', company: '' }, true],
    [{ title: 'Software engineer IA CIBER', company: '' }, true],
    [{ title: 'Ingeniero/Ingeniera de Soluciones de IA', company: '' }, true],
    [{ title: 'IA Developer', company: 'Globex Consulting' }, true],
    [{ title: 'Full Stack Developer AI', company: '' }, false],
    [{ title: 'Data Analyst', company: '' }, false],
  ];
  for (const [job, expected] of realistic) {
    check(
      `realistic title "${job.title}" (company "${job.company}") -> ${expected}`,
      passesFilter(job, DEFAULT_TITLE_FILTER),
      expected
    );
  }

  // --- resolveTitleFilter: storage-contract resolution order ---------------
  const CUSTOM_FILTER = {
    enabled: false,
    includeGroups: [['Custom']],
    excludeTerms: ['Nope'],
  };
  const LEGACY_FILTER = {
    enabled: true,
    includeGroups: [['Legacy']],
    excludeTerms: ['Old'],
  };

  check(
    'top-level titleFilter wins over legacy settings.titleFilter',
    resolveTitleFilter({
      titleFilter: CUSTOM_FILTER,
      settings: { titleFilter: LEGACY_FILTER },
    }),
    CUSTOM_FILTER
  );

  check(
    'legacy settings.titleFilter is used when the top-level key is missing',
    resolveTitleFilter({ settings: { titleFilter: LEGACY_FILTER } }),
    LEGACY_FILTER
  );

  check(
    'defaults are used when both the top-level and legacy keys are missing',
    resolveTitleFilter({}),
    DEFAULT_TITLE_FILTER
  );
  check(
    'defaults are used when data itself has no titleFilter or settings at all',
    resolveTitleFilter({ beacons: [] }),
    DEFAULT_TITLE_FILTER
  );

  // --- resolveTitleFilter: malformed stored values fall back safely --------
  check(
    'null top-level titleFilter falls back to legacy',
    resolveTitleFilter({ titleFilter: null, settings: { titleFilter: LEGACY_FILTER } }),
    LEGACY_FILTER
  );
  check(
    'string top-level titleFilter falls back to legacy',
    resolveTitleFilter({
      titleFilter: 'not-an-object',
      settings: { titleFilter: LEGACY_FILTER },
    }),
    LEGACY_FILTER
  );
  // --- resolveTitleFilter: a PARTIALLY valid top-level filter is REPAIRED,
  // never fully discarded in favor of legacy/defaults (beacon-title-filter
  // robustness follow-up: cleanTitleFilter already repairs each field
  // independently from DEFAULT_TITLE_FILTER — resolveTitleFilter's gate
  // used to throw that repair away by requiring BOTH arrays present) -------
  check(
    'top-level titleFilter missing includeGroups is repaired from defaults, not discarded',
    resolveTitleFilter({
      titleFilter: { enabled: true, excludeTerms: ['Custom'] },
      settings: { titleFilter: LEGACY_FILTER },
    }),
    {
      enabled: true,
      includeGroups: DEFAULT_TITLE_FILTER.includeGroups,
      excludeTerms: ['Custom'],
    }
  );
  check(
    'top-level titleFilter missing excludeTerms is repaired from defaults, not discarded',
    resolveTitleFilter({
      titleFilter: { enabled: true, includeGroups: [['Custom']] },
      settings: { titleFilter: LEGACY_FILTER },
    }),
    {
      enabled: true,
      includeGroups: [['Custom']],
      excludeTerms: DEFAULT_TITLE_FILTER.excludeTerms,
    }
  );
  check(
    'top-level titleFilter missing BOTH arrays is repaired (enabled kept), not discarded to legacy',
    resolveTitleFilter({
      titleFilter: { enabled: false },
      settings: { titleFilter: LEGACY_FILTER },
    }),
    {
      enabled: false,
      includeGroups: DEFAULT_TITLE_FILTER.includeGroups,
      excludeTerms: DEFAULT_TITLE_FILTER.excludeTerms,
    }
  );
  check(
    'null legacy settings.titleFilter falls back to defaults',
    resolveTitleFilter({ titleFilter: null, settings: { titleFilter: null } }),
    DEFAULT_TITLE_FILTER
  );
  check(
    'malformed legacy settings.titleFilter falls back to defaults',
    resolveTitleFilter({ titleFilter: undefined, settings: { titleFilter: 42 } }),
    DEFAULT_TITLE_FILTER
  );

  // --- resolveTitleFilter: the returned object is always a fresh copy ------
  {
    const resolvedDefault = resolveTitleFilter({});
    resolvedDefault.enabled = false;
    resolvedDefault.includeGroups[0].push('MUTATED');
    resolvedDefault.excludeTerms.push('MUTATED');
    assertFalse(
      'mutating the resolved defaults does not mutate DEFAULT_TITLE_FILTER.enabled',
      resolvedDefault.enabled === DEFAULT_TITLE_FILTER.enabled
    );
    assertFalse(
      'mutating the resolved defaults does not mutate DEFAULT_TITLE_FILTER.includeGroups',
      DEFAULT_TITLE_FILTER.includeGroups[0].includes('MUTATED')
    );
    assertFalse(
      'mutating the resolved defaults does not mutate DEFAULT_TITLE_FILTER.excludeTerms',
      DEFAULT_TITLE_FILTER.excludeTerms.includes('MUTATED')
    );

    const resolvedCustom = resolveTitleFilter({ titleFilter: CUSTOM_FILTER });
    resolvedCustom.includeGroups[0].push('MUTATED');
    assertFalse(
      'mutating a resolved top-level filter does not mutate the stored input',
      CUSTOM_FILTER.includeGroups[0].includes('MUTATED')
    );
  }

  // --- backend-profile T1: named profiles -----------------------------------
  check('ai profile IS the unchanged default', DEFAULT_TITLE_FILTERS.ai, DEFAULT_TITLE_FILTER);
  check('backend profile default export', DEFAULT_TITLE_FILTERS.backend, BACKEND_TITLE_FILTER);
  check('backend defaults: enabled', BACKEND_TITLE_FILTER.enabled, true);
  check('backend defaults: one include group', BACKEND_TITLE_FILTER.includeGroups, [[
    'Backend', 'Back-end', 'Back End', 'Software', 'Python', 'Django', 'FastAPI',
    'Developer', 'Engineer', 'Desarrollador', 'Programador', 'Ingeniero',
    'Desenvolupador', 'Enginyer',
  ]]);
  check('backend defaults: exclude terms', BACKEND_TITLE_FILTER.excludeTerms, [
    'Senior', 'Sr', 'Lead', 'Staff', 'Principal', 'Architect', 'Arquitecto', 'Head',
    'Manager', 'Director',
    'Java Developer', 'Java Engineer', 'Desarrollador Java', '.NET', 'C#', 'PHP',
    'Frontend', 'Front-end', 'Front End', 'React', 'Angular', 'Vue', 'iOS',
    'Android', 'Mobile', 'Salesforce', 'SAP', 'Golang', 'Ruby', 'Scala',
    'Bending Spoons', 'Crossing Hurdles', 'Joinrs', 'Evolve', 'Hire Feed',
    'Quik Hire Staffing', 'micro1', 'BJAK', 'Freelance',
  ]);

  const backendPass = [
    'Backend Developer Python', 'Software Engineer', 'Junior Python Developer',
    'Full Stack Python Developer', 'Backend Engineer (Python/TypeScript)',
    'Desarrollador Backend Junior', 'Software Engineer – AI',
  ];
  for (const title of backendPass) {
    assertTrue(`backend passes "${title}"`, passesFilter({ title, company: 'Acme' }, BACKEND_TITLE_FILTER));
  }
  const backendFail = [
    'Senior Backend Engineer', 'Java Developer', 'Frontend Developer React',
    '.NET Developer', 'PHP Developer', 'Tech Lead Python', 'Android Developer',
    'C# Developer', 'Backend Developer (C#/.NET)', 'ASP.NET Developer',
    'Sr. Software Engineer', 'Desarrollador Java', 'Engineering Manager Python',
  ];
  for (const title of backendFail) {
    assertFalse(`backend rejects "${title}"`, passesFilter({ title, company: 'Acme' }, BACKEND_TITLE_FILTER));
  }
  assertFalse('backend rejects blocklisted company', passesFilter({ title: 'Software Engineer', company: 'micro1' }, BACKEND_TITLE_FILTER));
  assertFalse('backend rejects Freelance', passesFilter({ title: 'Freelance Python Developer', company: '' }, BACKEND_TITLE_FILTER));
  assertFalse('backend needs a role term', passesFilter({ title: 'Data Analyst', company: '' }, BACKEND_TITLE_FILTER));
  // Punctuation terms: .NET / C# must not leak into plain words.
  assertTrue('"net" word is not ".NET"', passesFilter({ title: 'Python Developer, net zero startup', company: '' }, BACKEND_TITLE_FILTER));
  assertTrue('plain "C" is not "C#"', passesFilter({ title: 'Software Engineer (C, Linux)', company: '' }, BACKEND_TITLE_FILTER));
  assertTrue('matchesTerm C# in "C#/.NET Dev"', matchesTerm(normalize('C#/.NET Dev'), 'C#'));
  assertTrue('matchesTerm .NET in "ASP.NET Core"', matchesTerm(normalize('ASP.NET Core'), '.NET'));
  assertFalse('matchesTerm C# not in "C++ Dev"', matchesTerm(normalize('C++ Dev'), 'C#'));
  // AI profile keeps rejecting backend-only titles (group 1 needs an AI term).
  assertFalse('ai profile still drops plain backend titles', passesFilter({ title: 'Backend Developer Python', company: '' }, DEFAULT_TITLE_FILTERS.ai));

  // --- normalizeProfile ------------------------------------------------------
  check('normalizeProfile backend', normalizeProfile('backend'), 'backend');
  check('normalizeProfile ai', normalizeProfile('ai'), 'ai');
  check('normalizeProfile missing -> ai', normalizeProfile(undefined), 'ai');
  check('normalizeProfile unknown -> ai', normalizeProfile('golang'), 'ai');
  check('normalizeProfile null -> ai', normalizeProfile(null), 'ai');

  // --- resolveTitleFilters: storage back-compat -----------------------------
  check('empty storage -> both defaults', resolveTitleFilters({}), { ai: DEFAULT_TITLE_FILTER, backend: BACKEND_TITLE_FILTER });
  check(
    'stored titleFilter is the ai profile (owner filter not lost)',
    resolveTitleFilters({ titleFilter: CUSTOM_FILTER }).ai,
    CUSTOM_FILTER
  );
  check(
    'stored titleFilter does not touch the backend defaults',
    resolveTitleFilters({ titleFilter: CUSTOM_FILTER }).backend,
    BACKEND_TITLE_FILTER
  );
  check(
    'legacy settings.titleFilter still feeds ai',
    resolveTitleFilters({ settings: { titleFilter: LEGACY_FILTER } }).ai,
    LEGACY_FILTER
  );
  check(
    'titleFilterBackend is the backend profile',
    resolveTitleFilters({ titleFilterBackend: CUSTOM_FILTER }).backend,
    CUSTOM_FILTER
  );
  check(
    'backend profile is repaired independently (missing excludeTerms)',
    resolveTitleFilters({
      titleFilter: CUSTOM_FILTER,
      titleFilterBackend: { enabled: false, includeGroups: [['Only']] },
    }),
    {
      ai: CUSTOM_FILTER,
      backend: { enabled: false, includeGroups: [['Only']], excludeTerms: BACKEND_TITLE_FILTER.excludeTerms },
    }
  );
  check(
    'malformed titleFilterBackend falls back to backend defaults (not ai defaults)',
    resolveTitleFilters({ titleFilterBackend: 'junk' }).backend,
    BACKEND_TITLE_FILTER
  );
  {
    const r = resolveTitleFilters({});
    r.backend.includeGroups[0].push('MUTATED');
    r.backend.excludeTerms.push('MUTATED');
    assertFalse('resolved backend is a copy (groups)', BACKEND_TITLE_FILTER.includeGroups[0].includes('MUTATED'));
    assertFalse('resolved backend is a copy (excludes)', BACKEND_TITLE_FILTER.excludeTerms.includes('MUTATED'));
  }

  // --- filterForBeacon: per-beacon profile -----------------------------------
  {
    const filters = resolveTitleFilters({});
    check('beacon backend -> backend filter', filterForBeacon(filters, { filterProfile: 'backend' }), BACKEND_TITLE_FILTER);
    check('beacon ai -> ai filter', filterForBeacon(filters, { filterProfile: 'ai' }), DEFAULT_TITLE_FILTER);
    check('beacon without filterProfile -> ai (stored beacons)', filterForBeacon(filters, { id: 'x', name: 'old' }), DEFAULT_TITLE_FILTER);
    check('beacon unknown profile -> ai', filterForBeacon(filters, { filterProfile: 'nope' }), DEFAULT_TITLE_FILTER);
    check('null beacon -> ai', filterForBeacon(filters, null), DEFAULT_TITLE_FILTER);
    const job = { title: 'Backend Developer Python', company: '' };
    assertTrue('same job kept by a backend beacon', passesFilter(job, filterForBeacon(filters, { filterProfile: 'backend' })));
    assertFalse('same job dropped by a legacy beacon', passesFilter(job, filterForBeacon(filters, { name: 'legacy' })));
  }

  // --- profile title filters served by the bridge (profile-extraction E3) -----
  {
    const profile = {
      ai: { enabled: true, includeGroups: [['Data']], excludeTerms: ['Intern'] },
      backend: { enabled: false, includeGroups: [['Ops']], excludeTerms: [] },
    };
    // No profile filters: byte-for-byte the built-in defaults.
    check('no profile filters -> ai defaults', resolveTitleFilters({}).ai, DEFAULT_TITLE_FILTER);
    check('profile filters as defaults (ai)', resolveTitleFilters({ profileTitleFilters: profile }).ai, profile.ai);
    check('profile filters as defaults (backend)', resolveTitleFilters({ profileTitleFilters: profile }).backend, profile.backend);
    check('resolveTitleFilter (ai only) uses the profile too', resolveTitleFilter({ profileTitleFilters: profile }), profile.ai);
    // The owner's stored filter still wins over the profile defaults.
    const stored = { enabled: true, includeGroups: [['Mine']], excludeTerms: ['X'] };
    check('stored filter beats profile defaults', resolveTitleFilters({ titleFilter: stored, profileTitleFilters: profile }).ai, stored);
    // A partially stored filter is repaired from the profile, not the built-ins.
    check(
      'partial stored filter repaired from the profile',
      resolveTitleFilters({ titleFilter: { enabled: true }, profileTitleFilters: profile }).ai.includeGroups,
      [['Data']]
    );
    // Garbage profile data falls back to the built-ins.
    check('junk profile filters -> built-ins', resolveTitleFilters({ profileTitleFilters: 'junk' }).ai, DEFAULT_TITLE_FILTER);
    check('profile with only ai keeps backend built-in', resolveTitleFilters({ profileTitleFilters: { ai: profile.ai } }).backend, BACKEND_TITLE_FILTER);
    {
      const r = resolveTitleFilters({ profileTitleFilters: profile });
      r.ai.includeGroups[0].push('MUTATED');
      assertFalse('resolved profile filter is a copy', profile.ai.includeGroups[0].includes('MUTATED'));
    }
    // parseProfileTitleFilters: the bridge body -> storable object, or null.
    check('parse: body without titleFilters', parseProfileTitleFilters({ searches: [] }), null);
    check('parse: junk', parseProfileTitleFilters(null), null);
    check('parse: titleFilters repaired and cleaned', parseProfileTitleFilters({ titleFilters: profile }), profile);
    check(
      'parse: a profile missing from the body is absent',
      Object.keys(parseProfileTitleFilters({ titleFilters: { ai: profile.ai } })),
      ['ai']
    );
  }

  // adoptProfileTitleFilters: store / replace / clear in chrome.storage.local
  // (a fake area), and what getState() would then resolve as the defaults.
  {
    const area = {
      data: {},
      async set(obj) { Object.assign(this.data, JSON.parse(JSON.stringify(obj))); },
      async remove(key) { delete this.data[key]; },
      async get(keys) {
        const out = {};
        for (const k of keys) if (k in this.data) out[k] = this.data[k];
        return out;
      },
    };
    const served = {
      ai: { enabled: true, includeGroups: [['zzz profile']], excludeTerms: ['nope'] },
    };
    let data = await adoptProfileTitleFilters(area, { titleFilters: served });
    check('adopt: stored under profileTitleFilters', 'profileTitleFilters' in area.data, true);
    check('adopt: resolves as the ai default', resolveTitleFilters(data).ai.includeGroups, [['zzz profile']]);
    check('adopt: backend keeps its built-in', resolveTitleFilters(data).backend, BACKEND_TITLE_FILTER);
    // The owner's saved filter still wins over the stored defaults.
    area.data.titleFilter = { enabled: true, includeGroups: [['mine']], excludeTerms: [] };
    data = await adoptProfileTitleFilters(area, { titleFilters: served });
    check('adopt: saved filter beats the profile default', resolveTitleFilters(data).ai.includeGroups, [['mine']]);
    delete area.data.titleFilter;
    // A response WITHOUT titleFilters clears the stale key -> built-ins again.
    data = await adoptProfileTitleFilters(area, { searches: [] });
    check('adopt: absent titleFilters removes the stored key', 'profileTitleFilters' in area.data, false);
    check('adopt: back to the built-in ai filter', resolveTitleFilters(data).ai, DEFAULT_TITLE_FILTER);
    // Junk titleFilters also clear (nothing usable to keep).
    await adoptProfileTitleFilters(area, { titleFilters: served });
    await adoptProfileTitleFilters(area, { titleFilters: 'junk' });
    check('adopt: junk titleFilters removes the stored key', 'profileTitleFilters' in area.data, false);
  }


  if (failures.length) {
    console.error(`FAIL: ${failures.length} assertion(s) failed:`);
    for (const f of failures) console.error(' - ' + f);
    process.exitCode = 1;
  } else {
    console.log('OK: all extension/filter.js assertions passed');
  }
}

main().catch((e) => {
  console.error('harness crashed:', (e && e.stack) || e);
  process.exitCode = 1;
});
