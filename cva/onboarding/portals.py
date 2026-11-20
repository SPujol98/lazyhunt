"""Portal terms warnings for the setup questionnaire (step 8).

The texts are the browser extension's own (``extension/_locales/<lang>/
messages.json``), chosen exactly as ``extension/portals/terms.js`` does: a
portal with a strong warning has its own key (``terms`` + the capitalised
id of a portal the bot registers, e.g. ``termsLinkedin``), every other one
gets ``termsPortal`` with its label as ``$p1$``. Reading the extension's
catalogue keeps one source for what the owner is told before enabling a
portal, in the options page and in Telegram.
"""

import json
import stat
import tomllib
from collections.abc import Sequence
from functools import cache
from pathlib import Path
from typing import Final

from cva.config import check_toml_settings
from cva.fsutil import atomic_write
from cva.i18n import Lang, t
from cva.ingest.sources import SOURCES
from cva.onboarding.snapshot import write_target
from cva.setup.wizard import toml_set_key

LOCALES_DIR: Final[Path] = (
    Path(__file__).resolve().parents[2] / "extension" / "_locales"
)


@cache
def _messages(lang: Lang, locales_dir: Path) -> dict[str, str]:
    """``key -> message`` of the extension catalogue; ``{}`` when unreadable."""
    try:
        data = json.loads((locales_dir / lang / "messages.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        key: entry["message"]
        for key, entry in data.items()
        if isinstance(entry, dict) and isinstance(entry.get("message"), str)
    }


def portal_ids() -> tuple[str, ...]:
    """Every registered portal id, in registry order."""
    return tuple(SOURCES)


def portal_label(portal: str) -> str:
    spec = SOURCES.get(portal)
    return spec.label if spec is not None else portal


def terms_warning(portal: str, lang: Lang, locales_dir: Path = LOCALES_DIR) -> str:
    """The warning the extension shows before enabling ``portal``, in ``lang``."""
    label = portal_label(portal)
    messages = _messages(lang, locales_dir)
    # The key comes from the registered portal id only: no portal names here.
    strong = messages.get(f"terms{portal.capitalize()}") if portal in SOURCES else None
    if strong is not None:
        return strong
    generic = messages.get("termsPortal")
    if generic is not None:
        return generic.replace("$p1$", label)
    return t("onboarding.portals.terms_missing", lang, portal=label)


def read_enabled_portals(config_path: Path) -> list[str] | None:
    """``enabled_portals`` of ``config_path`` (registered ids, registry
    order); ``None`` when the file or the key is missing or unreadable."""
    try:
        data = tomllib.loads(config_path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get("enabled_portals")
    if not isinstance(value, list):
        return None
    return [p for p in portal_ids() if p in value]


def write_enabled_portals(config_path: Path, portals: Sequence[str]) -> list[str]:
    """Set ``enabled_portals`` in ``config_path`` (``config.local.toml``) to
    ``portals`` and return the list written (registry order).

    Only ids the bot registers are accepted (``ValueError`` otherwise). The
    setup wizard's line-preserving writer changes that one line (every other
    line and comment keeps its bytes); the result, merged over the
    ``config.toml`` next to it, must load with the :class:`~cva.config.Settings`
    validators before the file is replaced atomically (its mode kept).
    ``ValueError`` / ``OSError`` leave the file as it was."""
    unknown = [p for p in portals if p not in SOURCES]
    if unknown:
        raise ValueError(f"unknown portal id(s): {', '.join(unknown)}")
    chosen = [p for p in portal_ids() if p in portals]
    # Bytes as they are (``read_text`` would turn CRLF into LF).
    base = config_path.read_bytes().decode("utf-8") if config_path.is_file() else ""
    text = toml_set_key(base, "enabled_portals", chosen)
    shared = config_path.parent / "config.toml"
    documents = [tomllib.loads(shared.read_text("utf-8"))] if shared.is_file() else []
    check_toml_settings(*documents, tomllib.loads(text))
    mode = stat.S_IMODE(config_path.stat().st_mode) if config_path.is_file() else None
    # A symlinked config (dotfiles) is written through: the link stays.
    atomic_write(write_target(config_path), text, mode=mode)
    return chosen


def enabled_portals_line(portals: list[str]) -> str:
    """The ``config.local.toml`` line that enables exactly ``portals``."""
    return "enabled_portals = [" + ", ".join(json.dumps(p) for p in portals) + "]"
