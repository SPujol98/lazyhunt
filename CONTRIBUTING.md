# Contributing to lazyhunt

Thanks for helping. Please read the [Code of Conduct](CODE_OF_CONDUCT.md) first. Security issues go through the private process in [SECURITY.md](SECURITY.md), never public issues.

## Development setup

```bash
uv sync
uv run pre-commit install
```

Python 3.12 and [uv](https://docs.astral.sh/uv/) are required. The extension tests also use Node.js when it is installed (they are skipped otherwise).

## Checks before opening a PR

```bash
uv run pytest -q
uv run mypy --strict cva
uv run ruff check cva tests bot.py cli.py scripts
```

CI runs the same commands, plus secret scanning and CodeQL.

## Guidelines

- Use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
- Behaviour changes come with tests. Fix a bug by first adding a test that fails.
- Keep PRs focused and update docs or help text when you change a command, option or message.
- **Never commit personal data**: real CVs, job postings, `.env`, tokens, `credentials.json`, databases or collected listings. Use the synthetic demo profile in `profiles/demo/` for examples and tests.
- **New portals**: read the portal's terms of service first, keep the adapter off by default, and follow [docs/portals.md](docs/portals.md).
