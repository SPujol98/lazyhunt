"""Validated, atomic writers for ``preferences.toml`` and ``profile.toml``.

The onboarding (and any later ``/setup`` step) edits a profile file by reading
its document (:func:`read_document`, the plain mapping ``tomllib`` returns),
changing keys and handing the mapping back to :func:`write_preferences` or
:func:`write_profile`. Each writer:

1. emits TOML for the KNOWN schema only (:func:`toml_document`): strings,
   integers, booleans, lists of strings, lists of lists of strings and nested
   tables. Anything else (floats, dates, ``None``, arrays of tables, mixed
   lists) raises :class:`~cva.profile_identity.ProfileError`; no TOML library
   is needed to write;
2. parses the text back with ``tomllib`` and checks it is the same document;
3. validates it with the strict loader (``preferences_from_data`` /
   ``identity_from_data``), so nothing the bot would refuse at start-up is
   ever written;
4. keeps the previous file as ``<name>.bak`` and replaces the file through a
   temporary file + ``os.replace`` (:func:`cva.fsutil.atomic_write`).

The file is REBUILT from the document: hand-written comments, blank lines and
key order inside a table are not preserved (the ``.bak`` keeps the previous
text). A document, not a :class:`~cva.profile_prefs.ProfilePrefs`, is the input
on purpose: the loaded model is lossy (built-in families filled from other
tables, the company blocklist appended to every filter, defaults materialised),
so writing it back would freeze today's defaults into the user's file.

``profile.toml`` is written stricter than it is read: only the ``[identity]``
table and its ``name``/``file_slug``/``identity_claim_id`` keys are accepted.
"""

import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from cva.fsutil import atomic_write
from cva.profile_identity import Identity, ProfileError, identity_from_data
from cva.profile_prefs import ProfilePrefs, preferences_from_data

_BARE_KEY: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_-]+")
_ESCAPES: Final[dict[str, str]] = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}
_IDENTITY_KEYS: Final[frozenset[str]] = frozenset(
    {"name", "file_slug", "identity_claim_id"}
)


def _string(text: str) -> str:
    """``text`` as a TOML basic string (control characters escaped)."""
    out = []
    for char in text:
        if char in _ESCAPES:
            out.append(_ESCAPES[char])
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\u{ord(char):04X}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def _key(key: str) -> str:
    return key if _BARE_KEY.fullmatch(key) else _string(key)


def _refuse(where: str, value: object) -> ProfileError:
    return ProfileError(
        f"cannot write {where}: {type(value).__name__} is not part of the "
        "profile schema (strings, integers, booleans, lists of strings, tables)"
    )


def _value(value: object, where: str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, list | tuple):
        if all(isinstance(v, str) for v in value):
            return "[" + ", ".join(_string(v) for v in value) + "]"
        if all(
            isinstance(v, list | tuple) and all(isinstance(s, str) for s in v)
            for v in value
        ):
            return "[" + ", ".join(_value(list(v), where) for v in value) + "]"
    raise _refuse(where, value)


def _table(lines: list[str], path: list[str], table: Mapping[str, Any]) -> None:
    scalars = [(k, v) for k, v in table.items() if not isinstance(v, Mapping)]
    tables = [(k, v) for k, v in table.items() if isinstance(v, Mapping)]
    if path and (scalars or not tables):
        if lines:
            lines.append("")
        lines.append("[" + ".".join(_key(p) for p in path) + "]")
    for key, value in scalars:
        where = ".".join([*path, key])
        lines.append(f"{_key(key)} = {_value(value, where)}")
    for key, sub in tables:
        _table(lines, [*path, key], sub)


def toml_document(data: Mapping[str, Any]) -> str:
    """``data`` as TOML text; refuses values outside the profile schema."""
    lines: list[str] = []
    _table(lines, [], data)
    return "\n".join(lines) + "\n" if lines else ""


def read_document(path: Path) -> dict[str, Any]:
    """The parsed document at ``path``; ``{}`` when the file does not exist."""
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProfileError(f"{path.name} unreadable ({path}): {exc}") from exc


def _checked_text(data: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """The TOML text of ``data`` and its parse, proven to be ``data`` again."""
    text = toml_document(data)
    parsed = tomllib.loads(text)
    if parsed != _plain(data):
        raise ProfileError("cannot write: the document does not survive a round trip")
    return text, parsed


def _plain(value: Any) -> Any:
    """``value`` with mappings as dicts and tuples as lists (tomllib's shape)."""
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return value


def _replace(path: Path, text: str) -> None:
    """Keep the previous file as ``<name>.bak``, then swap ``text`` in."""
    if path.is_file():
        atomic_write(path.with_name(f"{path.name}.bak"), path.read_text("utf-8"))
    atomic_write(path, text)


def write_preferences(path: Path, data: Mapping[str, Any]) -> ProfilePrefs:
    """Validate ``data`` as ``preferences.toml`` and write it atomically.

    Returns the preferences the file now holds; on any error the file (and its
    ``.bak``) is left untouched. Comments of the previous file are not kept.
    """
    text, parsed = _checked_text(data)
    prefs = preferences_from_data(parsed)
    _replace(path, text)
    return prefs


def write_profile(path: Path, data: Mapping[str, Any]) -> Identity:
    """Validate ``data`` as ``profile.toml`` and write it atomically.

    Only ``[identity]`` with ``name``/``file_slug``/``identity_claim_id`` is
    accepted. Returns the identity the file now holds; on any error the file is
    left untouched. Comments of the previous file are not kept.
    """
    unknown = sorted(set(data) - {"identity"})
    if unknown:
        raise ProfileError(f"profile.toml unknown table(s): {', '.join(unknown)}")
    table = data.get("identity", {})
    if isinstance(table, Mapping):
        extra = sorted(set(table) - _IDENTITY_KEYS)
        if extra:
            raise ProfileError(
                f"profile.toml [identity] unknown key(s): {', '.join(extra)}"
            )
    text, parsed = _checked_text(data)
    identity = identity_from_data(parsed)
    _replace(path, text)
    return identity
