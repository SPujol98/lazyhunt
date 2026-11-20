// Portal registry (portal-adapters T1).
//
// Adapter contract: a plain object per portal in portals/<id>.js (default export)
//   id               stable portal id ('linkedin'); also the bot's source/origen
//   label            human name for UI
//   hostMatch(url)   true when the URL belongs to the portal
//   extractCards     page function injected with chrome.scripting.executeScript
//                    -> Promise<[{id, title, company, location, link}]>. It is
//                    serialized, so it must be self-contained (no imports, no
//                    module-scope bindings) and keep a stable function name.
//   noResultsMarkers regex sources (matched case-insensitively against the page
//                    text) for the portal's own "no results" copy
//   canonicalUrl(id) offer URL derived from the id, or null when the id alone
//                    is not enough (keep the card's link)
//   linkHost         host of offer links
//   settleMs         wait after the tab loads, before extraction
//   isSessionExpired(tabUrl)  optional login-wall check on the loaded tab URL
//   extractDetail    optional page function: description text of an offer page
//   detailArgs       optional array of arguments passed to extractDetail when
//                    injected (the shared extractor takes a description selector)
//   buildSearchUrl   (portal-adapters T2) search URL from {keywords, location, extra}
//   cardsCarryCompany optional, default true; false when the result cards never
//                    show the company (scan health then ignores empty companies)
//
// Owner-only portals (never in the public release) are listed in
// owner_portals.js and appended after the public ones.

import linkedin from './linkedin.js';
import infojobs from './infojobs.js';
import indeed from './indeed.js';
import tecnoempleo from './tecnoempleo.js';
import jobfluent from './jobfluent.js';
import manfred from './manfred.js';
import adzuna from './adzuna.js';
import foorilla from './foorilla.js';
import { OWNER_PORTALS } from './owner_portals.js';

export const PORTALS = [linkedin, infojobs, indeed, tecnoempleo, jobfluent, manfred, adzuna, foorilla, ...OWNER_PORTALS];

// LinkedIn is the fallback for unknown or malformed URLs, as the old
// beaconSite() did.
export function portalFor(url) {
  const found = PORTALS.find((p) => p.hostMatch(String(url || '')));
  return found || linkedin;
}

export function portalById(id) {
  return PORTALS.find((p) => p.id === id) || null;
}

// One RegExp source (flags: i) from a list of marker sources. The page
// function injected by background.js cannot close over module scope, so it
// receives this string as an argument.
export function markersToSource(markers) {
  return markers.map((p) => `(?:${p})`).join('|');
}

export function noResultsSource(portal) {
  return markersToSource(portal.noResultsMarkers);
}

// Union of every portal's markers (deduplicated, registry order).
export const ALL_NO_RESULTS_MARKERS = [
  ...new Set(PORTALS.flatMap((p) => p.noResultsMarkers)),
];
