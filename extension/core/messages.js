// The runtime messages the pages send (spec section 3, D3). Every answer is
// an object; a thrown error becomes { ok: false, error }.

import { t } from '../i18n.js';
import { portalFor } from '../portals/registry.js';
import { DEFAULT_SETTINGS } from './constants.js';
import { reschedule } from './schedule.js';
import { runRound, isRoundRunning } from './round.js';
import { scanPage, SessionExpiredError } from './page_scan.js';
import { ScrapeTab } from './scrape_tab.js';
import { readKeys, writeKeys, isPlainObject } from './storage.js';
import { errorMessage } from './util.js';

async function setPaused({ paused }) {
  const { settings } = await readKeys(['settings']);
  const next = { ...(isPlainObject(settings) ? settings : DEFAULT_SETTINGS), paused: Boolean(paused) };
  await writeKeys({ settings: next });
  await reschedule();
  return { ok: true, paused: next.paused };
}

// Runs a portal's extractor on one URL exactly like a scan, without seen
// state, bridge or report, in a tab of its own (D3: never the round's tab).
async function testScrape({ url }) {
  if (isRoundRunning()) {
    return {
      ok: false,
      error: t('sw_error_scan_running', undefined, 'A scan is running: try again when it finishes.'),
    };
  }
  const target = String(url || '').trim();
  if (!target) return { ok: false, error: t('sw_error_url_required', undefined, 'URL required') };
  const tab = new ScrapeTab({ remembered: false });
  try {
    const { cards } = await scanPage(tab, target, portalFor(target));
    return { ok: true, jobs: cards };
  } catch (error) {
    return { ok: false, error: error instanceof SessionExpiredError ? error.userMessage : errorMessage(error) };
  } finally {
    await tab.dispose();
  }
}

async function clearHistory({ beaconId }) {
  if (beaconId === undefined || beaconId === null || beaconId === '') {
    await writeKeys({ seenJobs: {} });
    return { ok: true };
  }
  const { seenJobs } = await readKeys(['seenJobs']);
  const next = { ...(isPlainObject(seenJobs) ? seenJobs : {}) };
  delete next[beaconId];
  await writeKeys({ seenJobs: next });
  return { ok: true };
}

// A round refused for a missing bridge answers not ok, with the reason the
// popup shows as is (spec 3).
async function runNow() {
  const summary = await runRound();
  if (!summary.bridgeMissing) return { ok: true, summary };
  return { ok: false, bridgeMissing: true, error: bridgeMissingText(), summary };
}

function bridgeMissingText() {
  return t(
    'sw_error_bridge_missing',
    undefined,
    'Connect the extension to the bot first: Options › Schedule and bot.',
  );
}

const HANDLERS = {
  'settings-updated': async () => {
    await reschedule();
    return { ok: true };
  },
  'run-now': runNow,
  'set-paused': setPaused,
  'test-scrape': testScrape,
  'clear-history': clearHistory,
};

export async function handleMessage(message) {
  const handler = message && Object.hasOwn(HANDLERS, message.type) ? HANDLERS[message.type] : null;
  if (!handler) return { ok: false, error: t('sw_error_unknown_message', undefined, 'unknown message type') };
  try {
    return await handler(message);
  } catch (error) {
    return { ok: false, error: errorMessage(error) };
  }
}
