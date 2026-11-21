// lazyhunt toolbar popup (docs/extension-spec.md 6.1, D4).
//
// Read-only view of the stored state: overall status, last and next round,
// every beacon the service worker would scan (manual beacons and the auto
// beacons derived from the searches) with its last result. It never writes to
// storage; its three actions are runtime messages to the service worker
// (`run-now`, `set-paused`) and opening the options page.

import { t, loadUiLanguage } from './i18n.js';
import { PORTALS, portalFor, portalById } from './portals/registry.js';
import { expandSearches, resolveEnabledPortals } from './searches.js';

const ALARM_NAME = 'check-jobs';
const STATE_KEYS = ['settings', 'beacons', 'searches', 'enabledPortals', 'lastCheck', 'stats', 'botConfig', 'lastRound'];

const $ = (id) => document.getElementById(id);

// t() with the English fallback filled in the same way Chrome fills $1..$n.
function tr(key, subs, english) {
  const list = subs === undefined || subs === null ? [] : [].concat(subs).map(String);
  const fallback = list.reduce((text, value, i) => text.split(`$${i + 1}`).join(value), english);
  return t(key, list.length ? list : undefined, fallback);
}

function localizeStatic() {
  document.documentElement.lang = tr('htmlLang', null, 'en');
  for (const el of document.querySelectorAll('[data-i18n]')) {
    el.textContent = tr(el.getAttribute('data-i18n'), null, el.textContent);
  }
}

// --- formatting --------------------------------------------------------------

function relativeTime(epochMs, now = Date.now()) {
  if (!epochMs || !Number.isFinite(Number(epochMs))) return '—';
  const diff = Number(epochMs) - now;
  const minutes = Math.round(Math.abs(diff) / 60000);
  let amount;
  if (minutes < 1) amount = tr('timeUnderMinute', null, '<1m');
  else if (minutes < 60) amount = tr('timeMinutes', minutes, '$1m');
  else if (minutes < 60 * 24) amount = tr('timeHours', Math.round(minutes / 60), '$1h');
  else amount = tr('timeDays', Math.round(minutes / 1440), '$1d');
  return diff > 0 ? tr('timeIn', amount, 'in $1') : tr('timeAgo', amount, '$1 ago');
}

function portalLabelOf(beacon) {
  if (beacon.auto && beacon.portal) {
    const portal = portalById(beacon.portal);
    return portal ? portal.label : beacon.portal;
  }
  return portalFor(beacon.url).label;
}

// --- state -------------------------------------------------------------------

function bridgeConfigured(settings) {
  return Boolean(String(settings.bridgeUrl || '').trim() && String(settings.bridgeSecret || '').trim());
}

function buildView(data) {
  const settings = data.settings && typeof data.settings === 'object' ? data.settings : {};
  const manual = Array.isArray(data.beacons) ? data.beacons.filter((b) => b && typeof b === 'object') : [];
  const enabledPortals = resolveEnabledPortals(data.enabledPortals, data.searches, manual, portalFor, PORTALS);
  const auto = expandSearches(data.searches, PORTALS, enabledPortals);
  const enabledSet = new Set(enabledPortals);
  const lastCheck = data.lastCheck && typeof data.lastCheck === 'object' ? data.lastCheck : {};

  const beacons = [
    ...manual.map((b) => ({
      ...b,
      enabled: b.enabled !== false,
      portalOff: !enabledSet.has(portalFor(b.url).id),
    })),
    ...auto.map((b) => ({ ...b, portalOff: false })),
  ];
  const scannable = beacons.filter((b) => b.enabled && b.url && !b.portalOff);
  const hasError = scannable.some((b) => {
    const entry = lastCheck[b.id];
    return entry && (entry.status === 'error' || entry.status === 'session_expired');
  });

  // beta3-ux: "no searches at all" (the owner must add some) differs from
  // "nothing scannable" (a portal switch or permission leaves them out).
  const searches = Array.isArray(data.searches) ? data.searches.filter((s) => s && typeof s === 'object') : [];
  const hasSearches = manual.length > 0 || searches.length > 0;

  return {
    settings,
    hasSearches,
    lastRound: data.lastRound && typeof data.lastRound === 'object' ? data.lastRound : null,
    botSetup: Boolean(data.botConfig && data.botConfig.setup === true),
    paused: settings.paused === true,
    botPaused: Boolean(data.botConfig && data.botConfig.paused === true),
    configured: bridgeConfigured(settings),
    beacons,
    scannable,
    hasError,
    lastCheck,
    stats: data.stats && typeof data.stats === 'object' ? data.stats : {},
  };
}

// Same order as the round's gates: local pause, missing bridge (nothing is
// scanned until the bot is connected; `connect` shows the call to action),
// the bot's pause, then what there is to scan.
function overallStatus(view) {
  if (view.paused) return { kind: 'warn', label: tr('statusPaused', null, 'Paused'), detail: tr('statusPausedDetail', null, 'Paused here. Resume to scan again.') };
  if (!view.configured) return { kind: 'warn', label: tr('statusNoBridge', null, 'Bot not connected'), detail: tr('statusNoBridgeDetail', null, 'Connect the extension to the bot first: Options › Schedule and bot.'), connect: true };
  if (view.botSetup) return { kind: 'warn', label: tr('statusBotSetup', null, 'Finish setup first'), detail: tr('statusBotSetupDetail', null, 'Finish /setup in the bot chat first.') };
  if (!view.hasSearches) return { kind: 'muted', label: tr('statusIdle', null, 'Idle'), detail: tr('popupNoSearches', null, 'No searches yet — open Options › Searches and press Suggest from my profile.') };
  if (view.botPaused) return { kind: 'warn', label: tr('statusBotPaused', null, 'Paused by the bot'), detail: tr('statusBotPausedDetail', null, 'The bot is paused. Resume it from the bot.') };
  if (!view.scannable.length) return { kind: 'muted', label: tr('statusIdle', null, 'Idle'), detail: tr('statusIdleNothing', null, 'Nothing to scan: enable a portal and add a search or a beacon.') };
  if (view.hasError) return { kind: 'error', label: tr('statusError', null, 'Needs attention'), detail: tr('statusErrorDetail', null, 'The last scan of some searches failed.') };
  return { kind: 'ok', label: tr('statusActive', null, 'Active'), detail: '' };
}

function beaconState(beacon, lastCheck) {
  if (!beacon.enabled) return { text: tr('beaconDisabled', null, 'Disabled'), tone: '' };
  if (beacon.portalOff) return { text: tr('beaconPortalOff', portalLabelOf(beacon), '$1 is not enabled'), tone: 'warn' };
  const entry = lastCheck[beacon.id];
  if (!entry) return { text: tr('beaconNotScanned', null, 'Not scanned yet'), tone: '' };
  switch (entry.status) {
    case 'no_permission':
      return { text: tr('beaconNoPermission', null, 'Permission missing: enable the portal in the options'), tone: 'warn' };
    case 'session_expired':
      return { text: tr('beaconSessionExpired', portalLabelOf(beacon), 'Session expired: sign in to $1 again'), tone: 'error' };
    case 'error':
      return { text: tr('beaconError', relativeTime(entry.at), 'Error · $1'), tone: 'error', title: String(entry.error || '') };
    default:
      return { text: tr('beaconJobs', [Number(entry.jobsFound) || 0, relativeTime(entry.at)], '$1 jobs · $2'), tone: '' };
  }
}

// --- rendering ---------------------------------------------------------------

function setChip(el, kind, label) {
  el.className = `chip${kind && kind !== 'muted' ? ` chip-${kind}` : ''}`;
  el.textContent = label;
}

function drawWatchItem(beacon, lastCheck) {
  const li = document.createElement('li');
  li.className = `beacon${beacon.enabled ? '' : ' is-disabled'}`;

  const body = document.createElement('div');
  body.className = 'beacon-body';
  const name = document.createElement('div');
  name.className = 'watch-name';
  name.textContent = beacon.name || beacon.url || tr('beaconUnnamed', null, '(unnamed)');
  name.title = beacon.url || '';
  const state = beaconState(beacon, lastCheck);
  const stateEl = document.createElement('div');
  stateEl.className = `beacon-state${state.tone ? ` is-${state.tone}` : ''}`;
  stateEl.textContent = state.text;
  if (state.title) stateEl.title = state.title;
  body.append(name, stateEl);
  li.append(body);

  const entry = lastCheck[beacon.id];
  const fresh = entry && entry.status === 'ok' ? Number(entry.newJobs) || 0 : 0;
  const badge = document.createElement('span');
  badge.className = `chip chip-plain beacon-new${fresh ? ' chip-accent' : ''}`;
  badge.textContent = String(fresh);
  badge.title = tr('beaconNewTitle', fresh, '$1 new in the last scan');
  li.append(badge);
  return li;
}

let view = null;
let busy = false;

async function render() {
  const data = await chrome.storage.local.get(STATE_KEYS);
  view = buildView(data);

  const status = overallStatus(view);
  setChip($('statusChip'), status.kind, status.label);
  $('statusDetail').textContent = status.detail;
  $('statusDetail').classList.toggle('is-warn', Boolean(status.connect));
  $('connectBotBtn').hidden = !status.connect;

  $('roundLast').textContent = relativeTime(view.stats.lastRoundAt);
  if (view.paused) {
    $('roundNext').textContent = tr('nextPaused', null, 'Paused');
  } else if (view.botPaused) {
    $('roundNext').textContent = tr('nextBotPaused', null, 'Paused by the bot');
  } else {
    let alarm = null;
    try {
      alarm = await chrome.alarms.get(ALARM_NAME);
    } catch (_) {
      alarm = null;
    }
    $('roundNext').textContent = alarm ? relativeTime(alarm.scheduledTime) : '—';
  }

  const list = $('watchList');
  list.replaceChildren(...view.beacons.map((b) => drawWatchItem(b, view.lastCheck)));
  $('watchCount').textContent = String(view.beacons.length);
  $('emptyState').hidden = view.beacons.length > 0;
  // No search at all: point at Options › Searches › Suggest from my profile.
  $('emptyText').textContent = view.hasSearches
    ? tr('popupEmpty', null, 'Nothing to watch yet. Add a search or a beacon in the options.')
    : tr('popupNoSearches', null, 'No searches yet — open Options › Searches and press Suggest from my profile.');
  $('openSearchesBtn').hidden = view.hasSearches;
  $('emptyOptionsBtn').hidden = !view.hasSearches;
  renderLastOutcome(view.lastRound);

  const pauseBtn = $('pauseBtn');
  pauseBtn.textContent = view.paused ? tr('popupResume', null, 'Resume') : tr('popupPause', null, 'Pause');
  pauseBtn.setAttribute('aria-pressed', view.paused ? 'true' : 'false');
  if (!busy) pauseBtn.disabled = false;
}

// The text of a round that stopped early (summary.reason, beta3-ux); '' when
// it ran.
function reasonText(reason) {
  switch (reason) {
    case 'paused': return tr('resultPaused', null, 'Paused here: nothing was scanned.');
    case 'botPaused': return tr('resultBotPaused', null, 'The bot is paused: nothing was scanned.');
    case 'botSetup': return tr('resultBotSetup', null, 'Finish /setup in the bot chat first: nothing was scanned.');
    case 'bridgeMissing': return tr('statusNoBridgeDetail', null, 'Connect the extension to the bot first: Options › Schedule and bot.');
    case 'botUnreachable': return tr('resultBotUnreachable', null, 'The bot is not reachable: is it running? Check the bridge URL in Options › Schedule and bot.');
    case 'botUnauthorized': return tr('resultBotUnauthorized', null, 'The bot rejected the secret: pair again with /connect in Options › Schedule and bot.');
    case 'noSearches': return tr('popupNoSearches', null, 'No searches yet — open Options › Searches and press Suggest from my profile.');
    case 'noPortal': return tr('resultNoPortal', null, 'Nothing to scan: enable a portal (and allow its permission) in Options › Portals.');
    case 'cancelled': return tr('resultCancelled', null, 'Stopped: paused.');
    default: return '';
  }
}

// The stored outcome of the last round (manual or scheduled), under the result line.
function renderLastOutcome(last) {
  const el = $('lastOutcome');
  if (!last || !last.at) {
    el.hidden = true;
    return;
  }
  const what = reasonText(last.reason)
    || tr('resultCounts', [Number(last.beaconsScanned) || 0, Number(last.totalNew) || 0], '$1 scanned · $2 new');
  el.textContent = tr('popupLastOutcome', [relativeTime(last.at), what], 'Last round $1: $2');
  el.hidden = false;
}

function showResult(text, isError = false) {
  const el = $('resultLine');
  el.textContent = text;
  el.className = `result${isError ? ' is-error' : ''}`;
}

function summaryText(summary) {
  const s = summary || {};
  if (s.skipped) return tr('resultSkipped', null, 'Another round is already running.');
  const reason = reasonText(s.reason);
  if (reason) return reason;
  if (s.paused) return tr('resultPaused', null, 'Paused here: nothing was scanned.');
  if (s.botPaused) return tr('resultBotPaused', null, 'The bot is paused: nothing was scanned.');
  const scanned = Number(s.beaconsScanned) || 0;
  const errors = Number(s.errors) || 0;
  if (!scanned && errors) return tr('resultOnlyErrors', errors, 'Nothing scanned: $1 errors.');
  if (!scanned) return tr('resultNothing', null, 'Nothing to scan.');
  const parts = [tr('resultCounts', [scanned, Number(s.totalNew) || 0], '$1 scanned · $2 new')];
  if (Number(s.filteredOut)) parts.push(tr('resultFiltered', s.filteredOut, '$1 filtered out'));
  if (Number(s.firstScans)) parts.push(tr('resultFirstScans', s.firstScans, '$1 first scans, sent to the bot'));
  if (errors) parts.push(tr('resultErrors', errors, '$1 errors'));
  return parts.join(' · ');
}

async function checkNow() {
  const button = $('checkNowBtn');
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  button.textContent = tr('popupChecking', null, 'Checking…');
  showResult('');
  try {
    const answer = await chrome.runtime.sendMessage({ type: 'run-now' });
    if (answer && answer.bridgeMissing) {
      showResult(String(answer.error || tr('statusNoBridgeDetail', null, 'Connect the extension to the bot first: Options › Schedule and bot.')), true);
    } else if (!answer || answer.ok === false) {
      showResult(tr('resultFailed', (answer && answer.error) || '?', 'Failed: $1'), true);
    } else {
      const s = answer.summary || {};
      // A round that stopped early is a problem to fix, shown as such.
      showResult(summaryText(s), Boolean(s.reason && s.reason !== 'paused'));
    }
  } catch (e) {
    showResult(tr('resultFailed', (e && e.message) || String(e), 'Failed: $1'), true);
  } finally {
    button.disabled = false;
    button.removeAttribute('aria-busy');
    button.textContent = tr('popupCheckNow', null, 'Check now');
    await render();
  }
}

// Only the local pause is toggled here; a pause set on the bot is lifted from
// the bot itself.
async function togglePause() {
  const button = $('pauseBtn');
  const paused = Boolean(view && view.paused);
  busy = true;
  button.disabled = true;
  try {
    const answer = await chrome.runtime.sendMessage({ type: 'set-paused', paused: !paused });
    if (!answer || answer.ok === false) {
      showResult(tr('resultFailed', (answer && answer.error) || '?', 'Failed: $1'), true);
    }
  } catch (e) {
    showResult(tr('resultFailed', (e && e.message) || String(e), 'Failed: $1'), true);
  } finally {
    busy = false;
    button.disabled = false;
    await render();
  }
}

function openOptions() {
  chrome.runtime.openOptionsPage();
}

// Options at its Searches section (where "Suggest from my profile" is).
function openSearches() {
  chrome.tabs.create({ url: chrome.runtime.getURL('options.html#searches') });
}

async function init() {
  await loadUiLanguage();
  localizeStatic();
  $('checkNowBtn').addEventListener('click', checkNow);
  $('pauseBtn').addEventListener('click', togglePause);
  $('optionsBtn').addEventListener('click', openOptions);
  $('emptyOptionsBtn').addEventListener('click', openOptions);
  $('openSearchesBtn').addEventListener('click', openSearches);
  $('connectBotBtn').addEventListener('click', openOptions);
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === 'local') render();
  });
  render();
}

init();
