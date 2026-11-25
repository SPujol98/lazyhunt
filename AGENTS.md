# Instructions for coding agents

This file is for AI coding assistants (Claude Code, Codex, Cursor, Copilot and
others) working in a lazyhunt clone, usually on behalf of a job seeker who is
setting it up. Read it before you change anything.

## The user's profile is theirs to fill in, through the bot

Everything about the user (facts, base CVs, voice, salary, preferences) lives
in `profiles/<name>/`. The bot only trusts what the user told it.

- **Do not create, write or edit files under `profiles/` on the user's
  behalf**, even when they ask you to "set up lazyhunt" or paste their CV.
  A profile you fill in yourself skips the bot's checks, and the bot ends up
  claiming things the user never confirmed.
- **Send the user to `/setup` (`/configurar` in Spanish) in their Telegram
  bot.** It asks the questions, reads their base CVs and writes the profile
  files. See [docs/onboarding.md](docs/onboarding.md).
- **To change answers later:** `/setup <section>` (`/configurar <sección>`)
  re-asks one part (for example `/setup salary`), or the extension's
  **Options › Profile** tab edits the current values.
- **Only if the user explicitly asks you to hand-edit a profile file**,
  follow [profiles/README.md](profiles/README.md) strictly and never invent
  facts: every claim, skill, number and date must be something the user
  stated. If something is missing, ask the user; do not guess or fill gaps
  from a CV template, a job offer or your own knowledge.
- `profiles/demo/` is a fictional example ("Alex Rivera"). Copy its format,
  never its content.

## Secrets

Never read aloud, print, edit, commit or move `.env`, `credentials.json`,
Gmail tokens or anything that holds a token or a secret. `uv run lazyhunt init`
writes `.env`; if a secret is wrong, tell the user to run it again.

## Setup and running

Follow [docs/getting-started.md](docs/getting-started.md) in order:

```bash
uv sync
uv run lazyhunt init     # first-run setup (Telegram token, AI provider, profile skeleton)
uv run lazyhunt bot      # start the bot; then /start <code> and /setup in Telegram
uv run lazyhunt --help   # every command
```

The older forms `uv run cli.py …` and `uv run bot.py` still work.

## Changing the code

```bash
uv run pytest -q
uv run mypy --strict cva
uv run ruff check cva tests bot.py cli.py scripts
```

Read [CONTRIBUTING.md](CONTRIBUTING.md): behaviour changes come with tests,
and personal data (real CVs, offers, `.env`, databases) is never committed.
Tests use the fictional `profiles/demo/` profile, never the user's.
