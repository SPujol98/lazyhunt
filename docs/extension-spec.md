# Browser extension: behaviour specification

Status: specification of the extension as it behaves today (the vendor-derived
code in `extension/` at tag `checkpoint/pre-extension-rewrite`), written for
the clean-room rewrite (`odd/tasks/extension-rewrite.md`, T0). It describes
behaviour, contracts, data shapes and edge cases. It is not a description of
the code: the rewrite must reach the same observable behaviour with its own
structure.

How to use it:

- "Must" items are the compatibility contract (S3): existing stored data must
  keep working after `git pull` + Reload. "Removed by S4" items must disappear.
  The pages (popup, options) are redesigned (S5), so only their features are
  specified, never their layout, DOM ids, CSS or wording.
- The characterization tests `tests/test_extension_characterization.py` pin the
  most important behaviour through the public surface only (Chrome events,
  runtime messages, storage, `chrome.*` calls, fetches, the page controls). They
  must stay green on the rewrite. Section 9 maps them to this document.
- Modules that contain no vendor code (measured in the feature log, L2) may be
  imported by the rewrite as they are and define the exact semantics this
  document refers to: `extension/filter.js`, `extension/searches.js`,
  `extension/scan_report.js`, `extension/slug.js`,
  `extension/portals/registry.js`, `extension/portals/terms.js`,
  `extension/portals/detail.js` and every portal adapter except
  `extension/portals/linkedin.js` (rewritten, section 5). Everything else
  (`manifest.json`, `background.js`, `popup.*`, `options.*`, `shared.css`,
  `icons/`, `portals/linkedin.js`) is rewritten from this document.

Vocabulary:

| Term | Meaning |
| --- | --- |
| portal | a job site with an adapter in `extension/portals/` (`linkedin`, `infojobs`, `indeed`, `tecnoempleo`, `jobfluent`, `manfred`, `adzuna`, `foorilla`) |
| manual beacon | a search results URL the owner pasted in Options; stored in `beacons` |
| search (auto search) | `{keywords, location, portals, ...}` stored in `searches`; expands to one auto beacon per selected and enabled portal |
| auto beacon | a beacon derived from a search on every round, id `auto:<search id>:<portal id>`, never stored |
| round | one pass over every scannable beacon (alarm or "run now") |
| the bot / the bridge | the local `cva` bot, reached at the configured bridge URL (`http://127.0.0.1:<port>/bridge`) |
| seen | an offer id recorded for a beacon so it is not sent again |

---

## 1. Manifest contract

- Manifest version 3. The service worker is an ES module (`"type": "module"`)
  because it imports the shared modules listed above.
- Required permissions, all used:
  - `alarms`: the periodic round.
  - `storage`: `chrome.storage.local` holds every setting and all state.
  - `tabs`: the hidden scrape tab is created, navigated, read (its final URL is
    the session-expiry check) and closed.
  - `scripting`: page functions are injected into the scrape tab.
  - `notifications`: the "session expired" desktop notification (the only one).
- Required host permissions: the loopback bridge `http://127.0.0.1/*` (bridge
  POST, scan reports, `GET /config`, `GET /searches/suggested`). Today the list
  also has `https://api.telegram.org/*`: removed by S4.
- Optional host permissions: exactly one origin per portal, derived from each
  adapter's `linkHost` (`https://<linkHost>/*`): `www.linkedin.com`,
  `www.infojobs.net`, `es.indeed.com`, `www.tecnoempleo.com`,
  `www.jobfluent.com`, `www.getmanfred.com`, `www.adzuna.es`, `foorilla.com`.
  They are requested at runtime when the owner enables a portal. They must stay
  optional: Chrome keeps the grants the owner already gave, and a portal is
  scanned only with its grant.
- Toolbar action with a popup page; an options page opened in its own tab
  (`options_ui.open_in_tab: true`); icons 16/48/128 (new icons per S6).
- The extension must keep its unpacked identity: same `extension/` directory,
  so the stored data of the owner's install is read by the rewrite (S3).
- The current CSP allows Google Fonts for the pages; not a requirement.

## 2. Storage contract (`chrome.storage.local`)

Every key below exists in installs today. The rewrite must read each one in the
shape given (including the legacy shapes) or migrate it once, and must write the
keys it keeps in the same shape. No key is ever written to `chrome.storage.sync`
or `session`.

### 2.1 `settings`

Object. Fields:

| Field | Type | Default (fresh install) | Notes |
| --- | --- | --- | --- |
| `intervalMinutes` | number | 60 | minutes between rounds; see 4.1 for how bad values are read |
| `paused` | boolean | false | local pause |
| `bridgeUrl` | string | `''` | the bot's POST endpoint, normally `http://127.0.0.1:8765/bridge` |
| `bridgeSecret` | string | `''` | shared secret, sent in the `X-CVA-Bridge-Secret` header; never exported |
| `botToken` | string | `''` | REMOVED by S4 (Telegram) |
| `chatId` | string | `''` | REMOVED by S4 (Telegram) |

Legacy fields that may still be present:

- `notifyOnFirstRun`: deleted on install/update/startup (the rest of `settings`
  is kept unchanged).
- `titleFilter`: a title filter nested in settings by old builds; read only as a
  fallback for the `ai` profile (2.6). Options does not write it back.

Writers: `onInstalled` (defaults when the key is absent), the `set-paused`
message, Options save and import. When `settings` is absent, the service worker
behaves as if it held the defaults (and `set-paused` writes the defaults plus
`paused`). Stored settings are never rewritten to the new defaults.

### 2.2 `beacons`

Array of manual beacons:
`{ id: string, name: string, url: string, enabled: boolean, filterProfile: 'ai'|'backend' }`.

- Options assigns ids `bcn_` + 8 random base-36 characters; legacy entries may
  lack `filterProfile` (read as `'ai'`) or `enabled` (anything but `false` is
  enabled). A beacon without `url` is never scanned.
- `onInstalled` writes `[]` when the key is absent. Options save and import
  replace the whole list. The service worker never writes it except the legacy
  migration below.
- Legacy: before the rename, manual beacons lived under `searches`. On install,
  update and browser startup, when `searches` exists and `beacons` does not,
  the value of `searches` is moved to `beacons` unchanged and `searches` is
  deleted. (See Findings F1 about the clash with the new meaning of
  `searches`.)

### 2.3 `searches`

Array of auto searches:
`{ id, name, keywords, location, filterProfile: 'ai'|'backend', portals: [portal id], titleFilter: 'on'|'off' }`.

- Normalisation (`searches.js` `normalizeSearch`): strings trimmed, unknown
  profile -> `'ai'`, portals de-duplicated and blank ones dropped,
  `titleFilter` is `'off'` only when exactly `'off'`.
- Options assigns ids `s_` + 8 random base-36 characters and re-assigns blank or
  duplicate ids on load and import (`dedupeSearchIds`), so auto beacon ids stay
  unique and stable across renames.
- Expansion (`expandSearches`): a search without keywords, or a portal unknown
  to the registry or not enabled, produces nothing; two searches sharing an id
  or producing the same URL (portals that ignore keywords) are opened once per
  round. The auto beacon is
  `{ id: 'auto:<search id or slug of name>:<portal id>', name: '<search name or keywords> · <portal label>', url, enabled: true, auto: true, portal, filterProfile, titleFilter }`.
- Auto beacons are never written to `beacons`. Their seen offers, attempt
  counters and `lastCheck` entries are keyed by their auto id.

### 2.4 `seenJobs`

`{ [beacon id]: [offer id, ...] }`, ids as strings, oldest first.

- After a beacon's scan, the ids of every card on the page (passing and
  filtered-out alike) are appended, except the offers that must be retried
  (detail cap, detail retry, bridge not accepted; 4.7–4.9). Already-seen ids
  keep their position.
- At most the newest 3000 ids per beacon are kept (the oldest are dropped).
- An empty or missing list means "first scan" for that beacon (only counted in
  the round summary; first scans are bridged like any other).
- Cleared per beacon or entirely by the `clear-history` message; partly cleared
  once by the seen-state recovery (2.11).

### 2.5 `lastCheck`

`{ [beacon id]: entry }`, the outcome of the beacon's last scan. Entries:

| status | other fields | when |
| --- | --- | --- |
| `ok` | `at`, `jobsFound` (cards on the page), `newJobs` (cards not seen before), `filteredOut` (new cards dropped by the title filter) | scan completed |
| `error` | `at`, `error` (message) | any scan failure (tab load timeout, injection error, ...) |
| `session_expired` | `at`, `error: 'LinkedIn session expired'` | login wall detected (4.5) |
| `no_permission` | `at`, `error: 'Permission for <portal id> is missing: enable the portal in options'` | portal enabled but its host grant is missing |

`at` is epoch milliseconds. Entries of other beacons are kept; entries are never
pruned (Findings F6). Read by the popup and the options page.

### 2.6 Title filters: `titleFilter`, `titleFilterBackend`, `profileTitleFilters`

A filter is `{ enabled: boolean, includeGroups: string[][], excludeTerms: string[] }`
(semantics: `filter.js` `passesFilter`: every include group must have a term
matching the title as whole words; any exclude term matching title or company
drops the offer; case-, accent- and hyphen-insensitive).

- `titleFilter` is the `ai` profile, `titleFilterBackend` the `backend`
  profile. Written by Options save and import.
- `profileTitleFilters` `{ ai?, backend? }` is written (or removed) by the
  "suggest searches" flow from the bot's answer; it replaces the built-in
  defaults of each profile.
- Resolution (`filter.js` `resolveTitleFilters`), per profile: the stored key if
  it is an object, repaired field by field from the profile defaults; for `ai`
  only, then the legacy `settings.titleFilter`; then the defaults
  (`profileTitleFilters[profile]` when present, else the built-in profile).
- A beacon uses the profile named by its `filterProfile` (`'backend'` or else
  `'ai'`).

### 2.7 `enabledPortals`

Array of portal ids the owner enabled (per-portal opt-in).

- Fresh install: `[]` (nothing is scanned until the owner enables a portal).
- Migration, on install/update/startup and only while the key is absent: the
  portals already used by stored searches and manual beacons (registry ids only,
  first-seen order; a manual beacon's portal is found from its URL host, unknown
  hosts count as LinkedIn). An absent key is read the same way everywhere until
  migrated. Never re-derived once present.
- Written by Options immediately when a portal is toggled (not on Save).

### 2.8 `botConfig`

`{ paused: boolean, enabledPortals: string[] | null, at: epoch ms }`: the last
valid answer of the bot's `GET /config`. Written by the service worker at the
start of each round and by Options when it opens. Read by the popup and Options
for display only; the round itself uses the fresh answer, never the stored one.

### 2.9 `stats`

`{ totalNotified: number, lastRoundAt: epoch ms | null }`. Written at the end
of every round that scanned something: `lastRoundAt` is set to now and
`totalNotified` carried over (today it also counts direct Telegram alerts:
REMOVED by S4, so it no longer grows; the stored value must be preserved, not
reset). Read by the popup.

### 2.10 `scrapeTabId`

Number: id of the hidden scrape tab while a round or a test scrape uses it;
deleted when it is disposed. A value left behind (service worker killed
mid-round) is reused if that tab still exists, otherwise ignored.

### 2.11 Detail-pass and recovery state

- `detailAttempts`: `{ [beacon id]: { [offer id]: failures } }`, failures so far
  for offers that could not be delivered (detail text unreadable or bridge
  4xx). A beacon's map is dropped when empty; counters of offers that left the
  results page are dropped.
- `portalSeenRecovery`: `true` once the one-time recovery ran. Recovery, at the
  start of the first round where the flag is absent: delete the `seenJobs` and
  `detailAttempts` entries of every AUTO beacon whose portal is not LinkedIn,
  add those ids to `recoveringBeacons`, set the flag. Manual beacons and
  LinkedIn auto beacons are untouched. On the owner's install it has already
  run; on a fresh install it only sets the flag.
- `recoveringBeacons`: array of beacon ids still draining their recovery
  backlog; a beacon leaves it after a scan with nothing deferred or retried.
  Today its only effect is to suppress direct Telegram alerts (REMOVED by S4),
  so after S4 it has no consumer; the rewrite may keep maintaining it or ignore
  it, but must not fail on it.

### 2.12 Keys removed by S4

None is a top-level key: `settings.botToken` and `settings.chatId` are the only
Telegram data. They may stay in storage untouched or be deleted by a migration;
nothing may read them.

## 3. Runtime messages

The service worker has one `chrome.runtime.onMessage` listener. Every message is
`{ type, ...payload }`, every answer is asynchronous (the channel is kept open)
and any thrown error becomes `{ ok: false, error: <message> }`. Senders are the
extension's own pages; the sender is not checked.

| type | payload | effect | answer | sent by |
| --- | --- | --- | --- | --- |
| `settings-updated` | none | reschedule the alarm from the stored settings (4.1) | `{ ok: true }` | Options after Save, import and every portal toggle |
| `run-now` | none | run a round now and wait for it (4) | `{ ok: true, summary }`; when the bridge is not configured (4.2) `{ ok: false, bridgeMissing: true, error: <localized "Connect the extension to the bot first: Options › Schedule and bot.">, summary }` | popup "check now" |
| `set-paused` | `paused` (truthy/falsy) | `settings.paused = !!paused` (other settings kept; defaults when absent), then reschedule (paused -> no alarm) | `{ ok: true, paused: <boolean stored> }` | popup pause/resume |
| `test-scrape` | `url` | open the URL in the scrape tab, run that portal's card extractor exactly like a scan (load, session check, settle, extract), dispose the tab. No seen state, no bridge, no report, no permission or pause check | `{ ok: true, jobs: [cards] }`, or `{ ok: false, error }` (e.g. session expired, load timeout) | Options "test" button of a manual beacon |
| `clear-history` | optional `beaconId` | with `beaconId`: delete that beacon's `seenJobs` entry; without: empty `seenJobs` to `{}`. Other keys untouched | `{ ok: true }` | Options "clear history" (always without `beaconId`) |
| `test-telegram` | `botToken`, `chatId` | REMOVED by S4 | | |
| anything else | | nothing | `{ ok: false, error: 'unknown message type' }` | |

`summary` (also the result of an alarm round, which nobody reads):

`{ skipped, beaconsScanned, totalJobsFound, totalNew, notified, firstScans, errors, filteredOut }`
plus `paused: true` or `botPaused: true` when the round stopped for a pause,
or `bridgeMissing: true` when it scanned nothing because the bridge is not
configured (4.2; all counters 0).
`skipped` is true when another round was already running (4.10). `notified`
counts direct Telegram alerts: REMOVED by S4 (drop the field or keep it 0).

## 4. The scan round

### 4.1 Scheduling

- One alarm named `check-jobs` (the popup reads its `scheduledTime`). It is
  (re)scheduled on install/update (after the migrations and defaults), on
  browser startup (after the migrations) and on `settings-updated`/`set-paused`.
- Rescheduling clears the alarm, then, unless `settings.paused`, creates it with
  a first run 30 seconds later (`delayInMinutes: 0.5`) and a period of
  `max(1, Number(intervalMinutes))` minutes, where a value that converts to
  `0`/`NaN` (missing, `0`, non-numeric text, `null`) reads as 15. So `'45'` ->
  45, `0` -> 15, `-3` -> 1, `0.5` -> 1. There is no upper clamp in the service
  worker (Options and import clamp to 1440). Without stored settings the
  default 60 applies.
- When the `check-jobs` alarm fires, a round runs. Other alarm names are
  ignored.
- Options warns (does not block) when the interval is under 30 minutes.

### 4.2 Pause and the bot's config

1. If `settings.paused`: stop at once, nothing else happens (no fetch, no tab);
   the summary carries `paused: true`.
2. If the bridge is not configured (`bridgeUrl` or `bridgeSecret` empty):
   stop; nothing else happens (no fetch, no tab, no seen-state recovery, no
   `no_permission` entry, no write to `seenJobs`, `detailAttempts`,
   `lastCheck` or `stats`); the summary carries `bridgeMissing: true`. Offers
   are never marked seen before the bot can receive them (public-release S2;
   before it, a round without a bridge scanned and marked every offer seen).
   Scheduled rounds and `run-now` behave alike; `test-scrape` is not a round
   and works without a bridge.
3. If the bridge is configured (a valid http(s) `bridgeUrl` and a non-empty
   secret): `GET <bridge origin>/config` with the `X-CVA-Bridge-Secret` header,
   abandoned after 5 seconds. A 2xx JSON object is parsed (`searches.js`
   `parseBotConfig`: `paused === true`, `enabledPortals` array of ids or
   `null`) and stored as `botConfig` with `at`. Anything else (invalid URL,
   non-2xx, network error, timeout, non-object) means "no answer" and the local
   rules apply.
4. If the bot answered `paused: true`: stop; summary `botPaused: true`.

### 4.3 What is scanned

1. The one-time seen-state recovery (2.11), if not done yet.
2. Beacons = manual beacons, then the auto beacons expanded from `searches`
   over the locally enabled portals. Manual beacons of a portal that is not
   enabled locally are skipped (and logged).
3. Allowed portals = locally enabled portals, narrowed to the bot's
   `enabledPortals` when the bot answered with a list (the bot never widens the
   local list). Beacons of other portals are skipped (logged).
4. Only enabled beacons with a URL are considered.
5. Each portal's host grant is checked once per round with
   `chrome.permissions.contains({ origins: ['https://<linkHost>/*'] })` (an
   exception counts as "not granted"). A beacon without the grant is skipped and
   gets a `no_permission` `lastCheck` entry.
6. If nothing is left: write the `no_permission` entries (if any) and return
   the empty summary. No tab is opened and `stats` is not touched.

### 4.4 The scrape tab

- One tab serves the whole round: if `scrapeTabId` names a tab that still
  exists it is reused, otherwise a new tab is created on `about:blank` with
  `active: false` and its id stored in `scrapeTabId`.
- Each page load: navigate the tab to the URL, then wait for its
  `chrome.tabs.onUpdated` `status: 'complete'` event, failing with a timeout
  error after 25 seconds (that beacon becomes an `error`).
- At the end of the round, whatever happened (errors included), the tab is
  closed (a failure to close is ignored) and `scrapeTabId` deleted. Then the
  round's state is written (4.11).
- `test-scrape` uses the same mechanism and also disposes the tab (Findings F4).

### 4.5 Scanning one beacon

For each beacon in order (the portal adapter is chosen by URL host via
`portalFor`, unknown hosts fall back to LinkedIn):

1. Load the beacon URL (4.4).
2. If the adapter has `isSessionExpired`, read the tab's final URL; if it says
   the session expired, the beacon fails with the session-expired error.
3. Wait the adapter's `settleMs`.
4. Inject the adapter's `extractCards` page function; its result (an array of
   cards, anything else reads as `[]`) is the beacon's cards
   `[{ id, title, company, location, link }]`.
5. If there are zero cards, inject a probe that tests the page's body text
   against the adapter's no-results markers (`noResultsSource`, one
   case-insensitive pattern). Any failure counts as "not confirmed".
6. Send the scan report (4.6).
7. New cards = cards whose id is not in the beacon's seen list.
8. Title filter (4.7), detail pass (4.8), bridge (4.9), seen update and
   `lastCheck` `ok` entry (4.11).
9. Unless the session died, wait a random 2.5–6 seconds before the next beacon.

Failure of a beacon (any exception): if the scan report was not sent yet, send
one with the error; record `lastCheck` `error` with the message and count one
error; go on with the next beacon. The session-expired failure instead records
`session_expired`, counts one error, shows one desktop notification (basic
type, the extension icon, a message asking to open LinkedIn and sign in again,
priority 2) and ends the scanning part of the round: no further beacon is
scanned.

### 4.6 Scan report (beacon health)

After every scan attempt the bot gets
`POST <bridgeUrl with its trailing /bridge replaced by /scan-report>` with the
JSON from `scan_report.js` `buildScanReport` (`beacon_id`, `beacon_name`, `at`
ISO, `status` among `ok`, `empty_unconfirmed`, `session_expired`,
`rate_limited`, `error`, `jobs_found` = raw cards before any filter,
`empty_company`). Only when `bridgeUrl` ends with `/bridge` and the secret is
set; header `X-CVA-Bridge-Secret`; abandoned after 5 seconds; any failure is
ignored and never delays or breaks the round. Zero cards without a confirmed
no-results marker is `empty_unconfirmed` (a throttled page must not look like a
healthy zero).

### 4.7 Title filter

- An auto beacon with `titleFilter: 'off'` sends every new card.
- Otherwise each new card must pass the beacon profile's filter (2.6); a
  disabled filter passes everything.
- Cards that fail are counted in `filteredOut` (summary and `lastCheck`) and
  are still marked seen: they are never sent later, even if the filter changes.

### 4.8 Detail pass (adapters with `extractDetail`; not LinkedIn)

- At most `MAX_DETAIL_PER_ROUND` = 15 passing offers per beacon per round are
  processed; the rest are "deferred": not sent, not marked seen, picked up next
  round.
- For each processed offer, sequentially: wait a random 1.5–3 seconds, load the
  offer link in the scrape tab, wait 2 seconds, inject `extractDetail` with the
  adapter's `detailArgs`. A non-empty string result is the offer `text`.
  Anything else (null, empty, an exception) is a failure.
- A failed offer is not sent and not marked seen; its counter in
  `detailAttempts` grows. At `MAX_DETAIL_ATTEMPTS` = 3 failures it is given up:
  counter removed and the offer marked seen (never sent).
- Without `extractDetail` (LinkedIn) every passing offer is sent with
  `text: null`.

### 4.9 Bridge POST

Only when both `bridgeUrl` and `bridgeSecret` are non-empty and there is at
least one offer to send. One request per beacon:

- `POST <bridgeUrl>`, headers `Content-Type: application/json` and
  `X-CVA-Bridge-Secret: <secret>`.
- Body: `{ beacon: <beacon name>, profile: <family id>, jobs: [{ title, company, location, url, text, easy_apply? }] }` (`easy_apply: true` only for LinkedIn Easy Apply cards; omitted otherwise)
  where empty `title`/`company`/`location`/`text` are sent as `null` and `url`
  is the card's link. The bot validates the URL (portal job URL with an id),
  rejects disabled portals with 400 and answers 503 while paused.
- The first scan of a beacon is bridged like any other (owner decision
  2026-10-09).
- 2xx: the sent offers' counters are cleared and they are marked seen.
- Network error or 5xx (outage, including the bot's pause): the offers stay
  unseen with no attempt burnt, so a later round resends them.
- 4xx (deterministic rejection): one attempt burnt per offer (shared counter
  with the detail pass); offers stay unseen until 3 attempts, then given up and
  marked seen.
- A round never reaches this step without a configured bridge (4.2).
- REMOVED by S4: the direct Telegram alert per sent offer, its first-scan and
  recovery suppression, and the "given up after 4xx" fallback alert. Nothing
  replaces them: the bot notifies the owner for every offer it accepts.

### 4.10 Concurrency guard

Only one round runs at a time in a service worker instance. A `run-now` or
alarm while a round runs returns at once with `skipped: true` (all counters 0)
and scans nothing; the running round is unaffected. The guard is released when
the round ends, whatever happened.

### 4.11 End of round

- Per scanned beacon, `lastCheck` `ok` entry and the seen list update (2.4).
- When the counters are in use for the beacon (a detail portal, or any failed
  bridge POST this round), offers no longer on the page lose their counter.
- Recovery membership updated (2.11).
- One write of `seenJobs`, `detailAttempts`, `recoveringBeacons`, `lastCheck`,
  `stats` (`lastRoundAt` now, `totalNotified` carried over).
- Toolbar badge: the sum of `newJobs` over every `lastCheck` entry as text, or
  empty when 0; dark green background.
- The summary (section 3) goes back to the caller.

### 4.12 Errors and logging

Skips (portal not enabled, not enabled on the bot, no grant), filtered counts,
given-up offers and caught errors go to the console. No error escapes a round;
an alarm round's rejection is only logged.

## 5. Portal adapters and the LinkedIn extractor

### 5.1 Adapter contract (unchanged; `portals/registry.js` header, `docs/portals.md`)

Each adapter is an object: `id`, `label`, `linkHost`, `settleMs`,
`noResultsMarkers` (pattern sources), `hostMatch(url)`, `buildSearchUrl({keywords, location, extra})`,
`extractCards` (page function), `canonicalUrl(id)`, optional
`isSessionExpired(tabUrl)`, `extractDetail` and `detailArgs`. Page functions
are serialized by `chrome.scripting.executeScript({ func, args })`: they must be
self-contained (no imports, no outer bindings) and have a stable name. The
registry order is LinkedIn first; `portalFor` falls back to LinkedIn.

### 5.2 LinkedIn adapter (rewritten in T3)

Identity: `id: 'linkedin'`, `label: 'LinkedIn'`, `linkHost: 'www.linkedin.com'`,
`settleMs: 2500`, no detail pass.

`hostMatch(url)`: true when the host is `linkedin.com` or any subdomain of it;
a URL without a scheme (`//www.linkedin.com/...` or bare host) is read as
https; anything unparsable is false.

`buildSearchUrl({ keywords, location, extra })`:
`https://www.linkedin.com/jobs/search/?` + query with `keywords` (trimmed, left
out when empty), `location` (trimmed, left out when empty so LinkedIn uses the
account default), `f_TPR=r604800` (past week) and `sortBy=DD` (newest first);
each `extra` entry overrides a parameter, and a `null`/`undefined`/empty value
removes it. Parameter order: keywords, location, f_TPR, sortBy, then extras.

`canonicalUrl(id)`: `https://www.linkedin.com/jobs/view/<id>/`.

`isSessionExpired(tabUrl)`: true when the URL is empty or contains `/login`,
`/authwall` or `/checkpoint`.

`noResultsMarkers`: LinkedIn's own empty-results copy in English ("no matching
jobs found"), Spanish (the "no se ha(n) encontrado / no se encontraron / no
hemos encontrado [ningún/ninguna] empleos/ofertas/trabajos/resultados" family
and "no hay ofertas [de trabajo] que coincidan") and Catalan ("no hem trobat
[cap] feina/feines/oferta(es)/resultats"), matched case-insensitively.

`extractCards()` page function, input: the loaded LinkedIn search results page;
output: a promise of `[{ id, title, company, location, link }]`:

1. Scroll the results list (the first of: the legacy results list container,
   the guest results list, `main`) and the window to the bottom, ignoring
   errors, so lazily loaded cards render; then wait 1.5 seconds.
2. Collect cards with three strategies; a later one runs only when the earlier
   ones found nothing:
   1. current UI: elements whose `componentkey` attribute is
      `job-card-component-ref-<digits>`; the digits are the id; lines = the
      element's visible text split by line.
   2. legacy list: `li.scaffold-layout__list-item` items containing a link to
      `/jobs/view/<digits>`; id from that link; lines = the item's text.
   3. last resort: every link to `/jobs/view/<digits>` on the page; the card is
      its closest `li`, else closest element with `data-job-id`, else its
      parent; lines = the link's first text line followed by the card's lines.
3. Per card: skip ids already collected (first occurrence wins); trim the lines;
   an Easy Apply badge line ("Easy Apply", "Solicitud sencilla", "Candidatura
   simplificada", "Postulación sencilla") sets `easyApply: true` on the card
   (absent otherwise) before being dropped; drop empty lines and LinkedIn "noise" lines (whole-line, case-insensitive):
   promoted / sponsored / viewed / applied / saved / new / reposted / easy apply
   badges in English and Spanish ("Promocionado", "Visto", "Aplicados",
   "Guardado", "Solicitud sencilla"), "today/yesterday/hoy/ayer", relative
   dates ("3 days ago", "hace 2 días", "publicado hace", "2 semanas"),
   applicant hints ("evaluating applicants", "actively evaluating", "evaluando
   solicitudes", "menos de 10/25 solicitantes") and network hints ("contacto(s)
   trabaja", "antiguo alumno", "de contacto").
4. Remaining lines by position: title (first, cut to 200 characters), company
   (second), location (third); missing ones are `''`. The fields are therefore
   positional: a card without a company line shifts the location into
   `company` (pinned by a characterization test; keep it unless the owner
   decides otherwise).
5. `link` is always the canonical URL of the id; `id` is the digit string.

Edge cases pinned by `tests/fixtures/portals/linkedin_*.html` (portals harness)
and the characterization harness: duplicate componentkey cards, unrelated
componentkey widgets, legacy items without a job link, relative and absolute
job links, cards with fewer than three lines.

## 6. Pages: feature checklist

The redesign may change layout, wording (locale files, S5) and control types.
It must keep these capabilities and their effects.

### 6.1 Popup

Reads `settings`, `beacons`, `lastCheck`, `stats`, `botConfig` and the
`check-jobs` alarm; re-renders on every `chrome.storage.local` change; writes
nothing itself.

- Overall status: local pause > bot not connected (bridge URL or secret
  missing: a warning with "Connect the extension to the bot first: Options ›
  Schedule and bot." and a "Connect to the bot" call to action that opens
  Options; public-release S2) > the bot's pause > idle (nothing to scan) >
  error (any `lastCheck` `error` or `session_expired`) > ok.
- Last round: relative time of `stats.lastRoundAt` ("<1m ago", "5m ago", "3h
  ago", "2d ago"; future times as "in ..."; "—" when unknown).
- Next round: the alarm's `scheduledTime` as a relative time; "paused" when
  locally paused, "paused by bot" when only the bot is paused.
- Total alerts (`stats.totalNotified`): Telegram counter, frozen after S4; the
  redesign may drop it.
- Beacon list (manual beacons only today, Findings F7) with a count; per beacon
  its name and state: disabled / not scanned yet / permission missing (enable
  the portal in options) / session expired / error with its time / "<n> jobs ·
  <time>"; and its `newJobs` count. Empty state pointing to Options.
- Pause/Resume control: sends `set-paused` with the opposite of the LOCAL
  `settings.paused` (a bot pause is lifted from Telegram with `/reanudar`, never
  from here); disabled while waiting; re-renders after.
- Check-now control: sends `run-now`, shows a busy state, then a one-line
  result from the summary: another round in progress / paused locally / paused
  by the bot / errors when nothing was scanned / "<n> scanned · <m> new" style
  counts; an `ok: false` answer or exception shows its error ("Failed: ..."),
  except `bridgeMissing: true`, whose error is shown as is.
- Open-options control (`chrome.runtime.openOptionsPage`).

### 6.2 Options page

Loads `settings`, `beacons`, `searches`, `titleFilter`, `titleFilterBackend`,
`profileTitleFilters`, `enabledPortals` (absent: the migration rule 2.7),
`botConfig`, `lastCheck`. Draft edits stay in the page until Save, except portal
toggles (immediate).

- Status summary: paused / bot not connected (bridge URL or secret missing,
  same text as the popup) / paused by the bot / no beacons / active.
- Missing bridge (public-release S2): the "Schedule and bot" section is
  highlighted, the empty (or, for the URL, invalid) bridge fields are marked
  (`is-missing`, `aria-invalid="true"`) and a hint says nothing is scanned
  until both are set. The marks follow the fields while typing.
- Bot config: on open, if the bridge is configured, `GET <origin>/config` with
  the secret header; a valid answer is stored as `botConfig` and shown (bot
  pause in the status, portal notes). Failures are silent.
- Manual beacons: add (new id, empty name/URL, enabled, profile `ai`); per row:
  enabled toggle, name, URL, profile (`ai`/`backend`), last scan time (local
  HH:MM, full timestamp on hover, "—" when never), test button (sends
  `test-scrape` with the trimmed URL; empty URL -> "URL required"; shows the
  number of cards and the first title, or the error), remove (asks for
  confirmation). Last-scan times refresh when `lastCheck` changes.
- Portals: every registry portal with an on/off control and notes ("permission
  missing: switch it off and on again" when enabled without a grant; "not
  enabled on the bot" when the bot's list excludes it).
  - Enabling: show `terms.js` `termsWarning(portal)` (LinkedIn's warns about
    its User Agreement and account restriction) and ask for confirmation;
    declined -> stays off. Then `chrome.permissions.request({ origins: ['https://<linkHost>/*'] })`;
    refused or failed -> stays off with a message. Granted -> add the id to
    `enabledPortals`, store it immediately, send `settings-updated`.
  - Disabling: `chrome.permissions.remove` for the origin (errors ignored),
    remove the id, store immediately, send `settings-updated`, message.
  - The search editor re-renders (portal availability changes).
- Auto searches: add (new id, empty texts, profile `ai`, title filter on,
  portals = `['linkedin']` only if LinkedIn is enabled, else none); per row:
  name, keywords, location, profile, title filter on/off, one checkbox per
  portal that can build search URLs (portals not enabled are shown as "off",
  not hidden), remove (confirmation). A read-only list shows the auto beacons
  the current draft would scan (name, URL, last scan time) and updates while
  typing; rapid edits must not duplicate rows.
- Suggest searches from the profile: `GET <bridge origin>/searches/suggested`
  with the secret header (needs a valid URL and a secret, else an error
  message; 401 -> secret rejected; other non-2xx -> status shown; network
  error -> bot unreachable). Valid rows (`searches.js` `parseSuggestions`) are
  listed, all pre-selected; the bot's `titleFilters` are adopted
  (`filter.js` `adoptProfileTitleFilters`: store `profileTitleFilters` or
  remove it when absent; if the owner has no saved filters, the editor shows
  the new defaults at once). "Add selected" merges with `mergeSuggestedSearches`
  (never edits or reorders existing searches, skips duplicates by normalised
  keywords + location) and reports added/skipped; Save persists.
- Scan cadence: interval in minutes, 1–1440, with a warning under 30 minutes
  (allowed). Default shown for a fresh install: 60.
- Bridge: URL and secret (secret masked; never exported).
- Title filter editor: choose the profile (AI / Backend); enabled toggle;
  include groups (one group per line, terms separated by commas); exclude terms
  (commas and/or new lines); blank entries dropped, surrounding quotes stripped
  (`filter.js` parse/format helpers). Switching profile keeps the unsaved edits
  of the one left.
- Maintenance: clear seen history (confirmation; sends `clear-history` without
  `beaconId`; shows the result).
- Backup: export and import (6.3).
- Save (button and Ctrl/Cmd+S). Validation, first failure wins, nothing stored
  on failure: an enabled beacon needs a name and a URL; beacon names must be
  unique; every search needs a name and keywords. Then it stores `settings`
  (interval clamped to 1–1440, a non-number reads as 15; trimmed URL/secret;
  current `paused`), `beacons`, `searches` (normalised), `titleFilter`,
  `titleFilterBackend`, and sends `settings-updated`. A transient "saved"
  state follows.
- When `settings.paused` changes elsewhere (popup), the status updates.
- REMOVED by S4: Telegram token and chat id fields and the "send test message"
  control.

### 6.3 Config file (export / import)

Export is a JSON file download (2-space indented) named
`linkedin-beacons-<YYYY-MM-DD>.json` today (the name may follow the new
branding). Content:

```json
{
  "version": 1,
  "format": "linkedin-beacons-config",
  "exportedAt": "<ISO timestamp>",
  "settings": {
    "botToken": "", "chatId": "",
    "intervalMinutes": 45,
    "bridgeUrl": "http://127.0.0.1:8765/bridge"
  },
  "beacons": [{ "id": "bcn_x", "name": "...", "url": "...", "enabled": true, "filterProfile": "ai" }],
  "searches": [{ "id": "s_x", "name": "...", "keywords": "...", "location": "...", "filterProfile": "ai", "portals": ["linkedin"], "titleFilter": "on" }],
  "titleFilter": { "enabled": true, "includeGroups": [["..."]], "excludeTerms": ["..."] },
  "titleFilterBackend": { "enabled": true, "includeGroups": [["..."]], "excludeTerms": ["..."] }
}
```

- `settings.botToken`/`chatId`: REMOVED by S4 (not exported; ignored on import).
- Never exported: `bridgeSecret`, `paused`, seen offers, `lastCheck`,
  `enabledPortals`, `botConfig`, `stats`, `profileTitleFilters`.
- The values are those currently in the page (stored values plus unsaved
  edits); exporting writes nothing.
- The format string `linkedin-beacons-config` and `version: 1` are the
  compatibility key: the rewrite must import the owner's existing files, and the
  characterization tests pin the exported format too (changing the exported
  string is a product decision to take with S6).

Import: pick a file; it must parse as JSON and have
`format === 'linkedin-beacons-config'`, else an error is shown and nothing
changes. Ask for confirmation ("replace the current configuration with N
beacons"; declined -> nothing changes). Then, immediately (no Save needed):

- `settings`: `intervalMinutes` from the file parsed as an integer and clamped
  to 1–1440 (non-number -> 15), `bridgeUrl` from the file (missing -> `''`),
  `paused` and `bridgeSecret` kept from this install (a secret inside the file
  is ignored).
- `beacons`: the file's list (missing -> `[]`), each cleaned: id kept or newly
  assigned, `name`/`url` as strings, `enabled` unless `false`, profile
  normalised.
- `searches`: the file's list normalised with unique ids when present; when the
  file has none (old export), the current ones are kept.
- `titleFilter`/`titleFilterBackend`: from the file, repaired field by field
  (`cleanTitleFilter`, each with its profile defaults) when present; when
  absent (old export), the STORED profiles are kept.
- These keys are stored, `settings-updated` is sent, the page re-renders and
  reports the number of beacons imported. Seen offers, `enabledPortals` and
  every other key are untouched.

## 7. Must not change

1. The storage keys and shapes of section 2, the legacy reads (`searches` as
   old beacons, `settings.titleFilter`, `notifyOnFirstRun` removal, absent
   `enabledPortals` derivation, absent `portalSeenRecovery`) and the rule that
   stored settings are never reset to defaults.
2. The alarm name `check-jobs`, its scheduling rules (4.1) and the pause
   semantics (local pause and bot pause; the popup toggles only the local one).
3. The message types and answers of section 3 (except `test-telegram`).
4. The round order and gates of 4.2–4.3: local pause before any fetch, the
   missing-bridge gate (public-release S2), bot config, enabled portals ∩ bot
   list, host grant, `no_permission` entries.
5. One hidden background tab per round, reused if stored, always disposed;
   25-second load timeout; session expiry stops the round with one notification.
6. Bridge contract (4.9) byte for byte: URL, headers, body shape, `null`s,
   first scan bridged, outage vs rejection rules, 3 attempts, 15 detail passes
   per beacon per round, scan reports (4.6) best-effort with a 5-second abort.
7. Seen semantics: per beacon id, filtered-out offers seen, retried offers not
   seen, 3000 newest kept.
8. Auto beacon ids `auto:<search id>:<portal id>` and names
   `<search name> · <portal label>`; auto beacons never stored.
9. The per-portal opt-in flow (terms warning, confirmation,
   `chrome.permissions.request`, immediate persistence, removal on disable) and
   optional host permissions per portal.
10. Export/import file format and rules (6.3), secret never exported, import
    never overwriting the secret or the pause.
11. LinkedIn extraction contract (5.2), the adapter contract (5.1) and the
    unchanged vendor-free modules.
12. Suggested searches, title filter editor, interval warning, test scrape,
    clear history, bot config display (6.2).

## 8. Removed by S4 (Telegram)

| Item | Today | After S4 |
| --- | --- | --- |
| `https://api.telegram.org/*` host permission | required | gone |
| `settings.botToken`, `settings.chatId` | read for alerts, edited in Options, exported/imported | never read, not edited, not exported, ignored on import; stored values harmless |
| `test-telegram` message | sends a test message | gone (falls under "unknown message type") |
| direct alert per sent offer in the round (HTML message with beacon, title, company, location, "View on <portal>") | sent unless first scan or recovering | gone; the bot notifies |
| 4xx give-up fallback alert | one alert per given-up offer | gone |
| `stats.totalNotified` increments, `summary.notified` | count of alerts | frozen value kept / field dropped or 0 |
| Options Telegram section, test button, "Telegram not set" status | present | gone |
| Popup "configured" = Telegram credentials; "sent to Telegram" result text | present | redefined (e.g. bridge configured) / gone |
| `recoveringBeacons` suppression of alerts | active | no consumer left (2.11) |

## 9. Characterization tests (T1) and what they pin

`tests/test_extension_characterization.py` runs
`tests/fixtures/extension_characterization_harness.js` (service worker) and
`tests/fixtures/extension_characterization_harness_pages.js` (popup, options)
over the fake browser in `tests/fixtures/extension_characterization_harness_lib.js`.
Entry points come from `manifest.json`; page functions run for real against
synthetic LinkedIn pages; timers are virtual. No internal function name, DOM
id, class or label is referenced.

| Test | Area | Pins |
| --- | --- | --- |
| `test_alarm_setup_interval_and_firing` | `alarm` | 4.1, defaults of 2.1/2.2/2.7 on a fresh install |
| `test_scrape_tab_lifecycle` | `tab` | 4.4, 4.3.6, `test-scrape` tab use |
| `test_legacy_storage_migrations` | `migrate` | 2.1 `notifyOnFirstRun`, 2.2 legacy `searches`, 2.6 resolution in a real round |
| `test_seen_jobs_dedup_across_rounds` | `seen` | 2.4, 4.7, 4.9 outage |
| `test_runtime_message_handlers` | `msg` | section 3 (not `test-telegram`), 4.10 |
| `test_round_contracts` | `round` | 4.5, 4.6, 4.9 payload, 4.11, 2.5 entries, auto beacons |
| `test_popup_run_now_and_pause_messages` | `popup` | 6.1 controls: `run-now`, `set-paused` from the local flag, open options; opening writes nothing |
| `test_options_export_config_format` | `export` | 6.3 export |
| `test_options_import_config_round_trip_and_validation` | `import` | 6.3 import, round trip, clamping, rejections |

Already covered elsewhere (keep green): `tests/test_extension_permissions.py`
(opt-in migration, gates, pause, `/config`), `tests/test_extension_scan_report.py`
and its background harness (reports, detail cap, retries, 4xx/5xx, recovery),
`tests/test_extension_searches.py`, `tests/test_extension_filter.py`,
`tests/test_extension_portals.py` (adapters, LinkedIn fixtures, detail
extractors). Several of those also make textual checks on the vendor code
(Findings F14) that T2–T5 must adapt without losing the behaviour they guard.

Harness model limits: page handlers must be attached to elements
(`addEventListener` or `on<event>`), page functions must be injected with
`executeScript({ func, args })`, and classic page scripts are run wrapped in a
function. If the redesign needs more of the DOM, extend the harness; never
weaken an assertion.

## 10. Findings (not fixed)

- F1. The legacy migration "`searches` -> `beacons` when `beacons` is absent"
  clashes with the current meaning of `searches` (auto searches): an install
  holding auto searches but no `beacons` key would turn them into broken manual
  beacons and lose them. Unreachable through the UI today (install seeds
  `beacons: []`, Save and import always write it) but worth a guard in the
  rewrite (e.g. migrate only entries that have a `url`). Not pinned beyond the
  legacy case.
- F2. Interval fallbacks disagree: fresh install 60, but an invalid/0 interval
  reads as 15 in the service worker, Options shows 15 for a missing interval
  and import falls back to 15.
- F3. The service worker has no upper bound for the interval; only Options and
  import clamp to 1440.
- F4. `test-scrape` is not covered by the concurrency guard and shares
  `scrapeTabId`: testing a beacon during a round reuses the round's tab and then
  closes it, so the round's current beacon fails.
- F5. `clear-history` leaves `detailAttempts` and `recoveringBeacons`; the
  per-beacon variant exists but no page sends it.
- F6. `lastCheck` entries are never pruned; the badge sums `newJobs` over all of
  them, including deleted or renamed beacons, and a beacon's `newJobs` keeps
  counting until its next scan.
- F7. The popup lists only manual beacons: auto-search beacons are invisible
  there, and with only auto searches the status reads "idle". Its "configured"
  test is the Telegram credentials, which S4 removes.
- F8. Import keeps the WORKING copy of the searches (unsaved edits included)
  when the file has none, while title filters fall back to the STORED ones;
  the comment in the code says the stored copy is the intent.
- F9. Save's duplicate-name check includes disabled beacons, so two disabled
  rows with empty names block saving with "Duplicate name".
- F10. The round filters "enabled with URL" twice (dead repetition), and the
  service worker logs every skipped beacon whenever it reads its state,
  including when only rescheduling the alarm.
- F11. Wording that no longer matches behaviour: the popup reports a first scan
  as "registered (silent)" and Options says clearing history means "no alerts",
  but first scans are bridged to the bot (owner decision 2026-10-09).
- F12. The session-expired `lastCheck` text and notification always say
  LinkedIn, whatever the portal (only LinkedIn has a session check today).
- F13. The export contains the Telegram token and chat id in clear text (gone
  with S4).
- F14. Existing tests coupled to the vendor implementation, to be adapted in
  T2–T5: textual checks on `background.js` function names (`runCheckRound`,
  `getState`, `sendBridge`) in `tests/test_extension_filter.py`,
  `tests/test_extension_scan_report.py`, `tests/test_extension_searches.py`;
  options/popup ids and strings in `tests/test_extension_filter.py`,
  `tests/test_extension_permissions.py`, `tests/test_extension_searches.py`;
  the page-function names `extractJobsFromPage`, `detectNoResultsInPage` and
  `extractDetailText` used to dispatch in `extension_background_harness.js`,
  `extension_permissions_harness.js` and asserted in
  `tests/test_extension_portals.py`; the manifest host list including Telegram
  in `tests/test_extension_permissions.py`; and the whole
  `tests/test_extension_fork.py` (retired by S6).
- F15. The round's `roundInProgress` guard is per service worker instance; if
  Chrome kills the worker mid-round the stored `scrapeTabId` survives and the
  hidden tab stays open until the next round reuses and closes it (handled, but
  the tab lingers).
- F16. While a portal page never finishes loading, each beacon costs the full
  25-second timeout; there is no per-round time budget.
- F17. No test covered (before T1) the alarm, the tab lifecycle, the legacy
  migrations, `clear-history`, `set-paused`, `test-scrape`, the concurrency
  guard, the popup controls or export/import; they are now pinned by section 9.
  Still unpinned: the popup/Options rendering details (on purpose, S5), the
  suggest-searches page flow (helpers are tested), Save validation messages,
  and the portal toggle flow in the page (manifest/structure checks only).

## 11. Decisions for the rewrite (parent, 2026-11-15)

These override the current behaviour described above; everything else stays.

- D1 (F1). The legacy `searches` -> `beacons` migration only moves entries that
  look like legacy manual beacons (they have a `url`); auto searches are never
  touched.
- D2 (F2, F3). One interval rule everywhere (service worker, Options, import):
  missing or invalid -> 60; otherwise rounded and clamped to 1..1440. The
  characterization tests already expect this (`0`/`'abc'`/`null` -> 60,
  `5000` -> 1440).
- D3 (F4). `test-scrape` never uses or closes the round's tab: while a round is
  running it answers `{ ok: false, error }` with a clear "a scan is running"
  message instead of scraping.
- D4 (F7). The popup lists manual and auto-search beacons, and "configured"
  means the bridge is configured (URL + secret), not Telegram.
- D5 (F11, F12). Texts say what happens now: first scans are sent to the bot;
  clearing history makes the next scan resend to the bot; the session-expired
  text names the portal.
- D6. Contracts that keep their names because other modules and tests use them:
  storage keys and shapes (section 2), message types (section 3), the portal
  adapter fields and page-function names (`extractJobsFromPage`,
  `detectNoResultsInPage`, `extractDetailText`), the alarm name `check-jobs`,
  the export `format`/`version`.
- D7 (S5). No external fonts or network requests from the pages: system font
  stack, light and dark themes (`prefers-color-scheme`); the CSP drops
  `fonts.googleapis.com`/`fonts.gstatic.com`.
