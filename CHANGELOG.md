# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.0.0-beta.2] - 2026-11-21

Second pre-release for testers: use the AI you already have.

### Added

- **Codex CLI provider** (`llm_provider = "codex_cli"`): lazyhunt runs your own logged-in OpenAI Codex CLI, like Claude Code, with no API key. It runs read-only in an empty folder, never sees your credentials, and can research company facts on the web. Tested with a simulated CLI; please report how it behaves with a real Codex login.
- `uv run cli.py init` now lets you pick the AI provider: Claude Code, Codex CLI, the Anthropic API, or any OpenAI-compatible API with presets for OpenAI, Gemini, OpenRouter, Groq, Mistral, Ollama and LM Studio (local). It asks for the model and the API key (stored only in `.env`).
- Gemini through its OpenAI-compatible endpoint is now tested against the real service with a free-tier AI Studio key.

### Changed

- AI calls retry temporary failures (HTTP 500/502/503/529, timeouts, dropped connections) twice with a short backoff, and errors now show the provider's own message (for example a retired model name).
- CI runs the test suite on Linux, macOS and Windows; paths shown by the bot use `/` on every system and files are written with identical bytes everywhere.
- Test suite tidy-up: duplicate tests removed, data-only copies parametrized, shared helpers.

### Fixed

- On Windows, generated CV paths and saved files no longer differ from Linux/macOS (backslashes, CRLF line endings).

## [2.0.0-beta.1] - 2026-11-20

Pre-release for testers: the guided first run. Please report anything confusing or broken in an issue.

### Added

- Guided first run in Telegram: `uv run cli.py init` now only asks for the Telegram token, the AI provider and your Telegram id; the bot starts in *setup mode* and `/setup` walks you through your profile with buttons, in English or Spanish: contact details, target positions, level, spoken languages, main skills, experience, education, work mode and city, minimum salary, accepted offer languages, portals (each behind its terms warning) and your base CV PDFs.
- Confirm is all-or-nothing: it writes `preferences.toml`, `profile.toml`, the CV manifest and a minimal working profile (claims and judge files built only from your answers, never overwriting files you wrote by hand), activates the chosen portals, and the bot switches to normal mode at once. If anything fails, nothing is changed and you are told which file to fix.
- `/setup <section>` re-asks a single part (for example `/setup salary`), prefilled with your current answers.
- Options › Profile in the extension: view and edit your positions, level, languages, skills, experience, location, salary and accepted offer languages.
- Suggested searches follow your answers: your roles in your city when you accept on-site or hybrid work, plus a remote search when you accept remote work.
- Offer cards warn (⚠️) when the stated work mode or salary does not match your preferences; offers are never dropped for it.
- Location matching by city name for any city or country outside the detailed Catalonia data (mismatches are flagged, never discarded).
- "🔎 On LinkedIn / Google / InfoJobs" buttons on offer cards search the same offer elsewhere.
- [docs/running.md](docs/running.md): keep the bot running on Linux (systemd), macOS (launchd) or Windows (Task Scheduler); README troubleshooting, updating, uninstalling and glossary.

### Changed

- The bot offers and delivers only the CV languages that have a base CV in your manifest.
- Offer extraction and the AI judge run concurrently, roughly halving the analysis time per offer.
- The synced tracker spreadsheet is written in the background a few seconds after a change instead of blocking the bot.
- foorilla is labelled experimental.

### Fixed

- `uv run cli.py --help` and `check` work before `init` (no more missing-token traceback).
- Pending offers are saved again when the bot shuts down.
- foorilla scans no longer raise a false "offers without a company" alert.

## [0.5.0] - 2026-11-19

### Added

- "🗑 Don't use" button on proposed cover letters: removes the letter and its sources from the chat.
- Follow-up drafts show where to send them ("📧 Send to: …" and where the address comes from); an address is never invented.
- Cover letters get a deterministic quality score (florid phrases, intensifiers, sentence length, must-have coverage, length, grounding); a letter below 70 is rewritten once and the better one is kept. The score and the must-haves it does not mention are shown with the letter.

### Changed

- Plainer cover-letter style: short, specific first-person sentences without superlatives or "passion" language.
- Two `/help` button descriptions are shorter, to fit the new button within Telegram's message limit.

## [0.4.2] - 2026-11-19

### Fixed

- An offer outside your home region that lists "teletrabajo" only among its benefits ("vacaciones, teletrabajo y flexibilidad") is no longer treated as a remote role by the location gate.

## [0.4.1] - 2026-11-19

### Fixed

- Follow-up drafts (and any other AI answer) no longer reach Telegram as raw JSON with literal `\n` when the model double-encodes its reply.

## [0.4.0] - 2026-11-19

### Added

- LinkedIn offer cards show a discreet "⚡ Easy Apply" line ("⚡ Solicitud sencilla" in Spanish) when the posting is Easy Apply.
- The chosen cover letter and `/cartabase` also arrive as a one-page PDF in the CV font, ready to attach.
- README roadmap.

### Changed

- The database schema moves to v24 (a new nullable `easy_apply` column) and migrates automatically on start-up; existing data is unchanged.
- Errors a user can trigger (for example `/sent` on an offer that was never analysed) are now clear messages in the interface language; the CLI prints them instead of a traceback.
- fpdf2 2.9 (demo CVs regenerated, visually identical) and anyio 4.16.

### Fixed

- Two processes opening the same old database at once (for example the bot starting while a CLI command runs) no longer fail with "duplicate column name".
- A LinkedIn "Postulación sencilla" badge line is no longer taken as the company name.

## [0.3.0] - 2026-11-19

### Added

- English Telegram interface, selected with the new `ui_language` setting (`auto`, `en` or `es`; `auto`, the default, follows the system language). It covers messages, buttons, `/help`, the command menu, the HTML dashboard, the CLI and the interview-prep output. The setup wizard asks for it.
- English command names: `/rescue`, `/calibration`, `/pause`, `/resume` and `/basecover`. The Spanish names keep working.
- `profiles/README.md`: a guide to the profile layout and where each file goes.
- Browser extension: a one-click language selector (Auto, EN, ES) in Options; Auto keeps the browser language.
- README: an illustrated offer card (a real bot message rendered with demo data).

### Changed

- `ui_language` defaults to `auto`: the system language (Spanish if it starts with `es`, English otherwise). If you already use the Spanish interface and want it regardless of the system, set `ui_language = "es"` in `config.local.toml` to keep it exactly as it was.
- CVs, cover letters and follow-up drafts still follow the language of each offer, whatever `ui_language` is.

## [0.2.0] - 2026-11-18

### Added

- User-defined role families: declare `[families.<id>]` in `preferences.toml` (label, CV languages, title terms, title filter, score threshold, search roles, cover-letter emphasis) and add base CVs to `cv_base_pdfs/manifest.json` (optional `delivery_name`). Telegram buttons, `/cv`, `/status`, the CLI, cover letters, salary rows, search suggestions and the browser extension follow your families. See `docs/role-families.md`.

### Changed

- The database schema moves to v23 and migrates automatically on start-up; existing data is unchanged. A profile with no `[families]` table behaves exactly as before.
- README rewritten as a step-by-step guide, with artwork and extension screenshots.
- Dependency updates: pydantic 2.14, python-telegram-bot 22.8, mypy 2.4, pytest 9.2 and the GitHub Actions used by CI.

## [0.1.0] - 2026-11-18

First public release.

### Added

- Chrome/Edge (Manifest V3) browser extension that reads saved searches in your own logged-in tab, with every portal opt-in and off by default.
- Local bridge (loopback only, shared-secret protected) between the extension and the bot.
- LLM offer judging with a 0-100 score, a verdict against your claims database, and deterministic gates for location, language, eligibility and walls.
- Tailored one-page CVs and cover letters built only from facts in your profile.
- Telegram bot as the only interface, with a kill switch (`/pausa`, `/reanudar`).
- Optional read-only Gmail status proposals for application replies.
- `cli.py init` interactive setup wizard.

[Unreleased]: https://github.com/SPujol98/lazyhunt/compare/v2.0.0-beta.2...HEAD
[2.0.0-beta.2]: https://github.com/SPujol98/lazyhunt/compare/v2.0.0-beta.1...v2.0.0-beta.2
[2.0.0-beta.1]: https://github.com/SPujol98/lazyhunt/compare/v0.5.0...v2.0.0-beta.1
[0.5.0]: https://github.com/SPujol98/lazyhunt/compare/v0.4.2...v0.5.0
[0.4.2]: https://github.com/SPujol98/lazyhunt/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/SPujol98/lazyhunt/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/SPujol98/lazyhunt/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/SPujol98/lazyhunt/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/SPujol98/lazyhunt/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/SPujol98/lazyhunt/releases/tag/v0.1.0
