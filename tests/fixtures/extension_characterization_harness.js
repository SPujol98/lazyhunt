'use strict';

// Characterization harness for the extension's service worker
// (odd/tasks/extension-rewrite.md T1, spec docs/extension-spec.md).
//
//   node extension_characterization_harness.js <area>
//   areas: alarm, tab, migrate, seen, msg, round, families
//
// Implementation-agnostic by design: the service worker named in
// extension/manifest.json is imported over the fake browser of
// extension_characterization_harness_lib.js and driven ONLY through Chrome
// events (runtime.onInstalled/onStartup/onMessage, alarms.onAlarm). Assertions
// read observable effects: chrome.storage.local contents, message responses,
// chrome.tabs/alarms/notifications/action calls and fetch calls to the bot.
// Search pages are synthetic LinkedIn result pages (componentkey cards) that
// the extension's own page function parses for real. Each scenario boots a
// fresh copy of the service worker. Exit 0 on success.

const {
  check, ok, logs, idle, fire, sendMessage, clone, makeBrowser, readManifest,
  importExtensionFile, setScenario, finish, startWatchdog, crashed,
} = require('./extension_characterization_harness_lib.js');

startWatchdog(90000);

const MANIFEST = readManifest();
const ALARM = 'check-jobs';
const BRIDGE = 'http://127.0.0.1:8765/bridge';
const SECRET = 's3cret';
const LI_A = 'https://www.linkedin.com/jobs/search/?keywords=ai&location=Barcelona';
const LI_B = 'https://www.linkedin.com/jobs/search/?keywords=ml&location=Madrid';
const NO_FILTER = { enabled: false, includeGroups: [], excludeTerms: [] };

const SETTINGS = {
  intervalMinutes: 30,
  paused: false,
  bridgeUrl: BRIDGE,
  bridgeSecret: SECRET,
};

const beacon = (id, url, extra = {}) => ({ id, name: `Beacon ${id}`, url, enabled: true, ...extra });

// A LinkedIn search results page in the current markup (componentkey cards).
function liPage(cards) {
  const body = cards.map((c) => (
    `<div componentkey="job-card-component-ref-${c.id}">`
    + `<div>${c.title}</div>`
    + (c.company === undefined ? '<div>Acme</div>' : c.company ? `<div>${c.company}</div>` : '')
    + (c.location === undefined ? '<div>Barcelona, Spain</div>' : '')
    + '</div>'
  )).join('');
  return `<main>${body}</main>`;
}
const NO_RESULTS_PAGE = '<main><p>No matching jobs found.</p></main>';

let b = null;

// A fresh browser + a fresh service worker instance, store pre-filled.
async function boot(name, store = {}, scenario = {}) {
  setScenario(name);
  logs.length = 0;
  b = makeBrowser();
  Object.assign(b.store, clone(store));
  Object.assign(b.scenario, scenario);
  globalThis.chrome = b.chrome;
  globalThis.fetch = b.fetch;
  await importExtensionFile(MANIFEST.background.service_worker);
  await idle();
  return b;
}

const send = async (msg) => {
  const r = await sendMessage(b.chrome.runtime.onMessage, msg);
  await idle();
  return r;
};
const bridged = () => b.calls.fetches.filter((f) => f.method === 'POST' && new URL(f.url).pathname === '/bridge');
const bridgedIds = () => bridged().flatMap((f) => f.body.jobs.map((j) => j.url.match(/view\/(\d+)/)[1]));
const visited = () => b.calls.tabsUpdate.filter((u) => u.props.url).map((u) => u.props.url);
const alarm = () => b.alarms.get(ALARM);

function hold() {
  let release;
  const promise = new Promise((resolve) => { release = resolve; });
  const gate = { promise, released: false };
  gate.release = () => { gate.released = true; release(); };
  return gate;
}

// Base store for a round over one or more LinkedIn beacons.
function roundStore(beacons, extra = {}) {
  return {
    settings: { ...SETTINGS },
    beacons,
    enabledPortals: ['linkedin'],
    titleFilter: NO_FILTER,
    titleFilterBackend: NO_FILTER,
    ...extra,
  };
}

// ------------------------------------------------------------------------------------------
async function areaAlarm() {
  await boot('alarm: fresh install');
  const before = Date.now();
  await fire(b.chrome.runtime.onInstalled, { reason: 'install' });
  const a = alarm();
  ok('an alarm named check-jobs exists', Boolean(a));
  check('fresh install period is 60 minutes', a && a.periodInMinutes, 60);
  ok('first round about 30 s after scheduling', a && a.scheduledTime >= before + 20000 && a.scheduledTime <= Date.now() + 40000, a);
  const s = b.store.settings || {};
  check('default settings are written', [s.intervalMinutes, s.paused, s.bridgeUrl, s.bridgeSecret], [60, false, '', '']);
  check('an empty manual beacon list is written', b.store.beacons, []);
  check('fresh install enables no portal', b.store.enabledPortals, []);

  await boot('alarm: startup keeps the stored interval', { settings: { ...SETTINGS, intervalMinutes: 15 }, beacons: [] });
  await fire(b.chrome.runtime.onStartup);
  check('startup period follows settings.intervalMinutes', alarm() && alarm().periodInMinutes, 15);
  check('startup does not rewrite stored settings', b.store.settings, { ...SETTINGS, intervalMinutes: 15 });

  await boot('alarm: startup without settings uses the default pace', {});
  await fire(b.chrome.runtime.onStartup);
  check('no stored settings -> 60 minute period', alarm() && alarm().periodInMinutes, 60);

  const cases = [[20, 20], ['45', 45], [0, 60], [-3, 1], ['abc', 60], [0.5, 1], [null, 60], [5000, 1440]];
  for (const [value, expected] of cases) {
    await boot(`alarm: interval ${JSON.stringify(value)}`, { settings: { ...SETTINGS, intervalMinutes: value }, beacons: [] });
    const r = await send({ type: 'settings-updated' });
    check('settings-updated answers ok', r, { ok: true });
    check(`period for intervalMinutes=${JSON.stringify(value)}`, alarm() && alarm().periodInMinutes, expected);
  }

  await boot('alarm: paused clears the alarm', { settings: { ...SETTINGS }, beacons: [] });
  await send({ type: 'settings-updated' });
  ok('alarm exists while running', Boolean(alarm()));
  b.store.settings = { ...SETTINGS, paused: true };
  await send({ type: 'settings-updated' });
  check('paused -> no check-jobs alarm', alarm(), undefined);
  check('paused -> no alarm at all', b.alarms.size, 0);

  await boot('alarm: rescheduling replaces the alarm', { settings: { ...SETTINGS, intervalMinutes: 30 }, beacons: [] });
  await send({ type: 'settings-updated' });
  b.store.settings = { ...SETTINGS, intervalMinutes: 90 };
  await send({ type: 'settings-updated' });
  check('a single alarm with the new period', [b.alarms.size, alarm() && alarm().periodInMinutes], [1, 90]);

  await boot('alarm: the check-jobs alarm runs a round', roundStore([beacon('b1', LI_A)]), {
    pages: { [LI_A]: liPage([{ id: '11', title: 'AI Engineer' }]) },
  });
  await fire(b.chrome.alarms.onAlarm, { name: ALARM, scheduledTime: Date.now() });
  check('the beacon page was opened', visited(), [LI_A]);
  ok('the round stamped stats.lastRoundAt', b.store.stats && typeof b.store.stats.lastRoundAt === 'number', b.store.stats);
  check('the round recorded the beacon', b.store.lastCheck && b.store.lastCheck.b1 && b.store.lastCheck.b1.status, 'ok');

  await boot('alarm: another alarm name does nothing', roundStore([beacon('b1', LI_A)]));
  await fire(b.chrome.alarms.onAlarm, { name: 'something-else', scheduledTime: Date.now() });
  check('no tab, no scan', [b.calls.tabsCreate.length, visited().length], [0, 0]);
  check('no round stats', b.store.stats, undefined);
}

// ------------------------------------------------------------------------------------------
async function areaTab() {
  const page = { [LI_A]: liPage([{ id: '21', title: 'AI Engineer' }]), [LI_B]: liPage([{ id: '22', title: 'ML Engineer' }]) };

  await boot('tab: a round uses one hidden tab and disposes it', roundStore([beacon('b1', LI_A), beacon('b2', LI_B)]), { pages: page });
  const seenDuringScan = [];
  b.scenario.onScript = (tab) => seenDuringScan.push([tab.id, b.store.scrapeTabId]);
  const r = await send({ type: 'run-now' });
  check('round ok', r && r.ok, true);
  check('exactly one tab created', b.calls.tabsCreate.length, 1);
  check('the tab is created in the background', b.calls.tabsCreate[0] && b.calls.tabsCreate[0].active, false);
  const tabId = seenDuringScan.length ? seenDuringScan[0][0] : null;
  ok('every scan ran in the created tab', seenDuringScan.every(([id]) => id === tabId), seenDuringScan);
  check('scrapeTabId holds the tab while the round runs', seenDuringScan.map(([, stored]) => stored === tabId), seenDuringScan.map(() => true));
  check('both beacons visited in order, in that tab', b.calls.tabsUpdate.filter((u) => u.props.url).map((u) => [u.id, u.props.url]), [[tabId, LI_A], [tabId, LI_B]]);
  check('the tab is closed at the end', [b.calls.tabsRemove, b.tabs.size], [[tabId], 0]);
  check('scrapeTabId is cleared at the end', 'scrapeTabId' in b.store, false);

  await boot('tab: a stored live tab is reused', roundStore([beacon('b1', LI_A)]), { pages: page });
  const existing = await b.chrome.tabs.create({ url: 'about:blank', active: false });
  b.calls.tabsCreate.length = 0;
  b.store.scrapeTabId = existing.id;
  await send({ type: 'run-now' });
  check('no new tab', b.calls.tabsCreate.length, 0);
  check('the stored tab is navigated', b.calls.tabsUpdate.filter((u) => u.props.url).map((u) => u.id), [existing.id]);
  check('and closed at the end', [b.calls.tabsRemove, 'scrapeTabId' in b.store], [[existing.id], false]);

  await boot('tab: a stale stored id gets a new tab', roundStore([beacon('b1', LI_A)], { scrapeTabId: 4242 }), { pages: page });
  const r2 = await send({ type: 'run-now' });
  check('round ok', r2 && r2.ok && r2.summary.beaconsScanned, 1);
  check('one new tab created', b.calls.tabsCreate.length, 1);
  check('no tab left, key cleared', [b.tabs.size, 'scrapeTabId' in b.store], [0, false]);

  await boot('tab: nothing to scan opens no tab', roundStore([]));
  await send({ type: 'run-now' });
  check('no tab created', b.calls.tabsCreate.length, 0);

  await boot('tab: disabled or URL-less beacons open no tab', roundStore([
    beacon('b1', LI_A, { enabled: false }), { id: 'b2', name: 'no url', url: '', enabled: true },
  ]));
  const r3 = await send({ type: 'run-now' });
  check('nothing scanned', [b.calls.tabsCreate.length, r3.summary.beaconsScanned], [0, 0]);

  await boot('tab: a page that never loads times out, the round goes on', roundStore([beacon('b1', LI_A), beacon('b2', LI_B)]), {
    pages: page, neverLoads: new Set([LI_A]),
  });
  const r4 = await send({ type: 'run-now' });
  check('round answers ok', r4 && r4.ok, true);
  check('the stuck beacon is an error', b.store.lastCheck.b1 && b.store.lastCheck.b1.status, 'error');
  ok('the error carries a message', b.store.lastCheck.b1 && typeof b.store.lastCheck.b1.error === 'string' && b.store.lastCheck.b1.error.length > 0);
  check('the next beacon is still scanned', b.store.lastCheck.b2 && b.store.lastCheck.b2.status, 'ok');
  check('summary counts one error', [r4.summary.errors, r4.summary.beaconsScanned], [1, 1]);
  check('the tab is still disposed', [b.tabs.size, 'scrapeTabId' in b.store], [0, false]);

  await boot('tab: test-scrape uses and disposes its own tab', roundStore([]), { pages: page });
  const t = await send({ type: 'test-scrape', url: LI_A });
  check('test-scrape answers the cards', t && t.ok && t.jobs.map((j) => j.id), ['21']);
  check('one tab created and closed', [b.calls.tabsCreate.length, b.tabs.size, 'scrapeTabId' in b.store], [1, 0, false]);
}

// ------------------------------------------------------------------------------------------
async function areaMigrate() {
  const legacy = [beacon('old1', LI_A), { id: 'old2', name: 'Old 2', url: LI_B, enabled: false }];
  for (const event of ['onInstalled', 'onStartup']) {
    await boot(`migrate: legacy searches -> beacons (${event})`, { settings: { ...SETTINGS }, searches: legacy });
    await fire(b.chrome.runtime[event], { reason: 'update' });
    check('beacons take the legacy list unchanged', b.store.beacons, legacy);
    check('the legacy key is removed', 'searches' in b.store, false);
  }

  const auto = [{ id: 's1', name: 'AI', keywords: 'ai engineer', location: 'Barcelona', filterProfile: 'ai', portals: ['linkedin'], titleFilter: 'on' }];
  await boot('migrate: beacons present -> searches untouched', { settings: { ...SETTINGS }, beacons: [beacon('b1', LI_A)], searches: auto });
  await fire(b.chrome.runtime.onInstalled, { reason: 'update' });
  check('beacons unchanged', b.store.beacons, [beacon('b1', LI_A)]);
  check('auto searches unchanged', b.store.searches, auto);

  await boot('migrate: notifyOnFirstRun is dropped', { settings: { ...SETTINGS, intervalMinutes: 15, notifyOnFirstRun: true }, beacons: [] });
  await fire(b.chrome.runtime.onStartup);
  check('the flag is gone, the rest kept', b.store.settings, { ...SETTINGS, intervalMinutes: 15 });

  await boot('migrate: install over existing data keeps it', {
    settings: { ...SETTINGS, intervalMinutes: 15 }, beacons: [beacon('b1', LI_A)],
    seenJobs: { b1: ['1', '2'] }, lastCheck: { b1: { at: 1, status: 'ok', jobsFound: 2, newJobs: 0, filteredOut: 0 } },
    stats: { totalNotified: 7, lastRoundAt: 1 },
  });
  await fire(b.chrome.runtime.onInstalled, { reason: 'update' });
  check('settings kept', b.store.settings, { ...SETTINGS, intervalMinutes: 15 });
  check('beacons kept', b.store.beacons, [beacon('b1', LI_A)]);
  check('seen offers kept', b.store.seenJobs, { b1: ['1', '2'] });
  check('lastCheck and stats kept', [b.store.lastCheck.b1.status, b.store.stats.totalNotified], ['ok', 7]);

  // Title filter storage: legacy settings.titleFilter is the fallback, the
  // top-level titleFilter key wins, titleFilterBackend serves backend beacons.
  const cards = [{ id: '31', title: 'Chef de cuisine' }, { id: '32', title: 'Python Engineer' }];
  const legacyFilter = { enabled: true, includeGroups: [['Chef']], excludeTerms: [] };
  await boot('migrate: legacy settings.titleFilter still filters', {
    settings: { ...SETTINGS, titleFilter: legacyFilter }, beacons: [beacon('b1', LI_A)], enabledPortals: ['linkedin'],
  }, { pages: { [LI_A]: liPage(cards) } });
  await send({ type: 'run-now' });
  check('only the job passing the legacy filter is bridged', bridgedIds(), ['31']);
  check('the filtered-out job is still marked seen', [...(b.store.seenJobs.b1 || [])].sort(), ['31', '32']);

  await boot('migrate: top-level titleFilter wins over the legacy copy', {
    settings: { ...SETTINGS, titleFilter: legacyFilter }, beacons: [beacon('b1', LI_A)], enabledPortals: ['linkedin'],
    titleFilter: { enabled: true, includeGroups: [['Engineer']], excludeTerms: [] },
  }, { pages: { [LI_A]: liPage(cards) } });
  await send({ type: 'run-now' });
  check('the top-level filter decides', bridgedIds(), ['32']);

  await boot('migrate: backend beacons use titleFilterBackend and say so', {
    settings: { ...SETTINGS }, beacons: [beacon('b1', LI_A, { filterProfile: 'backend' })], enabledPortals: ['linkedin'],
    titleFilter: { enabled: true, includeGroups: [['Chef']], excludeTerms: [] },
    titleFilterBackend: { enabled: true, includeGroups: [['Python']], excludeTerms: [] },
  }, { pages: { [LI_A]: liPage(cards) } });
  await send({ type: 'run-now' });
  check('backend filter decides', bridgedIds(), ['32']);
  check('payload profile is backend', bridged()[0] && bridged()[0].body.profile, 'backend');
}

// ------------------------------------------------------------------------------------------
async function areaSeen() {
  const two = [{ id: '41', title: 'AI Engineer' }, { id: '42', title: 'ML Engineer' }];
  await boot('seen: dedup across rounds', roundStore([beacon('b1', LI_A)]), { pages: { [LI_A]: liPage(two) } });
  const r1 = await send({ type: 'run-now' });
  check('first scan bridges every offer', bridgedIds(), ['41', '42']);
  check('first scan marks them seen', [...b.store.seenJobs.b1].sort(), ['41', '42']);
  check('first scan summary', [r1.summary.firstScans, r1.summary.totalNew, r1.summary.totalJobsFound], [1, 2, 2]);
  check('lastCheck counts new offers', [b.store.lastCheck.b1.jobsFound, b.store.lastCheck.b1.newJobs], [2, 2]);

  b.calls.fetches.length = 0;
  const r2 = await send({ type: 'run-now' });
  check('second round with the same page bridges nothing', bridged().length, 0);
  check('second round: nothing new', [r2.summary.totalNew, r2.summary.firstScans, b.store.lastCheck.b1.newJobs], [0, 0, 0]);

  b.scenario.pages[LI_A] = liPage([...two, { id: '43', title: 'Data Engineer' }]);
  b.calls.fetches.length = 0;
  await send({ type: 'run-now' });
  check('only the new offer is bridged', bridgedIds(), ['43']);
  check('seen grows', [...b.store.seenJobs.b1].sort(), ['41', '42', '43']);

  await boot('seen: a filtered-out offer is never sent later', roundStore([beacon('b1', LI_A)], {
    titleFilter: { enabled: true, includeGroups: [], excludeTerms: ['Senior'] },
  }), { pages: { [LI_A]: liPage([{ id: '51', title: 'AI Engineer' }, { id: '52', title: 'Senior AI Engineer' }]) } });
  const r3 = await send({ type: 'run-now' });
  check('the excluded offer is not bridged', bridgedIds(), ['51']);
  check('summary and lastCheck count it as filtered out', [r3.summary.filteredOut, b.store.lastCheck.b1.filteredOut], [1, 1]);
  b.store.titleFilter = NO_FILTER;
  b.calls.fetches.length = 0;
  await send({ type: 'run-now' });
  check('lifting the filter does not resend it', bridged().length, 0);

  const old = Array.from({ length: 3000 }, (_, i) => `old${i}`);
  await boot('seen: the per-beacon list keeps the newest 3000 ids', roundStore([beacon('b1', LI_A)], { seenJobs: { b1: old } }), {
    pages: { [LI_A]: liPage([{ id: '61', title: 'AI Engineer' }, { id: '62', title: 'ML Engineer' }]) },
  });
  await send({ type: 'run-now' });
  const seen = b.store.seenJobs.b1;
  check('capped at 3000', seen.length, 3000);
  check('the oldest ids are dropped first', [seen.includes('old0'), seen.includes('old1'), seen.includes('old2')], [false, false, true]);
  check('the new ids are kept', [seen.includes('61'), seen.includes('62')], [true, true]);
  check('a non-empty seen list is not a first scan: new offers still bridged', bridgedIds(), ['61', '62']);

  await boot('seen: seen ids are per beacon', roundStore([beacon('b1', LI_A), beacon('b2', LI_B)], { seenJobs: { b1: ['71'] } }), {
    pages: { [LI_A]: liPage([{ id: '71', title: 'AI Engineer' }]), [LI_B]: liPage([{ id: '71', title: 'AI Engineer' }]) },
  });
  await send({ type: 'run-now' });
  check('the same offer is new for the other beacon', bridged().map((f) => f.body.beacon), ['Beacon b2']);
  check('both beacons now know it', [b.store.seenJobs.b1, b.store.seenJobs.b2], [['71'], ['71']]);

  await boot('seen: a bridge outage keeps offers unseen for the next round', roundStore([beacon('b1', LI_A)]), {
    pages: { [LI_A]: liPage([{ id: '81', title: 'AI Engineer' }]) }, bridgeThrow: true,
  });
  await send({ type: 'run-now' });
  check('not marked seen', b.store.seenJobs.b1 || [], []);
  b.scenario.bridgeThrow = false;
  b.calls.fetches.length = 0;
  await send({ type: 'run-now' });
  check('resent once the bridge is back', bridgedIds(), ['81']);
  check('then seen', b.store.seenJobs.b1, ['81']);
}

// ------------------------------------------------------------------------------------------
async function areaMsg() {
  const page = { [LI_A]: liPage([{ id: '91', title: 'AI Engineer' }, { id: '92', title: 'ML Engineer', company: '' }]) };

  await boot('msg: run-now answers a summary', roundStore([beacon('b1', LI_A)]), { pages: page });
  const r = await send({ type: 'run-now' });
  check('ok', r && r.ok, true);
  const s = r.summary || {};
  check('summary fields', [s.skipped, s.beaconsScanned, s.totalJobsFound, s.totalNew, s.firstScans, s.errors, s.filteredOut],
    [false, 1, 2, 2, 1, 0, 0]);

  await boot('msg: run-now during a round is skipped', roundStore([beacon('b1', LI_A)]), { pages: page });
  const gate = hold();
  b.scenario.holdScript = gate;
  const first = sendMessage(b.chrome.runtime.onMessage, { type: 'run-now' });
  await idle();
  let second;
  const secondDone = sendMessage(b.chrome.runtime.onMessage, { type: 'run-now' }).then((r) => { second = r; });
  await idle();
  ok('the overlapping request answers without waiting for the running round', second !== undefined);
  gate.release();
  const done = await first;
  await secondDone;
  await idle();
  check('the overlapping request answers ok + skipped', [second && second.ok, second && second.summary && second.summary.skipped], [true, true]);
  check('the skipped request scanned nothing', second && second.summary && second.summary.beaconsScanned, 0);
  check('the first round completes normally', [done.ok, done.summary.skipped, done.summary.beaconsScanned], [true, false, 1]);
  check('only one scan happened', visited(), [LI_A]);
  const third = await send({ type: 'run-now' });
  check('a later request runs again', [third.summary.skipped, third.summary.beaconsScanned], [false, 1]);

  await boot('msg: set-paused true/false', { settings: { ...SETTINGS, intervalMinutes: 25 }, beacons: [] });
  await send({ type: 'settings-updated' });
  const p1 = await send({ type: 'set-paused', paused: true });
  check('answer', p1, { ok: true, paused: true });
  check('stored, other settings kept', b.store.settings, { ...SETTINGS, intervalMinutes: 25, paused: true });
  check('no alarm while paused', alarm(), undefined);
  const p2 = await send({ type: 'set-paused', paused: false });
  check('answer on resume', p2, { ok: true, paused: false });
  check('stored on resume', b.store.settings.paused, false);
  check('alarm back with the stored period', alarm() && alarm().periodInMinutes, 25);

  await boot('msg: set-paused without stored settings', {});
  await send({ type: 'set-paused', paused: true });
  const st = b.store.settings || {};
  check('settings created from the defaults, paused', [st.paused, st.intervalMinutes, st.bridgeUrl, st.bridgeSecret], [true, 60, '', '']);

  await boot('msg: paused run-now scans nothing', roundStore([beacon('b1', LI_A)], { settings: { ...SETTINGS, paused: true } }), { pages: page });
  const rp = await send({ type: 'run-now' });
  check('answers paused', [rp.ok, rp.summary.paused, rp.summary.beaconsScanned], [true, true, 0]);
  check('no tab', b.calls.tabsCreate.length, 0);

  await boot('msg: test-scrape returns the extracted cards', roundStore([]), { pages: page });
  const t = await send({ type: 'test-scrape', url: LI_A });
  check('ok', t && t.ok, true);
  check('cards as {id,title,company,location,link}', t.jobs, [
    { id: '91', title: 'AI Engineer', company: 'Acme', location: 'Barcelona, Spain', link: 'https://www.linkedin.com/jobs/view/91/' },
    { id: '92', title: 'ML Engineer', company: 'Barcelona, Spain', location: '', link: 'https://www.linkedin.com/jobs/view/92/' },
  ]);
  check('test-scrape stores no seen offer and sends nothing', [b.store.seenJobs, bridged().length], [undefined, 0]);

  await boot('msg: test-scrape on a logged-out session fails', roundStore([]), {
    pages: page, redirects: { [LI_A]: 'https://www.linkedin.com/login?session_redirect=x' },
  });
  const te = await send({ type: 'test-scrape', url: LI_A });
  check('answers not ok', te && te.ok, false);
  ok('with an error string', te && typeof te.error === 'string' && te.error.length > 0, te);
  check('tab disposed', b.tabs.size, 0);

  const seenJobs = { b1: ['1', '2'], b2: ['3'], 'auto:s1:linkedin': ['4'] };
  await boot('msg: clear-history for one beacon', { seenJobs });
  const c1 = await send({ type: 'clear-history', beaconId: 'b1' });
  check('answer', c1, { ok: true });
  check('only that beacon is forgotten', b.store.seenJobs, { b2: ['3'], 'auto:s1:linkedin': ['4'] });

  await boot('msg: clear-history for all', { seenJobs });
  const c2 = await send({ type: 'clear-history' });
  check('answer', c2, { ok: true });
  check('every beacon is forgotten', b.store.seenJobs, {});

  await boot('msg: clear-history then a round is a first scan again', roundStore([beacon('b1', LI_A)], { seenJobs: { b1: ['91', '92'] } }), { pages: page });
  await send({ type: 'clear-history' });
  const rc = await send({ type: 'run-now' });
  check('first scan again', rc.summary.firstScans, 1);
  check('offers bridged again', bridgedIds(), ['91', '92']);

  await boot('msg: unknown type', {});
  const u = await send({ type: 'no-such-message' });
  check('answers not ok', u && u.ok, false);
  ok('with an error string', u && typeof u.error === 'string' && u.error.length > 0, u);
}

// ------------------------------------------------------------------------------------------
async function areaRound() {
  const page = { [LI_A]: liPage([{ id: '101', title: 'AI Engineer' }, { id: '102', title: 'ML Engineer', company: '' }]) };

  await boot('round: bridge POST contract', roundStore([beacon('b1', LI_A)]), { pages: page });
  await send({ type: 'run-now' });
  const posts = bridged();
  check('one POST per beacon with new offers', posts.length, 1);
  const post = posts[0] || { headers: {}, body: {} };
  check('to the configured bridge URL', post.url, BRIDGE);
  check('JSON with the secret header', [post.headers['content-type'], post.headers['x-cva-bridge-secret']], ['application/json', SECRET]);
  check('payload', post.body, {
    beacon: 'Beacon b1',
    profile: 'ai',
    jobs: [
      { title: 'AI Engineer', company: 'Acme', location: 'Barcelona, Spain', url: 'https://www.linkedin.com/jobs/view/101/', text: null },
      { title: 'ML Engineer', company: 'Barcelona, Spain', location: null, url: 'https://www.linkedin.com/jobs/view/102/', text: null },
    ],
  });
  const lc = b.store.lastCheck.b1;
  check('lastCheck ok entry', [lc.status, lc.jobsFound, lc.newJobs, lc.filteredOut, typeof lc.at], ['ok', 2, 2, 0, 'number']);
  check('badge shows the new offers', b.calls.badgeText.slice(-1), ['2']);
  ok('scan report sent next to the bridge', b.calls.fetches.some((f) => f.method === 'POST' && f.url === 'http://127.0.0.1:8765/scan-report'));

  // Changed by public-release S2: without a bridge the round scans nothing
  // (it used to scan and mark every offer seen).
  await boot('round: no bridge without a secret', roundStore([beacon('b1', LI_A)], { settings: { ...SETTINGS, bridgeSecret: '' } }), { pages: page });
  const rn = await send({ type: 'run-now' });
  check('nothing posted anywhere', b.calls.fetches.length, 0);
  check('nothing scanned, nothing marked seen', [rn.ok, rn.bridgeMissing, rn.summary.totalNew, b.store.seenJobs], [false, true, 0, undefined]);

  await boot('round: stats and badge on a quiet round', roundStore([beacon('b1', LI_A)], {
    seenJobs: { b1: ['101', '102'] }, stats: { totalNotified: 7, lastRoundAt: 1 },
  }), { pages: page });
  await send({ type: 'run-now' });
  check('totalNotified preserved', b.store.stats.totalNotified, 7);
  ok('lastRoundAt advanced', b.store.stats.lastRoundAt > 1, b.store.stats);
  check('badge cleared when nothing is new', b.calls.badgeText.slice(-1), ['']);

  await boot('round: session expired stops the round', roundStore([beacon('b1', LI_A), beacon('b2', LI_B)]), {
    pages: { ...page, [LI_B]: liPage([{ id: '103', title: 'AI Engineer' }]) },
    redirects: { [LI_A]: 'https://www.linkedin.com/authwall?trk=x' },
  });
  const rs = await send({ type: 'run-now' });
  check('answers ok with one error', [rs.ok, rs.summary.errors, rs.summary.beaconsScanned], [true, 1, 0]);
  check('lastCheck says session_expired', b.store.lastCheck.b1 && b.store.lastCheck.b1.status, 'session_expired');
  check('the next beacon is not scanned', [visited(), b.store.lastCheck.b2], [[LI_A], undefined]);
  check('one browser notification', b.calls.notifications.length, 1);
  check('nothing bridged', bridged().length, 0);
  check('tab disposed', b.tabs.size, 0);

  await boot('round: a genuine empty result is ok', roundStore([beacon('b1', LI_A)]), { pages: { [LI_A]: NO_RESULTS_PAGE } });
  const re = await send({ type: 'run-now' });
  check('ok with zero jobs', [re.summary.beaconsScanned, b.store.lastCheck.b1.status, b.store.lastCheck.b1.jobsFound], [1, 'ok', 0]);
  const report = b.calls.fetches.find((f) => f.url.endsWith('/scan-report'));
  check('reported as a confirmed empty scan', report && report.body.status, 'ok');

  await boot('round: the portal permission is required', roundStore([beacon('b1', LI_A)]), { pages: page, granted: [] });
  const rq = await send({ type: 'run-now' });
  check('nothing scanned', [rq.summary.beaconsScanned, b.calls.tabsCreate.length], [0, 0]);
  check('lastCheck says no_permission', b.store.lastCheck.b1 && b.store.lastCheck.b1.status, 'no_permission');
  ok('the permission asked for is the LinkedIn origin', b.calls.permissionsContains.some((o) => o.includes('https://www.linkedin.com/*')), b.calls.permissionsContains);

  await boot('round: auto searches scan through their own ids', {
    settings: { ...SETTINGS }, beacons: [], enabledPortals: ['linkedin'], titleFilter: NO_FILTER,
    searches: [{ id: 's1', name: 'AI BCN', keywords: 'ai engineer', location: 'Barcelona', filterProfile: 'ai', portals: ['linkedin'], titleFilter: 'off' }],
  }, { pages: { 'https://www.linkedin.com/jobs/search/*': liPage([{ id: '104', title: 'Anything' }]) } });
  await send({ type: 'run-now' });
  const url = visited()[0] || '';
  ok('the LinkedIn search URL carries keywords and location', /keywords=ai(\+|%20)engineer/.test(url) && /location=Barcelona/.test(url), url);
  check('seen and lastCheck keyed auto:<search id>:<portal>', [Object.keys(b.store.seenJobs), Object.keys(b.store.lastCheck)], [['auto:s1:linkedin'], ['auto:s1:linkedin']]);
  check('the bridge beacon name is "<search name> · <portal label>"', bridged()[0] && bridged()[0].body.beacon, 'AI BCN · LinkedIn');
  check('auto beacons are never written into beacons', b.store.beacons, []);
}

// ------------------------------------------------------------------------------------------
// role-families T6 (odd/tasks/role-families.md S3, S5): a family the bot declared
// (stored as profileFamilies) keeps its id through the round, picks its own title
// filter (familyTitleFilters) and reaches the bot as the payload `profile`.
const FAMILIES = [
  { id: 'ai', label: 'AI', default: true },
  { id: 'backend', label: 'Backend', default: false },
  { id: 'frontend', label: 'Frontend', default: false },
];

async function areaFamilies() {
  const cards = [{ id: '51', title: 'React Developer' }, { id: '52', title: 'AI Engineer' }];
  const frontendFilter = { enabled: true, includeGroups: [['React']], excludeTerms: [] };

  await boot('families: a frontend search keeps its family and filter', roundStore([], {
    profileFamilies: FAMILIES,
    familyTitleFilters: { frontend: frontendFilter },
    titleFilter: { enabled: true, includeGroups: [['AI']], excludeTerms: [] },
    searches: [{ id: 's1', name: 'FE', keywords: 'react', location: 'Barcelona', filterProfile: 'frontend', portals: ['linkedin'], titleFilter: 'on' }],
  }), { pages: { 'https://www.linkedin.com/jobs/search/*': liPage(cards) } });
  await send({ type: 'run-now' });
  check('the frontend filter decides', bridgedIds(), ['51']);
  check('payload profile is frontend', bridged()[0] && bridged()[0].body.profile, 'frontend');

  await boot('families: a manual frontend beacon sends profile frontend', roundStore([beacon('b1', LI_A, { filterProfile: 'frontend' })], {
    profileFamilies: FAMILIES,
    familyTitleFilters: { frontend: frontendFilter },
  }), { pages: { [LI_A]: liPage(cards) } });
  await send({ type: 'run-now' });
  check('manual beacon: the frontend filter decides', bridgedIds(), ['51']);
  check('manual beacon: payload profile is frontend', bridged()[0] && bridged()[0].body.profile, 'frontend');

  await boot('families: an undeclared family degrades to the default one', roundStore([beacon('b1', LI_A, { filterProfile: 'nursing' })], {
    profileFamilies: FAMILIES,
    titleFilter: { enabled: true, includeGroups: [['AI']], excludeTerms: [] },
  }), { pages: { [LI_A]: liPage(cards) } });
  await send({ type: 'run-now' });
  check('unknown family: the default (ai) filter decides', bridgedIds(), ['52']);
  check('unknown family: payload profile is the default', bridged()[0] && bridged()[0].body.profile, 'ai');

  // GET /config (read by every round) keeps the family list in sync.
  const fe = { enabled: true, includeGroups: [['React']], excludeTerms: [] };
  const served = { ai: { enabled: true, includeGroups: [['Served']], excludeTerms: [] } };
  await boot('families: a round adopts the /config family list', roundStore([], {
    profileTitleFilters: served,
    familyTitleFilters: { frontend: { enabled: false, includeGroups: [['Mine']], excludeTerms: [] } },
  }), { config: { paused: false, enabledPortals: ['linkedin'], families: [...FAMILIES.slice(0, 2), { ...FAMILIES[2], titleFilter: fe }] } });
  await send({ type: 'run-now' });
  check('/config: profileFamilies stored', b.store.profileFamilies, FAMILIES);
  check('/config: the family filter joins the served defaults', b.store.profileTitleFilters, { ...served, frontend: fe });
  check('/config: user edits untouched', b.store.familyTitleFilters, { frontend: { enabled: false, includeGroups: [['Mine']], excludeTerms: [] } });

  const builtinStore = roundStore([], { profileTitleFilters: served });
  await boot('families: a built-in /config leaves storage unchanged', builtinStore, { config: { paused: false, enabledPortals: ['linkedin'] } });
  await send({ type: 'run-now' });
  const keep = (st) => Object.fromEntries(Object.entries(st).filter(([k]) => !['botConfig', 'stats', 'lastCheck', 'seenJobs', 'detailAttempts', 'recoveringBeacons', 'portalSeenRecovery'].includes(k)));
  check('/config without families: storage unchanged', keep(b.store), keep(clone(builtinStore)));

  await boot('families: /config without families removes a stale list', roundStore([], { profileFamilies: FAMILIES }), { config: { paused: false, enabledPortals: ['linkedin'] } });
  await send({ type: 'run-now' });
  check('/config without families: profileFamilies removed', 'profileFamilies' in b.store, false);

  await boot('families: without a family list frontend is unknown (built-ins)', roundStore([beacon('b1', LI_A, { filterProfile: 'frontend' })], {
    titleFilter: { enabled: true, includeGroups: [['AI']], excludeTerms: [] },
  }), { pages: { [LI_A]: liPage(cards) } });
  await send({ type: 'run-now' });
  check('built-ins: the ai filter decides', bridgedIds(), ['52']);
  check('built-ins: payload profile is ai', bridged()[0] && bridged()[0].body.profile, 'ai');
}

const AREAS = {
  alarm: areaAlarm, tab: areaTab, migrate: areaMigrate, seen: areaSeen, msg: areaMsg, round: areaRound,
  families: areaFamilies,
};

async function main() {
  const wanted = process.argv.slice(2);
  const names = wanted.length ? wanted : Object.keys(AREAS);
  for (const name of names) {
    if (!AREAS[name]) throw new Error(`unknown area ${name}; known: ${Object.keys(AREAS).join(', ')}`);
    await AREAS[name]();
  }
  finish(`service worker [${names.join(', ')}]`);
}

main().catch(crashed).finally(() => process.exit());
