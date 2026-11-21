// Everything the service worker says to the local bot (spec 4.2, 4.6, 4.9):
// its config, the per-scan health report and the offers themselves. The
// secret travels in the `X-CVA-Bridge-Secret` header.

import { FAMILY_ID_RE, adoptProfileFamilies, normalizeProfile } from '../filter.js';
import { configUrl, parseBotConfig } from '../searches.js';
import { buildScanReport, scanReportUrl } from '../scan_report.js';
import { BOT_REQUEST_TIMEOUT_MS, OFFERS_POST_TIMEOUT_MS } from './constants.js';
import { writeKeys, bridgeSecret, bridgeConfigured } from './storage.js';
import { warn } from './util.js';

const SECRET_HEADER = 'X-CVA-Bridge-Secret';

// A request abandoned after BOT_REQUEST_TIMEOUT_MS: a bot that never answers
// cannot hold the round up.
async function fetchBounded(url, init, ms = BOT_REQUEST_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

export const BOT_UNREACHABLE = 'botUnreachable';
export const BOT_UNAUTHORIZED = 'botUnauthorized';

// The bot's answer to GET /config: `config` is its {paused, enabledPortals,
// setup?}, stored as `botConfig` for the pages, or null (local rules apply)
// for an answer that is not one; `problem` is BOT_UNREACHABLE (no answer:
// the bot is not running, or a wrong URL) or BOT_UNAUTHORIZED (401/403: the
// secret is wrong), else null. The profile's role families on the same
// answer are adopted as `profileFamilies`.
export async function fetchBotConfig(settings) {
  const url = configUrl(settings.bridgeUrl);
  const secret = bridgeSecret(settings);
  if (!url || !secret) return { config: null, problem: null };
  let response;
  try {
    response = await fetchBounded(url, { method: 'GET', headers: { [SECRET_HEADER]: secret } });
  } catch {
    return { config: null, problem: BOT_UNREACHABLE };
  }
  if (response.status === 401 || response.status === 403) return { config: null, problem: BOT_UNAUTHORIZED };
  if (!response.ok) return { config: null, problem: null };
  try {
    const payload = await response.json();
    const config = parseBotConfig(payload);
    if (!config) return { config: null, problem: null };
    await writeKeys({ botConfig: { ...config, at: Date.now() } });
    try {
      await adoptProfileFamilies(chrome.storage.local, payload);
    } catch (error) {
      warn('could not store the bot\'s role families:', String(error));
    }
    return { config, problem: null };
  } catch {
    return { config: null, problem: null };
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

// The family the bot receives as `profile`: the round already normalized it
// against the profile's family list, so a declared id (role families) is sent
// as is; anything malformed falls back to the built-in rule.
const payloadProfile = (value) => (typeof value === 'string' && FAMILY_ID_RE.test(value) ? value : normalizeProfile(value));

const orNull = (value) => (value === undefined || value === null || value === '' ? null : value);

// `offers` are [{card, text}]. Answers BRIDGE_ACCEPTED (2xx), BRIDGE_REJECTED
// (4xx: a deterministic refusal) or BRIDGE_OUTAGE (network error, 5xx, the
// bot's pause, anything else: retried later at no cost).
export async function postOffers(settings, beacon, offers) {
  if (!bridgeConfigured(settings)) throw new Error('bridge not configured');
  const body = JSON.stringify({
    beacon: beacon.name,
    profile: payloadProfile(beacon.filterProfile),
    jobs: offers.map(({ card, text }) => ({
      title: orNull(card.title),
      company: orNull(card.company),
      location: orNull(card.location),
      url: card.link,
      text: orNull(text),
      // Sent only when the card carries the LinkedIn Easy Apply badge.
      ...(card.easyApply === true ? { easy_apply: true } : {}),
    })),
  });
  let response;
  try {
    response = await fetchBounded(settings.bridgeUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', [SECRET_HEADER]: bridgeSecret(settings) },
      body,
    }, OFFERS_POST_TIMEOUT_MS);
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
