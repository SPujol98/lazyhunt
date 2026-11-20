# Testing

```bash
uv run pytest -q                      # the whole suite
uv run pytest -q tests/test_bridge.py # one file
```

CI runs the suite on Linux, Windows and macOS, and mypy, `ruff check` and
`ruff format --check .` on Linux (`.github/workflows/ci.yml`).

## Coverage

`pytest-cov` is already a dev dependency and `pyproject.toml` holds the
settings (branch coverage of `cva`, `bot.py` and `cli.py`):

```bash
uv run pytest -q --cov --cov-report=term   # least-covered files first
uv run pytest -q --cov --cov-report=html   # browsable report in htmlcov/
```

Coverage is a map of what no test reaches, not a target: add tests for the
error paths a user can hit and for anything that writes data, through the
public interfaces, rather than for the percentage.

## Platform-specific tests

Some behaviour only exists on some systems (POSIX permission bits, symlinks
without a privilege on Windows). `tests/portability.py` has the markers for
those; use them only when the behaviour itself is platform-specific. Text files
are always read and written with an explicit `encoding="utf-8"`: to find an
implicit one, run
`PYTHONWARNDEFAULTENCODING=1 uv run pytest -q -W always::EncodingWarning`.
