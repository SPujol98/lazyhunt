// Profile-driven searches (portal-adapters T2). Pure helpers, no chrome.*.
//
// The `searches` setting is a list of
//   { id, name, keywords, location, filterProfile: <family id: 'ai', 'backend' or
//     one the bot's profile declares (role families)>,
//     portals: [portalId], titleFilter: 'on'|'off' }.
// Each (search x known portal) expands into an "auto beacon" whose URL comes
// from the portal adapter's buildSearchUrl. Auto beacons are derived on every
// round and never stored with the manual beacons.

import { normalizeProfile } from './filter.js';
import { slugify } from './slug.js';

// `families` is the profile's family list (filter.js resolveFamilies());
// without it the built-in two apply.
export function normalizeSearch(raw, families) {
  const r = raw && typeof raw === 'object' ? raw : {};
  const portals = Array.isArray(r.portals)
    ? [...new Set(r.portals.filter((p) => typeof p === 'string' && p))]
    : [];
  return {
    id: typeof r.id === 'string' ? r.id : '',
    name: String(r.name || '').trim(),
    keywords: String(r.keywords || '').trim(),
    location: String(r.location || '').trim(),
    filterProfile: normalizeProfile(r.filterProfile, families),
    portals,
    titleFilter: r.titleFilter === 'off' ? 'off' : 'on',
  };
}

export function normalizeSearches(list, families) {
  return Array.isArray(list) ? list.map((raw) => normalizeSearch(raw, families)) : [];
}

// Stable across renames when the search has an id (the options page assigns
// one); otherwise derived from the name.
export function autoBeaconId(search, portalId) {
  return `auto:${search.id || slugify(search.name)}:${portalId}`;
}

// Re-assigns the id of every search whose id is blank or already taken by an
// earlier one (hand-edited or imported data), so auto beacon ids stay unique.
export function dedupeSearchIds(searches, newId) {
  const used = new Set();
  return (searches || []).map((search) => {
    let id = typeof search.id === 'string' ? search.id : '';
    while (!id || used.has(id)) id = newId();
    used.add(id);
    return id === search.id ? search : { ...search, id };
  });
}

// Searches without keywords, or portals the registry does not know (e.g. an
// adapter removed later), expand to nothing. `enabledPortals` (array or Set of
// ids; undefined = every portal, as before the per-portal opt-in) additionally
// drops the portals the owner has not enabled.
export function expandSearches(searches, portals, enabledPortals, families) {
  const allowed = enabledPortals === undefined ? null : new Set(enabledPortals);
  const byId = new Map((portals || []).map((p) => [p.id, p]));
  const out = [];
  const usedIds = new Set();
  // Portals that ignore keywords (Manfred) build the same URL for every search:
  // open it once per round.
  const usedUrls = new Set();
  for (const search of normalizeSearches(searches, families)) {
    if (!search.keywords) continue;
    for (const portalId of search.portals) {
      const portal = byId.get(portalId);
      if (!portal || typeof portal.buildSearchUrl !== 'function') continue;
      if (allowed && !allowed.has(portalId)) continue;
      const id = autoBeaconId(search, portalId);
      // Two searches sharing an id (hand-edited storage) would share seen-ids:
      // the first one wins, the options page re-assigns ids on load.
      if (usedIds.has(id)) continue;
      const url = portal.buildSearchUrl({ keywords: search.keywords, location: search.location });
      if (usedUrls.has(url)) continue;
      usedIds.add(id);
      usedUrls.add(url);
      out.push({
        id,
        name: `${search.name || search.keywords} · ${portal.label}`,
        url,
        enabled: true,
        auto: true,
        portal: portalId,
        filterProfile: search.filterProfile,
        titleFilter: search.titleFilter,
      });
    }
  }
  return out;
}

// --- Suggested searches (options: "Sugerir búsquedas desde mi perfil") -------------
// The bot serves a deterministic list at GET <bridge origin>/searches/suggested.

// URL of the suggestions endpoint from the configured bridge URL
// (`http://127.0.0.1:8765/bridge`); null when it is blank or not an http(s) URL.
export function suggestionsUrl(bridgeUrl) {
  try {
    const u = new URL(String(bridgeUrl || '').trim());
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return null;
    return `${u.origin}/searches/suggested`;
  } catch (_) {
    return null;
  }
}

// Identity of a search for de-duplication: normalized keywords + location.
export function searchKey(search) {
  const words = (text) => slugify(text);
  return `${words(search && search.keywords)}|${words(search && search.location)}`;
}

// Rows of a bridge response that can become a search (keywords required).
export function parseSuggestions(payload, families) {
  const rows = payload && Array.isArray(payload.searches) ? payload.searches : [];
  return rows
    .filter((r) => r && typeof r === 'object')
    .map((raw) => normalizeSearch(raw, families))
    .filter((s) => s.keywords);
}

// Adds the picked suggestions to the existing searches. Never edits, reorders
// or drops an existing search; a pick already present (same normalized
// keywords + location, also among the picks) is skipped; new ids come from
// `newId` and never collide with an existing one.
export function mergeSuggestedSearches(existing, picked, newId, families) {
  const searches = [...(existing || [])];
  const keys = new Set(searches.map(searchKey));
  const ids = new Set(searches.map((s) => s.id));
  let added = 0;
  let skipped = 0;
  for (const raw of picked || []) {
    const search = normalizeSearch(raw, families);
    const key = searchKey(search);
    if (!search.keywords || keys.has(key)) {
      skipped += 1;
      continue;
    }
    let id = newId();
    while (!id || ids.has(id)) id = newId();
    keys.add(key);
    ids.add(id);
    searches.push({ ...search, id });
    added += 1;
  }
  return { searches, added, skipped };
}

// --- Per-portal opt-in (safe-defaults T2) ------------------------------------------
// `enabledPortals` (storage) lists the portal ids the owner turned on; a portal
// is scanned only when it is listed here, has its browser permission, and (when
// the bot publishes a list) is also enabled on the bot.

// Portal a beacon belongs to: auto beacons carry it, manual ones are routed by
// URL host exactly as the scan does (`portalFor`, unknown hosts -> linkedin).
export function beaconPortalId(beacon, portalFor) {
  if (beacon && beacon.auto && beacon.portal) return beacon.portal;
  return portalFor(String((beacon && beacon.url) || '')).id;
}

// One-time migration for installs that predate the opt-in: enable exactly the
// portals the stored searches and manual beacons already use (registry ids
// only, first-seen order), so the existing setup keeps scanning. A fresh
// install has nothing configured, so it gets none.
export function legacyEnabledPortals(searches, beacons, portalFor, portals) {
  const known = new Set((portals || []).map((p) => p.id));
  const out = [];
  const add = (id) => {
    if (known.has(id) && !out.includes(id)) out.push(id);
  };
  for (const search of normalizeSearches(searches)) search.portals.forEach(add);
  for (const beacon of Array.isArray(beacons) ? beacons : []) {
    if (beacon && beacon.url) add(beaconPortalId(beacon, portalFor));
  }
  return out;
}

// The stored list when present, otherwise the legacy derivation (so an install
// that has not run the migration yet behaves like its migrated self).
export function resolveEnabledPortals(stored, searches, beacons, portalFor, portals) {
  if (Array.isArray(stored)) return stored.filter((id) => typeof id === 'string' && id);
  return legacyEnabledPortals(searches, beacons, portalFor, portals);
}

export function filterBeaconsByPortal(beacons, enabledPortals, portalFor) {
  const enabled = new Set(enabledPortals || []);
  const allowed = [];
  const skipped = [];
  for (const beacon of beacons || []) {
    const portalId = beaconPortalId(beacon, portalFor);
    if (enabled.has(portalId)) allowed.push(beacon);
    else skipped.push({ beacon, portalId });
  }
  return { allowed, skipped };
}

// A portal must be enabled on both sides: the bot's list (null/undefined = the
// bot does not restrict) narrows the local one, never widens it.
export function combineEnabledPortals(local, botList) {
  if (!Array.isArray(botList)) return [...(local || [])];
  const bot = new Set(botList);
  return (local || []).filter((id) => bot.has(id));
}

// --- Bot config (GET <bridge origin>/config, secret in the header) -----------------

export function configUrl(bridgeUrl) {
  try {
    const u = new URL(String(bridgeUrl || '').trim());
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return null;
    return `${u.origin}/config`;
  } catch (_) {
    return null;
  }
}

// {paused, enabledPortals|null} from the bot's body; null when it is not an object.
export function parseBotConfig(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return null;
  return {
    paused: payload.paused === true,
    enabledPortals: Array.isArray(payload.enabledPortals)
      ? payload.enabledPortals.filter((id) => typeof id === 'string' && id)
      : null,
  };
}
