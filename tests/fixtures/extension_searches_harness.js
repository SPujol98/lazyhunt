'use strict';

// Node harness for portal URL building and extension/searches.js
// (portal-adapters T2). Invoked by tests/test_extension_searches.py.

const path = require('path');
const { pathToFileURL } = require('url');

const failures = [];
function check(name, actual, expected) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) failures.push(`${name}: expected ${e}, got ${a}`);
}
const load = (rel) => import(pathToFileURL(path.resolve(__dirname, '..', '..', 'extension', rel)).href);

async function main() {
  const { default: linkedin } = await load('portals/linkedin.js');
  const { default: infojobs } = await load('portals/infojobs.js');
  const { PORTALS } = await load('portals/registry.js');
  const {
    normalizeSearch, normalizeSearches, autoBeaconId, expandSearches, dedupeSearchIds,
    searchKey, mergeSuggestedSearches, suggestionsUrl, parseSuggestions,
    beaconPortalId, legacyEnabledPortals, resolveEnabledPortals, filterBeaconsByPortal,
    combineEnabledPortals, configUrl, parseBotConfig,
  } = await load('searches.js');
  const { portalFor } = await load('portals/registry.js');
  const { portalOrigin, termsWarning } = await load('portals/terms.js');
  const { slugify } = await load('slug.js');

  // --- shared slug helper (one for searches.js and the InfoJobs adapter) -----------
  check('slugify strips accents', slugify('A Coruña — Málaga'), 'a-coruna-malaga');
  check('slugify trims separators', slugify('  --AI  Bcn!!  '), 'ai-bcn');
  check('slugify tolerates junk', [slugify(undefined), slugify(null), slugify('')], ['', '', '']);
  check('slugify source uses the readable escape', require('fs').readFileSync(
    path.resolve(__dirname, '..', '..', 'extension', 'slug.js'), 'utf8').includes('\\u0300-\\u036f'), true);
  check('searches slug ids', autoBeaconId({ name: 'Sant Cugat del Vallès' }, 'p'), 'auto:sant-cugat-del-valles:p');

  // --- duplicate search ids (hand-edited import / storage) get re-assigned ---------
  {
    let n = 0;
    const out = dedupeSearchIds([{ id: 'a', name: '1' }, { id: 'a', name: '2' }, { id: '', name: '3' }, { id: 'b', name: '4' }, { id: 'a', name: '5' }],
      () => `new${++n}`);
    check('dedupe: first keeps its id, later duplicates and blanks get new ones', out.map((s) => s.id), ['a', 'new1', 'new2', 'b', 'new3']);
    check('dedupe: other fields untouched', out.map((s) => s.name), ['1', '2', '3', '4', '5']);
    const collide = ['x', 'x', 'y'];
    check('dedupe: a generated id that collides is regenerated',
      dedupeSearchIds([{ id: 'x' }, { id: 'x' }], () => collide.shift()).map((s) => s.id), ['x', 'y']);
    check('dedupe: ids are unique afterwards', new Set(out.map((s) => s.id)).size, 5);
  }
  {
    const dup = expandSearches([
      { id: 'a', name: 'one', keywords: 'ai', portals: ['linkedin'] },
      { id: 'a', name: 'two', keywords: 'ml', portals: ['linkedin'] },
    ], PORTALS);
    check('expand: a duplicate beacon id is never scanned twice', dup.map((b) => b.id), ['auto:a:linkedin']);
  }

  // --- LinkedIn buildSearchUrl ---------------------------------------------------
  const lk = (o) => new URL(linkedin.buildSearchUrl(o));
  {
    const u = lk({ keywords: 'AI Engineer', location: 'Barcelona, Spain' });
    check('linkedin: path', [u.origin, u.pathname], ['https://www.linkedin.com', '/jobs/search/']);
    check('linkedin: keywords+location', [u.searchParams.get('keywords'), u.searchParams.get('location')], ['AI Engineer', 'Barcelona, Spain']);
    check('linkedin: past-week + newest defaults', [u.searchParams.get('f_TPR'), u.searchParams.get('sortBy')], ['r604800', 'DD']);
  }
  check('linkedin: special characters are encoded', linkedin.buildSearchUrl({ keywords: 'C++ & "ML"' }).includes('keywords=C%2B%2B+%26+%22ML%22'), true);
  check('linkedin: accents round-trip', lk({ keywords: 'ingeniero de datos', location: 'Málaga' }).searchParams.get('location'), 'Málaga');
  check('linkedin: empty location is omitted', lk({ keywords: 'ai', location: '' }).searchParams.has('location'), false);
  check('linkedin: blank location is omitted', lk({ keywords: 'ai', location: '   ' }).searchParams.has('location'), false);
  check('linkedin: missing args do not throw', linkedin.buildSearchUrl().startsWith('https://www.linkedin.com/jobs/search/?'), true);
  check('linkedin: extra overrides defaults', lk({ keywords: 'ai', extra: { f_TPR: 'r86400' } }).searchParams.get('f_TPR'), 'r86400');
  check('linkedin: extra adds params', lk({ keywords: 'ai', extra: { f_WT: '2' } }).searchParams.get('f_WT'), '2');
  check('linkedin: empty extra value drops the default', lk({ keywords: 'ai', extra: { f_TPR: '' } }).searchParams.has('f_TPR'), false);
  check('linkedin: built URL is a linkedin URL', linkedin.hostMatch(linkedin.buildSearchUrl({ keywords: 'x' })), true);

  // --- InfoJobs buildSearchUrl (real format from the owner's saved search) ---------------
  const ij = (o) => new URL(infojobs.buildSearchUrl(o));
  {
    const u = ij({ keywords: 'ingeniero IA', location: 'Barcelona' });
    check('infojobs: list.xhtml path', [u.origin, u.pathname], ['https://www.infojobs.net', '/jobsearch/search-results/list.xhtml']);
    check('infojobs: keyword + recency', [u.searchParams.get('keyword'), u.searchParams.get('sinceDate')], ['ingeniero IA', '_7_DAYS']);
    check('infojobs: Barcelona province', [u.searchParams.get('provinceIds'), u.searchParams.get('searchByType')], ['9', 'province']);
    check('infojobs: Spain + page + sort', [u.searchParams.get('countryIds'), u.searchParams.get('page'), u.searchParams.get('sortBy')], ['17', '1', 'RELEVANCE']);
    check('infojobs: param order', [...u.searchParams.keys()],
      ['keyword', 'provinceIds', 'searchByType', 'countryIds', 'sinceDate', 'sortBy', 'page']);
  }
  check('infojobs: city with country suffix / case', ij({ keywords: 'ai', location: ' barcelona, España ' }).searchParams.get('provinceIds'), '9');
  {
    const u = ij({ keywords: 'ai', location: 'A Coruña' });
    check('infojobs: unknown city omits the province', [u.searchParams.has('provinceIds'), u.searchParams.has('searchByType'), u.searchParams.get('keyword')], [false, false, 'ai']);
  }
  check('infojobs: remote is not a province', ij({ keywords: 'ai', location: 'remoto' }).searchParams.has('provinceIds'), false);
  check('infojobs: empty location uses the generic search', [ij({ keywords: 'ai' }).pathname, ij({ keywords: 'ai', location: ' ' }).searchParams.has('provinceIds')],
    ['/jobsearch/search-results/list.xhtml', false]);
  check('infojobs: keywords are encoded', infojobs.buildSearchUrl({ keywords: 'c++ dev' }).includes('keyword=c%2B%2B+dev'), true);
  check('infojobs: accents are encoded', infojobs.buildSearchUrl({ keywords: 'ingeniero de datos Málaga' }).includes('keyword=ingeniero+de+datos+M%C3%A1laga'), true);
  check('infojobs: extra overrides', ij({ keywords: 'ai', extra: { sinceDate: '_15_DAYS' } }).searchParams.get('sinceDate'), '_15_DAYS');
  check('infojobs: extra empty drops a default', ij({ keywords: 'ai', extra: { sinceDate: '' } }).searchParams.has('sinceDate'), false);
  check('infojobs: built URL is an infojobs URL', infojobs.hostMatch(infojobs.buildSearchUrl({ keywords: 'x', location: 'Madrid' })), true);

  // --- suggested searches (options "Sugerir busquedas desde mi perfil") -----------------
  check('suggestionsUrl from the bridge URL', suggestionsUrl('http://127.0.0.1:8765/bridge'), 'http://127.0.0.1:8765/searches/suggested');
  check('suggestionsUrl without a path', suggestionsUrl(' http://127.0.0.1:9000 '), 'http://127.0.0.1:9000/searches/suggested');
  check('suggestionsUrl invalid/empty', [suggestionsUrl(''), suggestionsUrl('nonsense'), suggestionsUrl(undefined)], [null, null, null]);
  check('searchKey ignores case, accents and spacing',
    [searchKey({ keywords: ' AI  Engineer ', location: 'Barcelona' }) === searchKey({ keywords: 'ai engineer', location: 'BARCELONA ' }),
     searchKey({ keywords: 'x', location: 'Málaga' }) === searchKey({ keywords: 'x', location: 'malaga' }),
     searchKey({ keywords: 'x', location: 'a' }) === searchKey({ keywords: 'x', location: 'b' })], [true, true, false]);
  check('parseSuggestions keeps valid rows only', parseSuggestions({ searches: [
    { name: 'A', keywords: 'ai engineer', location: 'Barcelona', filterProfile: 'ai', portals: ['linkedin'], titleFilter: 'on' },
    { name: 'no keywords', keywords: ' ' }, 'junk', null,
  ] }).map((x) => x.name), ['A']);
  check('parseSuggestions tolerates junk', [parseSuggestions(null), parseSuggestions({}), parseSuggestions({ searches: 'x' })], [[], [], []]);
  {
    let n = 0;
    const newId = () => `n${++n}`;
    const existing = [{ id: 's_old', name: 'Mine', keywords: 'AI Engineer', location: 'barcelona', filterProfile: 'ai', portals: ['linkedin'], titleFilter: 'off' }];
    const picked = [
      { name: 'AI Engineer · Barcelona', keywords: 'ai engineer', location: 'Barcelona', filterProfile: 'ai', portals: ['linkedin', 'infojobs'], titleFilter: 'on' },
      { name: 'LLM Engineer · remoto', keywords: 'LLM Engineer', location: 'remoto', filterProfile: 'ai', portals: ['linkedin', 'infojobs'], titleFilter: 'on' },
      { name: 'dup of previous', keywords: 'llm  engineer', location: 'Remoto', filterProfile: 'ai', portals: ['linkedin'], titleFilter: 'on' },
    ];
    const out = mergeSuggestedSearches(existing, picked, newId);
    check('merge keeps existing untouched and first', out.searches[0], existing[0]);
    check('merge adds only the new, deduped ones', out.searches.map((x) => x.name), ['Mine', 'LLM Engineer · remoto']);
    check('merge assigns fresh ids', out.searches[1].id, 'n1');
    check('merge counts', [out.added, out.skipped], [1, 2]);
    check('merge does not mutate its inputs', [existing.length, existing[0].titleFilter, picked.length], [1, 'off', 3]);
    const again = mergeSuggestedSearches(out.searches, picked, newId);
    check('merging twice adds nothing', [again.added, again.skipped, again.searches.length], [0, 3, 2]);
    {
      const ids = ['s_old', 'fresh'];
      const clash = mergeSuggestedSearches(existing, [picked[1]], () => ids.shift());
      check('merge never reuses an existing id', clash.searches.map((x) => x.id), ['s_old', 'fresh']);
    }
  }

  // --- searches.js --------------------------------------------------------------------
  check('normalizeSearch defaults', normalizeSearch({ id: 's1', name: ' AI BCN ', keywords: ' ai engineer ' }), {
    id: 's1', name: 'AI BCN', keywords: 'ai engineer', location: '', filterProfile: 'ai', portals: [], titleFilter: 'on',
  });
  check('normalizeSearch keeps valid fields', normalizeSearch({
    id: 's2', name: 'BE', keywords: 'backend', location: 'Madrid', filterProfile: 'backend',
    portals: ['linkedin', 'infojobs', 'linkedin', 7], titleFilter: 'off',
  }), { id: 's2', name: 'BE', keywords: 'backend', location: 'Madrid', filterProfile: 'backend', portals: ['linkedin', 'infojobs'], titleFilter: 'off' });
  check('normalizeSearch repairs junk', normalizeSearch({ filterProfile: 'weird', titleFilter: 'maybe', portals: 'linkedin' }).filterProfile, 'ai');
  check('normalizeSearch repairs titleFilter', normalizeSearch({ titleFilter: 'maybe' }).titleFilter, 'on');
  check('normalizeSearches tolerates non-arrays', [normalizeSearches(undefined), normalizeSearches('x')], [[], []]);

  check('autoBeaconId uses the stable search id', autoBeaconId({ id: 's1', name: 'x' }, 'linkedin'), 'auto:s1:linkedin');
  check('autoBeaconId falls back to a name slug', autoBeaconId({ name: 'AI Bcn!' }, 'infojobs'), 'auto:ai-bcn:infojobs');
  check('autoBeaconId survives a rename', autoBeaconId({ id: 's1', name: 'renamed' }, 'linkedin'), 'auto:s1:linkedin');

  {
    const searches = [
      { id: 's1', name: 'AI BCN', keywords: 'ai engineer', location: 'Barcelona', filterProfile: 'ai', portals: ['linkedin', 'infojobs'], titleFilter: 'on' },
      { id: 's2', name: 'BE', keywords: 'backend', filterProfile: 'backend', portals: ['linkedin', 'nope'], titleFilter: 'off' },
      { id: 's3', name: 'No keywords', keywords: '  ', portals: ['linkedin'] },
      { id: 's4', name: 'No portals', keywords: 'x', portals: [] },
    ];
    const beacons = expandSearches(searches, PORTALS);
    check('expand: one beacon per (search x known portal), junk skipped', beacons.map((b) => b.id),
      ['auto:s1:linkedin', 'auto:s1:infojobs', 'auto:s2:linkedin']);
    const [first, , third] = beacons;
    check('expand: beacon shape', [first.name, first.enabled, first.auto, first.portal, first.filterProfile, first.titleFilter],
      ['AI BCN · LinkedIn', true, true, 'linkedin', 'ai', 'on']);
    check('expand: url comes from the adapter', first.url, linkedin.buildSearchUrl({ keywords: 'ai engineer', location: 'Barcelona' }));
    check('expand: per-search profile and title filter switch', [third.filterProfile, third.titleFilter], ['backend', 'off']);
    check('expand: ids are stable across runs', expandSearches(searches, PORTALS).map((b) => b.id), beacons.map((b) => b.id));
    check('expand: nothing to expand', expandSearches(undefined, PORTALS), []);
  }

  {
    const beacons = expandSearches([
      { id: 'm1', name: 'one', keywords: 'ai', portals: ['manfred'] },
      { id: 'm2', name: 'two', keywords: 'python', portals: ['manfred'] },
      { id: 'm3', name: 'three', keywords: 'ai', portals: ['linkedin'] },
    ], PORTALS);
    check('expand: identical built URLs (Manfred ignores keywords) open once per round',
      beacons.map((b) => b.id), ['auto:m1:manfred', 'auto:m3:linkedin']);
  }

  // --- safe defaults T2: per-portal opt-in (S3) ------------------------------------------
  {
    const searches = [
      { id: 's1', name: 'a', keywords: 'ai', portals: ['linkedin', 'infojobs'] },
      { id: 's2', name: 'b', keywords: 'be', portals: ['linkedin'] },
    ];
    check('expand: no enabled set keeps every portal (previous behaviour)',
      expandSearches(searches, PORTALS).map((b) => b.id), ['auto:s1:linkedin', 'auto:s1:infojobs', 'auto:s2:linkedin']);
    check('expand: only enabled portals expand (array)',
      expandSearches(searches, PORTALS, ['infojobs']).map((b) => b.id), ['auto:s1:infojobs']);
    check('expand: only enabled portals expand (Set); none enabled -> nothing',
      [expandSearches(searches, PORTALS, new Set(['linkedin'])).length, expandSearches(searches, PORTALS, []).length], [2, 0]);
  }
  {
    check('beaconPortalId: auto beacon uses its portal', beaconPortalId({ auto: true, portal: 'indeed', url: 'https://www.linkedin.com/x' }, portalFor), 'indeed');
    check('beaconPortalId: manual beacon is routed by URL host',
      beaconPortalId({ url: 'https://www.infojobs.net/ofertas-trabajo?q=1' }, portalFor), 'infojobs');
    check('beaconPortalId: unknown URL falls back like the scan does (linkedin)',
      beaconPortalId({ url: 'https://example.com/' }, portalFor), 'linkedin');

    const legacy = legacyEnabledPortals(
      [{ id: 's', name: 'n', keywords: 'ai', portals: ['indeed', 'linkedin', 'nope'] }],
      [{ id: 'b1', url: 'https://www.infojobs.net/x', enabled: false }, { id: 'b2', url: '' }, { id: 'b3', url: 'https://www.linkedin.com/jobs/' }],
      portalFor, PORTALS);
    check('legacy migration: every portal the searches and manual beacons use, known ids only, no duplicates',
      legacy, ['indeed', 'linkedin', 'infojobs']);
    check('legacy migration: a fresh install (nothing configured) enables nothing', legacyEnabledPortals(undefined, undefined, portalFor, PORTALS), []);
    check('resolve: a stored array wins over the legacy derivation',
      resolveEnabledPortals(['indeed'], [{ keywords: 'x', portals: ['linkedin'] }], [], portalFor, PORTALS), ['indeed']);
    check('resolve: absent key falls back to the legacy derivation',
      resolveEnabledPortals(undefined, [{ keywords: 'x', portals: ['linkedin'] }], [], portalFor, PORTALS), ['linkedin']);

    const beacons = [
      { id: 'm1', url: 'https://www.linkedin.com/jobs/' },
      { id: 'm2', url: 'https://www.infojobs.net/x' },
      { id: 'auto:s:indeed', auto: true, portal: 'indeed', url: 'https://es.indeed.com/jobs?q=ai' },
    ];
    const gated = filterBeaconsByPortal(beacons, ['linkedin', 'indeed'], portalFor);
    check('gate: allowed beacons keep their order', gated.allowed.map((b) => b.id), ['m1', 'auto:s:indeed']);
    check('gate: skipped beacons name their portal', gated.skipped.map((x) => [x.beacon.id, x.portalId]), [['m2', 'infojobs']]);
    check('gate: nothing enabled skips everything', filterBeaconsByPortal(beacons, [], portalFor).allowed, []);

    check('precedence: enabled on both sides', combineEnabledPortals(['linkedin', 'indeed'], ['indeed', 'adzuna']), ['indeed']);
    check('precedence: bot list absent or unusable leaves the local set', [combineEnabledPortals(['linkedin'], null), combineEnabledPortals(['linkedin'], undefined)], [['linkedin'], ['linkedin']]);
    check('precedence: bot lists none -> none', combineEnabledPortals(['linkedin'], []), []);
  }
  {
    check('configUrl derives /config from the bridge url', configUrl('http://127.0.0.1:8765/bridge'), 'http://127.0.0.1:8765/config');
    check('configUrl rejects blank / non-http', [configUrl(''), configUrl('ftp://x/bridge'), configUrl('nope')], [null, null, null]);
    check('parseBotConfig reads paused and enabledPortals', parseBotConfig({ paused: true, enabledPortals: ['linkedin', 3, 'indeed'] }), { paused: true, enabledPortals: ['linkedin', 'indeed'] });
    check('parseBotConfig: missing list means no restriction', parseBotConfig({ paused: false }), { paused: false, enabledPortals: null });
    check('parseBotConfig: junk payload is null', [parseBotConfig(null), parseBotConfig('x'), parseBotConfig([])], [null, null, null]);
  }
  {
    const manifest = JSON.parse(require('fs').readFileSync(path.resolve(__dirname, '..', '..', 'extension', 'manifest.json'), 'utf8'));
    check('portalOrigin is the host pattern of the portal', PORTALS.map((p) => portalOrigin(p)).sort(),
      [...(manifest.optional_host_permissions || [])].sort());
    check('manifest: portal hosts are optional, only the loopback bridge is required', manifest.host_permissions, ['http://127.0.0.1/*']);
    const linkedinTerms = termsWarning(linkedin);
    check('terms: LinkedIn wording is the strong one', [/User Agreement/.test(linkedinTerms), /scrape or automate/.test(linkedinTerms), /restricted/.test(linkedinTerms)], [true, true, true]);
    const other = termsWarning(infojobs);
    check('terms: other portals get the generic warning naming the portal', [/InfoJobs/.test(other), /User Agreement/.test(other)], [true, false]);
  }

  if (failures.length) {
    console.error(`FAIL: ${failures.length} assertion(s) failed:`);
    for (const f of failures) console.error(' - ' + f);
    process.exitCode = 1;
  } else {
    process.stdout.write('OK: all portal search URL and searches.js assertions passed\n');
  }
}

main().catch((e) => {
  console.error('harness crashed:', (e && e.stack) || e);
  process.exitCode = 1;
});
