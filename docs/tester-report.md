# Tester report (for your coding assistant)

This file is written for a coding assistant (Claude Code, Codex, Gemini CLI…)
opened in your lazyhunt folder. Tell it:

> Read `docs/tester-report.md` and follow it.

It gathers how your LazyHunt is set up and how it has been working, and writes
everything to one file, `tester-report.md`, in the lazyhunt folder. You then
read it, add your own notes, and send it to the maintainer.

---

## Instructions for the assistant

You are preparing a diagnostic report for the LazyHunt maintainer. Work
**read-only**: do not change any file, setting, database row or profile except
creating `tester-report.md` at the repository root. Write the report in the
language the user speaks to you.

### Privacy rules (mandatory)

Never copy into the report:

- any token, key, secret or password (`.env`, `TELEGRAM_*`, `*_API_KEY`,
  `bridge_secret`, OAuth files, `owner_telegram_id`);
- the CV, the claims (`claims.json`), the voice file, cover letters or the
  user's name, email, phone or address;
- full offer texts (titles, companies and portals are fine).

If you are not sure whether something is private, leave it out and mention it
under "Left out".

### 1. Environment

- Operating system and version, Python version (`uv run python --version`),
  browser the user says they use.
- LazyHunt version: `git describe --tags --always` and `git status --short`
  (only say whether there are local changes, not their content).
- Is the bot running right now? (Ask the user if you cannot tell.)

### 2. Settings (allow-list only)

From `config.local.toml` copy **only** these keys if present:
`ui_language`, `llm_provider`, `llm_model`, `llm_base_url` (host only),
`match_mode`, `threshold`, `enabled_portals`, `profile_dir`,
`tailored_cv`. Write "not set" for missing ones. Copy nothing else from that
file.

### 3. Search preferences

From `<profile_dir>/preferences.toml` copy the sections `search_targets`,
`location`, `gates`, `title_filters` and `stack_wall` as they are (these are
job preferences, not personal data). Do not copy `cover_letter`.

### 4. How it has been working (database, read-only)

Find the SQLite database under `data/` (the `*.db` file the bot uses). Open it
**read-only** (`sqlite3 "file:<path>?mode=ro" -readonly` or Python with
`mode=ro`). First look at the schema (`.tables`, `.schema offers`), then report:

- offers per state (`estado`) and per portal (derive the portal from the
  offer URL host) over the last 14 days and in total;
- how many offers per day arrived in the last 14 days;
- the score distribution of analysed offers (from `match_results`): count,
  min, median, max, and how many are at or above the threshold;
- the 10 most recent offers: id, date, portal, title, company, state, score;
- per watched search (`beacon_scans`): rounds in the last 14 days, cards found,
  last status and last error, if those columns exist;
- anything in `app_state` whose key mentions an outage, a pause or a retry
  (key and value only).

If a table or column does not exist, say so and move on; do not guess.

### 5. Recent bot log

If the user can give you the bot's terminal output or a log file, include the
last 50 lines, after removing any line that contains a token, key or secret.
Otherwise ask the user to paste them.

### 6. Questions for the user

Ask these and write the answers in the report:

1. Which searches did you create in the extension (keywords, location,
   portals)? A screenshot of Options › Searches is fine; attach it separately.
2. Which portals are switched on in Options › Portals?
3. Name 2–3 offers you received that did NOT fit you, and why.
4. Name 2–3 offers that fit you but never arrived or scored low (links).
5. Did any cover letter or CV contain something wrong (wrong company, a claim
   that is not true, odd wording)?
6. Any error message, confusing step or thing you expected to work and did not?

### 7. Write the report

Create `tester-report.md` with one section per step above, plus:

- **Observations:** anything that looks wrong to you (for example a portal
  with many cards found but nothing new for days, all scores far below the
  threshold, repeated errors, an outage that never cleared);
- **Left out:** what you skipped for privacy or because it did not exist.

Then tell the user where the file is and remind them to read it before
sending it.
