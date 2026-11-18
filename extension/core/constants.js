// Names and limits shared by the service worker modules. Storage keys and the
// alarm name are a contract with the pages and the stored data of existing
// installs (docs/extension-spec.md sections 2 and 4.1): never rename them.

export const ALARM_NAME = 'check-jobs';

export const DEFAULT_SETTINGS = Object.freeze({
  intervalMinutes: 60,
  paused: false,
  bridgeUrl: '',
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
export const DETAIL_SETTLE_MS = 2000;
export const BETWEEN_BEACONS_MS = [2500, 6000];
export const BEFORE_DETAIL_MS = [1500, 3000];

export const BADGE_COLOR = '#1b5e20';
export const NOTIFICATION_ICON = 'icons/icon128.png';

// Message of the error that marks a login wall; scan_report.js classifies
// exactly this text as `session_expired`.
export const SESSION_EXPIRED_CODE = 'SESSION_EXPIRED';
