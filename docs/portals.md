# Job portals

The browser extension reads job search pages in your own logged-in tab
and sends new offers to the bot (bridge). Each portal is an adapter module in
`extension/portals/`; adding one never touches the scan loop.

## Adapter contract

A plain object exported as default from `extension/portals/<id>.js`, listed in
`extension/portals/registry.js` (`PORTALS`). The authoritative description is
the header of `registry.js`; in short:

| Field | Meaning |
| --- | --- |
| `id` | stable id (`linkedin`); also the bot-side source |
| `label` | human name; used in auto beacon names and in the Telegram link text ("View on <label>") |
| `hostMatch(url)` | true when the URL belongs to the portal |
| `extractCards` | page function injected with `chrome.scripting.executeScript`; returns `[{id, title, company, location, link}]`. It is serialized, so it must be self-contained and keep a stable name |
| `noResultsMarkers` | regex sources for the portal's own "no results" copy (distinguishes a healthy empty scan from a blank or throttled page) |
| `canonicalUrl(id)` | offer URL from the id, or `null` when the id is not enough |
| `linkHost` | host of offer links |
| `settleMs` | wait after the tab loads, before extraction |
| `buildSearchUrl({keywords, location, extra})` | search URL for a profile-driven search |
| `isSessionExpired(tabUrl)` (optional) | login-wall guard |
| `extractDetail` (optional) | page function returning an offer's description text |
| `detailArgs` (optional) | arguments `background.js` injects `extractDetail` with (the shared extractor takes a description selector) |

`portalFor(url)` picks the adapter by host (LinkedIn is the fallback).
`background.js` resolves the adapter once per beacon and dispatches
everything through it.

## Auto searches

In Options, "Auto searches" holds a list of searches: `{name, keywords,
location, profile (ai|backend), portals[], titleFilter (on|off)}`. Each
(search x enabled portal) becomes an auto beacon `auto:<search id>:<portal>`
whose URL comes from that adapter's `buildSearchUrl`. Auto beacons are derived
every round, never stored with the manual beacons, scanned and reported like
them. `titleFilter: off` skips the cheap title pre-filter so every new card
goes to the bot, which judges it against the profile. Detail-pass portals
(InfoJobs) enrich at most 15 new offers per beacon per round; the rest wait
for the next round.

## Enabling portals (opt-in, safe defaults)

A fresh install scans no portal. In Options, "Portals" lists every registry
portal with a toggle. Enabling one shows its terms warning (LinkedIn gets a
strong one: its terms forbid extensions that scrape or automate the site and
the account may be restricted; `STRONG_TERMS` in `extension/portals/terms.js` maps each to its
locale key), asks for confirmation, then calls
`chrome.permissions.request` for that portal's host origin; disabling removes
the permission (`chrome.permissions.remove`). Portal hosts live in
`optional_host_permissions` in `manifest.json`; Telegram and the loopback bridge
stay required. The enabled ids are stored under the `enabledPortals` key.

- Migration: an install that predates the opt-in gets, once (update or browser
  startup, only while the key is absent), exactly the portals its searches and
  manual beacons already use, so the existing setup keeps scanning with no
  prompt. Chrome keeps host access already granted before the manifest change.
- Scanning (`getState` / `runCheckRound`) skips, and logs, a beacon whose portal
  is not enabled or whose browser permission is missing (`lastCheck` status
  `no_permission`); auto beacons of a disabled portal are not expanded. The
  search editor marks disabled portals "(off)".
- Precedence with the bot: when the bridge is configured, `GET <origin>/config`
  (secret header) is read when Options open and at the start of each round. If
  it lists `enabledPortals`, a portal must be enabled on BOTH sides to be
  scanned (the bot's list only narrows). A bot that does not answer leaves the
  local rules in force.
- Pause: `runCheckRound` and `run-now` return without scanning while
  `settings.paused` is set or the bot reports `paused` (`/pausa` in Telegram;
  lifted with `/reanudar`). The popup shows "paused by bot" and keeps its own
  Pause/Resume button for the local flag.
- Pace: a fresh install seeds `intervalMinutes` 60; stored settings are never
  rewritten. Options warn below 30 minutes (any value from 1 is still allowed).

## InfoJobs (verified against a real saved page)

- Search URL: `/jobsearch/search-results/list.xhtml?keyword=<kw>&provinceIds=<id>&searchByType=province&countryIds=17&sinceDate=_7_DAYS&sortBy=RELEVANCE&page=1`
  (params copied from a real search). Only `Barcelona = 9` is a
  verified `provinceIds`; any other location (or "remoto") drops
  `provinceIds`/`searchByType`, so the search is nationwide on the keywords.
  `sortBy=RELEVANCE` is what that real URL carried; `PUBLICATION_DATE` was
  not observed, so it is not used by default (override with `extra`).
- Cards: `.ij-OfferCard`; offer links look like
  `/<city>/<slug>/of-i<30 hex>?<tracking>`. The adapter strips the query and
  keeps the id with its leading `i` (the legacy `/of-<32 chars>` form is still
  accepted). The same offer can appear twice on a page; the id dedupes.
- No-results copy: the saved page was a results page, so the empty-state text
  is not verified; the Spanish markers stay. No result-count element was found
  in the page.
- The detail extractor (`extractDetail`) is unverified: no offer page was
  provided.
- Fixture: `tests/fixtures/portals/infojobs.html` is a sanitized copy (card
  markup kept; names, ids, logos, descriptions replaced).

## Detail pass (all portals but LinkedIn)

Every non-LinkedIn portal needs the offer text inline (the bot never fetches
those pages), so `background.js` loads each NEW offer's page in the same tab and
runs the portal's `extractDetail`. InfoJobs has its own extractor; Indeed,
Tecnoempleo, JobFluent and Manfred share `extension/portals/detail.js`
(`extractDetailText`): the portal's selector (Indeed: `#jobDescriptionText`),
else the largest of `main`, `article` and description-like containers
(`[class*="description" i]`, `[class*="descripcion" i]`, `[id*="description" i]`),
else the body text; trimmed, blank runs collapsed, capped at 19000 characters
(the bridge accepts 20000). It returns `null` under 200 characters or on a
challenge page, so such pages are never sent as offers.

Delivery rules: a job whose text cannot be read is not sent and not marked seen
(3 attempts, then given up); a job is marked seen only after the bridge answered
2xx. A network error or 5xx keeps the jobs unseen without burning attempts; an
HTTP 4xx (deterministic rejection) burns one attempt per job and at 3 the job is
given up (marked seen) and still gets its raw Telegram alert once. The challenge
check only matches specific markers (Cloudflare title/text/elements), never
generic words. Once per browser profile (`portalSeenRecovery`), the seen state of the auto
beacons of every portal but LinkedIn is cleared so offers lost before this fix
are resent, at most 15 per beacon per round; raw Telegram alerts stay off for
those beacons until their backlog is drained (`recoveringBeacons`).

**All of these detail selectors are UNVERIFIED against real pages** (only
synthetic fixtures exist: `tests/fixtures/portals/*_detail.html`). Only Indeed's
`#jobDescriptionText` is a known selector; Tecnoempleo, JobFluent and Manfred rely
on the generic fallback until a real offer page is saved and a selector added to
the adapter's `detailArgs`.

## Portal status

| Portal | Host | Mode | Verification |
| --- | --- | --- | --- |
| LinkedIn | `www.linkedin.com` | server guest flow + tab | original adapter |
| InfoJobs | `www.infojobs.net` | tab, inline-only | cards + search URL verified on a real page; detail extractor and empty-state copy unverified |
| Indeed | `es.indeed.com` | tab, inline-only | cards + search URL verified on a real page (16 cards); empty-state copy unverified |
| Tecnoempleo | `www.tecnoempleo.com` | in-tab reader only, never fetched by the bot | cards + empty copy + `pr` ids (Barcelona 240, Madrid 263) from a recon sample; detail unverified |
| JobFluent | `www.jobfluent.com` | in-tab reader only, never fetched by the bot | cards + empty copy from a recon sample; detail unverified; unknown cities fall back to Barcelona |
| Manfred | `www.getmanfred.com` | tab, inline-only | cards verified on a saved rendered page (3 cards); search ignores keywords; empty copy unverified |
| Adzuna | `www.adzuna.es` | in-tab reader only, never fetched by the bot (Cloudflare) | cards + search URL verified on a real page (10 cards); empty copy and detail unverified; aggregator, not suggested by default |
| foorilla (**experimental**) | `foorilla.com` | tab, inline-only, never fetched by the bot (offer panel is filled by htmx) | cards + detail panel verified on a real saved fragment/page; search param and empty copy unverified; global feed, not suggested by default |

## Indeed (verified against a real saved page)

- Search URL: `/jobs?q=<kw>&l=<place>&fromage=7&radius=25`. Barcelona maps to
  the exact place string Indeed produced, `Barcelona, Barcelona provincia`;
  other places are passed as typed; empty or "remoto" drops `l`/`radius`.
- Cards: `div.job_seen_beacon`; id = `data-jk` of `a[data-jk]` (16 hex).
  The card href is a tracking redirect, so the link is rebuilt as
  `https://es.indeed.com/viewjob?jk=<id>` (also the bot's canonical).
- Not verified: the empty-state copy (the saved page had results) and any
  detail page. `/analyze <url>` makes at most one plain GET for Indeed
  (`server_fetch` stays on); anti-bot normally blocks it and the bot degrades
  to the paste prompt. Only Tecnoempleo and JobFluent have zero server fetch.
- Fixture: `tests/fixtures/portals/indeed.html` is a sanitized 4-card copy.

## Tecnoempleo (in-tab only)

Design decision (2026-10-28): its robots.txt disallows AI crawlers, so it is
read only by the extension in your Chrome at low volume. Its
`SourceSpec` has `server_fetch=False`: `/analyze <url>` makes ZERO HTTP calls
and asks you to paste the text (extension beacons send inline text).

- Search URL: `/ofertas-trabajo/?te=<kw>&pr=<province>`; `pr` known for
  Barcelona (240) and Madrid (263); any other place omits `pr`.
- No recency parameter: cards are newest first and the adapter drops cards
  whose `dd/mm/yyyy` date is older than 7 days (a card without a date is kept).
- Cards: `div.p-3.border.rounded.mb-3.bg-white`; id `rf-<20 hex>` from the
  offer link; title `h3 a`; company `a.link-muted`; location first `<b>`.
- Empty copy: "No hemos encontrado ofertas de empleo".

## JobFluent (in-tab only)

Same decision as Tecnoempleo: read only in your Chrome tab,
`server_fetch=False`, `/analyze <url>` asks you to paste the text.

- Search URL: `/jobs-<city>?q=<kw>`; cities `barcelona`, `madrid`,
  `valencia`, `remote` (for "remoto"). An unknown city falls back to
  Barcelona: a generic `/jobs?q=` was not observed, so it is not used.
- Cards: `div.panel-offer` with id `offer-<6 hex>` (the card id is the 6 hex);
  link `.offer-title a[href]` (relative, query stripped, made absolute); the
  title reads "Title at Company" and is split on the last " at ". Cards carry
  no location: it is the searched city.
- No recency filter: cards whose `.published-date` ("added N days ago", weeks,
  months) is older than 7 days are dropped; today, yesterday, hours and a
  missing date are kept.
- Empty copy: "No <kw> jobs found in <City>".

## Manfred (minimal)

JS-rendered SPA; the adapter reads the rendered cards in your tab.

- Search URL: `/ofertas-empleo?onlyActive=true&currency=%E2%82%AC` plus
  `locations=<placeId>:Barcelona, España` for Barcelona only (the one value
  taken from your URL). Manfred has no keyword parameter, so keywords are
  ignored and any other place applies no location filter.
- Cards: links `/ofertas-empleo/<num>/<slug>`; id = the number; title `p`;
  company = logo `alt`; location after the "Ubicación" label.
- Not verified: the empty-state copy. Fixture: 3 sanitized cards (alt-only logos).

## Adzuna (in-tab only, aggregator)

Aggregator of other boards' offers; read only in your Chrome tab at low volume.
Its `SourceSpec` has `server_fetch=False` (Cloudflare blocked plain clients in
the research) and `suggest_by_default=False`: add it by hand in Options.

- Search URL: `/search?f=7&loc=<id>&q=<kw>` (copied from your URL: `f=7` = last
  7 days, `loc=206508` = Barcelona). Only Barcelona has a known `loc`; any other
  city drops it (nationwide on the keywords). "remoto" drops `loc` and adds
  `remote_only=1`, the portal's own "Teletrabajo" filter link in the page.
- Cards: `article[data-aid]`; id = `data-aid` (the same number is in
  `/details/<id>` and in the sponsored `/land/ad/<id>?...` links, which carry
  tracking), so the link is rebuilt as `https://www.adzuna.es/details/<id>`
  (also the bot's canonical). Title `h2`, company `.ui-company`, location
  `.ui-location`. The cards carry no date; `f=7` does the recency filter.
- Not verified: the empty-state copy (guessed from the "N Backend empleos en
  Barcelona" heading) and any detail page; the generic detail extractor is used.
- Fixture: `tests/fixtures/portals/adzuna.html`, 4 sanitized cards.

## foorilla (experimental, global feed)

**Experimental.** The search filter (`job_search`) has not been verified on
the live site, the cards carry no company name (it is only on the offer
page), and the feed is worldwide, so most offers are narrowed by the bot's
location gate and the title filter rather than by the search itself.

Worldwide hiring feed (`https://foorilla.com/hiring/jobs/`, an htmx fragment
that renders as a page). Robots.txt allows all (checked by hand), but the offer
panel `#main2` is filled by htmx, so a plain GET has no description:
`SourceSpec` has `server_fetch=False` and `/analyze <url>` asks you to paste the
text. The extension tab is the only reader. Not suggested by default: the feed
is global.

- Search URL: `/hiring/jobs/?job_search=<kw>`. No region or recency parameter
  exists in the saved fragment, so the list is the global latest and the bot's
  location gate plus the title filter narrow it; the location is never sent.
  `job_search` is the quick-search input's own request (`name="job_search"`,
  `hx-get="/hiring/jobs/"`); it was not tried live as a full page, so open the
  generated URL once to confirm it filters.
- Cards: `li.list-group-item` with `a.terminal-title`; its `href` is EMPTY, the
  path is in `hx-get="/hiring/jobs/<slug>-<id>/"`. Id = the trailing digits;
  link = `https://foorilla.com/hiring/jobs/<slug>-<id>/` (also the bot's
  canonical). The card has no company (detail page only); location is the
  right-hand text, with the `[R]` remote marker rendered as "(remote)".
  Because no card has a company, the adapter sets `cardsCarryCompany: false`
  and scan health never raises the "offers without a company" alert for it.
- Age: "55m ago", "1h ago", "2d ago", "3w ago"; cards older than 7 days are
  dropped, an unreadable age is kept. Level tags (`[EN]` = entry level, `[SE]`,
  `[MI]`, `[EX]`) have no field in the card schema and are ignored.
- Detail: the offer page renders the job inside `#main2`; the generic extractor
  is pointed at it (`detailArgs: ['#main2']`, verified on a saved offer page).
  Timing: the shared detail pass (`enrichJob`) waits for the tab to finish
  loading plus a fixed 2s settle; there is no wait-for-content. foorilla opts in
  to `strictPanel` (`detailArgs: ['#main2', true]`): if htmx has not filled
  `#main2` yet, the extractor returns null (no fallback to the page chrome), so
  the job is retried next round and given up after 3 attempts. Other portals
  keep the main/article/body fallback when their selector matches a short
  element.
- Empty copy: unverified guess.
- Fixture: `tests/fixtures/portals/foorilla.html` (5 sanitized items; two are
  older than 7 days and must be dropped) and `foorilla_detail.html`.

## Evaluated and rejected

- Welcome to the Jungle: searching requires signing up, so it is not read by
  the extension (2026-11-11).

## Adding a portal

Follow [adding-a-portal.md](adding-a-portal.md), by hand or by asking a
coding agent (Claude Code, Codex, …) to follow it. You provide the portal
name, a search URL you use, and a saved results page (Ctrl+S in the browser).
The recipe never browses the portal on its own. It then:

1. checks the ToS posture from what you tell it (personal use, low volume),
2. analyses the saved HTML,
3. runs `scripts/new_portal.py <id> <label> <host>` to scaffold the adapter,
   a fixture test and the registry entry,
4. writes the fixture tests first from a sanitized copy of the page (RED),
   then fills the adapter (GREEN),
5. adds the host to the manifest `optional_host_permissions` and teaches
   `cva/ingest/sources.py` the host,
6. runs the full verification.

Raw saved pages contain personal data and are never committed; only the
sanitized fixture is.

### After the merge: reload the extension

A new `optional_host_permissions` entry only takes effect when you reload the unpacked
extension in `chrome://extensions` (Reload). Then enable the portal in Options > Portals (Chrome asks for the permission). Then add a search for the portal in Options, and open the generated
search URL once to confirm the portal accepts the params.

### Bot-side: one entry in `sources.py`

The bot side of a new portal is a single `SourceSpec` entry in
`cva/ingest/sources.py` (`SOURCES`): its host(s) (`hosts`, registrable
domains), the offer-id regex (`job_id_re`, id in group 1), and optionally
`canonical_host`. Add tests in `tests/test_ingest_sources.py`. That entry is
everything the bot needs:

- `offers.origen` is the portal id taken from the offer URL host (the DB only
  requires a lowercase slug, so no migration per portal).
- The bridge accepts the URL, requires the inline offer text (portals are read
  in your logged-in tab; set `inline_only=False` only for a portal the server
  may fetch with the LinkedIn-style guest flow; a text-less job is dropped on
  its own and counted in the response as `dropped`, never failing the rest of
  the payload), and stores the offer under the
  normalized original URL (https, no query or fragment).
- The scheduler's inline retry and `fetch_offer` (one plain GET, no retries)
  route by that registry, not by portal name.

## Which searches add value (`/searches`)

`/searches` (`/busquedas`) lists every watched search (search × portal) over the
last 14 days: rounds (scan reports), offers found, offers that were **new** and
the last failure (`rate_limited`, `session_expired`, `error`), with the ones
that found nothing new first. Those are the candidates to remove in the
extension (Options › Searches). Rounds and found come from `beacon_scans`; the
new counts are appended by the bridge to the `app_state` key
`beacon_new_counts` (`{search: [[date, new], ...]}`, one row per day, pruned to
14 days), so they only accumulate from the version that added the command on.

An `error` failure also says why, when the extension could tell: the page did
not load in time (`timeout`), the scan tab was closed (`tab_closed`), no
internet connection (`offline`), the browser could not read the page
(`blocked`), or `unknown`. The extension sends it as the optional `reason` of
its scan report (`extension/scan_report.js`); the bot keeps the latest one per
search in the `app_state` key `beacon_last_errors`
(`{search: {at, status, reason}}`, pruned to 14 days, no schema change) and
shows it only next to the failure it belongs to. Reports without a `reason`
(older extensions) are accepted as before; an unknown code shows as
`unknown`.

## Terms of use and volume

The extension reads pages in your own logged-in browser tab, at the existing
interval and pacing, for your personal job search. Check each portal's terms
and robots.txt yourself before adding it, keep the volume low (no extra
polling, no parallel tabs, no server-side scraping), and do not redistribute
the data. If a portal's terms forbid automated reading, do not add it to the
public set.

## Posting guard

Offer text is cleaned before extraction and judging by
`cva/ingest/posting_guard.py`: zero-width characters, bidi overrides/isolates,
soft hyphens, BOMs, Unicode tag characters (decoded and reported) and
variation-selector byte runs are removed, and hidden HTML blocks in the
JSON-LD description are dropped. A flagged offer shows a line on the analysis
card ("Texto oculto en la oferta ..."). Idea adapted from ai-job-search
`tools/posting_guard.py` (MIT).

## Enabled portals and the kill switch

`enabled_portals` (config, optional) lists the portals allowed to feed the
bot, e.g. `enabled_portals = ["linkedin", "infojobs"]`. Unset (the default)
keeps the legacy behaviour: every registered portal is enabled. Unknown ids
are a startup error. The bridge answers 400 naming the portal for a job from a
portal that is not enabled, and the suggested searches only name enabled ones.

Portals are enabled in the extension alone: **Options › Portals** (each
switch shows the portal's terms warning and asks the browser for permission
to that one site), then the portals ticked in each search. `/setup` never
asks for portals nor writes `enabled_portals`; it is a manual advanced
setting. An `enabled_portals` list an older `/setup` wrote to
`config.local.toml` is removed once, on the bot's next start (only that key;
every other line and comment is kept); a list set by hand afterwards is kept
and enforced ([onboarding.md](onboarding.md#notes-and-limits)).

The global pause is `/pausa` and `/reanudar` in Telegram (stored in the
database, so it survives restarts); `paused = true` in the config is only the
default until the toggle is first used. While paused, beacon sweeps, the
auto-analysis and retry queue and the Gmail poll do nothing and `POST /bridge`
answers 503; manual commands such as `/analyze` still work.

The extension reads both from `GET /config` (same secret header as the other
bridge endpoints): `{"paused": false, "enabledPortals": ["linkedin", ...]}`.
