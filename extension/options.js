// lazyhunt options page (docs/extension-spec.md 6.2, 6.3; decisions D2, D5).
//
// The page keeps a draft of everything editable (interval, bridge, manual
// beacons, searches, both title filter profiles) and stores it only on Save.
// Two things apply at once instead: portal toggles (opt-in with terms warning
// and browser permission) and importing a configuration file. Storage keys and
// shapes, and the runtime messages sent to the service worker, follow the
// spec's sections 2 and 3.

import { t, applyUiLanguage, loadUiLanguage, normalizeUiLanguage } from './i18n.js';
import { PORTALS, portalFor } from './portals/registry.js';
import { portalOrigin, termsMessageKey, termsWarning } from './portals/terms.js';
import {
  adoptProfileFamilies, adoptProfileTitleFilters, cleanTitleFilter, defaultFamilyId, formatExcludeTerms, formatIncludeGroups,
  isBuiltinFamily, normalizeProfile, parseExcludeTerms, parseIncludeGroups, resolveFamilies,
  resolveTitleFilters,
} from './filter.js';
import {
  configUrl, dedupeSearchIds, expandSearches, mergeSuggestedSearches, normalizeSearches,
  pairUrl, parseBotConfig, parseSuggestions, resolveEnabledPortals, suggestionsUrl,
} from './searches.js';
import { mountProfileEditor, parseProfile, profileUrl } from './profile_editor.js';
import { DEFAULT_BRIDGE_URL, healedBridgeUrl, parseBridgeUrl } from './bridge_url.js';

const CONFIG_FORMAT = 'linkedin-beacons-config';
const CONFIG_VERSION = 1;
const DEFAULT_INTERVAL = 60;
const MIN_INTERVAL = 1;
const MAX_INTERVAL = 1440;
const WARN_INTERVAL = 30;
const FETCH_TIMEOUT_MS = 5000;
const SECRET_HEADER = 'X-CVA-Bridge-Secret';
const LOAD_KEYS = [
  'settings', 'beacons', 'searches', 'titleFilter', 'titleFilterBackend', 'profileTitleFilters',
  'profileFamilies', 'familyTitleFilters', 'enabledPortals', 'botConfig', 'lastCheck', 'uiLanguage',
];

const $ = (id) => document.getElementById(id);
const isObject = (v) => Boolean(v) && typeof v === 'object' && !Array.isArray(v);
const asObject = (v) => (isObject(v) ? v : {});

// --- i18n ------------------------------------------------------------------------

// t() with the English fallback filled in the same way Chrome fills $1..$n.
function tr(key, subs, english) {
  const list = subs === undefined || subs === null ? [] : [].concat(subs).map(String);
  const fallback = list.reduce((text, value, i) => text.split(`$${i + 1}`).join(value), english);
  return t(key, list.length ? list : undefined, fallback);
}

function localizeStatic() {
  document.documentElement.lang = tr('htmlLang', null, 'en');
  document.title = tr('optionsTitle', null, 'LazyHunt options');
  for (const node of document.querySelectorAll('[data-i18n]')) {
    node.textContent = tr(node.getAttribute('data-i18n'), null, node.textContent);
  }
  for (const attr of ['aria-label', 'placeholder', 'title']) {
    for (const node of document.querySelectorAll(`[data-i18n-${attr}]`)) {
      const english = node.getAttribute(attr) || '';
      node.setAttribute(attr, tr(node.getAttribute(`data-i18n-${attr}`), null, english));
    }
  }
}

// The language selector: stored at once (like the portal switches), applied
// to the open page without losing unsaved edits.
function renderLanguage() {
  const current = normalizeUiLanguage(state.stored && state.stored.uiLanguage);
  for (const button of $('languageSegment').querySelectorAll('[data-lang]')) {
    button.setAttribute('aria-pressed', button.getAttribute('data-lang') === current ? 'true' : 'false');
  }
}

async function setLanguage(value) {
  const lang = normalizeUiLanguage(value);
  await chrome.storage.local.set({ uiLanguage: lang });
  state.stored = { ...state.stored, uiLanguage: lang };
  await applyUiLanguage(lang);
  localizeStatic();
  renderLanguage();
  if (state.draft) drawEverything();
}

// --- pure helpers ----------------------------------------------------------------

// D2: one interval rule. Missing or invalid (including 0) -> 60; otherwise
// rounded and clamped to 1..1440.
function readInterval(value) {
  if (value === null || value === undefined || value === '') return DEFAULT_INTERVAL;
  const n = Number(value);
  if (!Number.isFinite(n) || n === 0) return DEFAULT_INTERVAL;
  return Math.min(MAX_INTERVAL, Math.max(MIN_INTERVAL, Math.round(n)));
}

function randomId(prefix) {
  let tail = '';
  while (tail.length < 8) tail += Math.random().toString(36).slice(2);
  return prefix + tail.slice(0, 8);
}
const newBeaconId = () => randomId('bcn_');
const newSearchId = () => randomId('s_');

// `families`: the profile's family list (role families); a declared
// filterProfile is kept, an unknown one becomes the default family.
function cleanBeacon(raw, families = state.families) {
  const b = asObject(raw);
  return {
    id: typeof b.id === 'string' && b.id ? b.id : newBeaconId(),
    name: b.name === undefined || b.name === null ? '' : String(b.name),
    url: b.url === undefined || b.url === null ? '' : String(b.url),
    enabled: b.enabled !== false,
    filterProfile: normalizeProfile(b.filterProfile, families),
  };
}

function filterDraft(filter) {
  return {
    enabled: filter.enabled !== false,
    include: formatIncludeGroups(filter.includeGroups),
    exclude: formatExcludeTerms(filter.excludeTerms),
  };
}

function parseFilterDraft(d) {
  return {
    enabled: Boolean(d.enabled),
    includeGroups: parseIncludeGroups(d.include),
    excludeTerms: parseExcludeTerms(d.exclude),
  };
}

function bridgeConfigured(url, secret) {
  return Boolean(parseBridgeUrl(url).url && String(secret || '').trim());
}

// beta4-polish S4: why a typed bridge URL cannot be saved; null when it can
// (a blank field means the default URL).
function bridgeUrlProblem(value) {
  switch (parseBridgeUrl(value).error) {
    case 'notLoopback': return tr('bridgeUrlNotLoopback', null, 'The bridge URL must point to this computer (127.0.0.1, localhost or [::1]).');
    case 'notBridge': return tr('bridgeUrlNotBridge', null, 'The bridge URL must end in /bridge, for example http://127.0.0.1:8765/bridge.');
    case 'invalid': return tr('bridgeUrlInvalid', null, 'The bridge URL must look like http://127.0.0.1:8765/bridge.');
    default: return null;
  }
}

// The URL to store for the typed value: clean, or the default when blank.
const bridgeUrlToStore = (value) => parseBridgeUrl(value).url || DEFAULT_BRIDGE_URL;

async function fetchWithTimeout(url, init) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
    else if (['value', 'checked', 'type', 'disabled', 'hidden', 'title', 'placeholder'].includes(key)) node[key] = value;
    else node.setAttribute(key, String(value));
  }
  for (const child of children) if (child !== null && child !== undefined) node.append(child);
  return node;
}

// --- state -----------------------------------------------------------------------

const state = {
  stored: {},
  paused: false,
  enabledPortals: [],
  grants: {},
  botConfig: null,
  lastCheck: {},
  draft: null,
  families: resolveFamilies({}), // the bot's role families; the built-in two until it sends some
  profile: 'ai',
  suggestions: [],
  lastScanCells: new Map(), // beacon id -> element showing its last scan
};

async function refreshGrants() {
  await Promise.all(PORTALS.map(async (portal) => {
    try {
      state.grants[portal.id] = Boolean(await chrome.permissions.contains({ origins: [portalOrigin(portal)] }));
    } catch (_) {
      state.grants[portal.id] = false;
    }
  }));
}

async function load() {
  const data = await chrome.storage.local.get(LOAD_KEYS);
  state.stored = data;
  const settings = asObject(data.settings);
  state.paused = settings.paused === true;
  state.enabledPortals = resolveEnabledPortals(data.enabledPortals, data.searches, data.beacons, portalFor, PORTALS);
  state.botConfig = isObject(data.botConfig) ? data.botConfig : null;
  state.lastCheck = asObject(data.lastCheck);
  state.families = resolveFamilies(data);
  state.profile = normalizeProfile(state.profile, state.families);
  const filters = resolveTitleFilters(data);
  state.draft = {
    interval: String(readInterval(settings.intervalMinutes)),
    // beta4-polish S4: prefilled with the default, a trailing "." healed.
    bridgeUrl: healedBridgeUrl(settings.bridgeUrl) || DEFAULT_BRIDGE_URL,
    bridgeSecret: String(settings.bridgeSecret || ''),
    beacons: (Array.isArray(data.beacons) ? data.beacons : []).filter(isObject).map((b) => cleanBeacon(b)),
    searches: dedupeSearchIds(normalizeSearches(data.searches, state.families), newSearchId),
    filters: Object.fromEntries(Object.entries(filters).map(([id, filter]) => [id, filterDraft(filter)])),
  };
  await refreshGrants();
  drawEverything();
}

function markDirty() {
  setSaveState(tr('saveStateDirty', null, 'Unsaved changes'));
}

function setSaveState(text) {
  $('saveState').textContent = text;
}

let toastTimer = null;
function toast(text, kind = 'info') {
  const node = $('toast');
  node.textContent = text;
  node.className = `toast toast-${kind}`;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, kind === 'error' ? 8000 : 4000);
}

function errorText(error) {
  return (error && error.message) || String(error || '?');
}

async function notifyWorker() {
  try {
    await chrome.runtime.sendMessage({ type: 'settings-updated' });
  } catch (_) {
    // The service worker reschedules on its next start anyway.
  }
}

// --- rendering -------------------------------------------------------------------

function drawEverything() {
  renderStatus();
  renderPortals();
  renderSearches();
  drawManualRows();
  renderFilter();
  renderConnection();
  renderBotInfo();
  renderLanguage();
}

function scannableCount() {
  const enabled = new Set(state.enabledPortals);
  const manual = state.draft.beacons.filter((b) => b.enabled && b.url.trim() && enabled.has(portalFor(b.url).id));
  return manual.length + expandSearches(state.draft.searches, PORTALS, state.enabledPortals, state.families).length;
}

function renderStatus() {
  const chip = $('statusChip');
  const banner = $('statusDetail');
  let kind = 'ok';
  let label = tr('statusActive', null, 'Active');
  let detail = '';
  const bridgeOk = bridgeConfigured(state.draft.bridgeUrl, state.draft.bridgeSecret);
  if (state.paused) {
    kind = 'warn';
    label = tr('statusPaused', null, 'Paused');
    detail = tr('optStatusPausedDetail', null, 'Scans are paused. Resume them from the toolbar popup.');
  } else if (!bridgeOk) {
    kind = 'warn';
    label = tr('statusNoBridge', null, 'Bot not connected');
    detail = tr('statusNoBridgeDetail', null, 'Connect the extension to the bot first: Options › Schedule and bot.');
  } else if (state.botConfig && state.botConfig.paused) {
    kind = 'warn';
    label = tr('statusBotPaused', null, 'Paused by the bot');
    detail = tr('statusBotPausedDetail', null, 'The bot is paused. Resume it from the bot.');
  } else if (!scannableCount()) {
    kind = 'muted';
    label = tr('statusNoBeacons', null, 'Nothing to scan');
    detail = tr('statusIdleNothing', null, 'Nothing to scan: enable a portal and add a search or a beacon.');
  }
  chip.className = `chip${kind === 'muted' ? '' : ` chip-${kind}`}`;
  chip.textContent = label;
  banner.textContent = detail;
  banner.hidden = !detail;
  banner.className = `status-banner status-${kind}`;
  renderBridgeFields(bridgeOk);
}

// S2: without the bridge the extension scans nothing, so the fields that
// are missing (or an invalid URL) are highlighted with an explanation.
function renderBridgeFields(bridgeOk) {
  const urlProblem = bridgeUrlProblem(state.draft.bridgeUrl);
  $('bridgeUrlError').textContent = urlProblem || '';
  $('bridgeUrlError').hidden = !urlProblem;
  const missing = {
    bridgeUrlInput: !parseBridgeUrl(state.draft.bridgeUrl).url,
    bridgeSecretInput: !String(state.draft.bridgeSecret || '').trim(),
  };
  for (const [id, isMissing] of Object.entries(missing)) {
    const input = $(id);
    input.classList.toggle('is-missing', isMissing);
    if (isMissing) input.setAttribute('aria-invalid', 'true');
    else input.removeAttribute('aria-invalid');
  }
  $('connection').classList.toggle('needs-bridge', !bridgeOk);
  $('bridgeMissingHint').hidden = bridgeOk;
}

function renderPortals() {
  const list = $('portalList');
  const enabled = new Set(state.enabledPortals);
  const botList = state.botConfig && Array.isArray(state.botConfig.enabledPortals) ? state.botConfig.enabledPortals : null;
  list.replaceChildren(...PORTALS.map((portal) => {
    const on = enabled.has(portal.id);
    const notes = [];
    if (on && state.grants[portal.id] === false) {
      notes.push(el('span', { class: 'chip chip-warn', text: tr('portalNoPermission', null, 'Permission missing: switch it off and on again') }));
    }
    if (on && botList && !botList.includes(portal.id)) {
      notes.push(el('span', { class: 'chip chip-info', text: tr('portalNotOnBot', null, 'Not enabled on the bot') }));
    }
    const toggle = el('input', {
      type: 'checkbox', class: 'switch', checked: on, role: 'switch',
      'aria-label': tr('portalToggleLabel', portal.label, 'Scan $1'),
      'data-portal': portal.id,
      onchange: (event) => togglePortal(portal, event.currentTarget || toggle),
    });
    return el('li', { class: `portal${on ? ' is-on' : ''}` },
      el('label', { class: 'portal-main' }, toggle, el('span', { class: 'portal-name', text: portal.label })),
      el('span', { class: 'portal-host mono muted', text: portal.linkHost }),
      el('span', { class: 'portal-notes' }, ...notes));
  }));
}

async function storeEnabledPortals(list) {
  state.enabledPortals = list;
  await chrome.storage.local.set({ enabledPortals: list });
  await notifyWorker();
}

async function togglePortal(portal, input) {
  const want = Boolean(input.checked);
  input.disabled = true;
  try {
    if (want) {
      // Strong-warning portals (such as LinkedIn) have their own
      // locale key; the rest share `termsPortal`, which takes the label.
      const termsKey = termsMessageKey(portal);
      const terms = termsKey === 'termsPortal'
        ? tr('termsPortal', portal.label, termsWarning(portal))
        : tr(termsKey, null, termsWarning(portal));
      if (!confirm(terms)) {
        input.checked = false;
        return;
      }
      let granted = false;
      try {
        granted = Boolean(await chrome.permissions.request({ origins: [portalOrigin(portal)] }));
      } catch (_) {
        granted = false;
      }
      if (!granted) {
        input.checked = false;
        toast(tr('portalRefused', portal.label, 'The browser permission for $1 was not granted: it stays off.'), 'error');
        return;
      }
      state.grants[portal.id] = true;
      const list = state.enabledPortals.includes(portal.id) ? [...state.enabledPortals] : [...state.enabledPortals, portal.id];
      await storeEnabledPortals(list);
      toast(tr('portalEnabled', portal.label, '$1 enabled.'), 'ok');
    } else {
      try {
        await chrome.permissions.remove({ origins: [portalOrigin(portal)] });
      } catch (_) {
        // Keeping a grant is harmless: the portal is no longer enabled.
      }
      state.grants[portal.id] = false;
      await storeEnabledPortals(state.enabledPortals.filter((id) => id !== portal.id));
      toast(tr('portalDisabled', portal.label, '$1 disabled.'), 'info');
    }
  } finally {
    input.disabled = false;
    renderPortals();
    renderSearches();
    renderStatus();
  }
}

// The built-in families keep their translated labels unless the profile
// renames them; a declared family shows the label the bot sent.
const BUILTIN_LABEL_KEYS = { ai: ['profileAi', 'AI'], backend: ['profileBackend', 'Backend'] };
function familyLabel(family) {
  const builtin = BUILTIN_LABEL_KEYS[family.id];
  return builtin && family.label === builtin[1] ? tr(builtin[0], null, builtin[1]) : family.label;
}

function profileSelect(value, onchange, label) {
  const select = el('select', { class: 'select', 'aria-label': label, onchange },
    ...state.families.map((family) => el('option', { value: family.id, text: familyLabel(family) })));
  select.value = normalizeProfile(value, state.families);
  return select;
}

function textField(label, value, oninput, extra = {}) {
  return el('label', { class: 'field' },
    el('span', { class: 'field-label', text: label }),
    el('input', { class: 'input', type: 'text', value, spellcheck: 'false', oninput, ...extra }));
}

function renderSearches() {
  const draft = state.draft;
  const enabled = new Set(state.enabledPortals);
  const portals = PORTALS.filter((p) => typeof p.buildSearchUrl === 'function');
  $('searchList').replaceChildren(...draft.searches.map((search, index) => {
    const edited = () => { markDirty(); renderPreview(); renderStatus(); };
    const portalBoxes = portals.map((portal) => el('label', { class: `check${enabled.has(portal.id) ? '' : ' is-off'}` },
      el('input', {
        type: 'checkbox', checked: search.portals.includes(portal.id),
        onchange: (event) => {
          const on = Boolean((event.currentTarget || event.target).checked);
          search.portals = on
            ? [...new Set([...search.portals, portal.id])]
            : search.portals.filter((id) => id !== portal.id);
          edited();
        },
      }),
      el('span', { text: enabled.has(portal.id) ? portal.label : tr('portalOffSuffix', portal.label, '$1 (off)') })));
    return el('div', { class: 'row search-row' },
      el('div', { class: 'row-fields grid-3' },
        textField(tr('searchName', null, 'Name'), search.name, (e) => { search.name = e.currentTarget.value; edited(); }),
        textField(tr('searchKeywords', null, 'Keywords'), search.keywords, (e) => { search.keywords = e.currentTarget.value; edited(); }),
        textField(tr('searchLocation', null, 'Location (optional)'), search.location, (e) => { search.location = e.currentTarget.value; edited(); })),
      el('div', { class: 'row-options' },
        el('label', { class: 'field field-inline' },
          el('span', { class: 'field-label', text: tr('profileLabel', null, 'Filter profile') }),
          profileSelect(search.filterProfile, (e) => { search.filterProfile = normalizeProfile(e.currentTarget.value, state.families); edited(); }, tr('profileLabel', null, 'Filter profile'))),
        el('label', { class: 'inline-field' },
          el('input', {
            type: 'checkbox', class: 'switch', checked: search.titleFilter !== 'off',
            onchange: (e) => { search.titleFilter = e.currentTarget.checked ? 'on' : 'off'; edited(); },
          }),
          el('span', { text: tr('searchTitleFilter', null, 'Apply the title filter') })),
        el('button', {
          type: 'button', class: 'btn btn-sm btn-danger row-remove', text: tr('remove', null, 'Remove'),
          onclick: () => {
            const name = search.name || search.keywords || String(index + 1);
            if (!confirm(tr('searchRemoveConfirm', name, 'Remove the search "$1"?'))) return;
            draft.searches = draft.searches.filter((s) => s !== search);
            markDirty();
            renderSearches();
            renderStatus();
          },
        })),
      el('fieldset', { class: 'portal-picks' },
        el('legend', { class: 'field-label', text: tr('searchPortals', null, 'Portals') }),
        ...portalBoxes));
  }));
  $('searchEmpty').hidden = draft.searches.length > 0;
  renderPreview();
}

function lastScanInfo(entry) {
  if (!entry || !entry.at) return { text: '—', title: tr('neverScanned', null, 'Never scanned') };
  const when = new Date(entry.at);
  const time = when.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const full = when.toLocaleString();
  return { text: time, title: entry.error ? `${full} · ${entry.error}` : full };
}

function lastScanCell(id) {
  const info = lastScanInfo(state.lastCheck[id]);
  const cell = el('span', { class: 'scan-time', text: info.text, title: info.title });
  state.lastScanCells.set(id, cell);
  return cell;
}

function refreshLastScan() {
  for (const [id, cell] of state.lastScanCells) {
    const info = lastScanInfo(state.lastCheck[id]);
    cell.textContent = info.text;
    cell.title = info.title;
  }
}

function renderPreview() {
  const auto = expandSearches(state.draft.searches, PORTALS, state.enabledPortals, state.families);
  $('previewCount').textContent = String(auto.length);
  $('previewList').replaceChildren(...(auto.length
    ? auto.map((beacon) => el('li', { class: 'preview-item' },
      el('span', { class: 'preview-name', text: beacon.name }),
      el('span', { class: 'preview-url mono muted', text: beacon.url }),
      lastScanCell(beacon.id)))
    : [el('li', { class: 'preview-item muted', text: tr('previewEmpty', null, 'Nothing yet: a search needs keywords and an enabled portal.') })]));
}

function drawManualRows() {
  const draft = state.draft;
  $('watchList').replaceChildren(...draft.beacons.map((beacon) => {
    const result = el('p', { class: 'test-result hint', 'aria-live': 'polite' });
    const edited = () => { markDirty(); renderStatus(); };
    return el('div', { class: `row beacon-row${beacon.enabled ? '' : ' is-disabled'}` },
      el('div', { class: 'row-fields beacon-fields' },
        el('label', { class: 'inline-field' },
          el('input', {
            type: 'checkbox', class: 'switch', checked: beacon.enabled,
            'aria-label': tr('beaconEnabled', null, 'Enabled'),
            onchange: (e) => { beacon.enabled = Boolean(e.currentTarget.checked); edited(); },
          })),
        textField(tr('beaconName', null, 'Name'), beacon.name, (e) => { beacon.name = e.currentTarget.value; edited(); }),
        textField(tr('beaconUrl', null, 'Results page URL'), beacon.url, (e) => { beacon.url = e.currentTarget.value; edited(); }, { type: 'url' }),
        el('label', { class: 'field field-narrow' },
          el('span', { class: 'field-label', text: tr('profileLabel', null, 'Filter profile') }),
          profileSelect(beacon.filterProfile, (e) => { beacon.filterProfile = normalizeProfile(e.currentTarget.value, state.families); markDirty(); }, tr('profileLabel', null, 'Filter profile')))),
      el('div', { class: 'row-options' },
        el('span', { class: 'hint' }, tr('lastScan', null, 'Last scan'), ' ', lastScanCell(beacon.id)),
        el('button', {
          type: 'button', class: 'btn btn-sm', text: tr('testBeacon', null, 'Test'),
          onclick: () => testBeacon(beacon, result),
        }),
        el('button', {
          type: 'button', class: 'btn btn-sm btn-danger row-remove', text: tr('remove', null, 'Remove'),
          onclick: () => {
            const name = beacon.name || beacon.url || '?';
            if (!confirm(tr('beaconRemoveConfirm', name, 'Remove the beacon "$1"?'))) return;
            draft.beacons = draft.beacons.filter((b) => b !== beacon);
            markDirty();
            drawManualRows();
            renderStatus();
          },
        })),
      result);
  }));
  $('watchEmpty').hidden = draft.beacons.length > 0;
}

function showTestResult(node, text, kind) {
  node.textContent = text;
  node.className = `test-result hint${kind ? ` is-${kind}` : ''}`;
}

async function testBeacon(beacon, out) {
  const url = String(beacon.url || '').trim();
  if (!url) {
    showTestResult(out, tr('testUrlRequired', null, 'URL required'), 'error');
    return;
  }
  showTestResult(out, tr('testRunning', null, 'Opening the page…'), '');
  try {
    const answer = await chrome.runtime.sendMessage({ type: 'test-scrape', url });
    if (!answer || answer.ok === false) {
      showTestResult(out, tr('testFailed', (answer && answer.error) || '?', 'Test failed: $1'), 'error');
      return;
    }
    const jobs = Array.isArray(answer.jobs) ? answer.jobs : [];
    showTestResult(out, jobs.length
      ? tr('testFound', [jobs.length, (jobs[0] && jobs[0].title) || '?'], '$1 offers read. First: $2')
      : tr('testNone', null, 'No offers read from the page.'), jobs.length ? 'ok' : 'warn');
  } catch (e) {
    showTestResult(out, tr('testFailed', errorText(e), 'Test failed: $1'), 'error');
  }
}

// Segment button id: profileAiBtn / profileBackendBtn for the built-ins (as
// in the HTML), profile<Id>Btn for a declared family.
function segmentId(id) {
  return `profile${id.split('_').map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join('')}Btn`;
}

function renderSegment() {
  $('profileSegment').replaceChildren(...state.families.map((family) => {
    const builtin = BUILTIN_LABEL_KEYS[family.id];
    return el('button', {
      id: segmentId(family.id),
      type: 'button',
      class: 'segment',
      'aria-pressed': state.profile === family.id ? 'true' : 'false',
      'data-i18n': builtin && family.label === builtin[1] ? builtin[0] : undefined,
      'data-family': family.id,
      text: familyLabel(family),
      onclick: () => selectProfile(family.id),
    });
  }));
}

function renderFilter() {
  const current = state.draft.filters[state.profile];
  renderSegment();
  $('filterEnabled').checked = current.enabled;
  $('filterInclude').value = current.include;
  $('filterExclude').value = current.exclude;
}

function renderConnection() {
  $('intervalInput').value = state.draft.interval;
  $('bridgeUrlInput').value = state.draft.bridgeUrl;
  $('bridgeSecretInput').value = state.draft.bridgeSecret;
  renderIntervalWarning();
  renderConnectionState();
}

// beta4-polish S4: instead of an empty secret field, what the bot answered
// (GET /config with the stored secret); the manual secret is under Advanced.
function renderConnectionState() {
  const node = $('connectionState');
  const connection = state.connection || { kind: 'none' };
  let text;
  let tone = 'warn';
  switch (connection.kind) {
    case 'ok':
      text = tr('connStateOk', null, '✅ Connected to your bot');
      tone = 'ok';
      break;
    case 'rejected':
      text = tr('connStateRejected', null, '⚠️ Secret rejected — send /connect for a new code');
      break;
    case 'unreachable':
      text = tr('connStateUnreachable', null, 'Bot not reachable — is `uv run bot.py` running?');
      break;
    case 'http':
      text = tr('connStateHttp', connection.status, 'The bot answered with HTTP $1 — check the bridge URL under Advanced.');
      break;
    case 'badUrl':
      text = tr('connStateBadUrl', null, 'The bridge URL is not valid — fix it under Advanced.');
      break;
    case 'checking':
      text = tr('connStateChecking', null, 'Checking the connection…');
      tone = '';
      break;
    default:
      text = tr('connStateNone', null, 'Not connected — send /connect to the bot and paste the code here');
  }
  node.textContent = text;
  node.className = `conn-state${tone ? ` is-${tone}` : ''}`;
}

function openAdvanced() {
  $('bridgeAdvanced').setAttribute('open', '');
}

function renderIntervalWarning() {
  $('intervalWarning').hidden = readInterval(state.draft.interval) >= WARN_INTERVAL;
}

function renderBotInfo() {
  const node = $('botInfo');
  const config = state.botConfig;
  if (!config) {
    node.hidden = true;
    return;
  }
  const at = config.at ? new Date(config.at).toLocaleString() : '—';
  const portals = Array.isArray(config.enabledPortals)
    ? config.enabledPortals.join(', ') || tr('botNoPortals', null, 'none')
    : tr('botAllPortals', null, 'all');
  node.textContent = tr('botInfo', [at, portals], 'Bot settings read at $1. Portals enabled on the bot: $2.');
  node.hidden = false;
}

// --- profile (onboarding-v2 T10) ---------------------------------------------------

// Sends one request to the bot's /profile; {status, payload} (status 0:
// the bot is unreachable).
async function profileRequest(init) {
  const url = profileUrl(state.draft.bridgeUrl);
  const secret = state.draft.bridgeSecret.trim();
  let response;
  try {
    response = await fetchWithTimeout(url, {
      ...init, headers: { ...(init.headers || {}), [SECRET_HEADER]: secret },
    });
  } catch (_) {
    return { status: 0, payload: null };
  }
  let payload = null;
  try {
    payload = await response.json();
  } catch (_) {
    payload = null;
  }
  return { status: response.status, payload };
}

function drawProfile(profile) {
  const root = $('profileEditor');
  mountProfileEditor(root, profile, {
    el,
    tr,
    toast,
    post: (body) => profileRequest({
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }),
    onSaved: drawProfile,
  });
  root.hidden = false;
  $('profileEmpty').hidden = true;
}

async function loadProfile() {
  if (!profileUrl(state.draft.bridgeUrl) || !state.draft.bridgeSecret.trim()) {
    toast(tr('suggestNeedsBridge', null, 'Set a valid bridge URL and the secret first.'), 'error');
    return;
  }
  const button = $('profileLoadBtn');
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  try {
    const { status, payload } = await profileRequest({ method: 'GET' });
    const profile = status === 200 ? parseProfile(payload) : null;
    if (profile) {
      drawProfile(profile);
    } else if (status === 0) {
      toast(tr('suggestUnreachable', null, 'The bot is not reachable. Is it running?'), 'error');
    } else if (status === 401) {
      toast(tr('suggestSecretRejected', null, 'The bot rejected the secret.'), 'error');
    } else if (status === 503) {
      toast(tr('profileNotReady', null, 'The bot has no editable profile yet: finish /setup in the bot chat first.'), 'error');
    } else {
      toast(tr('suggestHttpError', status, 'The bot answered with HTTP $1.'), 'error');
    }
  } finally {
    button.disabled = false;
    button.removeAttribute('aria-busy');
  }
}

// --- pairing with a one-time code (beta3-ux) ------------------------------------------

// Trades the code /connect gave for the bridge secret (POST /pair) and stores
// it exactly where a typed secret goes (settings.bridgeSecret), with the URL.
async function pairWithCode() {
  const code = $('pairCodeInput').value.trim();
  if (!code) {
    toast(tr('pairNeedsCode', null, 'Type the code the bot sent for /connect first.'), 'error');
    return;
  }
  const urlProblem = bridgeUrlProblem(state.draft.bridgeUrl);
  const bridgeUrl = bridgeUrlToStore(state.draft.bridgeUrl);
  const url = urlProblem ? null : pairUrl(bridgeUrl);
  if (!url) {
    openAdvanced();
    toast(urlProblem || tr('pairNeedsUrl', null, 'Type a valid bridge URL first, for example http://127.0.0.1:8765/bridge.'), 'error');
    return;
  }
  const button = $('pairBtn');
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  try {
    let response;
    try {
      response = await fetchWithTimeout(url, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code }),
      });
    } catch (_) {
      toast(tr('suggestUnreachable', null, 'The bot is not reachable. Is it running?'), 'error');
      return;
    }
    let payload = null;
    try {
      payload = await response.json();
    } catch (_) {
      payload = null;
    }
    const secret = payload && typeof payload.secret === 'string' ? payload.secret.trim() : '';
    if (response.status === 401) {
      toast(tr('pairRejected', null, 'The code is wrong, used or expired: send /connect to the bot for a new one.'), 'error');
      return;
    }
    if (!response.ok || !secret) {
      toast(tr('suggestHttpError', response.status, 'The bot answered with HTTP $1.'), 'error');
      return;
    }
    const fresh = asObject((await chrome.storage.local.get('settings')).settings);
    const settings = { ...fresh, bridgeUrl, bridgeSecret: secret };
    await chrome.storage.local.set({ settings });
    state.stored = { ...state.stored, settings };
    state.draft.bridgeUrl = bridgeUrl;
    state.draft.bridgeSecret = secret;
    $('pairCodeInput').value = '';
    renderConnection();
    renderStatus();
    await notifyWorker();
    toast(tr('pairDone', null, 'Paired: the bridge secret is saved.'), 'ok');
    await fetchBotConfig();
  } finally {
    button.disabled = false;
    button.removeAttribute('aria-busy');
  }
}

// --- suggestions -----------------------------------------------------------------

async function suggestSearches() {
  const url = suggestionsUrl(state.draft.bridgeUrl);
  const secret = state.draft.bridgeSecret.trim();
  if (!url || !secret) {
    toast(tr('suggestNeedsBridge', null, 'Set a valid bridge URL and the secret first.'), 'error');
    return;
  }
  const button = $('suggestBtn');
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  try {
    let response;
    try {
      response = await fetchWithTimeout(url, { headers: { [SECRET_HEADER]: secret } });
    } catch (_) {
      toast(tr('suggestUnreachable', null, 'The bot is not reachable. Is it running?'), 'error');
      return;
    }
    if (response.status === 401) {
      toast(tr('suggestSecretRejected', null, 'The bot rejected the secret.'), 'error');
      return;
    }
    if (!response.ok) {
      toast(tr('suggestHttpError', response.status, 'The bot answered with HTTP $1.'), 'error');
      return;
    }
    let payload = null;
    try {
      payload = await response.json();
    } catch (_) {
      payload = null;
    }
    const data = await adoptProfileTitleFilters(chrome.storage.local, payload);
    state.stored = {
      ...state.stored, profileTitleFilters: data.profileTitleFilters, profileFamilies: data.profileFamilies,
    };
    // The bot's family list (role families) rebuilds the selects and the segment.
    state.families = resolveFamilies(data);
    state.profile = normalizeProfile(state.profile, state.families);
    const rows = parseSuggestions(payload, state.families);
    const resolved = resolveTitleFilters(data);
    // Without saved filters the editor shows the profile's new defaults now.
    const saved = asObject(data.familyTitleFilters);
    for (const [id, filter] of Object.entries(resolved)) {
      const kept = id === 'ai' ? isObject(data.titleFilter)
        : id === 'backend' ? isObject(data.titleFilterBackend)
          : isObject(saved[id]) || Boolean(state.draft.filters[id]);
      if (!kept) state.draft.filters[id] = filterDraft(filter);
    }
    renderFilter();
    renderSearches();
    drawManualRows();
    state.suggestions = rows.map((search) => ({ search, selected: true }));
    renderSuggestions();
    toast(rows.length
      ? tr('suggestFound', rows.length, '$1 suggestions from your profile.')
      : tr('suggestNone', null, 'The bot has no suggestions for your profile.'), rows.length ? 'ok' : 'info');
  } finally {
    button.disabled = false;
    button.removeAttribute('aria-busy');
  }
}

function renderSuggestions() {
  $('suggestPanel').hidden = state.suggestions.length === 0;
  $('suggestList').replaceChildren(...state.suggestions.map((item) => {
    const s = item.search;
    const details = [s.keywords, s.location, s.portals.join(', ')].filter(Boolean).join(' · ');
    return el('li', { class: 'suggest-item' },
      el('label', { class: 'check' },
        el('input', {
          type: 'checkbox', checked: item.selected,
          onchange: (e) => { item.selected = Boolean(e.currentTarget.checked); },
        }),
        el('span', { class: 'suggest-name', text: s.name || s.keywords }),
        el('span', { class: 'muted', text: details })));
  }));
}

function addSuggested() {
  const picked = state.suggestions.filter((item) => item.selected).map((item) => item.search);
  if (!picked.length) {
    toast(tr('suggestNothingSelected', null, 'Select at least one suggestion.'), 'error');
    return;
  }
  const { searches, added, skipped } = mergeSuggestedSearches(state.draft.searches, picked, newSearchId, state.families);
  state.draft.searches = searches;
  state.suggestions = [];
  renderSuggestions();
  renderSearches();
  renderStatus();
  if (added) markDirty();
  toast(tr('suggestAdded', [added, skipped], 'Added $1, skipped $2 already present. Save to keep them.'), 'ok');
}

// --- save ------------------------------------------------------------------------

// First failure wins; null when the draft can be stored.
function validate() {
  const urlProblem = bridgeUrlProblem(state.draft.bridgeUrl);
  if (urlProblem) {
    openAdvanced();
    return urlProblem;
  }
  const names = new Set();
  for (const [i, beacon] of state.draft.beacons.entries()) {
    const name = beacon.name.trim();
    if (beacon.enabled && (!name || !beacon.url.trim())) {
      return tr('errorBeaconIncomplete', i + 1, 'Beacon $1 needs a name and a URL.');
    }
    if (name && names.has(name)) return tr('errorBeaconDuplicate', name, 'Two beacons are called "$1": names must be unique.');
    if (name) names.add(name);
  }
  for (const [i, search] of state.draft.searches.entries()) {
    if (!String(search.name || '').trim() || !String(search.keywords || '').trim()) {
      return tr('errorSearchIncomplete', i + 1, 'Search $1 needs a name and keywords.');
    }
  }
  return null;
}

function currentFilters() {
  return {
    ai: parseFilterDraft(state.draft.filters.ai),
    backend: parseFilterDraft(state.draft.filters.backend),
  };
}

// The filters of the declared (non built-in) families, stored under
// `familyTitleFilters`; null when the profile has none (built-ins only).
function currentFamilyFilters() {
  const out = {};
  for (const { id } of state.families) {
    if (!isBuiltinFamily(id) && state.draft.filters[id]) out[id] = parseFilterDraft(state.draft.filters[id]);
  }
  return Object.keys(out).length ? out : null;
}

let saving = false;
async function save() {
  if (saving) return;
  const problem = validate();
  if (problem) {
    toast(problem, 'error');
    return;
  }
  saving = true;
  const button = $('saveBtn');
  button.disabled = true;
  try {
    const fresh = asObject((await chrome.storage.local.get('settings')).settings);
    const draft = state.draft;
    const settings = {
      intervalMinutes: readInterval(draft.interval),
      paused: fresh.paused === true,
      bridgeUrl: bridgeUrlToStore(draft.bridgeUrl),
      bridgeSecret: draft.bridgeSecret.trim(),
    };
    const beacons = draft.beacons.map((b) => ({
      id: b.id, name: b.name.trim(), url: b.url.trim(), enabled: b.enabled,
      filterProfile: normalizeProfile(b.filterProfile, state.families),
    }));
    const searches = dedupeSearchIds(normalizeSearches(draft.searches, state.families), newSearchId);
    const filters = currentFilters();
    const values = { settings, beacons, searches, titleFilter: filters.ai, titleFilterBackend: filters.backend };
    // Declared families' filters join the ones stored for families the bot no
    // longer declares, so switching profiles never loses an edit.
    const familyFilters = currentFamilyFilters();
    if (familyFilters) {
      const stored = asObject((await chrome.storage.local.get('familyTitleFilters')).familyTitleFilters);
      values.familyTitleFilters = { ...stored, ...familyFilters };
    }
    await chrome.storage.local.set(values);
    const connectionChanged = settings.bridgeUrl !== asObject(state.stored.settings).bridgeUrl
      || settings.bridgeSecret !== String(asObject(state.stored.settings).bridgeSecret || '');
    state.stored = { ...state.stored, settings };
    draft.interval = String(settings.intervalMinutes);
    draft.bridgeUrl = settings.bridgeUrl;
    draft.searches = searches;
    renderConnection();
    await notifyWorker();
    if (connectionChanged) await fetchBotConfig();
    setSaveState(tr('saveStateSaved', null, 'Saved'));
    setTimeout(() => {
      if ($('saveState').textContent === tr('saveStateSaved', null, 'Saved')) setSaveState('');
    }, 2500);
  } catch (e) {
    toast(tr('saveFailed', errorText(e), 'Could not save: $1'), 'error');
  } finally {
    saving = false;
    button.disabled = false;
  }
}

// --- export / import -------------------------------------------------------------

function exportConfig() {
  const draft = state.draft;
  const filters = currentFilters();
  const config = {
    version: CONFIG_VERSION,
    format: CONFIG_FORMAT,
    exportedAt: new Date().toISOString(),
    settings: { intervalMinutes: readInterval(draft.interval), bridgeUrl: parseBridgeUrl(draft.bridgeUrl).url || draft.bridgeUrl.trim() },
    beacons: draft.beacons.map((beacon) => cleanBeacon(beacon)),
    searches: normalizeSearches(draft.searches, state.families),
    titleFilter: filters.ai,
    titleFilterBackend: filters.backend,
  };
  // The interface language, only when one is forced: an `auto` file is the
  // same as before and older versions ignore the key.
  const language = normalizeUiLanguage(state.stored && state.stored.uiLanguage);
  if (language !== 'auto') config.uiLanguage = language;
  // Declared families' filters (role families); absent for the built-ins, so
  // such a file is the same as before. Older versions ignore the key.
  const familyFilters = currentFamilyFilters();
  if (familyFilters) config.familyTitleFilters = familyFilters;
  const blob = new Blob([JSON.stringify(config, null, 2)], { type: 'application/json' });
  const href = URL.createObjectURL(blob);
  const now = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const link = document.createElement('a');
  link.href = href;
  link.download = `lazyhunt-config-${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
  toast(tr('exportDone', null, 'Configuration exported (without the secret).'), 'ok');
}

function pickImportFile() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = 'application/json,.json';
  input.addEventListener('change', async () => {
    const file = input.files && input.files[0];
    if (!file) return;
    try {
      await importConfig(await file.text());
    } catch (e) {
      toast(tr('importFailed', errorText(e), 'Import failed: $1'), 'error');
    }
  });
  input.click();
}

async function importConfig(text) {
  let config;
  try {
    config = JSON.parse(text);
  } catch (_) {
    toast(tr('importInvalidJson', null, 'This file is not valid JSON.'), 'error');
    return;
  }
  if (!isObject(config) || config.format !== CONFIG_FORMAT) {
    toast(tr('importWrongFormat', null, 'This is not a LazyHunt configuration file.'), 'error');
    return;
  }
  const current = await chrome.storage.local.get([
    'settings', 'profileTitleFilters', 'profileFamilies', 'familyTitleFilters',
  ]);
  // Families a file names that this profile does not declare become the
  // default family (role families S5), and the page says so.
  const families = resolveFamilies(current);
  const known = new Set(families.map((f) => f.id));
  const unknown = new Set();
  const noteUnknown = (raw) => {
    const id = isObject(raw) ? raw.filterProfile : undefined;
    if (typeof id === 'string' && id && !known.has(id)) unknown.add(id);
  };
  const rawBeacons = (Array.isArray(config.beacons) ? config.beacons : []).filter(isObject);
  rawBeacons.forEach(noteUnknown);
  const beacons = rawBeacons.map((b) => cleanBeacon(b, families));
  if (!confirm(tr('importConfirm', beacons.length, 'Replace the current configuration with the $1 beacons of this file?'))) return;

  const here = asObject(current.settings);
  const fileSettings = asObject(config.settings);
  // beta4-polish S4: the file's bridge URL is stored clean; one that is not a
  // bridge URL is not stored (the current one is kept) and the page says so.
  const fileUrl = parseBridgeUrl(typeof fileSettings.bridgeUrl === 'string' ? fileSettings.bridgeUrl : '');
  const values = {
    settings: {
      intervalMinutes: readInterval(fileSettings.intervalMinutes),
      paused: here.paused === true,
      bridgeUrl: fileUrl.error ? String(here.bridgeUrl || '') : fileUrl.url,
      bridgeSecret: String(here.bridgeSecret || ''),
    },
    beacons,
  };
  // An old export without a language keeps the stored one.
  if (config.uiLanguage === 'en' || config.uiLanguage === 'es' || config.uiLanguage === 'auto') {
    values.uiLanguage = config.uiLanguage;
  }
  // An old export without searches or filters keeps the stored ones.
  if (Array.isArray(config.searches)) {
    config.searches.forEach(noteUnknown);
    values.searches = dedupeSearchIds(normalizeSearches(config.searches, families), newSearchId);
  }
  const defaults = resolveTitleFilters({
    profileTitleFilters: current.profileTitleFilters, profileFamilies: current.profileFamilies,
  });
  if (isObject(config.titleFilter)) values.titleFilter = cleanTitleFilter(config.titleFilter, defaults.ai);
  if (isObject(config.titleFilterBackend)) {
    values.titleFilterBackend = cleanTitleFilter(config.titleFilterBackend, defaults.backend);
  }
  // Declared families' filters: only those of a family this profile declares.
  const fileFamilies = asObject(config.familyTitleFilters);
  const imported = {};
  for (const { id } of families) {
    if (!isBuiltinFamily(id) && isObject(fileFamilies[id])) imported[id] = cleanTitleFilter(fileFamilies[id], defaults[id]);
  }
  if (Object.keys(imported).length) {
    values.familyTitleFilters = { ...asObject(current.familyTitleFilters), ...imported };
  }
  await chrome.storage.local.set(values);
  await notifyWorker();
  if (values.uiLanguage) {
    await applyUiLanguage(values.uiLanguage);
    localizeStatic();
  }
  await load();
  setSaveState('');
  let done = tr('importDone', beacons.length, 'Imported $1 beacons.');
  if (fileUrl.error) {
    done = `${done} ${tr('importBridgeUrlKept', null, 'The bridge URL of the file is not valid: the current one was kept.')}`;
  }
  if (!unknown.size) {
    toast(done, 'ok');
    return;
  }
  const fallback = families.find((f) => f.id === defaultFamilyId(families));
  toast(`${done} ${tr('importUnknownFamilies', [[...unknown].join(', '), familyLabel(fallback)],
    'Profiles this bot does not declare ($1) now use $2.')}`, 'info');
}

// --- maintenance -----------------------------------------------------------------

async function clearHistory() {
  if (!confirm(tr('clearHistoryConfirm', null, 'Clear the seen offers of every beacon? The next scan will send every offer it finds to the bot again.'))) return;
  try {
    const answer = await chrome.runtime.sendMessage({ type: 'clear-history' });
    if (answer && answer.ok) toast(tr('clearHistoryDone', null, 'History cleared.'), 'ok');
    else toast(tr('clearHistoryFailed', (answer && answer.error) || '?', 'Could not clear the history: $1'), 'error');
  } catch (e) {
    toast(tr('clearHistoryFailed', errorText(e), 'Could not clear the history: $1'), 'error');
  }
}

// The role families on the bot's /config answer (also adopted by every
// round): a changed list rebuilds the selects and the segment; filters the
// owner is editing are kept, new families start from their defaults.
async function adoptConfigFamilies(payload) {
  await adoptProfileFamilies(chrome.storage.local, payload);
  const data = await chrome.storage.local.get([
    'settings', 'titleFilter', 'titleFilterBackend', 'profileTitleFilters', 'profileFamilies', 'familyTitleFilters',
  ]);
  const families = resolveFamilies(data);
  if (JSON.stringify(families) === JSON.stringify(state.families)) return;
  state.stored = { ...state.stored, profileTitleFilters: data.profileTitleFilters, profileFamilies: data.profileFamilies };
  state.families = families;
  state.profile = normalizeProfile(state.profile, families);
  for (const [id, filter] of Object.entries(resolveTitleFilters(data))) {
    if (!state.draft.filters[id]) state.draft.filters[id] = filterDraft(filter);
  }
  renderFilter();
  renderSearches();
  drawManualRows();
}

function setConnection(connection) {
  state.connection = connection;
  renderConnectionState();
}

// GET /config with the stored secret: the bot's settings for the page, and
// the connection state shown instead of the secret (beta4-polish S4).
async function fetchBotConfig() {
  const settings = asObject(state.stored.settings);
  const secret = String(settings.bridgeSecret || '').trim();
  if (!secret) {
    setConnection({ kind: 'none' });
    return;
  }
  const bridgeUrl = healedBridgeUrl(settings.bridgeUrl);
  const url = parseBridgeUrl(bridgeUrl).error ? null : configUrl(bridgeUrl);
  if (!url) {
    setConnection({ kind: 'badUrl' });
    return;
  }
  setConnection({ kind: 'checking' });
  let response;
  try {
    response = await fetchWithTimeout(url, { headers: { [SECRET_HEADER]: secret } });
  } catch (_) {
    setConnection({ kind: 'unreachable' });
    return;
  }
  if (response.status === 401 || response.status === 403) {
    setConnection({ kind: 'rejected' });
    return;
  }
  if (!response.ok) {
    setConnection({ kind: 'http', status: response.status });
    return;
  }
  setConnection({ kind: 'ok' });
  try {
    const payload = await response.json();
    const parsed = parseBotConfig(payload);
    if (!parsed) return;
    const botConfig = { ...parsed, at: Date.now() };
    state.botConfig = botConfig;
    await chrome.storage.local.set({ botConfig });
    await adoptConfigFamilies(payload);
    renderPortals();
    renderStatus();
    renderBotInfo();
  } catch (_) {
    // Silent: the page works without the bot.
  }
}

// --- wiring ----------------------------------------------------------------------

function addBeacon() {
  state.draft.beacons.push({ id: newBeaconId(), name: '', url: '', enabled: true, filterProfile: defaultFamilyId(state.families) });
  markDirty();
  drawManualRows();
}

function addSearch() {
  state.draft.searches.push({
    id: newSearchId(), name: '', keywords: '', location: '', filterProfile: defaultFamilyId(state.families),
    portals: state.enabledPortals.includes('linkedin') ? ['linkedin'] : [], titleFilter: 'on',
  });
  markDirty();
  renderSearches();
}

function selectProfile(profile) {
  state.profile = profile;
  renderFilter();
}

function toggleSecret() {
  const input = $('bridgeSecretInput');
  const button = $('toggleSecretBtn');
  const show = input.type === 'password';
  input.type = show ? 'text' : 'password';
  button.setAttribute('aria-pressed', show ? 'true' : 'false');
  button.textContent = show ? tr('secretHide', null, 'Hide') : tr('secretShow', null, 'Show');
}

function onStorageChanged(changes, area) {
  if (area !== 'local' || !state.draft) return;
  if (changes.settings) {
    state.stored = { ...state.stored, settings: changes.settings.newValue };
    state.paused = asObject(changes.settings.newValue).paused === true;
    renderStatus();
  }
  if (changes.lastCheck) {
    state.lastCheck = asObject(changes.lastCheck.newValue);
    refreshLastScan();
  }
  if (changes.botConfig) {
    state.botConfig = isObject(changes.botConfig.newValue) ? changes.botConfig.newValue : null;
    renderPortals();
    renderStatus();
    renderBotInfo();
  }
  if (changes.enabledPortals && Array.isArray(changes.enabledPortals.newValue)) {
    const next = changes.enabledPortals.newValue;
    if (JSON.stringify(next) !== JSON.stringify(state.enabledPortals)) {
      state.enabledPortals = [...next];
      renderPortals();
      renderSearches();
      renderStatus();
    }
  }
}

function wire() {
  const draft = () => state.draft;
  $('saveBtn').addEventListener('click', save);
  $('addSearchBtn').addEventListener('click', addSearch);
  $('suggestBtn').addEventListener('click', suggestSearches);
  $('profileLoadBtn').addEventListener('click', loadProfile);
  $('addSuggestedBtn').addEventListener('click', addSuggested);
  $('addBeaconBtn').addEventListener('click', addBeacon);
  $('filterEnabled').addEventListener('change', (e) => {
    draft().filters[state.profile].enabled = Boolean(e.currentTarget.checked);
    markDirty();
  });
  $('filterInclude').addEventListener('input', (e) => {
    draft().filters[state.profile].include = e.currentTarget.value;
    markDirty();
  });
  $('filterExclude').addEventListener('input', (e) => {
    draft().filters[state.profile].exclude = e.currentTarget.value;
    markDirty();
  });
  $('intervalInput').addEventListener('input', (e) => {
    draft().interval = e.currentTarget.value;
    renderIntervalWarning();
    markDirty();
  });
  $('bridgeUrlInput').addEventListener('input', (e) => {
    draft().bridgeUrl = e.currentTarget.value;
    renderStatus();
    markDirty();
  });
  $('bridgeSecretInput').addEventListener('input', (e) => {
    draft().bridgeSecret = e.currentTarget.value;
    renderStatus();
    markDirty();
  });
  $('toggleSecretBtn').addEventListener('click', toggleSecret);
  $('pairBtn').addEventListener('click', pairWithCode);
  $('clearHistoryBtn').addEventListener('click', clearHistory);
  for (const button of $('languageSegment').querySelectorAll('[data-lang]')) {
    button.addEventListener('click', () => setLanguage(button.getAttribute('data-lang')));
  }
  $('exportBtn').addEventListener('click', exportConfig);
  $('importBtn').addEventListener('click', pickImportFile);
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && String(e.key).toLowerCase() === 's') {
      e.preventDefault();
      save();
    }
  });
  chrome.storage.onChanged.addListener(onStorageChanged);
}

async function init() {
  await loadUiLanguage();
  localizeStatic();
  wire();
  await load();
  await fetchBotConfig();
}

init();
