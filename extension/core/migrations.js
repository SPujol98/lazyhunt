// One-time storage migrations, run on install/update and on browser startup
// (spec 2.1, 2.2, 2.7, 2.12 and D1). Each step only acts on the legacy shape
// it repairs, so running them again changes nothing.

import { PORTALS, portalFor } from '../portals/registry.js';
import { legacyEnabledPortals } from '../searches.js';
import { DEFAULT_SETTINGS, RETIRED_SETTING_FIELDS } from './constants.js';
import { readKeys, writeKeys, removeKeys, isPlainObject } from './storage.js';
import { healedBridgeUrl } from '../bridge_url.js';

// Before the rename, manual beacons lived under `searches`. D1: only entries
// with a `url` are legacy beacons; auto searches stay where they are.
async function moveLegacyBeacons() {
  const { beacons, searches } = await readKeys(['beacons', 'searches']);
  if (beacons !== undefined || !Array.isArray(searches)) return;
  const isLegacyBeacon = (entry) => isPlainObject(entry) && Boolean(entry.url);
  const legacy = searches.filter(isLegacyBeacon);
  if (!legacy.length) return;
  const remaining = searches.filter((entry) => !isLegacyBeacon(entry));
  await writeKeys(remaining.length ? { beacons: legacy, searches: remaining } : { beacons: legacy });
  if (!remaining.length) await removeKeys(['searches']);
}

// Drops the fields nothing reads any more (the old first-run flag and, S4,
// the old chat-alert credentials); the rest of `settings` is kept as it is.
async function dropRetiredSettings() {
  const { settings } = await readKeys(['settings']);
  if (!isPlainObject(settings)) return;
  if (!RETIRED_SETTING_FIELDS.some((field) => field in settings)) return;
  const kept = { ...settings };
  for (const field of RETIRED_SETTING_FIELDS) delete kept[field];
  await writeKeys({ settings: kept });
}

// beta4-polish S4: a bridge URL stored with trailing punctuation (".../bridge.")
// is rewritten in its clean form; a value that is not a bridge URL is kept.
async function healBridgeUrl() {
  const { settings } = await readKeys(['settings']);
  if (!isPlainObject(settings) || typeof settings.bridgeUrl !== 'string') return;
  const healed = healedBridgeUrl(settings.bridgeUrl);
  if (healed !== settings.bridgeUrl) await writeKeys({ settings: { ...settings, bridgeUrl: healed } });
}

async function writeInstallDefaults() {
  const { settings, beacons } = await readKeys(['settings', 'beacons']);
  const values = {};
  if (settings === undefined) values.settings = { ...DEFAULT_SETTINGS };
  if (beacons === undefined) values.beacons = [];
  if (Object.keys(values).length) await writeKeys(values);
}

// Installs older than the per-portal opt-in keep scanning the portals they
// already use; a fresh install gets none. Never re-derived once stored.
async function deriveEnabledPortals() {
  const { enabledPortals, searches, beacons } = await readKeys(['enabledPortals', 'searches', 'beacons']);
  if (enabledPortals !== undefined) return;
  await writeKeys({ enabledPortals: legacyEnabledPortals(searches, beacons, portalFor, PORTALS) });
}

export async function migrateStorage({ installed }) {
  await moveLegacyBeacons();
  await dropRetiredSettings();
  await healBridgeUrl();
  if (installed) await writeInstallDefaults();
  await deriveEnabledPortals();
}
