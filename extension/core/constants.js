// Names and limits shared by the service worker modules. Storage keys and the
// alarm name are a contract with the pages and the stored data of existing
// installs (docs/extension-spec.md sections 2 and 4.1): never rename them.

import { DEFAULT_BRIDGE_URL } from '../bridge_url.js';

export const ALARM_NAME = 'check-jobs';

// beta4-polish S4: the bridge URL is the same on every install unless the
// owner changes `bridge_port`, so a fresh install starts with it.
export const DEFAULT_SETTINGS = Object.freeze({
  intervalMinutes: 60,
  paused: false,
  bridgeUrl: DEFAULT_BRIDGE_URL,
  bridgeSecret: '',
});

// Settings fields that older builds stored and nothing reads any more.
export const RETIRED_SETTING_FIELDS = ['notifyOnFirstRun', 'botToken', 'chatId'];

export const DEFAULT_INTERVAL_MINUTES = 60;
export const MAX_INTERVAL_MINUTES = 1440;

export const SEEN_LIMIT_PER_BEACON = 3000;
export const MAX_DETAIL_PER_ROUND = 15;
export const MAX_DETAIL_ATTEMPTS = 3;

export const PAGE_LOAD_TIMEOUT_MS = 25000;
export const BOT_REQUEST_TIMEOUT_MS = 5000;
// beta3-ux: no browser call of a round may wait forever.
export const OFFERS_POST_TIMEOUT_MS = 20000;
export const SCRIPT_TIMEOUT_MS = 20000;
export const TAB_CALL_TIMEOUT_MS = 10000;
export const DETAIL_SETTLE_MS = 2000;
export const BETWEEN_BEACONS_MS = [2500, 6000];
export const BEFORE_DETAIL_MS = [1500, 3000];

export const BADGE_COLOR = '#1b5e20';
// A round whose offers or scan reports did not reach the bot (beta4-polish S4).
export const BADGE_WARN_COLOR = '#b26a00';
export const BADGE_WARN_TEXT = '!';
export const NOTIFICATION_ICON = 'icons/icon128.png';

// Message of the error that marks a login wall; scan_report.js classifies
// exactly this text as `session_expired`.
export const SESSION_EXPIRED_CODE = 'SESSION_EXPIRED';
