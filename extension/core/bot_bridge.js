// Everything the service worker says to the local bot (spec 4.2, 4.6, 4.9):
// its config, the per-scan health report and the offers themselves. The
// secret travels in the `X-CVA-Bridge-Secret` header.

import { normalizeProfile } from '../filter.js';
import { configUrl, parseBotConfig } from '../searches.js';
import { buildScanReport, scanReportUrl } from '../scan_report.js';
import { BOT_REQUEST_TIMEOUT_MS } from './constants.js';
import { writeKeys, bridgeSecret, bridgeConfigured } from './storage.js';
import { warn } from './util.js';

const SECRET_HEADER = 'X-CVA-Bridge-Secret';

// A request abandoned after BOT_REQUEST_TIMEOUT_MS: a bot that never answers
// cannot hold the round up.
async function fetchBounded(url, init) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), BOT_REQUEST_TIMEOUT_MS);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

// The bot's {paused, enabledPortals} when it answers, stored as `botConfig`
// for the pages; null (local rules apply) for any other outcome.
export async function fetchBotConfig(settings) {
  const url = configUrl(settings.bridgeUrl);
  const secret = bridgeSecret(settings);
  if (!url || !secret) return null;
  try {
    const response = await fetchBounded(url, { method: 'GET', headers: { [SECRET_HEADER]: secret } });
    if (!response.ok) return null;
    const config = parseBotConfig(await response.json());
    if (!config) return null;
    await writeKeys({ botConfig: { ...config, at: Date.now() } });
    return config;
  } catch {
    return null;
  }
}

// Best effort: a failed report is logged and never affects the round.
export async function reportScan(settings, beacon, outcome) {
  const url = scanReportUrl(settings.bridgeUrl);
  const secret = bridgeSecret(settings);
  if (!url || !secret) return;
  try {
    await fetchBounded(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', [SECRET_HEADER]: secret },
      body: JSON.stringify(buildScanReport(beacon, outcome, Date.now())),
    });
  } catch (error) {
    warn(`scan report for "${beacon.name}" failed:`, String(error));
  }
}

export const BRIDGE_ACCEPTED = 'accepted';
export const BRIDGE_OUTAGE = 'outage';
export const BRIDGE_REJECTED = 'rejected';

const orNull = (value) => (value === undefined || value === null || value === '' ? null : value);

// `offers` are [{card, text}]. Answers BRIDGE_ACCEPTED (2xx), BRIDGE_REJECTED
// (4xx: a deterministic refusal) or BRIDGE_OUTAGE (network error, 5xx, the
// bot's pause, anything else: retried later at no cost).
export async function postOffers(settings, beacon, offers) {
  if (!bridgeConfigured(settings)) throw new Error('bridge not configured');
  const body = JSON.stringify({
    beacon: beacon.name,
    profile: normalizeProfile(beacon.filterProfile),
    jobs: offers.map(({ card, text }) => ({
      title: orNull(card.title),
      company: orNull(card.company),
      location: orNull(card.location),
      url: card.link,
      text: orNull(text),
    })),
  });
  let response;
  try {
    response = await fetch(settings.bridgeUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', [SECRET_HEADER]: bridgeSecret(settings) },
      body,
    });
  } catch (error) {
    warn(`bridge unreachable for "${beacon.name}":`, String(error));
    return BRIDGE_OUTAGE;
  }
  if (response.ok) return BRIDGE_ACCEPTED;
  if (response.status >= 400 && response.status < 500) {
    warn(`bridge rejected ${offers.length} offer(s) of "${beacon.name}" with HTTP ${response.status}`);
    return BRIDGE_REJECTED;
  }
  warn(`bridge answered HTTP ${response.status} for "${beacon.name}"; offers kept for the next round`);
  return BRIDGE_OUTAGE;
}
