"""The ``config.local.toml`` writer of ``/setup`` (onboarding-v2, verifier
advisories): a symlinked file (dotfiles) is written through its link, which
stays a link, by the language switch and Confirm's undo. (The removal of an
old ``enabled_portals`` list keeps comments and links too:
``tests/test_onboarding_portals.py``.)
"""

import tomllib
from pathlib import Path

from tests.portability import symlink_or_skip
from tests.test_onboarding import _answer_everything, _setup_mode


def test_undo_puts_a_symlinked_file_back_through_the_link(tmp_path: Path) -> None:
    # Confirm's snapshot (cva/onboarding/snapshot.py) restores the target
    # of a symlinked file and keeps the link.
    from cva.onboarding.snapshot import Snapshot

    real = tmp_path / "dotfiles" / "cva.toml"
    real.parent.mkdir()
    real.write_text("before\n", encoding="utf-8")
    link = tmp_path / "config.local.toml"
    symlink_or_skip(link, real)
    snap = Snapshot.take([link], [])
    real.write_text("after\n", encoding="utf-8")
    snap.restore()
    assert link.is_symlink() and real.read_text("utf-8") == "before\n"


def test_language_switch_writes_through_a_symlinked_config(tmp_path: Path) -> None:
    d, _, config = _setup_mode(tmp_path, "en")
    real = tmp_path / "dotfiles" / "cva.toml"
    real.parent.mkdir()
    real.write_text("# mine\n", encoding="utf-8")
    symlink_or_skip(config, real)
    d.start()
    _answer_everything(d, "es")
    d.press("confirm")
    assert config.is_symlink()
    assert tomllib.loads(real.read_text("utf-8"))["ui_language"] == "es"
