'use strict';

// Service worker behaviours of the clean-room rewrite that the
// characterization harness does not pin (odd/tasks/extension-rewrite.md T2/T3,
// docs/extension-spec.md section 11 and S4). Same fake browser and the same
// public surface only: Chrome events, runtime messages, storage, chrome.*
// calls and fetches.
//
//   node extension_engine_harness.js <area>
//   areas: migrate, testscrape, telegram, texts, linkedin, report, nobridge

const {
  check, ok, logs, idle, fire, sendMessage, clone, makeBrowser, readManifest,
  importExtensionFile, setScenario, finish, startWatchdog, crashed,
} = require('./extension_characterization_harness_lib.js');

startWatchdog(90000);

const MANIFEST = readManifest();
const BRIDGE = 'http://127.0.0.1:8765/bridge';
const LI_A = 'https://www.linkedin.com/jobs/search/?keywords=ai&location=Barcelona';
const NO_FILTER = { enabled: false, includeGroups: [], excludeTerms: [] };
const SETTINGS = { intervalMinutes: 30, paused: false, bridgeUrl: BRIDGE, bridgeSecret: 's3cret' };
const beacon = (id, url, extra = {}) => ({ id, name: `Beacon ${id}`, url, enabled: true, ...extra });
const liPage = (cards) => `<main>${cards.map((c) => (
  `<div componentkey="job-card-component-ref-${c.id}">${c.lines.map((l) => `<div>${l}</div>`).join('')}</div>`
)).join('')}</main>`;
const roundStore = (beacons, extra = {}) => ({
  settings: { ...SETTINGS }, beacons, enabledPortals: ['linkedin'], titleFilter: NO_FILTER, titleFilterBackend: NO_FILTER, ...extra,
});

let b = null;
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
}
const send = async (msg) => {
  const r = await sendMessage(b.chrome.runtime.onMessage, msg);
  await idle();
  return r;
};

async function areaMigrate() {
  const auto = [{ id: 's1', name: 'AI', keywords: 'ai engineer', location: 'Barcelona', filterProfile: 'ai', portals: ['linkedin'], titleFilter: 'on' }];
  for (const event of ['onInstalled', 'onStartup']) {
    await boot(`D1: auto searches without beacons are never moved (${event})`, { settings: { ...SETTINGS }, searches: auto });
    await fire(b.chrome.runtime[event], { reason: 'update' });
    check('searches keep the auto searches', b.store.searches, auto);
    ok('no auto search became a manual beacon', !(b.store.beacons || []).some((x) => x.id === 's1'), b.store.beacons);
  }

  const legacy = beacon('old1', LI_A);
  await boot('D1: a mixed legacy list moves only the entries with a url', { settings: { ...SETTINGS }, searches: [legacy, ...auto] });
  await fire(b.chrome.runtime.onStartup);
  check('beacons get the legacy beacon', b.store.beacons, [legacy]);
  check('searches keep the auto search', b.store.searches, auto);
}

function hold() {
  let release;
  const promise = new Promise((resolve) => { release = resolve; });
  const gate = { promise, released: false };
  gate.release = () => { gate.released = true; release(); };
  return gate;
}

async function areaTestScrape() {
  const page = { [LI_A]: liPage([{ id: '1', lines: ['AI Engineer', 'Acme', 'Barcelona'] }]) };
  await boot('D3: test-scrape during a round never touches the round tab', roundStore([beacon('b1', LI_A)]), { pages: page });
  const gate = hold();
  b.scenario.holdScript = gate;
  const round = sendMessage(b.chrome.runtime.onMessage, { type: 'run-now' });
  await idle();
  const roundTab = b.store.scrapeTabId;
  const answer = await sendMessage(b.chrome.runtime.onMessage, { type: 'test-scrape', url: LI_A });
  check('answers not ok', answer && answer.ok, false);
  ok('says a scan is running', answer && /scan is running/i.test(answer.error || ''), answer);
  check('no extra tab, the round tab still open', [b.calls.tabsCreate.length, b.calls.tabsRemove.length, b.tabs.has(roundTab)], [1, 0, true]);
  gate.release();
  const done = await round;
  await idle();
  check('the round completes and scans its beacon', [done.ok, done.summary.beaconsScanned, b.store.lastCheck.b1.status], [true, 1, 'ok']);

  await boot('D3: test-scrape outside a round leaves a stored round tab alone', roundStore([]), { pages: page });
  const stray = await b.chrome.tabs.create({ url: 'about:blank', active: false });
  b.store.scrapeTabId = stray.id;
  b.calls.tabsCreate.length = 0;
  const t = await send({ type: 'test-scrape', url: LI_A });
  check('cards answered', t && t.ok && t.jobs.map((j) => j.id), ['1']);
  check('its own tab, closed; the stored one untouched', [b.calls.tabsCreate.length, b.calls.tabsRemove.includes(stray.id), b.store.scrapeTabId], [1, false, stray.id]);
}

async function areaTelegram() {
  await boot('S4: stored Telegram credentials are dropped on startup', {
    settings: { ...SETTINGS, botToken: '123:abc', chatId: '42' }, beacons: [],
  });
  await fire(b.chrome.runtime.onStartup);
  check('settings keep everything else', b.store.settings, SETTINGS);

  await boot('S4: a round never calls Telegram', roundStore([beacon('b1', LI_A)], {
    settings: { ...SETTINGS, botToken: '123:abc', chatId: '42' },
  }), { pages: { [LI_A]: liPage([{ id: '7', lines: ['AI Engineer', 'Acme', 'Barcelona'] }]) } });
  const r = await send({ type: 'run-now' });
  check('round ok', [r.ok, r.summary.beaconsScanned], [true, 1]);
  check('only the bot is contacted', [...new Set(b.calls.fetches.map((f) => new URL(f.url).host))], ['127.0.0.1:8765']);
  check('the offer went to the bridge', b.calls.fetches.filter((f) => f.url === BRIDGE).length, 1);

  await boot('S4: test-telegram is an unknown message', {});
  const u = await send({ type: 'test-telegram', botToken: 'x', chatId: 'y' });
  check('answers unknown message type', u, { ok: false, error: 'unknown message type' });
  check('nothing fetched', b.calls.fetches.length, 0);
}

async function areaTexts() {
  await boot('D5: the session-expired text names the portal', roundStore([beacon('b1', LI_A)]), {
    pages: { [LI_A]: '<main></main>' }, redirects: { [LI_A]: 'https://www.linkedin.com/checkpoint/challenge' },
  });
  await send({ type: 'run-now' });
  check('lastCheck entry', [b.store.lastCheck.b1.status, b.store.lastCheck.b1.error], ['session_expired', 'LinkedIn session expired']);
  const n = b.calls.notifications[0] || {};
  check('one basic, high priority notification', [b.calls.notifications.length, n.type, n.priority], [1, 'basic', 2]);
  ok('the notification names the portal', /LinkedIn/.test(`${n.title} ${n.message}`), n);
  const report = b.calls.fetches.find((f) => f.url.endsWith('/scan-report'));
  check('reported as session_expired', report && report.body.status, 'session_expired');

  await boot('spec 2.5: the no_permission entry', roundStore([beacon('b1', LI_A)]), { granted: [] });
  await send({ type: 'run-now' });
  check('entry', [b.store.lastCheck.b1.status, b.store.lastCheck.b1.error],
    ['no_permission', 'Permission for linkedin is missing: enable the portal in options']);
}

async function areaLinkedin() {
  const noisy = [
    { id: '11', lines: ['Promocionado', 'Ingeniero de IA', 'Visto', 'Acme', 'Hace 2 días', 'Barcelona', 'Solicitud sencilla'] },
    { id: '12', lines: ['Data Engineer', 'Easy Apply', 'Globex', '3 days ago', 'Actively evaluating applicants', 'Madrid'] },
    { id: '13', lines: ['ML Engineer', 'Initech', 'Menos de 25 solicitantes', '2 contactos trabajan aquí', 'Valencia', 'hoy'] },
    { id: '14', lines: [`${'Very long title '.repeat(20)}`, 'Hooli', 'Remote'] },
  ];
  await boot('spec 5.2: noise lines are dropped, fields stay positional', roundStore([]), { pages: { [LI_A]: liPage(noisy) } });
  const t = await send({ type: 'test-scrape', url: LI_A });
  check('ok', t && t.ok, true);
  check('fields', (t.jobs || []).slice(0, 3).map((j) => [j.title, j.company, j.location]), [
    ['Ingeniero de IA', 'Acme', 'Barcelona'],
    ['Data Engineer', 'Globex', 'Madrid'],
    ['ML Engineer', 'Initech', 'Valencia'],
  ]);
  check('titles are cut to 200 characters', t.jobs && t.jobs[3] && t.jobs[3].title.length, 200);
}

async function areaReport() {
  const page = liPage([{ id: '7', lines: ['AI Engineer', 'Acme', 'Barcelona'] }]);
  for (const [name, scenario] of [['HTTP 500', { reportStatus: 500 }], ['network error', { reportThrow: true }]]) {
    await boot(`scan report ${name} never breaks the round`, roundStore([beacon('b1', LI_A)]), { pages: { [LI_A]: page }, ...scenario });
    const r = await send({ type: 'run-now' });
    check(`${name}: round ok`, [r.ok, r.summary.beaconsScanned], [true, 1]);
    check(`${name}: the offer still went to the bridge`, b.calls.fetches.filter((f) => f.url === BRIDGE).length, 1);
    check(`${name}: the report was attempted`, b.calls.fetches.some((f) => new URL(f.url).pathname === '/scan-report'), true);
    check(`${name}: the beacon is recorded as ok`, b.store.lastCheck && b.store.lastCheck.b1 && b.store.lastCheck.b1.status, 'ok');
  }
}

// S2 (public-release): without a bridge URL or secret the round scans
// nothing, so a fresh install never marks offers seen that went nowhere.
const NO_BRIDGE_TEXT = /Connect the extension to the bot first: Options › Schedule and bot/;
const nothingTouched = (label, before) => {
  check(`${label}: no tab opened`, b.calls.tabsCreate.length, 0);
  check(`${label}: nothing fetched`, b.calls.fetches.length, 0);
  check(`${label}: seenJobs, detailAttempts and lastCheck untouched`,
    [b.store.seenJobs, b.store.detailAttempts, b.store.lastCheck],
    [before.seenJobs, before.detailAttempts, before.lastCheck]);
};

async function areaNoBridge() {
  const page = { [LI_A]: liPage([
    { id: '1', lines: ['AI Engineer', 'Acme', 'Barcelona'] },
    { id: '2', lines: ['ML Engineer', 'Globex', 'Madrid'] },
  ]) };
  const variants = [
    ['no URL', { bridgeUrl: '' }],
    ['no secret', { bridgeSecret: '' }],
    ['neither', { bridgeUrl: '', bridgeSecret: '' }],
  ];
  for (const [label, missing] of variants) {
    const store = roundStore([beacon('b1', LI_A)], { settings: { ...SETTINGS, ...missing } });
    await boot(`S2: run-now without a bridge (${label}) scans nothing`, store, { pages: page });
    const r = await send({ type: 'run-now' });
    check(`${label}: answers the missing-bridge state`, [r && r.ok, r && r.bridgeMissing, r && r.summary && r.summary.bridgeMissing, r && r.summary && r.summary.beaconsScanned], [false, true, true, 0]);
    ok(`${label}: the error tells where to connect the bot`, r && NO_BRIDGE_TEXT.test(r.error || ''), r);
    nothingTouched(label, store);

    await boot(`S2: a scheduled round without a bridge (${label}) scans nothing`, store, { pages: page });
    await fire(b.chrome.alarms.onAlarm, { name: 'check-jobs', scheduledTime: Date.now() });
    nothingTouched(`alarm, ${label}`, store);
  }

  const pending = roundStore([beacon('b1', LI_A)], {
    settings: { ...SETTINGS, bridgeSecret: '' },
    seenJobs: { 'auto:s1:infojobs': ['9'] },
    detailAttempts: { 'auto:s1:infojobs': { 9: 1 } },
  });
  await boot('S2: no bridge leaves the seen-state recovery and missing grants alone', pending, { pages: page, granted: [] });
  await send({ type: 'run-now' });
  nothingTouched('recovery and no grant', pending);
  check('recovery not consumed', b.store.portalSeenRecovery, undefined);

  await boot('S2: once the bridge is set, the next round scans and bridges everything', roundStore([beacon('b1', LI_A)], {
    settings: { ...SETTINGS, bridgeUrl: '', bridgeSecret: '' },
  }), { pages: page });
  const before = await send({ type: 'run-now' });
  check('first refused', [before.ok, before.bridgeMissing], [false, true]);
  b.store.settings = { ...SETTINGS };
  const after = await send({ type: 'run-now' });
  check('then scanned as before', [after.ok, after.bridgeMissing, after.summary.beaconsScanned, after.summary.firstScans, after.summary.totalNew], [true, undefined, 1, 1, 2]);
  const posts = b.calls.fetches.filter((f) => f.url === BRIDGE);
  check('the first scan sends every offer to the bridge', posts.map((p) => p.body.jobs.map((j) => j.title)), [['AI Engineer', 'ML Engineer']]);
  check('and marks them seen', b.store.seenJobs.b1, ['1', '2']);

  await boot('S2: test-scrape works without a bridge', roundStore([], { settings: { ...SETTINGS, bridgeUrl: '', bridgeSecret: '' } }), { pages: page });
  const t = await send({ type: 'test-scrape', url: LI_A });
  check('cards answered', [t && t.ok, t && (t.jobs || []).map((j) => j.id)], [true, ['1', '2']]);
  check('nothing fetched or stored', [b.calls.fetches.length, b.store.seenJobs, b.store.lastCheck], [0, undefined, undefined]);
}

const AREAS = { nobridge: areaNoBridge, report: areaReport, migrate: areaMigrate, testscrape: areaTestScrape, telegram: areaTelegram, texts: areaTexts, linkedin: areaLinkedin };

async function main() {
  const wanted = process.argv.slice(2);
  const names = wanted.length ? wanted : Object.keys(AREAS);
  for (const name of names) {
    if (!AREAS[name]) throw new Error(`unknown area ${name}; known: ${Object.keys(AREAS).join(', ')}`);
    await AREAS[name]();
  }
  finish(`engine [${names.join(', ')}]`);
}

main().catch(crashed).finally(() => process.exit());
