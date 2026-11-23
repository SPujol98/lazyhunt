// chrome.storage.local access and the settings rules (spec 2.1, 4.1, D2).
// Every read names its keys explicitly and removals go one key at a time.

import {
  DEFAULT_SETTINGS, DEFAULT_INTERVAL_MINUTES, MAX_INTERVAL_MINUTES,
} from './constants.js';
import { healedBridgeUrl } from '../bridge_url.js';

const area = () => chrome.storage.local;

export function readKeys(keys) {
  return area().get(keys);
}

export function writeKeys(values) {
  return area().set(values);
}

export async function removeKeys(keys) {
  for (const key of keys) await area().remove(key);
}

export function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

// The stored settings over the defaults: an install without `settings`
// behaves as if it held the defaults. A bridge URL saved with trailing
// punctuation is used in its clean form (beta4-polish S4). Nothing is
// written back.
export async function readSettings() {
  const { settings } = await readKeys(['settings']);
  const merged = { ...DEFAULT_SETTINGS, ...(isPlainObject(settings) ? settings : {}) };
  return { ...merged, bridgeUrl: healedBridgeUrl(merged.bridgeUrl) };
}

// D2: missing or invalid (NaN, 0) -> 60; otherwise rounded and clamped to
// 1..1440.
export function intervalMinutes(value) {
  const n = Number(value);
  if (value === null || value === undefined || !Number.isFinite(n) || n === 0) {
    return DEFAULT_INTERVAL_MINUTES;
  }
  return Math.min(MAX_INTERVAL_MINUTES, Math.max(1, Math.round(n)));
}

export function bridgeSecret(settings) {
  return String(settings.bridgeSecret || '');
}

// The bridge POST needs both values (spec 4.9).
export function bridgeConfigured(settings) {
  return Boolean(String(settings.bridgeUrl || '') && bridgeSecret(settings));
}
