// The scan round (spec 4): gates, the one-time seen-state recovery, one pass
// over the planned beacons in a single hidden tab, then one write of the
// round's state. Only one round runs at a time per service worker instance.

import { t, applyUiLanguage } from '../i18n.js';
import { normalizeProfile, resolveFamilies, resolveTitleFilters } from '../filter.js';
import { autoBeaconId, normalizeSearches } from '../searches.js';
import { reportScan, fetchBotConfig } from './bot_bridge.js';
import { planBeacons, portalOfBeacon, noPermissionEntry } from './beacon_plan.js';
import { scanPage, SessionExpiredError } from './page_scan.js';
import { deliver } from './delivery.js';
import { ScrapeTab } from './scrape_tab.js';
import {
  readKeys, writeKeys, readSettings, isPlainObject, bridgeConfigured,
} from './storage.js';
import { BADGE_COLOR, NOTIFICATION_ICON, BETWEEN_BEACONS_MS } from './constants.js';
import { sleep, randomBetween, errorMessage, log, warn } from './util.js';

let roundRunning = false;

export function isRoundRunning() {
  return roundRunning;
}

function emptySummary(extra = {}) {
  return {
    skipped: false,
    beaconsScanned: 0,
    totalJobsFound: 0,
    totalNew: 0,
    firstScans: 0,
    errors: 0,
    filteredOut: 0,
    ...extra,
  };
}

const asObject = (value) => (isPlainObject(value) ? value : {});

// Once per install: the seen state of auto beacons of portals other than
// LinkedIn is forgotten (those offers never reached the bot), and the
// cleared beacons are listed in `recoveringBeacons` (spec 2.11).
async function recoverPortalSeenStateOnce() {
  const data = await readKeys(['portalSeenRecovery', 'seenJobs', 'detailAttempts', 'recoveringBeacons']);
  if (data.portalSeenRecovery !== undefined) return;
  const seenJobs = { ...asObject(data.seenJobs) };
  const detailAttempts = { ...asObject(data.detailAttempts) };
  const cleared = new Set();
  const isRecoverable = (id) => id.startsWith('auto:') && id.split(':').pop() !== 'linkedin';
  for (const map of [seenJobs, detailAttempts]) {
    for (const id of Object.keys(map).filter(isRecoverable)) {
      delete map[id];
      cleared.add(id);
    }
  }
  if (!cleared.size) {
    await writeKeys({ portalSeenRecovery: true });
    return;
  }
  const recovering = Array.isArray(data.recoveringBeacons) ? data.recoveringBeacons : [];
  await writeKeys({
    seenJobs,
    detailAttempts,
    recoveringBeacons: [...new Set([...recovering, ...cleared])],
    portalSeenRecovery: true,
  });
}

const ROUND_KEYS = [
  'settings', 'beacons', 'searches', 'enabledPortals', 'titleFilter', 'titleFilterBackend',
  'profileTitleFilters', 'profileFamilies', 'familyTitleFilters',
  'seenJobs', 'detailAttempts', 'recoveringBeacons', 'lastCheck', 'stats', 'uiLanguage',
];

// Auto beacon id -> the family of its search, normalized against the
// profile's family list (role families: a declared id such as 'frontend' is
// kept, an unknown one becomes the default family).
function searchFamilies(searches, families) {
  const out = new Map();
  for (const search of normalizeSearches(searches, families)) {
    for (const portalId of search.portals) {
      const id = autoBeaconId(search, portalId);
      if (!out.has(id)) out.set(id, search.filterProfile); // first wins, like expandSearches
    }
  }
  return out;
}

// The beacon as delivered: its `filterProfile` is the family whose title
// filter applies and that the bot receives as the payload `profile`.
function withFamily(beacon, state) {
  const fromSearch = beacon.auto ? state.searchFamilies.get(beacon.id) : undefined;
  const filterProfile = fromSearch || normalizeProfile(beacon.filterProfile, state.families);
  return { ...beacon, filterProfile };
}

async function loadRoundState() {
  const data = await readKeys(ROUND_KEYS);
  await applyUiLanguage(data.uiLanguage);
  const families = resolveFamilies(data);
  return {
    ...data,
    families,
    searchFamilies: searchFamilies(data.searches, families),
    filters: resolveTitleFilters(data),
    seenJobs: { ...asObject(data.seenJobs) },
    detailAttempts: { ...asObject(data.detailAttempts) },
    recovering: new Set(Array.isArray(data.recoveringBeacons) ? data.recoveringBeacons : []),
    lastCheck: { ...asObject(data.lastCheck) },
  };
}

async function notifySessionExpired(portal) {
  try {
    await chrome.notifications.create({
      type: 'basic',
      iconUrl: NOTIFICATION_ICON,
      title: t('sw_notification_session_title', [portal.label], `${portal.label} session expired`),
      message: t(
        'sw_notification_session_message',
        [portal.label],
        `Open ${portal.label} in this browser and sign in again; the scans are stopped until then.`,
      ),
      priority: 2,
    });
  } catch (error) {
    warn('could not show the session notification:', String(error));
  }
}

async function updateBadge(lastCheck) {
  const total = Object.values(lastCheck).reduce((sum, entry) => sum + (Number(entry && entry.newJobs) || 0), 0);
  try {
    await chrome.action.setBadgeBackgroundColor({ color: BADGE_COLOR });
    await chrome.action.setBadgeText({ text: total ? String(total) : '' });
  } catch (error) {
    warn('could not update the badge:', String(error));
  }
}

// Scans one beacon and records its outcome in `state`. Returns false when the
// round must stop (the portal's session expired).
async function scanBeacon({ tab, beacon, settings, state, summary }) {
  const portal = portalOfBeacon(beacon);
  const at = () => Date.now();
  let reported = false;
  try {
    const { cards, noResults } = await scanPage(tab, beacon.url, portal);
    await reportScan(settings, beacon, { jobs: cards, noResults });
    reported = true;
    const seenList = Array.isArray(state.seenJobs[beacon.id]) ? state.seenJobs[beacon.id].map(String) : [];
    const result = await deliver({
      tab, portal, beacon: withFamily(beacon, state), cards, settings,
      filters: state.filters,
      seenList,
      attempts: asObject(state.detailAttempts[beacon.id]),
    });
    state.seenJobs[beacon.id] = result.seenList;
    if (Object.keys(result.attempts).length) state.detailAttempts[beacon.id] = result.attempts;
    else delete state.detailAttempts[beacon.id];
    if (result.drained) state.recovering.delete(beacon.id);
    state.lastCheck[beacon.id] = {
      at: at(), status: 'ok', jobsFound: cards.length, newJobs: result.newJobs, filteredOut: result.filteredOut,
    };
    summary.beaconsScanned += 1;
    summary.totalJobsFound += cards.length;
    summary.totalNew += result.newJobs;
    summary.filteredOut += result.filteredOut;
    if (!seenList.length) summary.firstScans += 1;
    return true;
  } catch (error) {
    if (!reported) await reportScan(settings, beacon, { error });
    summary.errors += 1;
    if (error instanceof SessionExpiredError) {
      warn(`"${beacon.name}": ${error.userMessage}; round stopped`);
      state.lastCheck[beacon.id] = { at: at(), status: 'session_expired', error: error.userMessage };
      await notifySessionExpired(error.portal);
      return false;
    }
    warn(`"${beacon.name}" failed:`, errorMessage(error));
    state.lastCheck[beacon.id] = { at: at(), status: 'error', error: errorMessage(error) };
    return true;
  }
}

async function scanAll(beacons, settings, state, summary) {
  const tab = new ScrapeTab();
  try {
    for (let i = 0; i < beacons.length; i += 1) {
      const goOn = await scanBeacon({ tab, beacon: beacons[i], settings, state, summary });
      if (!goOn) break;
      if (i < beacons.length - 1) await sleep(randomBetween(BETWEEN_BEACONS_MS));
    }
  } finally {
    await tab.dispose();
  }
}

async function runGatedRound() {
  const settings = await readSettings();
  if (settings.paused) return emptySummary({ paused: true });
  // Without the bridge nothing could be delivered: scan nothing, so no offer
  // is marked seen before the bot can receive it (spec 4.2).
  if (!bridgeConfigured(settings)) {
    warn('bridge not configured: round skipped, connect the extension to the bot in Options');
    return emptySummary({ bridgeMissing: true });
  }
  const botConfig = await fetchBotConfig(settings);
  if (botConfig && botConfig.paused) return emptySummary({ botPaused: true });

  await recoverPortalSeenStateOnce();
  const state = await loadRoundState();
  const { toScan, missingGrant } = await planBeacons(state, botConfig);
  for (const { beacon, portal } of missingGrant) state.lastCheck[beacon.id] = noPermissionEntry(portal, Date.now());
  const summary = emptySummary();
  if (!toScan.length) {
    if (missingGrant.length) await writeKeys({ lastCheck: state.lastCheck });
    return summary;
  }

  await scanAll(toScan, settings, state, summary);
  const stats = asObject(state.stats);
  await writeKeys({
    seenJobs: state.seenJobs,
    detailAttempts: state.detailAttempts,
    recoveringBeacons: [...state.recovering],
    lastCheck: state.lastCheck,
    stats: { ...stats, totalNotified: Number(stats.totalNotified) || 0, lastRoundAt: Date.now() },
  });
  await updateBadge(state.lastCheck);
  log('round done', JSON.stringify(summary));
  return summary;
}

// Never rejects: a failure outside the per-beacon handling is logged and
// counted as an error.
export async function runRound() {
  if (roundRunning) return emptySummary({ skipped: true });
  roundRunning = true;
  try {
    return await runGatedRound();
  } catch (error) {
    warn('round failed:', errorMessage(error));
    return emptySummary({ errors: 1 });
  } finally {
    roundRunning = false;
  }
}
