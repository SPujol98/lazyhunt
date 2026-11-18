'use strict';

// Characterization harness for the extension's pages: the toolbar popup and
// the options page (odd/tasks/extension-rewrite.md T1, docs/extension-spec.md).
//
//   node extension_characterization_harness_pages.js <area>
//   areas: popup, export, import
//
// The pages are about to be redesigned (S5), so nothing here knows a DOM id,
// class, label or layout. The page scripts named by extension/manifest.json
// (action.default_popup, options_ui.page -> their <script src>) run over a
// "property bag" DOM: every element any selector asks for exists, remembers
// what the script writes into it and records its event listeners. The harness
// then presses each click handler the page registered, ONE PER FRESH PAGE
// LOAD, and records what that press did: runtime messages, storage writes,
// files offered for download (Blob / data: URL) and file pickers opened (fed a
// config file through their change handler). The assertions are about those
// effects only: "some control sends {type:'run-now'}", "the exported file has
// this format", "importing this file stores that".
//
// Limits of the model (extend the harness if the redesign needs it, never
// weaken an assertion): handlers must be attached to elements (addEventListener
// or on<event>), not delegated from document; values the script reads back must
// be ones it (or the harness) wrote through .value/.checked.

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const {
  check, ok, logs, idle, clone, makeBrowser, readManifest, importExtensionFile,
  setScenario, finish, startWatchdog, crashed, EXTENSION_DIR,
} = require('./extension_characterization_harness_lib.js');

startWatchdog(120000);
const MANIFEST = readManifest();

// --- property-bag DOM --------------------------------------------------------------------
function makeDom() {
  const reg = {
    all: [], // every element, in creation order
    listeners: [], // {el, type, fn}
    downloads: [], // {text, filename}
    blobs: [], // text of every Blob created
  };

  const EVENT_PROPS = /^on[a-z]+$/;

  function makeEl(tag = 'div') {
    const props = {
      tagName: String(tag).toUpperCase(),
      nodeName: String(tag).toUpperCase(),
      nodeType: 1,
      value: '',
      textContent: '',
      innerHTML: '',
      innerText: '',
      className: '',
      id: '',
      title: '',
      checked: false,
      disabled: false,
      hidden: false,
      type: String(tag).toLowerCase() === 'input' ? 'text' : '',
      href: '',
      download: '',
      name: '',
      files: undefined,
      selectedIndex: 0,
      isConnected: true,
    };
    const attrs = {};
    const style = {};
    const dataset = {};
    const children = [];
    const childQueries = new Map();
    const classes = new Set();
    let self = null;

    const classList = {
      add: (...c) => c.forEach((x) => classes.add(x)),
      remove: (...c) => c.forEach((x) => classes.delete(x)),
      toggle: (c, force) => {
        const on = force === undefined ? !classes.has(c) : !!force;
        if (on) classes.add(c); else classes.delete(c);
        return on;
      },
      contains: (c) => classes.has(c),
      replace: (a, c) => { classes.delete(a); classes.add(c); },
    };

    const query = (selector) => {
      if (!childQueries.has(selector)) childQueries.set(selector, makeEl(guessTag(selector)));
      return childQueries.get(selector);
    };

    const dispatch = (type, extra = {}) => {
      const event = makeEvent(type, self, extra);
      for (const l of reg.listeners.filter((x) => x.el === self && x.type === type)) l.fn.call(self, event);
      const prop = props[`on${type}`];
      if (typeof prop === 'function') prop.call(self, event);
      return !event.defaultPrevented;
    };

    const methods = {
      addEventListener: (type, fn) => { if (typeof fn === 'function') reg.listeners.push({ el: self, type, fn }); },
      removeEventListener: (type, fn) => {
        const i = reg.listeners.findIndex((x) => x.el === self && x.type === type && x.fn === fn);
        if (i >= 0) reg.listeners.splice(i, 1);
      },
      dispatchEvent: (ev) => dispatch(ev && ev.type),
      querySelector: query,
      getElementById: (id) => query(`#${id}`),
      closest: (selector) => query(`closest:${selector}`),
      querySelectorAll: () => [],
      getElementsByClassName: () => [],
      getElementsByTagName: () => [],
      appendChild: (c) => { children.push(c); return c; },
      append: (...c) => { children.push(...c); },
      prepend: (...c) => { children.unshift(...c); },
      insertBefore: (c) => { children.push(c); return c; },
      insertAdjacentElement: (_p, c) => c,
      insertAdjacentHTML: () => {},
      replaceChildren: (...c) => { children.length = 0; children.push(...c); },
      removeChild: (c) => c,
      remove: () => {},
      before: () => {}, after: () => {}, replaceWith: () => {},
      cloneNode: () => makeEl(tag),
      setAttribute: (k, v) => { attrs[k] = String(v); if (k in props) props[k] = v; },
      getAttribute: (k) => (k in attrs ? attrs[k] : null),
      hasAttribute: (k) => k in attrs,
      removeAttribute: (k) => { delete attrs[k]; },
      toggleAttribute: (k, force) => {
        const on = force === undefined ? !(k in attrs) : !!force;
        if (on) attrs[k] = ''; else delete attrs[k];
        return on;
      },
      matches: () => false,
      contains: () => false,
      focus: () => {}, blur: () => {}, select: () => {}, scrollIntoView: () => {},
      showModal: () => {}, show: () => {}, close: () => {}, reset: () => {},
      requestSubmit: () => dispatch('submit'),
      submit: () => dispatch('submit'),
      showPicker: () => dispatch('click'),
      getBoundingClientRect: () => ({ top: 0, left: 0, width: 0, height: 0 }),
      click: () => {
        dispatch('click');
        const href = String(props.href || '');
        if (props.download || /^data:/.test(href) || /^blob:/.test(href)) {
          let text = null;
          if (/^data:/.test(href)) text = decodeURIComponent(href.replace(/^data:[^,]*,/, ''));
          else if (/^blob:/.test(href)) text = reg.blobs[reg.blobs.length - 1] || null;
          if (text !== null) reg.downloads.push({ text, filename: props.download });
        }
      },
      toString: () => '[object HTMLElement]',
    };

    const target = function element() {};
    self = new Proxy(target, {
      get(_t, p) {
        if (p === 'then') return undefined;
        if (p === Symbol.toPrimitive) return () => String(props.value || props.textContent || '');
        if (typeof p === 'symbol') return undefined;
        if (p === 'classList') return classList;
        if (p === 'style') return style;
        if (p === 'dataset') return dataset;
        if (p === 'children' || p === 'childNodes') return children;
        if (p === 'firstElementChild' || p === 'firstChild') return children[0] || query(':first');
        if (p === 'lastElementChild' || p === 'lastChild') return children[children.length - 1] || null;
        if (p === 'content') return self; // <template>
        if (p === 'parentElement' || p === 'parentNode' || p === 'form') return query(':parent');
        if (p === 'elements') return { namedItem: (n) => query(`[name="${n}"]`) };
        if (p === 'options') return [];
        if (p === 'ownerDocument') return globalThis.document;
        if (p === 'valueAsNumber') return Number(props.value);
        if (p in methods) return methods[p];
        if (p in props) return props[p];
        if (EVENT_PROPS.test(p)) return null;
        return undefined;
      },
      set(_t, p, v) {
        if (p === 'className') { classes.clear(); String(v).split(/\s+/).filter(Boolean).forEach((c) => classes.add(c)); }
        props[p] = v;
        return true;
      },
    });
    reg.all.push({ el: self, props });
    return self;
  }

  function guessTag(selector) {
    const m = String(selector).match(/^([a-z]+)/i);
    return m ? m[1] : 'div';
  }

  function makeEvent(type, target, extra) {
    const ev = {
      type, target, currentTarget: target, key: '', button: 0, detail: 1,
      defaultPrevented: false,
      preventDefault() { ev.defaultPrevented = true; },
      stopPropagation() {}, stopImmediatePropagation() {},
      ...extra,
    };
    return ev;
  }

  const document = makeEl('#document');
  const docListeners = [];
  const docExtras = {
    createElement: (tag) => makeEl(tag),
    createElementNS: (_ns, tag) => makeEl(tag),
    createTextNode: (text) => ({ nodeType: 3, textContent: String(text) }),
    createDocumentFragment: () => makeEl('#fragment'),
    body: makeEl('body'),
    head: makeEl('head'),
    documentElement: makeEl('html'),
    readyState: 'complete',
    visibilityState: 'visible',
    hidden: false,
    title: '',
    activeElement: null,
    addEventListener: (type, fn) => docListeners.push({ type, fn }),
    removeEventListener: () => {},
  };
  const doc = new Proxy({}, {
    get(_t, p) {
      if (p in docExtras) return docExtras[p];
      return document[p];
    },
    set(_t, p, v) { docExtras[p] = v; return true; },
  });

  return { reg, doc, docListeners, makeEl };
}

// --- page loading ---------------------------------------------------------------------------
function pageScripts(htmlRel) {
  const html = fs.readFileSync(path.join(EXTENSION_DIR, htmlRel), 'utf8');
  const dir = path.dirname(htmlRel);
  return [...html.matchAll(/<script\b([^>]*)>/g)]
    .map((m) => ({ attrs: m[1], src: (m[1].match(/\bsrc="([^"]+)"/) || [])[1] }))
    .filter((t) => t.src)
    .map((t) => ({ file: path.join(dir, t.src), module: /\btype="module"/.test(t.attrs) }));
}

// A classic <script> runs as a script (wrapped so a reload can redeclare its
// top-level bindings); a type="module" one is imported as an ES module.
async function runScript({ file, module }) {
  if (module) {
    await importExtensionFile(file);
    return;
  }
  const source = fs.readFileSync(path.join(EXTENSION_DIR, file), 'utf8');
  vm.runInThisContext(`(function () {\n${source}\n})();`, { filename: file });
}

let b = null;
let dom = null;

async function loadPage(htmlRel, store, scenario = {}) {
  logs.length = 0;
  b = makeBrowser();
  Object.assign(b.store, clone(store));
  Object.assign(b.scenario, scenario);
  dom = makeDom();
  globalThis.chrome = b.chrome;
  globalThis.fetch = b.fetch;
  globalThis.document = dom.doc;
  globalThis.window = globalThis;
  globalThis.confirm = () => scenario.confirmAnswer !== false;
  globalThis.alert = () => {};
  globalThis.prompt = () => null;
  globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);
  globalThis.location = { href: `chrome-extension://harness-extension-id/${htmlRel}`, reload() {} };
  globalThis.addEventListener = (type, fn) => dom.docListeners.push({ type, fn });
  const NodeBlob = require('buffer').Blob;
  globalThis.Blob = class extends NodeBlob {
    constructor(parts = [], opts) {
      super(parts, opts);
      dom.reg.blobs.push(parts.map((p) => (typeof p === 'string' ? p : '')).join(''));
    }
  };
  URL.createObjectURL = () => 'blob:chrome-extension://harness/1';
  URL.revokeObjectURL = () => {};
  for (const script of pageScripts(htmlRel)) await runScript(script);
  for (const l of dom.docListeners.filter((x) => x.type === 'DOMContentLoaded' || x.type === 'load')) {
    await l.fn({ type: l.type });
  }
  await idle();
}

// Handlers a user can trigger by clicking or submitting, in registration order.
function pressables() {
  const out = [];
  for (const l of dom.reg.listeners) if (l.type === 'click' || l.type === 'submit') out.push({ el: l.el, type: l.type, fn: l.fn });
  for (const { el, props } of dom.reg.all) {
    for (const type of ['click', 'submit']) {
      if (typeof props[`on${type}`] === 'function') out.push({ el, type, fn: props[`on${type}`] });
    }
  }
  return out;
}

async function press(handler) {
  const ev = {
    type: handler.type, target: handler.el, currentTarget: handler.el, button: 0, detail: 1,
    preventDefault() {}, stopPropagation() {},
  };
  await handler.fn.call(handler.el, ev);
  await idle();
}

// File pickers the page has opened or holds: inputs of type file with a change handler.
function filePickers() {
  return dom.reg.all.filter(({ el, props }) => String(props.type).toLowerCase() === 'file'
    && (typeof props.onchange === 'function' || typeof props.oninput === 'function'
      || dom.reg.listeners.some((l) => l.el === el && (l.type === 'change' || l.type === 'input'))));
}

async function feedFile(picker, text, name = 'config.json') {
  const file = {
    name, type: 'application/json', size: text.length,
    text: async () => text,
    arrayBuffer: async () => Buffer.from(text),
  };
  picker.props.files = [file];
  picker.props.value = `C:\\fakepath\\${name}`;
  const ev = { type: 'change', target: picker.el, currentTarget: picker.el, preventDefault() {}, stopPropagation() {} };
  for (const l of dom.reg.listeners.filter((x) => x.el === picker.el && (x.type === 'change' || x.type === 'input'))) await l.fn.call(picker.el, ev);
  if (typeof picker.props.onchange === 'function') await picker.props.onchange.call(picker.el, ev);
  await idle();
}

// Loads the page once per handler and presses only that one; returns per-press effects.
async function exploreHandlers(htmlRel, store, scenario, afterPress) {
  await loadPage(htmlRel, store, scenario);
  const count = pressables().length;
  const effects = [];
  for (let i = 0; i < count; i += 1) {
    await loadPage(htmlRel, store, scenario);
    const before = clone(b.store);
    const handler = pressables()[i];
    if (!handler) continue;
    b.calls.messagesSent.length = 0;
    await press(handler);
    const effect = {
      index: i,
      messages: clone(b.calls.messagesSent),
      openOptions: b.calls.openOptionsPage,
      tabsCreated: clone(b.calls.tabsCreate),
      downloads: clone(dom.reg.downloads),
      before,
    };
    if (afterPress) await afterPress(effect);
    effect.after = clone(b.store);
    effects.push(effect);
  }
  return effects;
}

const sameJson = (a, c) => JSON.stringify(a) === JSON.stringify(c);

// --- popup -----------------------------------------------------------------------------------
const SUMMARY = { skipped: false, beaconsScanned: 1, totalJobsFound: 3, totalNew: 1, notified: 0, firstScans: 0, errors: 0, filteredOut: 0 };
function popupBackground(msg) {
  if (msg.type === 'run-now') return { ok: true, summary: SUMMARY };
  if (msg.type === 'set-paused') return { ok: true, paused: !!msg.paused };
  return { ok: true };
}

async function areaPopup() {
  const popup = MANIFEST.action.default_popup;
  const base = {
    settings: { intervalMinutes: 30, paused: false, bridgeUrl: 'http://127.0.0.1:8765/bridge', bridgeSecret: 's' },
    beacons: [{ id: 'b1', name: 'AI BCN', url: 'https://www.linkedin.com/jobs/search/?keywords=ai', enabled: true }],
    lastCheck: { b1: { at: Date.now() - 60000, status: 'ok', jobsFound: 3, newJobs: 1, filteredOut: 0 } },
    stats: { totalNotified: 0, lastRoundAt: Date.now() - 60000 },
    enabledPortals: ['linkedin'],
  };

  setScenario('popup: loads without touching storage');
  await loadPage(popup, base, { messageHandler: popupBackground });
  check('opening the popup writes nothing', b.store, base);
  check('opening the popup sends no message', b.calls.messagesSent, []);
  ok('the popup registers at least two controls', pressables().length >= 2, pressables().length);

  setScenario('popup: run now');
  let effects = await exploreHandlers(popup, base, { messageHandler: popupBackground });
  const runNow = effects.filter((e) => e.messages.some((m) => m.type === 'run-now'));
  check('exactly one control asks for a round', runNow.length, 1);
  check('and it sends exactly {type:"run-now"}', runNow[0] && runNow[0].messages, [{ type: 'run-now' }]);
  check('pressing it writes nothing itself', runNow[0] && sameJson(runNow[0].before, runNow[0].after), true);

  setScenario('popup: pause while running');
  const pauses = effects.filter((e) => e.messages.some((m) => m.type === 'set-paused'));
  check('exactly one control toggles the pause', pauses.length, 1);
  check('running -> it asks to pause', pauses[0] && pauses[0].messages, [{ type: 'set-paused', paused: true }]);

  setScenario('popup: options shortcut');
  ok('one control opens the options page', effects.some((e) => e.openOptions > 0
    || e.tabsCreated.some((t) => String(t.url || '').includes(MANIFEST.options_ui.page))));

  setScenario('popup: resume while paused');
  const paused = { ...base, settings: { ...base.settings, paused: true } };
  effects = await exploreHandlers(popup, paused, { messageHandler: popupBackground });
  const resumes = effects.filter((e) => e.messages.some((m) => m.type === 'set-paused'));
  check('paused -> it asks to resume', resumes.length === 1 && resumes[0].messages, [{ type: 'set-paused', paused: false }]);

  setScenario('popup: the local pause decides the toggle, not the bot pause');
  const botPaused = { ...base, botConfig: { paused: true, enabledPortals: null, at: Date.now() } };
  effects = await exploreHandlers(popup, botPaused, { messageHandler: popupBackground });
  const toggles = effects.filter((e) => e.messages.some((m) => m.type === 'set-paused'));
  check('bot paused, local running -> it asks to pause locally', toggles.length === 1 && toggles[0].messages, [{ type: 'set-paused', paused: true }]);
}

// --- options: export / import ----------------------------------------------------------------
const FORMAT = 'linkedin-beacons-config';
const TF_AI = { enabled: true, includeGroups: [['AI', 'LLM'], ['Engineer', 'Developer']], excludeTerms: ['Senior', 'Lead'] };
const TF_BACKEND = { enabled: false, includeGroups: [['Python', 'Backend']], excludeTerms: ['PHP'] };
const SEARCH = { id: 's_one', name: 'AI BCN', keywords: 'ai engineer', location: 'Barcelona', filterProfile: 'ai', portals: ['linkedin'], titleFilter: 'off' };
const STORE_A = {
  settings: { intervalMinutes: 45, paused: false, bridgeUrl: 'http://127.0.0.1:8765/bridge', bridgeSecret: 'TOP-SECRET-A' },
  beacons: [
    { id: 'bcn_1', name: 'Manual LI', url: 'https://www.linkedin.com/jobs/search/?keywords=ml', enabled: true, filterProfile: 'ai' },
    { id: 'bcn_2', name: 'Off one', url: 'https://www.linkedin.com/jobs/search/?keywords=x', enabled: false, filterProfile: 'backend' },
  ],
  searches: [SEARCH],
  titleFilter: TF_AI,
  titleFilterBackend: TF_BACKEND,
  enabledPortals: ['linkedin'],
  seenJobs: { bcn_1: ['1'] },
};

function exportedFiles(effects) {
  const files = [];
  for (const e of effects) {
    for (const d of e.downloads) {
      try {
        const parsed = JSON.parse(d.text);
        if (parsed && parsed.format === FORMAT) files.push({ effect: e, parsed, text: d.text, filename: d.filename });
      } catch (_) { /* not JSON */ }
    }
  }
  return files;
}

async function exportFrom(store) {
  const effects = await exploreHandlers(MANIFEST.options_ui.page, store, {});
  return exportedFiles(effects);
}

async function areaExport() {
  setScenario('export: the config file format');
  const files = await exportFrom(STORE_A);
  check('exactly one control exports a config file', files.length, 1);
  const file = files[0] || { parsed: {}, text: '' };
  const p = file.parsed;
  check('format and version', [p.format, p.version], [FORMAT, 1]);
  ok('exportedAt is an ISO timestamp', typeof p.exportedAt === 'string' && !Number.isNaN(Date.parse(p.exportedAt)), p.exportedAt);
  check('settings carry the interval and the bridge URL', [p.settings && p.settings.intervalMinutes, p.settings && p.settings.bridgeUrl], [45, 'http://127.0.0.1:8765/bridge']);
  check('the bridge secret is never exported', [file.text.includes('TOP-SECRET-A'), p.settings && 'bridgeSecret' in p.settings], [false, false]);
  check('the paused flag is not exported', p.settings && 'paused' in p.settings, false);
  check('beacons as {id,name,url,enabled,filterProfile}', p.beacons, STORE_A.beacons);
  check('searches', p.searches, [SEARCH]);
  check('both title filter profiles', [p.titleFilter, p.titleFilterBackend], [TF_AI, TF_BACKEND]);
  check('seen offers and runtime state are not exported', ['seenJobs', 'lastCheck', 'enabledPortals', 'stats', 'botConfig'].filter((k) => k in p), []);
  ok('offered as a .json file', !file.filename || /\.json$/.test(file.filename), file.filename);
  check('exporting writes nothing to storage', file.effect && sameJson(file.effect.before, file.effect.after), true);

  setScenario('export: defaults for a store with missing pieces');
  const sparse = await exportFrom({ settings: { intervalMinutes: 60, paused: false, bridgeUrl: '', bridgeSecret: '' } });
  const sp = sparse[0] ? sparse[0].parsed : {};
  check('empty lists', [sp.beacons, sp.searches], [[], []]);
  ok('title filters are still exported (built-in defaults)', sp.titleFilter && Array.isArray(sp.titleFilter.includeGroups) && sp.titleFilter.includeGroups.length > 0, sp.titleFilter);
}

// Presses each control once per fresh load and feeds `text` to any file picker
// it opened; returns the effects of the presses that consumed the file.
async function importInto(store, text, scenario = {}) {
  const fed = [];
  const effects = await exploreHandlers(MANIFEST.options_ui.page, store, scenario, async (effect) => {
    const pickers = filePickers();
    if (!pickers.length) return;
    b.calls.messagesSent.length = 0;
    await feedFile(pickers[pickers.length - 1], text);
    effect.importMessages = clone(b.calls.messagesSent);
    fed.push(effect);
  });
  return { fed, effects };
}

const STORE_B = {
  settings: { intervalMinutes: 90, paused: true, bridgeUrl: 'http://127.0.0.1:9999/bridge', bridgeSecret: 'SECRET-B' },
  beacons: [{ id: 'old', name: 'Old', url: 'https://www.linkedin.com/jobs/search/?keywords=old', enabled: true, filterProfile: 'ai' }],
  searches: [{ id: 's_old', name: 'Old search', keywords: 'old', location: '', filterProfile: 'ai', portals: ['linkedin'], titleFilter: 'on' }],
  titleFilter: { enabled: true, includeGroups: [['Old']], excludeTerms: [] },
  titleFilterBackend: { enabled: true, includeGroups: [['OldBackend']], excludeTerms: [] },
  enabledPortals: ['linkedin'],
  seenJobs: { old: ['9'] },
};

async function areaImport() {
  setScenario('import: round trip from an export');
  const exported = (await exportFrom(STORE_A))[0];
  ok('an export to import exists', Boolean(exported));
  const { fed } = await importInto(STORE_B, exported ? exported.text : '{}');
  check('exactly one control imports a config file', fed.length, 1);
  const after = fed[0] ? fed[0].after : {};
  check('settings from the file; secret and pause kept from this install', after.settings && {
    intervalMinutes: after.settings.intervalMinutes, bridgeUrl: after.settings.bridgeUrl,
    bridgeSecret: after.settings.bridgeSecret, paused: after.settings.paused,
  }, { intervalMinutes: 45, bridgeUrl: 'http://127.0.0.1:8765/bridge', bridgeSecret: 'SECRET-B', paused: true });
  check('beacons replaced', after.beacons, STORE_A.beacons);
  check('searches replaced', after.searches, [SEARCH]);
  check('title filters replaced', [after.titleFilter, after.titleFilterBackend], [TF_AI, TF_BACKEND]);
  check('seen offers and enabled portals untouched', [after.seenJobs, after.enabledPortals], [STORE_B.seenJobs, STORE_B.enabledPortals]);
  ok('the service worker is told to reschedule', fed[0] && fed[0].importMessages.some((m) => m.type === 'settings-updated'), fed[0] && fed[0].importMessages);

  setScenario('import: cleaning and clamping');
  const messy = {
    format: FORMAT, version: 1,
    settings: { intervalMinutes: 5000, bridgeUrl: 'http://127.0.0.1:8765/bridge', bridgeSecret: 'FROM-FILE' },
    beacons: [
      { name: 'No id', url: 'https://www.linkedin.com/jobs/search/?keywords=a' },
      { id: 'x2', name: 7, url: 'https://www.linkedin.com/jobs/search/?keywords=b', enabled: false, filterProfile: 'weird' },
    ],
    searches: [
      { id: 'dup', name: ' One ', keywords: ' k1 ', portals: ['linkedin', 'linkedin', ''], filterProfile: 'backend' },
      { id: 'dup', name: 'Two', keywords: 'k2', titleFilter: 'maybe' },
    ],
    titleFilter: { enabled: true, includeGroups: [['A']] },
  };
  let res = await importInto(STORE_B, JSON.stringify(messy));
  let a = res.fed[0] ? res.fed[0].after : {};
  check('interval clamped to 1440', a.settings && a.settings.intervalMinutes, 1440);
  check('a secret inside the file is ignored', a.settings && a.settings.bridgeSecret, 'SECRET-B');
  const bs = a.beacons || [];
  ok('a beacon without id gets one', bs[0] && typeof bs[0].id === 'string' && bs[0].id.length > 0, bs[0]);
  check('beacon defaults', bs.map((x) => [x.name, x.enabled, x.filterProfile]), [['No id', true, 'ai'], ['7', false, 'ai']]);
  const ss = a.searches || [];
  check('searches normalized', ss.map((s) => [s.name, s.keywords, s.location, s.filterProfile, s.portals, s.titleFilter]),
    [['One', 'k1', '', 'backend', ['linkedin'], 'on'], ['Two', 'k2', '', 'ai', [], 'on']]);
  ok('duplicate search ids are made unique', ss.length === 2 && ss[0].id && ss[1].id && ss[0].id !== ss[1].id, ss.map((s) => s.id));
  check('a partial title filter is repaired field by field', a.titleFilter && [a.titleFilter.enabled, a.titleFilter.includeGroups, Array.isArray(a.titleFilter.excludeTerms)], [true, [['A']], true]);

  for (const [value, expected] of [[0, 60], ['abc', 60], [-4, 1], ['20', 20]]) {
    setScenario(`import: intervalMinutes ${JSON.stringify(value)}`);
    res = await importInto(STORE_B, JSON.stringify({ format: FORMAT, version: 1, settings: { intervalMinutes: value }, beacons: [] }));
    a = res.fed[0] ? res.fed[0].after : {};
    check(`interval ${JSON.stringify(value)} -> ${expected}`, a.settings && a.settings.intervalMinutes, expected);
  }

  setScenario('import: an old export keeps the stored searches and filters');
  res = await importInto(STORE_B, JSON.stringify({ format: FORMAT, version: 1, settings: { intervalMinutes: 30 }, beacons: [] }));
  a = res.fed[0] ? res.fed[0].after : {};
  check('beacons replaced (empty)', a.beacons, []);
  check('searches kept', a.searches, STORE_B.searches);
  check('title filters kept', [a.titleFilter, a.titleFilterBackend], [STORE_B.titleFilter, STORE_B.titleFilterBackend]);
  check('bridge URL cleared when the file has none', a.settings && a.settings.bridgeUrl, '');

  setScenario('import: a file of another format is rejected');
  res = await importInto(STORE_B, JSON.stringify({ format: 'something-else', beacons: [] }));
  check('storage unchanged', res.fed.length === 1 && sameJson(res.fed[0].after, STORE_B), true);
  check('no reschedule message', res.fed[0] && res.fed[0].importMessages.filter((m) => m.type === 'settings-updated'), []);

  setScenario('import: invalid JSON is rejected');
  res = await importInto(STORE_B, '{not json');
  check('storage unchanged', res.fed.length === 1 && sameJson(res.fed[0].after, STORE_B), true);

  setScenario('import: declining the confirmation changes nothing');
  res = await importInto(STORE_B, exported ? exported.text : '{}', { confirmAnswer: false });
  check('storage unchanged', res.fed.length === 1 && sameJson(res.fed[0].after, STORE_B), true);
}


// --- options: role families in export / import (role-families T6, S3/S5) --------------------
const FAMILIES3 = [
  { id: 'ai', label: 'AI', default: true },
  { id: 'backend', label: 'Backend', default: false },
  { id: 'frontend', label: 'Frontend', default: false },
];
const TF_FE = { enabled: true, includeGroups: [['React']], excludeTerms: ['Senior'] };
const FE_SEARCH = { id: 's_fe', name: 'FE', keywords: 'react', location: 'Barcelona', filterProfile: 'frontend', portals: ['linkedin'], titleFilter: 'on' };
const STORE_FE = {
  ...STORE_A,
  profileFamilies: FAMILIES3,
  familyTitleFilters: { frontend: TF_FE },
  searches: [SEARCH, FE_SEARCH],
  beacons: [...STORE_A.beacons, { id: 'bcn_3', name: 'FE manual', url: 'https://www.linkedin.com/jobs/search/?keywords=fe', enabled: true, filterProfile: 'frontend' }],
};

async function areaFamilies() {
  setScenario('families: export keeps the frontend family and its filter');
  const file = (await exportFrom(STORE_FE))[0];
  ok('an export exists', Boolean(file));
  const p = file ? file.parsed : {};
  check('format and version unchanged', [p.format, p.version], [FORMAT, 1]);
  check('searches keep frontend', (p.searches || []).map((x) => x.filterProfile), ['ai', 'frontend']);
  check('beacons keep frontend', (p.beacons || []).map((x) => x.filterProfile), ['ai', 'backend', 'frontend']);
  check('the frontend filter is exported', p.familyTitleFilters, { frontend: TF_FE });
  check('built-in filters keep their keys', [p.titleFilter, p.titleFilterBackend], [TF_AI, TF_BACKEND]);

  setScenario('families: a built-in-only export has no family map');
  const plain = (await exportFrom(STORE_A))[0];
  check('no familyTitleFilters key', plain && 'familyTitleFilters' in plain.parsed, false);

  setScenario('families: importing into a profile with that family keeps it');
  const target = { ...STORE_B, profileFamilies: FAMILIES3 };
  let res = await importInto(target, file ? file.text : '{}');
  let a = res.fed[0] ? res.fed[0].after : {};
  check('searches keep frontend', (a.searches || []).map((x) => x.filterProfile), ['ai', 'frontend']);
  check('beacons keep frontend', (a.beacons || []).map((x) => x.filterProfile), ['ai', 'backend', 'frontend']);
  check('the frontend filter is stored', a.familyTitleFilters, { frontend: TF_FE });

  setScenario('families: importing into a profile without that family degrades to the default');
  res = await importInto(STORE_B, file ? file.text : '{}');
  check('the import ran (no crash)', res.fed.length, 1);
  a = res.fed[0] ? res.fed[0].after : {};
  check('searches degrade to the default family', (a.searches || []).map((x) => x.filterProfile), ['ai', 'ai']);
  check('beacons degrade to the default family', (a.beacons || []).map((x) => x.filterProfile), ['ai', 'backend', 'ai']);
  check('built-in filters imported', [a.titleFilter, a.titleFilterBackend], [TF_AI, TF_BACKEND]);

  setScenario('families: a v1 export of the previous version imports unchanged');
  const v1 = {
    version: 1, format: FORMAT, exportedAt: '2026-01-01T00:00:00.000Z',
    settings: { intervalMinutes: 45, bridgeUrl: 'http://127.0.0.1:8765/bridge' },
    beacons: STORE_A.beacons, searches: [SEARCH], titleFilter: TF_AI, titleFilterBackend: TF_BACKEND,
  };
  res = await importInto(STORE_B, JSON.stringify(v1));
  a = res.fed[0] ? res.fed[0].after : {};
  check('beacons', a.beacons, STORE_A.beacons);
  check('searches', a.searches, [SEARCH]);
  check('filters', [a.titleFilter, a.titleFilterBackend], [TF_AI, TF_BACKEND]);
  check('no family map written', 'familyTitleFilters' in a, false);
}

const AREAS = { popup: areaPopup, export: areaExport, import: areaImport, families: areaFamilies };

async function main() {
  const wanted = process.argv.slice(2);
  const names = wanted.length ? wanted : Object.keys(AREAS);
  for (const name of names) {
    if (!AREAS[name]) throw new Error(`unknown area ${name}; known: ${Object.keys(AREAS).join(', ')}`);
    await AREAS[name]();
  }
  finish(`pages [${names.join(', ')}]`);
}

main().catch(crashed).finally(() => process.exit());
