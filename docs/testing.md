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

## Measuring the judge (`judge-eval`)

`uv run cli.py judge-eval` judges every offer of the labelled eval bank (the
`eval/` folder of your `profile_dir`, or `--labels <path>`) with the
configured LLM and prints, per offer, the label and the judge's score and
verdict, then the MAE and the verdict agreement. It spends LLM quota and never
runs in the test suite (tests use fake LLMs).

The judge gets exactly what the bot's analysis gives it for your profile: the
profile's judge persona, role families, `ui_language` and the deterministic
gates (a gate FAIL forces `discard`), so the numbers hold for any profile. The
outcome examples you approve with `/calibration` are left out on purpose, so
the eval stays an independent check. Without a `profile_dir` it keeps the original owner's persona.
Add `--details` to print each offer's judgment as well (score, verdict, walls,
gaps and the judge's rationale next to the label).
