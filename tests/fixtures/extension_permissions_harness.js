'use strict';

// Node harness for the safe-defaults wiring in extension/background.js
// (safe-defaults T2): per-portal opt-in (enabledPortals + browser permission),
// the owner migration, the pause (local and the bot's), the bot /config sync
// and the fresh-install pace. background.js is imported as a module over
// stubbed chrome.*, fetch and timers; rounds are driven through `run-now`.
// Invoked by tests/test_extension_permissions.py. Exit 0 on success.

const path = require('path');
const { pathToFileURL } = require('url');

const failures = [];
function check(name, actual, expected) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) failures.push(`${name}: expected ${e}, got ${a}`);
}

const logs = [];
const realError = console.error;
console.error = (...args) => logs.push(args.join(' '));
console.log = (...args) => logs.push(args.join(' '));

const realSetTimeout = globalThis.setTimeout;
const watchdog = realSetTimeout(() => {
  realError('harness watchdog: a scenario did not finish');
  process.exit(2);
}, 30000);
watchdog.unref();

// Every delay elapses on the next macrotask.
const pending = new Map();
let nextTimer = 1;
globalThis.setTimeout = (fn) => {
  const id = nextTimer++;
  pending.set(id, setImmediate(() => { pending.delete(id); fn(); }));
  return id;
};
globalThis.clearTimeout = (id) => {
  clearImmediate(pending.get(id));
  pending.delete(id);
};

const TAB = 7;
const store = {};
const handlers = {};
const visited = [];
const alarmCalls = [];
const permissionChecks = [];
let scenario = {};

const clone = (v) => (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));

globalThis.chrome = {
  storage: {
    local: {
      async get(keys) {
        const out = {};
        for (const key of Array.isArray(keys) ? keys : [keys]) {
          if (key in store) out[key] = clone(store[key]);
        }
        return out;
      },
      async set(values) { Object.assign(store, clone(values)); },
      async remove(key) { delete store[key]; },
    },
  },
  tabs: {
    async create() { return { id: TAB }; },
    async update(_id, props) { if (props && props.url) visited.push(props.url); },
    async get() { return { id: TAB, url: 'https://www.linkedin.com/jobs/search/?keywords=ai' }; },
    async remove() {},
    onUpdated: {
      addListener(listener) { listener(TAB, { status: 'complete' }); },
      removeListener() {},
    },
  },
  scripting: {
    async executeScript({ func }) {
      if (func.name === 'extractJobsFromPage' || /^extract\w+Cards$/.test(func.name)) return [{ result: [] }];
      if (func.name === 'detectNoResultsInPage') return [{ result: true }];
      throw new Error(`unexpected injected function: ${func.name}`);
    },
  },
  permissions: {
    async contains({ origins }) {
      permissionChecks.push(origins);
      if (!scenario.granted) return true;
      return origins.every((o) => scenario.granted.includes(o));
    },
  },
  alarms: {
    async clear() {},
    async create(name, info) { alarmCalls.push({ name, info }); },
    onAlarm: { addListener() {} },
  },
  runtime: {
    onInstalled: { addListener(l) { handlers.installed = l; } },
    onStartup: { addListener(l) { handlers.startup = l; } },
    onMessage: { addListener(l) { handlers.message = l; } },
  },
  action: {
    async setBadgeBackgroundColor() {},
    async setBadgeText() {},
  },
  notifications: { async create() {} },
};

const fetches = [];
globalThis.fetch = async (url, init = {}) => {
  fetches.push({ url, headers: init.headers || {}, method: init.method || 'GET' });
  if (url.endsWith('/config')) {
    if (scenario.configThrow) throw new Error('down');
    return { ok: true, status: 200, json: async () => scenario.config, text: async () => '' };
  }
  return { ok: true, status: 200, text: async () => '' };
};

const LINKEDIN = 'https://www.linkedin.com/jobs/search/?keywords=ai';
const INFOJOBS = 'https://www.infojobs.net/ofertas-trabajo?q=ai';
const SETTINGS = {
  botToken: '', chatId: '', intervalMinutes: 15, paused: false, bridgeUrl: '', bridgeSecret: '',
};
// Rounds scan only with a bridge (public-release S2).
const BRIDGED = { ...SETTINGS, bridgeUrl: 'http://127.0.0.1:8765/bridge', bridgeSecret: 's3cret' };

function reset(next) {
  scenario = next.scenario || {};
  for (const key of Object.keys(store)) delete store[key];
  Object.assign(store, clone(next.store || {}));
  visited.length = 0;
  alarmCalls.length = 0;
  permissionChecks.length = 0;
  fetches.length = 0;
  logs.length = 0;
}

const send = (msg) => new Promise((resolve) => { handlers.message(msg, null, resolve); });
const hostsVisited = () => visited.map((u) => new URL(u).hostname);

async function main() {
  const modUrl = pathToFileURL(path.resolve(__dirname, '..', '..', 'extension', 'background.js')).href;
  await import(modUrl);

  // --- S4 fresh install: nothing enabled, 60 minute pace ----------------------------------
  reset({});
  await handlers.installed({ reason: 'install' });
  check('fresh: no portal is enabled', store.enabledPortals, []);
  check('fresh: default interval is 60', store.settings && store.settings.intervalMinutes, 60);
  check('fresh: the alarm period is 60', alarmCalls.map((c) => c.info.periodInMinutes), [60]);

  // --- S3 owner migration: current searches and manual beacons keep working ---------------
  reset({
    store: {
      settings: { ...BRIDGED, intervalMinutes: 15 },
      beacons: [{ id: 'b1', name: 'LI', url: LINKEDIN, enabled: true }],
      searches: [{ id: 's1', name: 'IJ', keywords: 'ai', portals: ['infojobs'] }],
    },
  });
  await handlers.installed({ reason: 'update' });
  check('migration: enables exactly the portals already in use', [...store.enabledPortals].sort(), ['infojobs', 'linkedin']);
  check('migration: stored interval untouched (S4 existing install)', store.settings.intervalMinutes, 15);
  check('migration: the alarm keeps the stored period', alarmCalls.map((c) => c.info.periodInMinutes), [15]);
  const migrated = await send({ type: 'run-now' });
  check('migration: owner beacons keep scanning with no prompt', [migrated.ok, hostsVisited()], [true, ['www.linkedin.com', 'www.infojobs.net']]);
  check('migration: round scanned both', migrated.summary.beaconsScanned, 2);

  // a second run never re-derives over what the owner changed afterwards
  store.enabledPortals = ['linkedin'];
  await handlers.startup();
  check('migration: runs only while the key is absent', store.enabledPortals, ['linkedin']);

  // startup also migrates an install that never saw the update event
  reset({ store: { settings: SETTINGS, beacons: [{ id: 'b1', name: 'LI', url: LINKEDIN }] } });
  await handlers.startup();
  check('migration: also on startup', store.enabledPortals, ['linkedin']);

  // --- S3 scanning gates --------------------------------------------------------------------
  reset({
    store: {
      settings: BRIDGED,
      beacons: [{ id: 'b1', name: 'LI', url: LINKEDIN, enabled: true }, { id: 'b2', name: 'IJ', url: INFOJOBS, enabled: true }],
      enabledPortals: ['linkedin'],
    },
  });
  let r = await send({ type: 'run-now' });
  check('gate: a portal that is not enabled is not scanned (manual beacon)', [hostsVisited(), r.summary.beaconsScanned], [['www.linkedin.com'], 1]);
  check('gate: the skip is logged', logs.some((l) => /infojobs/.test(l) && /not enabled/.test(l)), true);
  check('gate: the permission is checked for the enabled portal origin', permissionChecks, [['https://www.linkedin.com/*']]);

  reset({
    store: { settings: BRIDGED, beacons: [{ id: 'b1', name: 'LI', url: LINKEDIN, enabled: true }], enabledPortals: [] },
  });
  r = await send({ type: 'run-now' });
  check('gate: nothing enabled -> nothing scanned', [visited.length, r.summary.beaconsScanned], [0, 0]);

  reset({
    store: { settings: BRIDGED, beacons: [{ id: 'b1', name: 'LI', url: LINKEDIN, enabled: true }], enabledPortals: ['linkedin'] },
    scenario: { granted: [] },
  });
  r = await send({ type: 'run-now' });
  check('gate: enabled but permission missing -> skipped', [visited.length, r.summary.beaconsScanned], [0, 0]);
  check('gate: missing permission is logged and recorded', [logs.some((l) => /permission/.test(l)), store.lastCheck && store.lastCheck.b1 && store.lastCheck.b1.status], [true, 'no_permission']);

  reset({
    store: {
      settings: BRIDGED,
      searches: [{ id: 's1', name: 'x', keywords: 'ai', portals: ['linkedin', 'infojobs'] }],
      enabledPortals: ['infojobs'],
    },
  });
  r = await send({ type: 'run-now' });
  check('gate: auto beacons of a disabled portal are not expanded', hostsVisited(), ['www.infojobs.net']);

  // --- S2 pause ---------------------------------------------------------------------------------
  reset({
    store: { settings: { ...SETTINGS, paused: true, bridgeUrl: 'http://127.0.0.1:8765/bridge', bridgeSecret: 's' }, beacons: [{ id: 'b1', name: 'LI', url: LINKEDIN, enabled: true }], enabledPortals: ['linkedin'] },
  });
  r = await send({ type: 'run-now' });
  check('pause: run-now does not scan while paused', [r.ok, r.summary.paused, r.summary.beaconsScanned, visited.length], [true, true, 0, 0]);
  check('pause: a paused round does not even ask the bot', fetches.length, 0);

  reset({ store: { settings: SETTINGS, beacons: [], enabledPortals: [] } });
  await send({ type: 'set-paused', paused: true });
  check('pause: set-paused still clears the alarm and creates none', alarmCalls.length, 0);
  await send({ type: 'set-paused', paused: false });
  check('pause: resuming recreates the alarm with the stored period', alarmCalls.map((c) => c.info.periodInMinutes), [15]);

  // --- S2/S3 the bot's /config ---------------------------------------------------------------
  const TWO = [{ id: 'b1', name: 'LI', url: LINKEDIN, enabled: true }, { id: 'b2', name: 'IJ', url: INFOJOBS, enabled: true }];
  reset({ store: { settings: BRIDGED, beacons: TWO, enabledPortals: ['linkedin', 'infojobs'] }, scenario: { config: { paused: true, enabledPortals: ['linkedin', 'infojobs'] } } });
  r = await send({ type: 'run-now' });
  check('bot pause: the round does not scan', [r.summary.botPaused, r.summary.beaconsScanned, visited.length], [true, 0, 0]);
  const cfg = fetches.find((f) => f.url.endsWith('/config'));
  check('bot config: GET /config next to the bridge with the secret header', cfg && [cfg.url, cfg.method, cfg.headers['X-CVA-Bridge-Secret']], ['http://127.0.0.1:8765/config', 'GET', 's3cret']);
  check('bot config is remembered for the popup', store.botConfig && [store.botConfig.paused, store.botConfig.enabledPortals], [true, ['linkedin', 'infojobs']]);

  reset({ store: { settings: BRIDGED, beacons: TWO, enabledPortals: ['linkedin', 'infojobs'] }, scenario: { config: { paused: false, enabledPortals: ['infojobs'] } } });
  r = await send({ type: 'run-now' });
  check('precedence: a portal must be enabled on both sides', hostsVisited(), ['www.infojobs.net']);
  check('precedence: the bot skip is logged', logs.some((l) => /linkedin/.test(l) && /bot/.test(l)), true);

  reset({ store: { settings: BRIDGED, beacons: TWO, enabledPortals: ['linkedin'] }, scenario: { config: { paused: false, enabledPortals: ['linkedin', 'infojobs'] } } });
  r = await send({ type: 'run-now' });
  check('precedence: enabled on the bot only is still off locally', hostsVisited(), ['www.linkedin.com']);

  reset({ store: { settings: BRIDGED, beacons: TWO, enabledPortals: ['linkedin', 'infojobs'] }, scenario: { configThrow: true } });
  r = await send({ type: 'run-now' });
  // Changed by beta3-ux: an unreachable bot could receive nothing (its offers
  // stayed unseen for the next round anyway), so the round stops at once with
  // a reason the popup shows instead of scanning for minutes. A bot that
  // answers without a usable /config (404, older bot) still runs (local rules).
  check('bot unreachable: the round stops with a reason, nothing visited', [r.summary.botPaused === true, r.summary.reason, hostsVisited()], [false, 'botUnreachable', []]);

  reset({ store: { settings: SETTINGS, beacons: TWO, enabledPortals: ['linkedin', 'infojobs'] } });
  r = await send({ type: 'run-now' });
  check('no bridge configured: /config is never requested and nothing is scanned (S2)', [fetches.filter((f) => f.url.endsWith('/config')).length, visited.length, r.bridgeMissing], [0, 0, true]);
}

main().then(() => {
  if (failures.length) {
    realError(`FAIL: ${failures.length} assertion(s) failed:`);
    for (const f of failures) realError(' - ' + f);
    process.exitCode = 1;
  } else {
    process.stdout.write('OK: all safe-defaults background assertions passed\n');
  }
}).catch((e) => {
  realError('harness crashed:', (e && e.stack) || e);
  process.exitCode = 1;
});
