# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/SPujol98/lazyhunt/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/SPujol98/lazyhunt/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/SPujol98/lazyhunt/releases/tag/v0.1.0
