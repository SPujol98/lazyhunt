// The scan round (spec 4): gates, the one-time seen-state recovery, one pass
// over the planned beacons in a single hidden tab, then one write of the
// round's state. Only one round runs at a time per service worker instance.

import { t, applyUiLanguage } from '../i18n.js';
import { normalizeProfile, resolveFamilies, resolveTitleFilters } from '../filter.js';
import { autoBeaconId, normalizeSearches } from '../searches.js';
import { reportScan, fetchBotConfig } from './bot_bridge.js';
import { RoundCancelled, beginRound, bounded, endRound, throwIfCancelled } from './cancel.js';
import { planBeacons, portalOfBeacon, noPermissionEntry } from './beacon_plan.js';
import { scanPage, SessionExpiredError } from './page_scan.js';
import { deliver } from './delivery.js';
import { ScrapeTab } from './scrape_tab.js';
import {
  readKeys, writeKeys, readSettings, isPlainObject, bridgeConfigured,
} from './storage.js';
import {
  BADGE_COLOR, BADGE_WARN_COLOR, BADGE_WARN_TEXT, NOTIFICATION_ICON, BETWEEN_BEACONS_MS,
} from './constants.js';
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
    // beta4-polish S4: what actually reached the bot.
    totalSent: 0,
    undelivered: 0,
    reportsFailed: 0,
    deliveryProblem: null,
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

// A round whose offers or scan reports did not reach the bot shows a warning
// instead of a success count (beta4-polish S4).
async function updateBadge(lastCheck, summary) {
  const total = Object.values(lastCheck).reduce((sum, entry) => sum + (Number(entry && entry.newJobs) || 0), 0);
  const failed = summary.undelivered > 0 || summary.reportsFailed > 0;
  try {
    await chrome.action.setBadgeBackgroundColor({ color: failed ? BADGE_WARN_COLOR : BADGE_COLOR });
    await chrome.action.setBadgeText({ text: failed ? BADGE_WARN_TEXT : (total ? String(total) : '') });
  } catch (error) {
    warn('could not update the badge:', String(error));
  }
}

// The first problem of the round is the one the popup explains.
function noteProblem(summary, problem) {
  if (problem && !summary.deliveryProblem) summary.deliveryProblem = problem;
}

async function report(settings, beacon, outcome, summary) {
  const problem = await reportScan(settings, beacon, outcome);
  if (problem) {
    summary.reportsFailed += 1;
    noteProblem(summary, problem);
  }
}

// Scans one beacon and records its outcome in `state`. Returns false when the
// round must stop (the portal's session expired).
async function scanBeacon({ tab, beacon, settings, state, summary }) {
  const portal = portalOfBeacon(beacon);
  const at = () => Date.now();
  let reported = false;
  try {
    throwIfCancelled();
    const { cards, noResults } = await scanPage(tab, beacon.url, portal);
    await report(settings, beacon, { jobs: cards, noResults }, summary);
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
      at: at(),
      status: 'ok',
      jobsFound: cards.length,
      newJobs: result.newJobs,
      filteredOut: result.filteredOut,
      sent: result.sent,
      undelivered: result.undelivered,
    };
    summary.beaconsScanned += 1;
    summary.totalJobsFound += cards.length;
    summary.totalNew += result.newJobs;
    summary.filteredOut += result.filteredOut;
    summary.totalSent += result.sent;
    summary.undelivered += result.undelivered;
    noteProblem(summary, result.problem);
    if (!seenList.length) summary.firstScans += 1;
    return true;
  } catch (error) {
    if (error instanceof RoundCancelled) throw error; // Pause: not a scan error
    if (!reported) await report(settings, beacon, { error }, summary);
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

// A Pause (cancelRound) stops between and inside beacons; what the finished
// beacons recorded is kept (summary.cancelled).
async function scanAll(beacons, settings, state, summary) {
  const tab = new ScrapeTab();
  try {
    for (let i = 0; i < beacons.length; i += 1) {
      const goOn = await scanBeacon({ tab, beacon: beacons[i], settings, state, summary });
      if (!goOn) break;
      if (i < beacons.length - 1) await bounded(sleep(randomBetween(BETWEEN_BEACONS_MS)));
    }
  } catch (error) {
    if (!(error instanceof RoundCancelled)) throw error;
    log('round cancelled (paused)');
    summary.cancelled = true;
    summary.reason = 'cancelled';
  } finally {
    await tab.dispose();
  }
}

// Why a round scanned nothing, for the popup (beta3-ux): the beacons the
// owner has at all versus the ones a portal switch or grant leaves out.
function hasAnyBeacon(state) {
  const manual = (Array.isArray(state.beacons) ? state.beacons : []).filter((b) => b && b.enabled !== false && b.url);
  const searches = normalizeSearches(state.searches, state.families);
  return manual.length > 0 || searches.length > 0;
}

// Every gate answers at once with a `reason` the popup explains (beta3-ux):
// a round that cannot deliver anything never opens a tab.
async function runGatedRound() {
  const settings = await readSettings();
  if (settings.paused) return emptySummary({ paused: true, reason: 'paused' });
  // Without the bridge nothing could be delivered: scan nothing, so no offer
  // is marked seen before the bot can receive it (spec 4.2).
  if (!bridgeConfigured(settings)) {
    warn('bridge not configured: round skipped, connect the extension to the bot in Options');
    return emptySummary({ bridgeMissing: true, reason: 'bridgeMissing' });
  }
  const { config: botConfig, problem } = await fetchBotConfig(settings);
  if (problem) {
    warn(`round skipped: ${problem}`);
    return emptySummary({ [problem]: true, reason: problem });
  }
  if (botConfig && botConfig.setup) return emptySummary({ botPaused: true, botSetup: true, reason: 'botSetup' });
  if (botConfig && botConfig.paused) return emptySummary({ botPaused: true, reason: 'botPaused' });

  await recoverPortalSeenStateOnce();
  const state = await loadRoundState();
  const { toScan, missingGrant } = await planBeacons(state, botConfig);
  for (const { beacon, portal } of missingGrant) state.lastCheck[beacon.id] = noPermissionEntry(portal, Date.now());
  const summary = emptySummary();
  if (!toScan.length) {
    if (missingGrant.length) await writeKeys({ lastCheck: state.lastCheck });
    summary.reason = hasAnyBeacon(state) ? 'noPortal' : 'noSearches';
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
  await updateBadge(state.lastCheck, summary);
  log('round done', JSON.stringify(summary));
  return summary;
}

// The outcome the popup shows after the round (beta3-ux): when, why it
// stopped (null: it ran) and its counts.
async function recordOutcome(summary) {
  try {
    await writeKeys({
      lastRound: {
        at: Date.now(),
        reason: summary.reason || null,
        beaconsScanned: summary.beaconsScanned,
        totalNew: summary.totalNew,
        errors: summary.errors,
        sent: summary.totalSent,
        undelivered: summary.undelivered,
        reportsFailed: summary.reportsFailed,
        problem: summary.deliveryProblem,
      },
    });
  } catch (error) {
    warn('could not store the round outcome:', String(error));
  }
}

// Never rejects: a failure outside the per-beacon handling is logged and
// counted as an error.
export async function runRound() {
  if (roundRunning) return emptySummary({ skipped: true });
  roundRunning = true;
  beginRound();
  let summary;
  try {
    summary = await runGatedRound();
  } catch (error) {
    warn('round failed:', errorMessage(error));
    summary = emptySummary({ errors: 1 });
  } finally {
    endRound();
    roundRunning = false;
  }
  await recordOutcome(summary);
  return summary;
}
