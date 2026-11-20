"""The ``config.local.toml`` writer of ``/setup`` (onboarding-v2, verifier
advisories): a comment ending the ``enabled_portals`` line is kept, and a
symlinked file (dotfiles) is written through its link, which stays a link,
by the portals step, the language switch and Confirm's undo.
"""

import tomllib
from pathlib import Path

import pytest

import bot
from cva.i18n import t
from tests.test_onboarding import Driver, _answer_everything, _setup_mode
from tests.test_onboarding_sections import _set_up, _start


def _portals_run(d: Driver) -> None:
    _start(d, "/setup portals")
    for portal in ("linkedin", "infojobs", "indeed"):
        chosen = d.context.user_data["onboarding"]["a"].get("portals") or []
        if (portal in ("linkedin",)) != (portal in chosen):
            d.press(f"t:{portal}")
            if portal not in chosen:
                d.press("yes")
    d.press("done")
    d.press("confirm")


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (
            'enabled_portals = ["indeed"]  # my portals\n',
            'enabled_portals = ["linkedin"]  # my portals\n',
        ),
        (
            'enabled_portals = [\n  "indeed",\n]   # mine # really\n',
            'enabled_portals = ["linkedin"]   # mine # really\n',
        ),
        (
            "enabled_portals = ['in#deed']\n",
            'enabled_portals = ["linkedin"]\n',
        ),
    ],
)
def test_setup_portals_keeps_a_trailing_comment(
    tmp_path: Path, before: str, after: str
) -> None:
    d, _ = _set_up(tmp_path)
    config: Path = d.context.bot_data[bot._CONFIG_PATH_KEY]
    config.write_text(f'# top\n{before}ui_language = "en"\n', encoding="utf-8")
    _portals_run(d)
    assert d.replies[-1].startswith(t("onboarding.saved", "en"))
    assert config.read_text("utf-8") == f'# top\n{after}ui_language = "en"\n'


def test_setup_portals_writes_through_a_symlinked_config(tmp_path: Path) -> None:
    d, _ = _set_up(tmp_path)
    config: Path = d.context.bot_data[bot._CONFIG_PATH_KEY]
    real = tmp_path / "dotfiles" / "cva.toml"
    real.parent.mkdir()
    real.write_text(config.read_text("utf-8"), encoding="utf-8")
    config.unlink()
    config.symlink_to(real)
    _portals_run(d)
    assert d.replies[-1].startswith(t("onboarding.saved", "en"))
    assert config.is_symlink() and config.resolve() == real.resolve()
    assert tomllib.loads(real.read_text("utf-8"))["enabled_portals"] == ["linkedin"]


def test_undo_puts_a_symlinked_file_back_through_the_link(tmp_path: Path) -> None:
    # Confirm's snapshot (cva/onboarding/snapshot.py) restores the target
    # of a symlinked file and keeps the link.
    from cva.onboarding.snapshot import Snapshot

    real = tmp_path / "dotfiles" / "cva.toml"
    real.parent.mkdir()
    real.write_text("before\n", encoding="utf-8")
    link = tmp_path / "config.local.toml"
    link.symlink_to(real)
    snap = Snapshot.take([link], [])
    real.write_text("after\n", encoding="utf-8")
    snap.restore()
    assert link.is_symlink() and real.read_text("utf-8") == "before\n"


def test_language_switch_writes_through_a_symlinked_config(tmp_path: Path) -> None:
    d, _, config = _setup_mode(tmp_path, "en")
    real = tmp_path / "dotfiles" / "cva.toml"
    real.parent.mkdir()
    real.write_text("# mine\n", encoding="utf-8")
    config.symlink_to(real)
    d.start()
    _answer_everything(d, "es")
    d.press("confirm")
    assert config.is_symlink()
    assert tomllib.loads(real.read_text("utf-8"))["ui_language"] == "es"
