# Adding a job portal

A step-by-step recipe for adding a portal, written so that you or any coding agent (Claude Code, Codex, …) can follow it: point the agent at this file with a search URL and a saved results page.

Add one job portal as a pluggable adapter, tests first. The contract and the
background are in `docs/portals.md`; the adapter contract is documented at the
top of `extension/portals/registry.js`.

## Hard rules

- NEVER browse, fetch or scrape the live portal yourself. Work only from what
  the owner provides: the portal name, one search URL and one saved results
  page (browser Ctrl+S, "Webpage, HTML only" or complete). Ask for anything
  missing and wait.
- NEVER commit a raw saved page. The fixture is a SANITIZED copy: strip names,
  emails, phone numbers, tokens, cookies, `<script>` payloads and any personal
  data; keep the portal's real card markup and 3+ cards.
- Tests first (TDD): write the fixture and expected cards, watch the test go
  RED, then fill the adapter until GREEN. Record both honestly.
- Personal use, low volume: the extension reads pages in the owner's own
  logged-in tab at the existing pace. Do not add faster polling, parallel tabs
  or server-side fetching for the portal.

## Workflow

1. **Inputs.** The owner gives: portal name (becomes the label), the id (short
   lowercase, e.g. `tecnoempleo`), the host (e.g. `www.tecnoempleo.com`), a
   search URL for a query they use, and the saved results page path.
2. **ToS / robots posture.** From the information the owner provides (their
   own check of robots.txt and the terms), note in the feature document:
   personal use only, owner's own session, low volume, no redistribution. If
   the owner says the terms forbid automated reading, stop and report; do not
   proceed. Do not fetch robots.txt yourself.
3. **Analyse the saved HTML** (read it locally). Find: the card container and
   a stable selector; the offer id and the link pattern (and whether the id
   alone can rebuild the canonical URL, else `canonicalUrl: () => null`);
   title, company, location; the portal's "no results" copy; and, from the
   search URL, the params for keywords, location and recency. Decide whether
   the cards need a per-offer detail pass (`extractDetail`) because the list
   lacks the description; most portals only need the cards, the bot judges
   the title/company/URL otherwise.
4. **Scaffold.** `uv run python scripts/new_portal.py <id> "<Label>" <host>`.
   It refuses to overwrite and prints the next steps. It creates the adapter
   skeleton, fixture placeholders, a pytest file, and edits the registry.
5. **Fixture tests first.** Put the sanitized page in
   `tests/fixtures/portals/<id>.html` and the cards it must yield in
   `<id>.expected.json` (`cards`, `no_results_text`, `urls`). Run
   `uv run pytest -q tests/test_portal_<id>.py`: it must be RED. The harness
   uses `tests/fixtures/minidom.js`, which supports only compound selectors
   (tag, `.class`, `[attr]`, `[attr=v]`, `[attr^=v]`, `[attr*=v]`), `closest`,
   `getAttribute`, `parentElement` and a simplified `innerText`; keep the
   extractor to those, or extend minidom with a test.
6. **Fill the adapter** in `extension/portals/<id>.js` until GREEN:
   `extractCards` (self-contained: it is serialized by
   `chrome.scripting.executeScript`; keep its name), `noResultsMarkers`,
   `hostMatch`, `buildSearchUrl` (use `slugify` from `../slug.js` for path
   locations; add the observed recency param), `canonicalUrl`, optional
   `extractDetail` / `isSessionExpired`. The registry entry already exists.
7. **Wire the rest.**
   - Add `https://<host>/*` to `host_permissions` in
     `extension/manifest.json`. Tell the owner: reload the unpacked extension
     in `chrome://extensions` and accept the new permission prompt; until then
     the portal cannot be read.
   - Teach the bot the host with ONE `SourceSpec` entry in `SOURCES` in
     `cva/ingest/sources.py` (`hosts`, `job_id_re`, optional `canonical_host`),
     with tests in `tests/test_ingest_sources.py`. `offers.origen`, the bridge
     validation, the inline-text rule, the scheduler retry and the fetch
     routing all read that registry; nothing else bot-side is needed.
8. **Full verification**, reporting `<command>: <result>` for each:
   `uv run pytest -q`, `uv run mypy --strict cva`,
   `uv run ruff check cva tests bot.py cli.py scripts`,
   `uv run ruff format --check <touched py files>`, and `node --check` on
   every changed JS file. Commit with Conventional Commits
   (`feat(portals): add <id> adapter`), fixtures and tests in the same commit.
9. **Close.** Report the owner actions: reload the extension, accept the
   permission, add a search for the portal in Options, open the generated
   search URL once to confirm the params work (the recipe could not verify them
   live), and that the first scan of a new beacon is analysed without cards
   until the bot's gate passes.

## Done when

- `tests/test_portal_<id>.py` is GREEN and was observed RED first.
- The portal is in the registry, the manifest `optional_host_permissions` and
  `cva/ingest/sources.py`, and its user-facing label and terms read well in
  `extension/_locales` if it needs its own strings.
- No raw saved page or personal data is in the diff (`git diff --cached`).
