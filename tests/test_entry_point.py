"""``lazyhunt`` console command (beta.8): ``uv run lazyhunt <verb>``.

``[project.scripts]`` maps ``lazyhunt`` to :func:`cva.cli_entry.main`, which
runs every ``cli.py`` verb plus ``bot`` (start the Telegram bot). The old
``uv run cli.py …`` and ``uv run bot.py`` forms keep working.
"""

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import bot
import cli
from cva import cli_entry

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def no_token_clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A working directory with no ``.env``, no TOML and no token in the env."""
    clone = tmp_path / "clone"
    clone.mkdir()
    monkeypatch.chdir(clone)
    monkeypatch.delenv("CVA_TELEGRAM_TOKEN", raising=False)
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    return clone


def test_pyproject_declares_the_lazyhunt_command() -> None:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["scripts"]["lazyhunt"] == "cva.cli_entry:main"


def test_lazyhunt_help_lists_every_verb_and_bot(
    no_token_clone: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        cli_entry.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage: lazyhunt" in out
    for verb in ("init", "bot", "check", "analyze", "cv", "beacon", "gmail-auth"):
        assert verb in out, verb


def test_lazyhunt_bot_starts_the_bot(
    no_token_clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setenv("CVA_TELEGRAM_TOKEN", "123:abc")
    monkeypatch.setattr(bot, "main", lambda: calls.append("bot"))
    cli_entry.main(["bot"])
    assert calls == ["bot"]


def test_lazyhunt_bot_without_a_token_points_at_init(
    no_token_clone: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(bot, "main", lambda: pytest.fail("bot started"))
    with pytest.raises(SystemExit) as exc:
        cli_entry.main(["bot"])
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == cli.NO_TOKEN_MESSAGE
    assert captured.out == ""


def test_lazyhunt_bot_rejects_extra_arguments(no_token_clone: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        cli_entry.main(["bot", "--nope"])
    assert exc.value.code == 2


def test_entry_point_works_outside_the_repository_folder(tmp_path: Path) -> None:
    """A console script does not run from the repository folder: the entry
    point still finds ``cli.py`` and ``bot.py``."""
    result = subprocess.run(
        [sys.executable, "-c", "from cva.cli_entry import main; main(['--help'])"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "LC_ALL": "C.UTF-8"},
    )
    assert result.returncode == 0, result.stderr
    assert "usage: lazyhunt" in result.stdout


def test_cli_py_keeps_its_own_name(
    no_token_clone: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert "usage: cli.py" in out and "bot" in out
