'use strict';

// Shared fake browser for the extension characterization harnesses
// (odd/tasks/extension-rewrite.md T1). It models only the PUBLIC surface an
// MV3 extension sees: chrome.* APIs, fetch, timers and, for injected page
// functions, a minimal DOM (minidom.js). Nothing here knows the names of the
// extension's internal functions, so the same harness drives the current code
// and its clean-room rewrite.
//
// Conventions the extension is expected to follow (the contract, see
// docs/extension-spec.md):
//   * entry points are read from extension/manifest.json (service worker,
//     popup, options page); scripts are loaded with a cache-busting import();
//   * page functions are injected with chrome.scripting.executeScript({func,
//     args}); the harness runs them for real against the HTML the fake tab is
//     showing;
//   * time is virtual: setTimeout/setInterval callbacks run in due order as
//     soon as the process is otherwise idle, so the extension's pacing sleeps
//     and timeouts cost nothing but keep their relative order.

const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');
const { installDocument, uninstallDocument } = require('./minidom.js');

const EXTENSION_DIR = path.resolve(__dirname, '..', '..', 'extension');
const FIXTURES_DIR = __dirname;

const failures = [];
let currentScenario = '';

function check(name, actual, expected) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) failures.push(`${currentScenario} :: ${name}: expected ${e}, got ${a}`);
}

function ok(name, condition, detail) {
  if (!condition) {
    failures.push(`${currentScenario} :: ${name}${detail === undefined ? '' : `: ${JSON.stringify(detail)}`}`);
  }
}

// --- quiet console (the extension logs skips and errors) --------------------------
const logs = [];
const realConsoleError = console.error;
console.error = (...args) => logs.push(args.map(String).join(' '));
console.log = (...args) => logs.push(args.map(String).join(' '));
console.warn = (...args) => logs.push(args.map(String).join(' '));
console.info = (...args) => logs.push(args.map(String).join(' '));

const realSetTimeout = globalThis.setTimeout;
const realSetImmediate = globalThis.setImmediate;

// --- virtual timers -------------------------------------------------------------------
let virtualNow = 0;
let timerSeq = 0;
const timers = new Map(); // id -> {due, seq, fn, interval, delay}
let pumpScheduled = false;
// While a page script is held by a test (scenario.holdScript), long timers
// (the extension's safety timeouts, >= FROZEN_MS) do not elapse: the held
// script models a page that is busy for a moment, not one that hangs (a
// hanging page is scenario.stuckScript, whose timeouts do fire).
const FROZEN_MS = 10000;
let heldScripts = 0;
const runnable = (t) => !(heldScripts > 0 && t.delay >= FROZEN_MS);
const runnableCount = () => [...timers.values()].filter(runnable).length;

function kick() {
  if (pumpScheduled) return;
  pumpScheduled = true;
  realSetImmediate(pump);
}

function pump() {
  pumpScheduled = false;
  if (!runnableCount()) return;
  let next = null;
  for (const [id, t] of timers) {
    if (!runnable(t)) continue;
    if (!next || t.due < next.t.due || (t.due === next.t.due && t.seq < next.t.seq)) next = { id, t };
  }
  virtualNow = Math.max(virtualNow, next.t.due);
  if (next.t.interval) {
    next.t.due = virtualNow + next.t.interval;
    next.t.seq = ++timerSeq;
  } else {
    timers.delete(next.id);
  }
  try {
    next.t.fn();
  } finally {
    kick();
  }
}

let nextTimerId = 1;
function addTimer(fn, ms, args, interval) {
  const id = nextTimerId++;
  const delay = Math.max(0, Number(ms) || 0);
  timers.set(id, {
    due: virtualNow + delay,
    seq: ++timerSeq,
    fn: () => fn(...args),
    interval: interval ? Math.max(1, delay) : 0,
    delay,
  });
  kick();
  return id;
}
globalThis.setTimeout = (fn, ms, ...args) => addTimer(fn, ms, args, false);
globalThis.setInterval = (fn, ms, ...args) => addTimer(fn, ms, args, true);
globalThis.clearTimeout = (id) => { timers.delete(id); };
globalThis.clearInterval = (id) => { timers.delete(id); };

// Lets every pending promise chain and due timer settle.
async function settle(rounds = 50) {
  for (let i = 0; i < rounds; i += 1) {
    await new Promise((resolve) => realSetImmediate(resolve));
  }
}

// Waits until no timer is pending and the event loop stayed idle for a while:
// every round, listener and page script that can finish has finished.
async function idle() {
  let quiet = 0;
  for (let i = 0; i < 200000 && quiet < 8; i += 1) {
    await new Promise((resolve) => realSetImmediate(resolve));
    quiet = runnableCount() === 0 && !pumpScheduled ? quiet + 1 : 0;
  }
}

// Fires a chrome event the way Chrome does and waits for its effects.
async function fire(event, ...args) {
  const results = event.listeners.map((l) => l(...args));
  await Promise.all(results.map((r) => (r && typeof r.then === 'function' ? r.catch((e) => logs.push(String(e))) : r)));
  await idle();
}

// Sends a runtime message to the extension's onMessage listeners. Resolves
// with the response given through sendResponse or a returned promise.
function sendMessage(event, msg, sender = { id: 'harness-extension-id' }) {
  return new Promise((resolve) => {
    let answered = false;
    const reply = (value) => {
      if (answered) return;
      answered = true;
      resolve(value);
    };
    let keepOpen = false;
    for (const l of event.listeners) {
      const r = l(clone(msg), sender, reply);
      if (r === true) keepOpen = true;
      else if (r && typeof r.then === 'function') {
        keepOpen = true;
        r.then((v) => { if (v !== undefined) reply(v); }, (e) => reply({ ok: false, error: String(e) }));
      }
    }
    if (!keepOpen && !answered) realSetImmediate(() => reply(undefined));
  });
}

const clone = (v) => (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));

// --- chrome.* -----------------------------------------------------------------------------
function makeEvent() {
  const listeners = [];
  return {
    listeners,
    addListener(fn) { listeners.push(fn); },
    removeListener(fn) {
      const i = listeners.indexOf(fn);
      if (i >= 0) listeners.splice(i, 1);
    },
    hasListener(fn) { return listeners.includes(fn); },
    hasListeners() { return listeners.length > 0; },
  };
}

function makeBrowser() {
  const store = {};
  const calls = {
    tabsCreate: [], tabsUpdate: [], tabsRemove: [], tabsGet: [],
    alarmsCreate: [], alarmsClear: [], badgeText: [], badgeColor: [], notifications: [],
    permissionsContains: [], permissionsRequest: [], permissionsRemove: [],
    executeScript: [], messagesSent: [], openOptionsPage: 0, fetches: [],
  };
  const tabs = new Map(); // id -> {id, url, status, active}
  const alarms = new Map(); // name -> {name, scheduledTime, periodInMinutes}
  const scenario = {
    pages: {}, // url (exact or prefix match) -> html
    redirects: {}, // url -> final url shown by the tab after load
    neverLoads: new Set(), // urls whose load never completes
    granted: null, // null = every origin granted; array = granted origins
    requestGrants: true, // answer of chrome.permissions.request
    config: null, // body of GET /config; null -> 404
    bridgeStatus: 200,
    bridgeThrow: false,
    holdScript: null, // {resolve} gate: card extraction waits for it
    messageHandler: null, // answers chrome.runtime.sendMessage from pages
  };
  let nextTabId = 100;

  const onChanged = makeEvent();
  function emitChanges(changes) {
    if (!Object.keys(changes).length) return;
    for (const l of [...onChanged.listeners]) {
      try { l(clone(changes), 'local'); } catch (e) { logs.push(String(e)); }
    }
  }

  const local = {
    async get(keys) {
      const out = {};
      if (keys === null || keys === undefined) return clone(store);
      if (typeof keys === 'string') keys = [keys];
      if (Array.isArray(keys)) {
        for (const k of keys) if (k in store) out[k] = clone(store[k]);
        return out;
      }
      for (const [k, def] of Object.entries(keys)) out[k] = k in store ? clone(store[k]) : clone(def);
      return out;
    },
    async set(values) {
      const changes = {};
      for (const [k, v] of Object.entries(values)) {
        changes[k] = { oldValue: clone(store[k]), newValue: clone(v) };
        store[k] = clone(v);
      }
      emitChanges(changes);
    },
    async remove(keys) {
      const changes = {};
      for (const k of typeof keys === 'string' ? [keys] : keys) {
        if (k in store) changes[k] = { oldValue: clone(store[k]) };
        delete store[k];
      }
      emitChanges(changes);
    },
    async clear() {
      const changes = {};
      for (const k of Object.keys(store)) changes[k] = { oldValue: clone(store[k]) };
      for (const k of Object.keys(store)) delete store[k];
      emitChanges(changes);
    },
  };

  function pageFor(url) {
    if (url in scenario.pages) return scenario.pages[url];
    for (const [prefix, html] of Object.entries(scenario.pages)) {
      if (prefix.endsWith('*') && url.startsWith(prefix.slice(0, -1))) return html;
    }
    return '<main></main>';
  }

  const onUpdated = makeEvent();
  function navigate(tab, url) {
    tab.url = url;
    tab.pendingUrl = url;
    tab.status = 'loading';
    if (scenario.neverLoads.has(url)) return;
    setTimeout(() => {
      if (!tabs.has(tab.id) || tab.pendingUrl !== url) return;
      tab.url = scenario.redirects[url] || url;
      tab.status = 'complete';
      delete tab.pendingUrl;
      for (const l of [...onUpdated.listeners]) {
        l(tab.id, { status: 'complete' }, { ...tab });
      }
    }, 20);
  }

  const runtimeEvents = {
    onInstalled: makeEvent(), onStartup: makeEvent(), onMessage: makeEvent(),
  };
  const onAlarm = makeEvent();

  async function runPageFunction(tab, func, args) {
    const html = pageFor(tab.url);
    installDocument(html);
    globalThis.location = { href: tab.url };
    try {
      return await func(...(args || []));
    } finally {
      uninstallDocument();
      delete globalThis.location;
    }
  }

  const chrome = {
    storage: { local, onChanged, session: undefined },
    tabs: {
      async create(props = {}) {
        calls.tabsCreate.push(clone(props));
        const tab = { id: nextTabId++, url: '', status: 'complete', active: !!props.active };
        tabs.set(tab.id, tab);
        if (props.url) {
          tab.url = props.url;
          if (props.url !== 'about:blank') navigate(tab, props.url);
        }
        return { ...tab };
      },
      async get(id) {
        calls.tabsGet.push(id);
        const tab = tabs.get(id);
        if (!tab) throw new Error(`No tab with id: ${id}.`);
        return { ...tab };
      },
      async update(id, props = {}) {
        calls.tabsUpdate.push({ id, props: clone(props) });
        const tab = tabs.get(id);
        if (!tab) throw new Error(`No tab with id: ${id}.`);
        if (props.url) navigate(tab, props.url);
        return { ...tab };
      },
      async remove(ids) {
        for (const id of Array.isArray(ids) ? ids : [ids]) {
          calls.tabsRemove.push(id);
          if (!tabs.has(id)) throw new Error(`No tab with id: ${id}.`);
          tabs.delete(id);
        }
      },
      async query() { return [...tabs.values()].map((t) => ({ ...t })); },
      onUpdated,
      onRemoved: makeEvent(),
    },
    scripting: {
      async executeScript(details) {
        const { target, func, args, files } = details || {};
        if (!func) throw new Error(`harness supports executeScript({func}) only, got ${JSON.stringify(files)}`);
        const tab = tabs.get(target && target.tabId);
        if (!tab) throw new Error('No tab with id');
        calls.executeScript.push({ tabId: tab.id, url: tab.url, args: clone(args || []) });
        if (scenario.onScript) scenario.onScript(tab);
        if (scenario.stuckScript) await new Promise(() => {}); // a page that never answers
        if (scenario.holdScript && !scenario.holdScript.released) {
          heldScripts += 1;
          try {
            await scenario.holdScript.promise;
          } finally {
            heldScripts -= 1;
            kick();
          }
        }
        const result = await runPageFunction(tab, func, args);
        return [{ frameId: 0, result }];
      },
    },
    permissions: {
      async contains({ origins = [] } = {}) {
        calls.permissionsContains.push(clone(origins));
        if (!scenario.granted) return true;
        return origins.every((o) => scenario.granted.includes(o));
      },
      async request({ origins = [] } = {}) {
        calls.permissionsRequest.push(clone(origins));
        if (scenario.requestGrants && Array.isArray(scenario.granted)) {
          scenario.granted.push(...origins);
        }
        return scenario.requestGrants;
      },
      async remove({ origins = [] } = {}) {
        calls.permissionsRemove.push(clone(origins));
        if (Array.isArray(scenario.granted)) {
          scenario.granted = scenario.granted.filter((o) => !origins.includes(o));
        }
        return true;
      },
      onAdded: makeEvent(),
      onRemoved: makeEvent(),
    },
    alarms: {
      async create(name, info) {
        if (typeof name === 'object') { info = name; name = ''; }
        calls.alarmsCreate.push({ name, info: clone(info) });
        const now = Date.now();
        const scheduledTime = info.when !== undefined
          ? info.when
          : now + (info.delayInMinutes !== undefined ? info.delayInMinutes : info.periodInMinutes) * 60000;
        alarms.set(name, { name, scheduledTime, periodInMinutes: info.periodInMinutes });
      },
      async clear(name = '') {
        calls.alarmsClear.push(name);
        return alarms.delete(name);
      },
      async clearAll() {
        calls.alarmsClear.push('*');
        const had = alarms.size > 0;
        alarms.clear();
        return had;
      },
      async get(name = '') { return alarms.has(name) ? { ...alarms.get(name) } : undefined; },
      async getAll() { return [...alarms.values()].map((a) => ({ ...a })); },
      onAlarm,
    },
    runtime: {
      ...runtimeEvents,
      id: 'harness-extension-id',
      lastError: undefined,
      getURL: (p) => `chrome-extension://harness-extension-id/${p}`,
      getManifest: () => JSON.parse(fs.readFileSync(path.join(EXTENSION_DIR, 'manifest.json'), 'utf8')),
      async sendMessage(msg) {
        calls.messagesSent.push(clone(msg));
        if (scenario.messageHandler) return scenario.messageHandler(clone(msg));
        return { ok: true };
      },
      openOptionsPage() { calls.openOptionsPage += 1; return Promise.resolve(); },
    },
    action: {
      async setBadgeText({ text }) { calls.badgeText.push(text); },
      async setBadgeBackgroundColor({ color }) { calls.badgeColor.push(color); },
      async setTitle() {},
    },
    notifications: {
      async create(idOrOptions, maybeOptions) {
        calls.notifications.push(clone(typeof idOrOptions === 'object' ? idOrOptions : maybeOptions));
        return 'n';
      },
    },
    i18n: {
      getMessage: (key) => key,
      getUILanguage: () => 'en',
    },
  };

  async function fetchStub(url, init = {}) {
    const headers = {};
    const rawHeaders = init.headers || {};
    if (typeof rawHeaders.forEach === 'function' && !Array.isArray(rawHeaders)) {
      rawHeaders.forEach((v, k) => { headers[k.toLowerCase()] = v; });
    } else {
      for (const [k, v] of Array.isArray(rawHeaders) ? rawHeaders : Object.entries(rawHeaders)) {
        headers[k.toLowerCase()] = v;
      }
    }
    const call = {
      url: String(url),
      method: (init.method || 'GET').toUpperCase(),
      headers,
      body: init.body ? JSON.parse(init.body) : null,
    };
    calls.fetches.push(call);
    const respond = (status, body) => ({
      ok: status >= 200 && status < 300,
      status,
      json: async () => clone(body),
      text: async () => JSON.stringify(body),
    });
    const u = new URL(call.url);
    if (u.pathname === '/config') {
      if (scenario.config === null) return respond(404, { error: 'not found' });
      return respond(200, scenario.config);
    }
    if (u.pathname === '/scan-report') {
      if (scenario.reportThrow) throw new TypeError('Failed to fetch');
      return respond(scenario.reportStatus || 200, { recorded: true });
    }
    if (u.pathname === '/searches/suggested') return respond(200, scenario.suggested || { searches: [] });
    if (u.pathname === '/bridge') {
      if (scenario.bridgeThrow) throw new TypeError('Failed to fetch');
      return respond(scenario.bridgeStatus, scenario.bridgeStatus === 200 ? { received: 1 } : { error: 'x' });
    }
    throw new TypeError(`harness: unexpected fetch ${call.url}`);
  }

  return { chrome, store, calls, tabs, alarms, scenario, fetch: fetchStub, onChanged };
}

function readManifest() {
  return JSON.parse(fs.readFileSync(path.join(EXTENSION_DIR, 'manifest.json'), 'utf8'));
}

let importCounter = 0;
function importExtensionFile(rel) {
  importCounter += 1;
  const url = pathToFileURL(path.join(EXTENSION_DIR, rel)).href;
  return import(`${url}?harness=${importCounter}`);
}

function fixture(rel) {
  return fs.readFileSync(path.join(FIXTURES_DIR, rel), 'utf8');
}

function setScenario(name) {
  currentScenario = name;
}

// A scenario that waits on a promise nothing will ever settle leaves Node with
// an empty event loop: it would exit silently with code 0. Fail instead.
let finished = false;
process.exitCode = 3;
process.on('exit', () => {
  if (!finished) {
    realConsoleError(`harness stopped before finishing: scenario "${currentScenario}" waits on a promise that never settles (deadlock)`);
    for (const f of failures) realConsoleError(' - ' + f);
    process.exitCode = 3;
  }
});

function finish(label) {
  finished = true;
  if (failures.length) {
    realConsoleError(`FAIL (${label}): ${failures.length} assertion(s) failed:`);
    for (const f of failures) realConsoleError(' - ' + f);
    process.exitCode = 1;
  } else {
    process.exitCode = 0;
    process.stdout.write(`OK: ${label} characterization assertions passed\n`);
  }
}

function crashed(e) {
  finished = true;
  realConsoleError(`harness crashed in scenario "${currentScenario}":`, (e && e.stack) || e);
  process.exitCode = 1;
}

function startWatchdog(ms = 60000) {
  const w = realSetTimeout(() => {
    realConsoleError(`harness watchdog: scenario "${currentScenario}" did not finish`);
    for (const f of failures) realConsoleError(' - ' + f);
    process.exit(2);
  }, ms);
  w.unref();
}

module.exports = {
  EXTENSION_DIR, check, ok, logs, failures, settle, idle, fire, sendMessage, clone, makeBrowser, makeEvent,
  readManifest, importExtensionFile, fixture, setScenario, finish, startWatchdog, crashed,
  realConsoleError,
};
