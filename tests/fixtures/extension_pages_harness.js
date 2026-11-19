'use strict';

// Behaviour harness for the redesigned popup and options pages
// (odd/tasks/extension-rewrite.md T4, docs/extension-spec.md 6.1-6.3, D2, D4).
//
//   node extension_pages_harness.js <area>
//   areas: i18n, portals, save, interval, suggest, maintenance, popup, nobridge, families
//
// Unlike the id-free characterization harness, this one builds the DOM from
// the page's own HTML (minidom.parseHtml), so getElementById returns null for
// an id the HTML does not have and the page script fails loudly. Controls are
// driven like a user would: click(), typing (value + an input event), change
// events on checkboxes, Ctrl+S on the document. The fake browser is the shared
// one in extension_characterization_harness_lib.js.

const fs = require('fs');
const path = require('path');
const { parseHtml } = require('./minidom.js');
const {
  check, ok, idle, clone, makeBrowser, readManifest, importExtensionFile,
  setScenario, finish, startWatchdog, crashed, EXTENSION_DIR,
} = require('./extension_characterization_harness_lib.js');

startWatchdog(120000);
const MANIFEST = readManifest();

// --- HTML-backed fake DOM ----------------------------------------------------------

function matchesSimple(node, selector) {
  const m = selector.trim().match(/^([a-zA-Z][\w-]*)?((?:#[\w-]+|\.[\w-]+|\[[^\]]+\])*)$/);
  if (!m) throw new Error(`harness DOM: unsupported selector ${selector}`);
  if (m[1] && node.localName !== m[1].toLowerCase()) return false;
  for (const part of m[2].match(/#[\w-]+|\.[\w-]+|\[[^\]]+\]/g) || []) {
    if (part[0] === '#' && node.getAttribute('id') !== part.slice(1)) return false;
    if (part[0] === '.' && !node.classList.contains(part.slice(1))) return false;
    if (part[0] === '[') {
      const a = part.slice(1, -1).match(/^([\w-]+)(?:="([^"]*)")?$/);
      if (!node.hasAttribute(a[1])) return false;
      if (a[2] !== undefined && node.getAttribute(a[1]) !== a[2]) return false;
    }
  }
  return true;
}

class TextNode {
  constructor(text) { this.nodeType = 3; this.data = String(text); this.parentNode = null; }
  get textContent() { return this.data; }
}

class Element {
  constructor(doc, tag, attrs = {}) {
    this.ownerDocument = doc;
    this.localName = String(tag).toLowerCase();
    this.tagName = this.localName.toUpperCase();
    this.nodeType = 1;
    this.attrs = { ...attrs };
    this.childNodes = [];
    this.parentNode = null;
    this.listeners = [];
    this.style = {};
    this.files = undefined;
    this._value = undefined;
    this._checked = 'checked' in attrs;
    this._disabled = 'disabled' in attrs;
    const self = this;
    this.classList = {
      contains: (c) => (self.attrs.class || '').split(/\s+/).includes(c),
      add: (...cs) => { const s = new Set((self.attrs.class || '').split(/\s+/).filter(Boolean)); cs.forEach((c) => s.add(c)); self.attrs.class = [...s].join(' '); },
      remove: (...cs) => { self.attrs.class = (self.attrs.class || '').split(/\s+/).filter((c) => c && !cs.includes(c)).join(' '); },
      toggle: (c, force) => { const on = force === undefined ? !self.classList.contains(c) : !!force; if (on) self.classList.add(c); else self.classList.remove(c); return on; },
    };
  }

  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  removeAttribute(k) { delete this.attrs[k]; }
  hasAttribute(k) { return k in this.attrs; }

  get id() { return this.attrs.id || ''; }
  set id(v) { this.attrs.id = String(v); }
  get className() { return this.attrs.class || ''; }
  set className(v) { this.attrs.class = String(v); }
  get hidden() { return 'hidden' in this.attrs; }
  set hidden(v) { if (v) this.attrs.hidden = ''; else delete this.attrs.hidden; }
  get title() { return this.attrs.title || ''; }
  set title(v) { this.attrs.title = String(v); }
  get placeholder() { return this.attrs.placeholder || ''; }
  set placeholder(v) { this.attrs.placeholder = String(v); }
  get href() { return this.attrs.href || ''; }
  set href(v) { this.attrs.href = String(v); }
  get download() { return this.attrs.download || ''; }
  set download(v) { this.attrs.download = String(v); }
  get accept() { return this.attrs.accept || ''; }
  set accept(v) { this.attrs.accept = String(v); }
  get type() { return this.attrs.type || (this.localName === 'input' ? 'text' : ''); }
  set type(v) { this.attrs.type = String(v); }
  get disabled() { return this._disabled; }
  set disabled(v) { this._disabled = !!v; }
  get checked() { return this._checked; }
  set checked(v) { this._checked = !!v; }
  get value() {
    if (this._value !== undefined) return this._value;
    if (this.localName === 'textarea') return this.textContent;
    return this.attrs.value || '';
  }
  set value(v) { this._value = String(v); }

  get children() { return this.childNodes.filter((n) => n.nodeType === 1); }
  get parentElement() { return this.parentNode && this.parentNode.nodeType === 1 ? this.parentNode : null; }
  get textContent() { return this.childNodes.map((n) => n.textContent).join(''); }
  set textContent(v) { this.replaceChildren(String(v)); }

  _adopt(n) {
    const node = typeof n === 'string' || typeof n === 'number' ? new TextNode(n) : n;
    if (node.parentNode && node.parentNode.childNodes) node.parentNode.childNodes = node.parentNode.childNodes.filter((c) => c !== node);
    node.parentNode = this;
    return node;
  }
  append(...nodes) { for (const n of nodes) this.childNodes.push(this._adopt(n)); }
  appendChild(n) { this.append(n); return n; }
  replaceChildren(...nodes) { for (const c of this.childNodes) c.parentNode = null; this.childNodes = []; this.append(...nodes); }
  remove() { if (this.parentNode) this.parentNode.childNodes = this.parentNode.childNodes.filter((c) => c !== this); this.parentNode = null; }

  addEventListener(type, fn) { this.listeners.push({ type, fn }); }
  removeEventListener(type, fn) { this.listeners = this.listeners.filter((l) => l.type !== type || l.fn !== fn); }
  dispatch(type, extra = {}) {
    const ev = {
      type, target: this, currentTarget: this, defaultPrevented: false,
      preventDefault() { ev.defaultPrevented = true; }, stopPropagation() {}, ...extra,
    };
    const results = [];
    for (const l of this.listeners.filter((x) => x.type === type)) results.push(l.fn.call(this, ev));
    return { ev, results };
  }
  click() {
    if (this.disabled) return;
    if (this.localName === 'input' && (this.type === 'checkbox' || this.type === 'radio')) {
      this.checked = !this.checked;
      this.dispatch('click');
      this.dispatch('input');
      this.dispatch('change');
      return;
    }
    this.dispatch('click');
    if (this.localName === 'a' && this.download) this.ownerDocument.downloads.push({ href: this.href, filename: this.download });
  }
  focus() {}
  blur() {}

  descendants() {
    const out = [];
    const walk = (n) => { for (const c of n.childNodes) if (c.nodeType === 1) { out.push(c); walk(c); } };
    walk(this);
    return out;
  }
  querySelectorAll(selector) {
    const parts = selector.split(',');
    return this.descendants().filter((n) => parts.some((p) => matchesSimple(n, p)));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

function buildDocument(html) {
  const doc = {
    downloads: [],
    listeners: [],
    title: '',
    createElement: (tag) => new Element(doc, tag),
    createTextNode: (text) => new TextNode(text),
    addEventListener: (type, fn) => doc.listeners.push({ type, fn }),
    removeEventListener: () => {},
    getElementById: (id) => doc.documentElement.querySelector(`#${id}`) || (doc.documentElement.id === id ? doc.documentElement : null),
    querySelector: (s) => doc.documentElement.querySelector(s),
    querySelectorAll: (s) => doc.documentElement.querySelectorAll(s),
    dispatch(type, extra = {}) {
      const ev = { type, target: doc, defaultPrevented: false, preventDefault() { ev.defaultPrevented = true; }, ...extra };
      for (const l of doc.listeners.filter((x) => x.type === type)) l.fn(ev);
      return ev;
    },
  };
  const convert = (el) => {
    const node = new Element(doc, el.tag, el.attrs);
    for (const child of el.children) {
      if (typeof child === 'string') {
        if (el.tag !== 'script' && el.tag !== 'style') node.append(child);
      } else node.append(convert(child));
    }
    return node;
  };
  const root = parseHtml(html);
  const htmlEl = root.children.find((c) => typeof c !== 'string' && c.tag === 'html');
  doc.documentElement = convert(htmlEl);
  doc.body = doc.documentElement.querySelector('body');
  doc.head = doc.documentElement.querySelector('head');
  return doc;
}

// --- page loading ------------------------------------------------------------------

const LOCALES = path.join(EXTENSION_DIR, '_locales');
function catalogue(lang) {
  return JSON.parse(fs.readFileSync(path.join(LOCALES, lang, 'messages.json'), 'utf8'));
}

// chrome.i18n.getMessage over a messages.json, with Chrome's placeholder rules.
function i18nFrom(messages) {
  return (key, subs) => {
    const entry = messages[key];
    if (!entry) return '';
    const list = subs === undefined ? [] : [].concat(subs).map(String);
    const placeholders = entry.placeholders || {};
    return entry.message.replace(/\$([A-Za-z0-9_@]+)\$/g, (_m, name) => {
      const ph = placeholders[name.toLowerCase()] || placeholders[name];
      if (!ph) return '';
      return ph.content.replace(/\$(\d)/g, (_x, n) => list[Number(n) - 1] || '');
    }).replace(/\$\$/g, '$');
  };
}

let b = null;
let doc = null;
let confirms = [];

async function loadPage(htmlRel, store, scenario = {}) {
  b = makeBrowser();
  Object.assign(b.store, clone(store));
  Object.assign(b.scenario, scenario);
  if (scenario.lang) b.chrome.i18n.getMessage = i18nFrom(catalogue(scenario.lang));
  const html = fs.readFileSync(path.join(EXTENSION_DIR, htmlRel), 'utf8');
  doc = buildDocument(html);
  confirms = [];
  globalThis.chrome = b.chrome;
  const baseFetch = scenario.fetch || b.fetch;
  const localeUrl = /^chrome-extension:\/\/harness-extension-id\/(_locales\/[a-z]+\/messages\.json)$/;
  // The extension's own catalogues are served from disk, like chrome.runtime.getURL.
  globalThis.fetch = async (url, ...rest) => {
    const m = localeUrl.exec(String(url));
    if (!m) return baseFetch(url, ...rest);
    const text = fs.readFileSync(path.join(EXTENSION_DIR, m[1]), 'utf8');
    return { ok: true, status: 200, json: async () => JSON.parse(text), text: async () => text };
  };
  globalThis.document = doc;
  globalThis.window = globalThis;
  globalThis.confirm = (text) => { confirms.push(String(text)); return scenario.confirmAnswer !== false; };
  globalThis.alert = () => {};
  const blobs = [];
  const NodeBlob = require('buffer').Blob;
  globalThis.Blob = class extends NodeBlob {
    constructor(parts = [], opts) { super(parts, opts); blobs.push(parts.join('')); }
  };
  URL.createObjectURL = () => `blob:harness/${blobs.length}`;
  URL.revokeObjectURL = () => {};
  doc.blobs = blobs;
  const scripts = [...html.matchAll(/<script\b[^>]*\bsrc="([^"]+)"[^>]*>/g)].map((m) => m[1]);
  for (const src of scripts) await importExtensionFile(path.join(path.dirname(htmlRel), src));
  await idle();
}

const $ = (id) => {
  const node = doc.getElementById(id);
  if (!node) throw new Error(`no element #${id}`);
  return node;
};

async function click(node) {
  node.click();
  await idle();
}

async function type(node, text) {
  node.value = text;
  node.dispatch('input');
  await idle();
}

async function toggle(node) {
  node.click();
  await idle();
}

const POPUP = MANIFEST.action.default_popup;
const OPTIONS = MANIFEST.options_ui.page;
const BRIDGE = 'http://127.0.0.1:8765/bridge';
const sent = (t) => b.calls.messagesSent.filter((m) => m.type === t);

// --- i18n --------------------------------------------------------------------------

function staticTexts(htmlRel) {
  const html = fs.readFileSync(path.join(EXTENSION_DIR, htmlRel), 'utf8');
  return [...html.matchAll(/data-i18n="(\w+)"[^>]*>([^<]*)</g)].map((m) => ({ key: m[1], english: m[2] }));
}

async function areaI18n() {
  const es = catalogue('es');
  const msg = i18nFrom(es);
  const store = {
    settings: { intervalMinutes: 60, paused: false, bridgeUrl: BRIDGE, bridgeSecret: 's' },
    beacons: [{ id: 'b1', name: 'Manual', url: 'https://www.linkedin.com/jobs/search/?keywords=x', enabled: true }],
    enabledPortals: ['linkedin'],
  };

  for (const page of [POPUP, OPTIONS]) {
    setScenario(`i18n: ${page} in Spanish`);
    await loadPage(page, store, { lang: 'es' });
    const texts = staticTexts(page);
    ok('the page has translatable static text', texts.length > 5, texts.length);
    const wrong = texts.filter(({ key }) => doc.querySelector(`[data-i18n="${key}"]`).textContent !== msg(key));
    check('every data-i18n element shows the Spanish message', wrong.map((w) => w.key), []);
    check('the document language follows the locale', doc.documentElement.lang, 'es');

    setScenario(`i18n: ${page} without translations keeps the English text`);
    await loadPage(page, store, {});
    const changed = texts.filter(({ key, english }) => doc.querySelector(`[data-i18n="${key}"]`).textContent !== english);
    check('data-i18n elements keep their English text', changed.map((w) => w.key), []);
  }

  setScenario('i18n: rendered strings are translated too');
  await loadPage(POPUP, store, { lang: 'es' });
  check('popup status chip', $('statusChip').textContent, msg('statusActive'));
  check('popup pause button', $('pauseBtn').textContent, msg('popupPause'));
  await loadPage(OPTIONS, store, { lang: 'es' });
  check('options nav aria-label', doc.querySelector('nav').getAttribute('aria-label'), msg('navLabel'));
  const toggleEl = doc.querySelector('[data-portal="linkedin"]');
  check('portal switch label', toggleEl && toggleEl.getAttribute('aria-label'), msg('portalToggleLabel', ['LinkedIn']));
}

// --- portals (per-portal opt-in) ---------------------------------------------------

const portalToggle = (id) => doc.querySelector(`[data-portal="${id}"]`);
const portalItem = (id) => portalToggle(id).parentNode.parentNode;

async function areaPortals() {
  const base = { settings: { intervalMinutes: 60, paused: false, bridgeUrl: '', bridgeSecret: '' }, enabledPortals: [] };

  setScenario('portals: enabling asks for the terms, then the permission, and stores at once');
  await loadPage(OPTIONS, base, { granted: [] });
  check('every registry portal has a switch', ['linkedin', 'infojobs', 'indeed', 'tecnoempleo', 'jobfluent', 'manfred', 'adzuna', 'foorilla'].map((id) => Boolean(portalToggle(id))), Array(8).fill(true));
  await toggle(portalToggle('infojobs'));
  check('one confirmation with the portal terms', confirms.length === 1 && confirms[0].includes('InfoJobs') && /terms of use/.test(confirms[0]), true);
  check('the browser permission is requested for its origin', b.calls.permissionsRequest, [['https://www.infojobs.net/*']]);
  check('enabledPortals stored immediately (no Save)', b.store.enabledPortals, ['infojobs']);
  check('the service worker is told', sent('settings-updated').length, 1);
  check('the switch stays on', portalToggle('infojobs').checked, true);
  check('settings untouched', b.store.settings, base.settings);

  setScenario('portals: LinkedIn shows its own warning');
  await loadPage(OPTIONS, base, { granted: [] });
  await toggle(portalToggle('linkedin'));
  ok('LinkedIn warning about the User Agreement', confirms.length === 1 && /User Agreement/.test(confirms[0]), confirms);

  // With the real catalogues (what Chrome shows), not only the English fallback.
  for (const [lang, linkedinStrong] of [
    ['en', /User Agreement/],
    ['es', /Condiciones de uso de LinkedIn/],
  ]) {
    setScenario(`portals: strong warnings come from the ${lang} catalogue`);
    await loadPage(OPTIONS, base, { granted: [], lang, confirmAnswer: false });
    await toggle(portalToggle('linkedin'));
    await toggle(portalToggle('infojobs'));
    ok(`${lang}: LinkedIn keeps its strong warning`, confirms.length === 2 && linkedinStrong.test(confirms[0]), confirms);
    ok(`${lang}: other portals keep the generic one`, confirms[1].includes('InfoJobs') && !linkedinStrong.test(confirms[1]), confirms);
  }

  setScenario('portals: declining the terms leaves it off');
  await loadPage(OPTIONS, base, { granted: [], confirmAnswer: false });
  await toggle(portalToggle('indeed'));
  check('no permission request', b.calls.permissionsRequest, []);
  check('nothing stored', b.store.enabledPortals, []);
  check('no message', b.calls.messagesSent, []);
  check('the switch is back off', portalToggle('indeed').checked, false);

  setScenario('portals: a refused permission leaves it off with a message');
  await loadPage(OPTIONS, base, { granted: [], requestGrants: false });
  await toggle(portalToggle('adzuna'));
  check('requested', b.calls.permissionsRequest, [['https://www.adzuna.es/*']]);
  check('nothing stored', b.store.enabledPortals, []);
  check('the switch is back off', portalToggle('adzuna').checked, false);
  ok('the page says why', /not granted/.test($('toast').textContent), $('toast').textContent);

  setScenario('portals: disabling removes the permission and stores at once');
  await loadPage(OPTIONS, { ...base, enabledPortals: ['linkedin', 'infojobs'] }, { granted: ['https://www.linkedin.com/*', 'https://www.infojobs.net/*'] });
  check('enabled portals start on', [portalToggle('linkedin').checked, portalToggle('infojobs').checked, portalToggle('indeed').checked], [true, true, false]);
  await toggle(portalToggle('linkedin'));
  check('no confirmation to disable', confirms, []);
  check('permission removed for its origin', b.calls.permissionsRemove, [['https://www.linkedin.com/*']]);
  check('enabledPortals stored immediately', b.store.enabledPortals, ['infojobs']);
  check('the service worker is told', sent('settings-updated').length, 1);

  setScenario('portals: an absent enabledPortals reads like the migration');
  await loadPage(OPTIONS, { settings: base.settings, searches: [{ id: 's1', name: 'A', keywords: 'a', portals: ['tecnoempleo'] }] }, {});
  check('portals used by stored searches show as enabled', [portalToggle('tecnoempleo').checked, portalToggle('linkedin').checked], [true, false]);
  check('nothing is written on open', 'enabledPortals' in b.store, false);

  setScenario('portals: notes for a missing grant and for the bot list');
  await loadPage(OPTIONS, {
    settings: { intervalMinutes: 60, paused: false, bridgeUrl: BRIDGE, bridgeSecret: 's' },
    enabledPortals: ['linkedin', 'infojobs'],
  }, { granted: ['https://www.infojobs.net/*'], config: { paused: false, enabledPortals: ['linkedin'] } });
  ok('enabled without grant -> permission note', /Permission missing/.test(portalItem('linkedin').textContent), portalItem('linkedin').textContent);
  ok('excluded by the bot -> bot note', /Not enabled on the bot/.test(portalItem('infojobs').textContent), portalItem('infojobs').textContent);
  const configFetch = b.calls.fetches.find((f) => f.url === 'http://127.0.0.1:8765/config');
  check('the bot config is read with the secret header', configFetch && configFetch.headers['x-cva-bridge-secret'], 's');
  check('and stored as botConfig', b.store.botConfig && [b.store.botConfig.paused, b.store.botConfig.enabledPortals, typeof b.store.botConfig.at], [false, ['linkedin'], 'number']);
}

// --- save --------------------------------------------------------------------------

async function areaSave() {
  const settings = { intervalMinutes: 45, paused: true, bridgeUrl: '', bridgeSecret: '', botToken: 'tok', chatId: '42' };

  setScenario('save: an enabled beacon needs a name and a URL');
  await loadPage(OPTIONS, { settings, beacons: [], searches: [] });
  await click($('addBeaconBtn'));
  check('a row was added', $('watchList').children.length, 1);
  await click($('saveBtn'));
  check('nothing stored', [b.store.beacons, b.store.settings], [[], settings]);
  check('no reschedule', sent('settings-updated'), []);
  check('the error is shown', $('toast').textContent, 'Beacon 1 needs a name and a URL.');

  setScenario('save: beacon names must be unique');
  const dupes = [
    { id: 'a', name: 'Same', url: 'https://www.linkedin.com/jobs/search/?keywords=a', enabled: true, filterProfile: 'ai' },
    { id: 'b', name: 'Same', url: 'https://www.linkedin.com/jobs/search/?keywords=b', enabled: false, filterProfile: 'ai' },
  ];
  await loadPage(OPTIONS, { settings, beacons: dupes, searches: [] });
  await click($('saveBtn'));
  check('nothing stored', b.store.beacons, dupes);
  check('the error is shown', $('toast').textContent, 'Two beacons are called "Same": names must be unique.');

  setScenario('save: disabled beacons without a name do not block saving');
  const blanks = [
    { id: 'a', name: '', url: '', enabled: false, filterProfile: 'ai' },
    { id: 'b', name: '', url: '', enabled: false, filterProfile: 'backend' },
  ];
  await loadPage(OPTIONS, { settings, beacons: blanks, searches: [] });
  await click($('saveBtn'));
  check('stored', b.store.beacons, blanks);

  setScenario('save: every search needs a name and keywords');
  await loadPage(OPTIONS, { settings, beacons: [], searches: [{ id: 's1', name: 'Only name', keywords: '' }] });
  await click($('saveBtn'));
  check('nothing stored', b.store.searches, [{ id: 's1', name: 'Only name', keywords: '' }]);
  check('the error is shown', $('toast').textContent, 'Search 1 needs a name and keywords.');

  setScenario('save: stores the draft, clamps, trims and keeps the pause');
  await loadPage(OPTIONS, { settings, beacons: [], searches: [] });
  await type($('intervalInput'), '5000');
  await type($('bridgeUrlInput'), `  ${BRIDGE}  `);
  await type($('bridgeSecretInput'), ' s3cret ');
  await type($('filterInclude'), 'AI, "LLM"\n\nEngineer');
  await type($('filterExclude'), 'Senior\nLead,  ');
  await click($('profileBackendBtn'));
  await toggle($('filterEnabled'));
  await type($('filterInclude'), 'Python');
  await click($('profileAiBtn'));
  check('switching profile keeps the unsaved edits', $('filterInclude').value, 'AI, "LLM"\n\nEngineer');
  await click($('addBeaconBtn'));
  const fields = $('watchList').querySelectorAll('input');
  await type(fields[1], ' My beacon ');
  await type(fields[2], ' https://www.infojobs.net/ofertas?q=x ');
  doc.dispatch('keydown', { key: 's', ctrlKey: true });
  await idle();
  check('settings stored (no Telegram fields, pause kept, D2 clamp)', b.store.settings, { intervalMinutes: 1440, paused: true, bridgeUrl: BRIDGE, bridgeSecret: 's3cret' });
  check('ai filter parsed', b.store.titleFilter, { enabled: true, includeGroups: [['AI', 'LLM'], ['Engineer']], excludeTerms: ['Senior', 'Lead'] });
  check('backend filter parsed and disabled', b.store.titleFilterBackend && [b.store.titleFilterBackend.enabled, b.store.titleFilterBackend.includeGroups], [false, [['Python']]]);
  const saved = (b.store.beacons || [])[0] || {};
  check('beacon stored trimmed', [saved.name, saved.url, saved.enabled, saved.filterProfile], ['My beacon', 'https://www.infojobs.net/ofertas?q=x', true, 'ai']);
  ok('beacon id is bcn_ + 8 characters', /^bcn_[a-z0-9]{8}$/.test(saved.id || ''), saved.id);
  check('searches stored', b.store.searches, []);
  check('the service worker is told once', sent('settings-updated').length, 1);
  check('the interval field shows the stored value', $('intervalInput').value, '1440');

  for (const [typed, expected] of [['abc', 60], ['0', 60], ['', 60], ['-3', 1], ['29.6', 30]]) {
    setScenario(`save: interval ${JSON.stringify(typed)} -> ${expected}`);
    await loadPage(OPTIONS, { settings, beacons: [], searches: [] });
    await type($('intervalInput'), typed);
    await click($('saveBtn'));
    check('stored interval', b.store.settings && b.store.settings.intervalMinutes, expected);
  }

  setScenario('save: a pause changed elsewhere after opening is kept');
  await loadPage(OPTIONS, { settings, beacons: [], searches: [] });
  await b.chrome.storage.local.set({ settings: { ...settings, paused: false } });
  await idle();
  check('the status follows the new pause', $('statusChip').textContent !== 'Paused', true);
  await click($('saveBtn'));
  check('paused from storage, not from the page load', b.store.settings.paused, false);
}

// --- interval (D2 + warning) -------------------------------------------------------

async function areaInterval() {
  setScenario('interval: a fresh install shows 60 without warning');
  await loadPage(OPTIONS, {});
  check('value', $('intervalInput').value, '60');
  check('no warning', $('intervalWarning').hidden, true);

  setScenario('interval: an invalid stored value reads as 60');
  await loadPage(OPTIONS, { settings: { intervalMinutes: 'abc' } });
  check('value', $('intervalInput').value, '60');

  setScenario('interval: under 30 minutes warns but is allowed');
  await loadPage(OPTIONS, { settings: { intervalMinutes: 20, paused: false, bridgeUrl: '', bridgeSecret: '' } });
  check('warning shown', $('intervalWarning').hidden, false);
  await type($('intervalInput'), '30');
  check('warning hidden at 30', $('intervalWarning').hidden, true);
  await type($('intervalInput'), '10');
  await click($('saveBtn'));
  check('10 is stored', b.store.settings.intervalMinutes, 10);
}

// --- suggestions -------------------------------------------------------------------

async function areaSuggest() {
  const store = {
    settings: { intervalMinutes: 60, paused: false, bridgeUrl: BRIDGE, bridgeSecret: 'sec' },
    enabledPortals: ['linkedin'],
    searches: [{ id: 's_old', name: 'Existing', keywords: 'existing kw', location: '', filterProfile: 'ai', portals: ['linkedin'], titleFilter: 'on' }],
  };
  const suggested = {
    searches: [
      { name: 'AI BCN', keywords: 'ai engineer', location: 'Barcelona', portals: ['linkedin'] },
      { name: 'Again', keywords: 'Existing KW' },
      { name: 'No keywords' },
    ],
    titleFilters: { ai: { enabled: true, includeGroups: [['Agent']], excludeTerms: ['Intern'] } },
  };

  setScenario('suggest: lists the bot rows and adopts its title filters');
  await loadPage(OPTIONS, store, { suggested });
  await click($('suggestBtn'));
  const call = b.calls.fetches.find((f) => f.url === 'http://127.0.0.1:8765/searches/suggested');
  check('GET with the secret header', call && [call.method, call.headers['x-cva-bridge-secret']], ['GET', 'sec']);
  check('panel shown with the valid rows, all selected', [$('suggestPanel').hidden, $('suggestList').querySelectorAll('input').map((i) => i.checked)], [false, [true, true]]);
  check('profileTitleFilters stored', b.store.profileTitleFilters, { ai: { enabled: true, includeGroups: [['Agent']], excludeTerms: ['Intern'] } });
  check('no saved filters -> the editor shows the new defaults', [$('filterInclude').value, $('filterExclude').value], ['Agent', 'Intern']);
  await click($('addSuggestedBtn'));
  check('added and skipped reported', $('toast').textContent, 'Added 1, skipped 1 already present. Save to keep them.');
  check('not stored before Save', b.store.searches, store.searches);
  await click($('saveBtn'));
  const searches = b.store.searches || [];
  check('Save persists the merge (existing first, untouched)', searches.map((s) => [s.name, s.keywords, s.location]), [['Existing', 'existing kw', ''], ['AI BCN', 'ai engineer', 'Barcelona']]);
  ok('the new search has a fresh s_ id', /^s_[a-z0-9]{8}$/.test((searches[1] || {}).id || ''), searches[1]);

  setScenario('suggest: saved filters are not replaced in the editor');
  await loadPage(OPTIONS, { ...store, titleFilter: { enabled: true, includeGroups: [['Mine']], excludeTerms: [] } }, { suggested });
  await click($('suggestBtn'));
  check('editor keeps the saved filter', $('filterInclude').value, 'Mine');

  setScenario('suggest: needs a bridge URL and secret');
  await loadPage(OPTIONS, { ...store, settings: { ...store.settings, bridgeSecret: '' } }, { suggested });
  await click($('suggestBtn'));
  check('no fetch', b.calls.fetches.filter((f) => f.url.includes('/searches/suggested')), []);
  check('error shown', $('toast').textContent, 'Set a valid bridge URL and the secret first.');

  const respond = (status) => async () => ({ ok: status < 300, status, json: async () => ({}) });
  for (const [label, fetchImpl, text] of [
    ['401', respond(401), 'The bot rejected the secret.'],
    ['500', respond(500), 'The bot answered with HTTP 500.'],
    ['network error', async () => { throw new TypeError('Failed to fetch'); }, 'The bot is not reachable. Is it running?'],
  ]) {
    setScenario(`suggest: ${label}`);
    await loadPage(OPTIONS, { ...store, settings: { ...store.settings, bridgeUrl: '' } }, {});
    globalThis.fetch = fetchImpl;
    await type($('bridgeUrlInput'), BRIDGE);
    await click($('suggestBtn'));
    check('message', $('toast').textContent, text);
    check('nothing stored', b.store.searches, store.searches);
  }
}

// --- maintenance: clear history, test scrape ---------------------------------------

async function areaMaintenance() {
  const store = {
    settings: { intervalMinutes: 60, paused: false, bridgeUrl: '', bridgeSecret: '' },
    beacons: [{ id: 'b1', name: 'One', url: '  https://www.linkedin.com/jobs/search/?keywords=x  ', enabled: true, filterProfile: 'ai' }],
    enabledPortals: ['linkedin'],
  };

  setScenario('maintenance: clear history asks, says the bot gets offers again, sends no beaconId');
  await loadPage(OPTIONS, store);
  await click($('clearHistoryBtn'));
  ok('the confirmation says the next scan resends to the bot (D5)', confirms.length === 1 && /send every offer it finds to the bot again/.test(confirms[0]), confirms);
  check('message', b.calls.messagesSent, [{ type: 'clear-history' }]);
  check('result shown', $('toast').textContent, 'History cleared.');

  setScenario('maintenance: declining clears nothing');
  await loadPage(OPTIONS, store, { confirmAnswer: false });
  await click($('clearHistoryBtn'));
  check('no message', b.calls.messagesSent, []);

  const testButton = () => $('watchList').querySelectorAll('button').find((x) => x.textContent === 'Test');
  const testResult = () => $('watchList').querySelector('.test-result');

  setScenario('maintenance: test scrape sends the trimmed URL and shows the result');
  await loadPage(OPTIONS, store, { messageHandler: () => ({ ok: true, jobs: [{ title: 'AI Dev' }, { title: 'Other' }] }) });
  await click(testButton());
  check('message', b.calls.messagesSent, [{ type: 'test-scrape', url: 'https://www.linkedin.com/jobs/search/?keywords=x' }]);
  check('result', testResult().textContent, '2 offers read. First: AI Dev');

  setScenario('maintenance: test scrape error');
  await loadPage(OPTIONS, store, { messageHandler: () => ({ ok: false, error: 'a scan is running' }) });
  await click(testButton());
  check('result', testResult().textContent, 'Test failed: a scan is running');

  setScenario('maintenance: test scrape without URL');
  await loadPage(OPTIONS, { ...store, beacons: [{ ...store.beacons[0], url: '   ' }] });
  await click(testButton());
  check('no message', b.calls.messagesSent, []);
  check('result', testResult().textContent, 'URL required');
}

// --- popup (D4) --------------------------------------------------------------------

async function areaPopup() {
  const now = Date.now();
  const search = { id: 's_ai', name: 'AI BCN', keywords: 'ai engineer', location: 'Barcelona', filterProfile: 'ai', portals: ['linkedin', 'infojobs'], titleFilter: 'on' };
  const store = {
    settings: { intervalMinutes: 60, paused: false, bridgeUrl: BRIDGE, bridgeSecret: 's' },
    beacons: [],
    searches: [search],
    enabledPortals: ['linkedin'],
    lastCheck: { 'auto:s_ai:linkedin': { at: now - 5 * 60000, status: 'ok', jobsFound: 4, newJobs: 2, filteredOut: 0 } },
    stats: { totalNotified: 7, lastRoundAt: now - 5 * 60000 },
  };
  const items = () => $('watchList').children;

  setScenario('popup: auto-search beacons are listed (D4)');
  await loadPage(POPUP, store);
  check('one auto beacon (only enabled portals)', items().length, 1);
  check('its name and state', [items()[0].querySelector('.watch-name').textContent, items()[0].querySelector('.beacon-state').textContent], ['AI BCN · LinkedIn', '4 jobs · 5m ago']);
  check('its new-jobs count', items()[0].querySelector('.beacon-new').textContent, '2');
  check('count and status', [$('watchCount').textContent, $('statusChip').textContent, $('emptyState').hidden], ['1', 'Active', true]);
  check('last round', $('roundLast').textContent, '5m ago');
  check('nothing written', b.store, store);

  setScenario('popup: "configured" means the bridge URL and secret (D4)');
  await loadPage(POPUP, { ...store, settings: { ...store.settings, bridgeSecret: '' } });
  // Changed by public-release S2: the missing bridge is a call to action, not "Idle".
  check('not connected', $('statusChip').textContent, 'Bot not connected');
  ok('explains how to connect the bot', /Connect the extension to the bot/.test($('statusDetail').textContent), $('statusDetail').textContent);

  setScenario('popup: nothing to scan');
  await loadPage(POPUP, { ...store, searches: [] });
  check('idle and empty state', [$('statusChip').textContent, $('emptyState').hidden], ['Idle', false]);

  setScenario('popup: session expired names the portal, error status');
  await loadPage(POPUP, { ...store, lastCheck: { 'auto:s_ai:linkedin': { at: now, status: 'session_expired', error: 'LinkedIn session expired' } } });
  check('state', items()[0].querySelector('.beacon-state').textContent, 'Session expired: sign in to LinkedIn again');
  check('status', $('statusChip').textContent, 'Needs attention');

  setScenario('popup: manual beacons, disabled, portal off and missing permission');
  await loadPage(POPUP, {
    ...store,
    searches: [],
    beacons: [
      { id: 'm1', name: 'Off', url: 'https://www.linkedin.com/jobs/search/?keywords=a', enabled: false },
      { id: 'm2', name: 'Indeed one', url: 'https://es.indeed.com/jobs?q=a', enabled: true },
      { id: 'm3', name: 'No grant', url: 'https://www.linkedin.com/jobs/search/?keywords=b', enabled: true },
    ],
    lastCheck: { m3: { at: now, status: 'no_permission', error: 'x' } },
  });
  check('states', items().map((li) => li.querySelector('.beacon-state').textContent), ['Disabled', 'Indeed is not enabled', 'Permission missing: enable the portal in the options']);

  setScenario('popup: paused by the bot');
  await loadPage(POPUP, { ...store, botConfig: { paused: true, enabledPortals: null, at: now } });
  check('status and next round', [$('statusChip').textContent, $('roundNext').textContent], ['Paused by the bot', 'Paused by the bot']);

  setScenario('popup: check-now result says first scans went to the bot (D5)');
  await loadPage(POPUP, store, { messageHandler: () => ({ ok: true, summary: { skipped: false, beaconsScanned: 2, totalJobsFound: 9, totalNew: 3, firstScans: 1, errors: 0, filteredOut: 1 } }) });
  await click($('checkNowBtn'));
  check('result', $('resultLine').textContent, '2 scanned · 3 new · 1 filtered out · 1 first scans, sent to the bot');

  setScenario('popup: check-now failures');
  await loadPage(POPUP, store, { messageHandler: () => ({ ok: false, error: 'boom' }) });
  await click($('checkNowBtn'));
  check('error shown', $('resultLine').textContent, 'Failed: boom');
  await loadPage(POPUP, store, { messageHandler: () => ({ ok: true, summary: { skipped: true } }) });
  await click($('checkNowBtn'));
  check('skipped', $('resultLine').textContent, 'Another round is already running.');
}

// --- missing bridge (public-release S2) ---------------------------------------------

async function areaNoBridge() {
  const store = {
    settings: { intervalMinutes: 60, paused: false, bridgeUrl: BRIDGE, bridgeSecret: 's' },
    beacons: [{ id: 'b1', name: 'Manual', url: 'https://www.linkedin.com/jobs/search/?keywords=x', enabled: true }],
    enabledPortals: ['linkedin'],
  };
  const english = { statusNoBridge: 'Bot not connected', statusNoBridgeDetail: 'Connect the extension to the bot first: Options › Schedule and bot.' };
  const isMissing = (id) => $(id).classList.contains('is-missing') && $(id).getAttribute('aria-invalid') === 'true';

  for (const lang of ['en', 'es']) {
    const msg = lang === 'es' ? i18nFrom(catalogue('es')) : (key) => english[key];
    const scenario = lang === 'es' ? { lang } : {};
    for (const [label, missing] of [['no URL', { bridgeUrl: '' }], ['no secret', { bridgeSecret: '' }], ['neither', { bridgeUrl: '', bridgeSecret: '' }]]) {
      const noBridge = { ...store, settings: { ...store.settings, ...missing } };

      setScenario(`nobridge (${lang}, ${label}): the popup calls to connect the bot`);
      await loadPage(POPUP, noBridge, scenario);
      check('status chip and detail', [$('statusChip').textContent, $('statusDetail').textContent], [msg('statusNoBridge'), msg('statusNoBridgeDetail')]);
      ok('the chip warns', $('statusChip').classList.contains('chip-warn'), $('statusChip').getAttribute('class'));
      check('the call to action is shown', $('connectBotBtn').hidden, false);
      await click($('connectBotBtn'));
      check('it opens the options page', b.calls.openOptionsPage, 1);
      check('nothing written', b.store, noBridge);

      setScenario(`nobridge (${lang}, ${label}): Options highlights the missing fields`);
      await loadPage(OPTIONS, noBridge, scenario);
      check('status chip and banner', [$('statusChip').textContent, $('statusDetail').textContent, $('statusDetail').hidden], [msg('statusNoBridge'), msg('statusNoBridgeDetail'), false]);
      check('missing fields highlighted', [isMissing('bridgeUrlInput'), isMissing('bridgeSecretInput')], ['bridgeUrl' in missing, 'bridgeSecret' in missing]);
      ok('the connection section is marked', $('connection').classList.contains('needs-bridge'), $('connection').getAttribute('class'));
    }

    setScenario(`nobridge (${lang}): nothing to scan still shows the bridge call first`);
    await loadPage(POPUP, { ...store, beacons: [], settings: { ...store.settings, bridgeSecret: '' } }, scenario);
    check('status', $('statusChip').textContent, msg('statusNoBridge'));
  }

  setScenario('nobridge: filling both fields clears the highlight');
  await loadPage(OPTIONS, { ...store, settings: { ...store.settings, bridgeUrl: '', bridgeSecret: '' } });
  await type($('bridgeUrlInput'), BRIDGE);
  check('URL no longer missing, secret still missing', [isMissing('bridgeUrlInput'), isMissing('bridgeSecretInput')], [false, true]);
  await type($('bridgeSecretInput'), 's3cret');
  check('neither missing, section unmarked, status active', [isMissing('bridgeSecretInput'), $('connection').classList.contains('needs-bridge'), $('statusChip').textContent], [false, false, 'Active']);

  setScenario('nobridge: with the bridge set, no call to action');
  await loadPage(POPUP, store);
  check('popup', [$('statusChip').textContent, $('connectBotBtn').hidden], ['Active', true]);
  await loadPage(OPTIONS, store);
  check('options', [isMissing('bridgeUrlInput'), isMissing('bridgeSecretInput'), $('connection').classList.contains('needs-bridge')], [false, false, false]);

  setScenario('nobridge: check now answers the missing-bridge state');
  const answer = { ok: false, bridgeMissing: true, error: 'Connect the extension to the bot first: Options › Schedule and bot.' };
  await loadPage(POPUP, { ...store, settings: { ...store.settings, bridgeSecret: '' } }, { messageHandler: () => answer });
  await click($('checkNowBtn'));
  check('the error is shown as is', [$('resultLine').textContent, $('resultLine').classList.contains('is-error')], [answer.error, true]);
}


// --- role families (role-families T6, S3/S5) ----------------------------------------

const FAMILIES3 = [
  { id: 'ai', label: 'AI', default: true },
  { id: 'backend', label: 'Backend', default: false },
  { id: 'frontend', label: 'Frontend', default: false },
];
const FE_FILTER = { enabled: true, includeGroups: [['React', 'Vue']], excludeTerms: ['Senior'] };

const segmentButtons = () => $('profileSegment').querySelectorAll('button');
const selectOptions = (select) => select.querySelectorAll('option').map((o) => [o.value, o.textContent]);

async function areaFamilies() {
  const settings = { intervalMinutes: 60, paused: false, bridgeUrl: BRIDGE, bridgeSecret: 'sec' };

  setScenario('families: without a list the built-in two keep today\'s UI');
  await loadPage(OPTIONS, { settings, searches: [{ id: 's1', name: 'A', keywords: 'a', filterProfile: 'backend', portals: [] }] });
  check('segment = AI, Backend', segmentButtons().map((n) => [n.id, n.textContent]), [['profileAiBtn', 'AI'], ['profileBackendBtn', 'Backend']]);
  const builtinSelect = $('searchList').querySelector('select');
  check('search select = AI, Backend', selectOptions(builtinSelect), [['ai', 'AI'], ['backend', 'Backend']]);
  check('search select keeps backend', builtinSelect.value, 'backend');

  setScenario('families: the built-in labels follow the locale');
  await loadPage(OPTIONS, { settings, profileFamilies: FAMILIES3 }, { lang: 'es' });
  const es = i18nFrom(catalogue('es'));
  check('segment labels', segmentButtons().map((n) => n.textContent), [es('profileAi'), es('profileBackend'), 'Frontend']);

  setScenario('families: a 3-family list builds the select and the segment');
  const store = {
    settings,
    profileFamilies: FAMILIES3,
    familyTitleFilters: { frontend: FE_FILTER },
    titleFilter: { enabled: true, includeGroups: [['AI']], excludeTerms: [] },
    titleFilterBackend: { enabled: true, includeGroups: [['Python']], excludeTerms: [] },
    searches: [{ id: 's_fe', name: 'FE', keywords: 'react', location: 'Barcelona', filterProfile: 'frontend', portals: ['linkedin'], titleFilter: 'on' }],
    beacons: [{ id: 'b1', name: 'Manual', url: 'https://www.linkedin.com/jobs/search/?keywords=x', enabled: true, filterProfile: 'frontend' }],
    enabledPortals: ['linkedin'],
  };
  await loadPage(OPTIONS, store);
  check('segment = AI, Backend, Frontend', segmentButtons().map((n) => n.textContent), ['AI', 'Backend', 'Frontend']);
  const select = $('searchList').querySelector('select');
  check('search select options', selectOptions(select), [['ai', 'AI'], ['backend', 'Backend'], ['frontend', 'Frontend']]);
  check('the frontend search keeps its family', select.value, 'frontend');
  check('the frontend beacon keeps its family', $('watchList').querySelector('select').value, 'frontend');
  const feBtn = segmentButtons()[2];
  await click(feBtn);
  check('the frontend segment is pressed', segmentButtons().map((n) => n.getAttribute('aria-pressed')), ['false', 'false', 'true']);
  check('the editor shows the frontend filter', [$('filterInclude').value, $('filterExclude').value], ['React, Vue', 'Senior']);
  await type($('filterInclude'), 'Angular');
  await click(segmentButtons()[0]);
  check('switching back shows the ai filter', $('filterInclude').value, 'AI');
  await click($('saveBtn'));
  check('the search is saved with frontend', (b.store.searches || []).map((s) => s.filterProfile), ['frontend']);
  check('the beacon is saved with frontend', (b.store.beacons || []).map((x) => x.filterProfile), ['frontend']);
  check('the frontend filter persists', b.store.familyTitleFilters, { frontend: { enabled: true, includeGroups: [['Angular']], excludeTerms: ['Senior'] } });
  check('titleFilter and titleFilterBackend keep their keys', [b.store.titleFilter.includeGroups, b.store.titleFilterBackend.includeGroups], [[['AI']], [['Python']]]);
  const saved = clone(b.store);

  setScenario('families: reload shows the saved frontend filter');
  await loadPage(OPTIONS, saved);
  await click(segmentButtons()[2]);
  check('reloaded frontend filter', $('filterInclude').value, 'Angular');
  check('reloaded search family', $('searchList').querySelector('select').value, 'frontend');

  setScenario('families: old storage (titleFilter/titleFilterBackend only) saves unchanged');
  const old = {
    settings,
    titleFilter: { enabled: true, includeGroups: [['Old']], excludeTerms: [] },
    titleFilterBackend: { enabled: false, includeGroups: [['OldBackend']], excludeTerms: ['x'] },
    searches: [{ id: 's1', name: 'A', keywords: 'a', location: '', filterProfile: 'ai', portals: [], titleFilter: 'on' }],
    beacons: [],
  };
  await loadPage(OPTIONS, old);
  await click($('saveBtn'));
  check('stored filters unchanged', [b.store.titleFilter, b.store.titleFilterBackend], [old.titleFilter, old.titleFilterBackend]);
  check('no family map written', 'familyTitleFilters' in b.store, false);
  check('searches unchanged', b.store.searches, old.searches);

  setScenario('families: suggest adopts the bot\'s family list');
  const suggested = {
    searches: [],
    families: [
      { id: 'ai', label: 'AI', default: true, titleFilter: { enabled: true, includeGroups: [['AI']], excludeTerms: [] } },
      { id: 'frontend', label: 'Frontend', default: false, titleFilter: FE_FILTER },
      { id: 'BAD id', label: 'x' },
    ],
  };
  await loadPage(OPTIONS, { settings, searches: [] }, { suggested });
  await click($('suggestBtn'));
  check('profileFamilies stored (invalid ids dropped)', b.store.profileFamilies, [
    { id: 'ai', label: 'AI', default: true }, { id: 'frontend', label: 'Frontend', default: false },
  ]);
  check('the family filter becomes the frontend default', b.store.profileTitleFilters && b.store.profileTitleFilters.frontend, FE_FILTER);
  check('segment rebuilt from the list', segmentButtons().map((n) => n.textContent), ['AI', 'Frontend']);
  await click(segmentButtons()[1]);
  check('the editor shows the served frontend default', $('filterInclude').value, 'React, Vue');

  setScenario('families: the options page adopts the /config family list');
  await loadPage(OPTIONS, { settings, searches: [] }, { config: { paused: false, enabledPortals: ['linkedin'], families: FAMILIES3 } });
  check('profileFamilies stored from /config', b.store.profileFamilies, FAMILIES3);
  check('segment rebuilt from /config', segmentButtons().map((n) => n.textContent), ['AI', 'Backend', 'Frontend']);

  setScenario('families: a bot without families brings the built-ins back');
  await loadPage(OPTIONS, { settings, searches: [], profileFamilies: FAMILIES3 }, { suggested: { searches: [] } });
  await click($('suggestBtn'));
  check('profileFamilies removed', 'profileFamilies' in b.store, false);
  check('segment = AI, Backend', segmentButtons().map((n) => n.textContent), ['AI', 'Backend']);
}

// --- interface language selector (bot-i18n S9) ---------------------------------------

async function areaLanguage() {
  const en = i18nFrom(catalogue('en'));
  const es = i18nFrom(catalogue('es'));
  const settings = { intervalMinutes: 60, paused: false, bridgeUrl: BRIDGE, bridgeSecret: 's' };
  const base = {
    settings,
    beacons: [{ id: 'b1', name: 'Manual', url: 'https://www.linkedin.com/jobs/search/?keywords=x', enabled: true }],
    enabledPortals: ['linkedin'],
  };
  const pressed = () => [...$('languageSegment').querySelectorAll('[data-lang]')].map((n) => [n.getAttribute('data-lang'), n.getAttribute('aria-pressed')]);
  const langButton = (lang) => $('languageSegment').querySelector(`[data-lang="${lang}"]`);
  const nav = () => doc.querySelector('nav').getAttribute('aria-label');

  setScenario('language: auto follows chrome.i18n and writes nothing');
  await loadPage(OPTIONS, base, { lang: 'es' });
  check('options in the browser language', [doc.documentElement.lang, nav()], ['es', es('navLabel')]);
  check('auto is pressed', pressed(), [['auto', 'true'], ['en', 'false'], ['es', 'false']]);
  check('the selector is a labelled group', [$('languageSegment').getAttribute('role'), $('languageSegment').getAttribute('aria-label')], ['group', es('languageLabel')]);
  check('nothing stored', 'uiLanguage' in b.store, false);
  await loadPage(POPUP, base, { lang: 'es' });
  check('popup in the browser language', $('pauseBtn').textContent, es('popupPause'));

  setScenario('language: a forced language wins over the browser language');
  await loadPage(OPTIONS, { ...base, uiLanguage: 'en' }, { lang: 'es' });
  check('English forced in a Spanish browser', [doc.documentElement.lang, nav(), pressed()[1]], ['en', en('navLabel'), ['en', 'true']]);
  await loadPage(OPTIONS, { ...base, uiLanguage: 'es' }, {});
  check('Spanish forced with no chrome.i18n', [doc.documentElement.lang, nav()], ['es', es('navLabel')]);
  const toggleEl = doc.querySelector('[data-portal="linkedin"]');
  check('placeholders resolved like chrome.i18n', toggleEl.getAttribute('aria-label'), es('portalToggleLabel', ['LinkedIn']));
  await loadPage(POPUP, { ...base, uiLanguage: 'es' }, {});
  check('popup renders in the stored language', [$('pauseBtn').textContent, $('statusChip').textContent], [es('popupPause'), es('statusActive')]);
  await loadPage(POPUP, { ...base, uiLanguage: 'nonsense' }, { lang: 'es' });
  check('an invalid value behaves as auto', $('pauseBtn').textContent, es('popupPause'));

  setScenario('language: clicking a language persists it and re-renders at once');
  await loadPage(OPTIONS, base, {});
  await type($('intervalInput'), '45');
  await click(langButton('es'));
  check('stored', b.store.uiLanguage, 'es');
  check('re-rendered in Spanish', [doc.documentElement.lang, nav(), pressed()[2]], ['es', es('navLabel'), ['es', 'true']]);
  check('portal switch label follows', doc.querySelector('[data-portal="linkedin"]').getAttribute('aria-label'), es('portalToggleLabel', ['LinkedIn']));
  check('the unsaved edit survives', $('intervalInput').value, '45');
  await click(langButton('en'));
  check('back to English', [b.store.uiLanguage, nav()], ['en', en('navLabel')]);
  await click(langButton('auto'));
  check('auto stored and the chrome.i18n text returns', [b.store.uiLanguage, pressed()[0]], ['auto', ['auto', 'true']]);

  setScenario('language: export carries a forced language, import restores it, old files still import');
  const exportJson = async (store) => {
    await loadPage(OPTIONS, store, {});
    await click($('exportBtn'));
    return JSON.parse(doc.blobs[doc.blobs.length - 1]);
  };
  const importJson = async (store, config) => {
    await loadPage(OPTIONS, store, {});
    let input = null;
    const create = doc.createElement;
    doc.createElement = (tag) => { const el = create(tag); if (tag === 'input') input = el; return el; };
    await click($('importBtn'));
    input.files = [{ text: async () => JSON.stringify(config) }];
    input.dispatch('change');
    await idle();
  };
  const exported = await exportJson({ ...base, uiLanguage: 'es' });
  check('export has the language', exported.uiLanguage, 'es');
  check('the format is unchanged', [exported.format, exported.version], ['linkedin-beacons-config', 1]);
  const exportedAuto = await exportJson(base);
  check('auto export has no language key', 'uiLanguage' in exportedAuto, false);
  await importJson({ ...base, uiLanguage: 'en' }, exported);
  check('import stores the file language', b.store.uiLanguage, 'es');
  check('and the page switched', nav(), es('navLabel'));
  await importJson({ ...base, uiLanguage: 'en' }, exportedAuto);
  check('an old export keeps the stored language', b.store.uiLanguage, 'en');
  check('and still imports', b.store.beacons.length, 1);
  await importJson({ ...base, uiLanguage: 'en' }, { ...exported, uiLanguage: 'klingon' });
  check('an invalid language in a file is ignored', b.store.uiLanguage, 'en');

  setScenario('language: t() substitutes like chrome.i18n and falls back');
  await loadPage(POPUP, { ...base, uiLanguage: 'es' }, { lang: 'en' });
  const { t, applyUiLanguage } = await import(require('url').pathToFileURL(path.join(EXTENSION_DIR, 'i18n.js')).href);
  check('one substitution', t('timeAgo', ['5m'], 'x'), es('timeAgo', ['5m']));
  check('two substitutions', t('beaconJobs', [3, 'ok'], 'x'), es('beaconJobs', [3, 'ok']));
  check('a missing substitution is empty', t('beaconJobs', [3], 'x'), es('beaconJobs', [3]));
  check('an unknown key uses the fallback', t('noSuchKey', undefined, 'fb'), 'fb');
  await applyUiLanguage('auto');
  check('auto goes back to chrome.i18n', t('timeAgo', ['5m'], 'x'), en('timeAgo', ['5m']));
}

const AREAS = {
  language: areaLanguage,
  nobridge: areaNoBridge,
  i18n: areaI18n,
  portals: areaPortals,
  save: areaSave,
  interval: areaInterval,
  suggest: areaSuggest,
  maintenance: areaMaintenance,
  popup: areaPopup,
  families: areaFamilies,
};

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
