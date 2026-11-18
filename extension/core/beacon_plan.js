// Which beacons a round scans (spec 4.3): manual beacons then auto beacons,
// narrowed by the local opt-in, the bot's portal list and the browser's host
// grant, in that order.

import { t } from '../i18n.js';
import { PORTALS, portalFor, portalById } from '../portals/registry.js';
import { portalOrigin } from '../portals/terms.js';
import {
  beaconPortalId, combineEnabledPortals, expandSearches, filterBeaconsByPortal,
  resolveEnabledPortals,
} from '../searches.js';
import { log } from './util.js';

const scannable = (beacon) => beacon && beacon.enabled !== false && Boolean(beacon.url);

export function portalOfBeacon(beacon) {
  return portalById(beaconPortalId(beacon, portalFor)) || portalFor(beacon.url);
}

async function hasGrant(portal) {
  try {
    return Boolean(await chrome.permissions.contains({ origins: [portalOrigin(portal)] }));
  } catch {
    return false;
  }
}

function logSkipped(skipped, reason) {
  for (const { beacon, portalId } of skipped) log(`skipped "${beacon.name}" (${portalId}): ${reason}`);
}

export function noPermissionEntry(portal, at) {
  return {
    at,
    status: 'no_permission',
    error: t(
      'sw_error_no_permission',
      [portal.id],
      `Permission for ${portal.id} is missing: enable the portal in options`,
    ),
  };
}

// `state` holds the stored beacons, searches and enabledPortals; `botConfig`
// is the bot's fresh answer or null. Returns the beacons to scan, in order,
// and the beacons skipped for a missing host grant (with their portal).
export async function planBeacons(state, botConfig) {
  const localPortals = resolveEnabledPortals(
    state.enabledPortals, state.searches, state.beacons, portalFor, PORTALS,
  );
  const manual = (Array.isArray(state.beacons) ? state.beacons : []).filter(scannable);
  const local = filterBeaconsByPortal(manual, localPortals, portalFor);
  logSkipped(local.skipped, 'portal not enabled in options');

  const candidates = [...local.allowed, ...expandSearches(state.searches, PORTALS, localPortals)];
  const allowedPortals = combineEnabledPortals(localPortals, botConfig ? botConfig.enabledPortals : null);
  const onBot = filterBeaconsByPortal(candidates, allowedPortals, portalFor);
  logSkipped(onBot.skipped, 'portal not enabled on the bot');

  const grants = new Map();
  const toScan = [];
  const missingGrant = [];
  for (const beacon of onBot.allowed.filter(scannable)) {
    const portal = portalOfBeacon(beacon);
    if (!grants.has(portal.id)) grants.set(portal.id, await hasGrant(portal));
    if (grants.get(portal.id)) {
      toScan.push(beacon);
    } else {
      log(`skipped "${beacon.name}" (${portal.id}): browser permission missing`);
      missingGrant.push({ beacon, portal });
    }
  }
  return { toScan, missingGrant };
}
