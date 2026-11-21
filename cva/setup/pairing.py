"""One-time pairing codes (beta3-ux T1): no numeric id, no ``.env`` to open.

Two short-lived codes replace the two most confusing setup steps:

* the **owner claim code**: while ``owner_telegram_id`` is unset, the bot
  prints a code in the terminal it was started from; the first Telegram user
  who sends ``/start <code>`` to the bot becomes its owner
  (:func:`save_owner_id` writes the id to ``config.local.toml``);
* the **extension pairing code**: the owner asks the bot for one
  (``/connect``) and types it in the extension's Options, which trades it
  once for the bridge secret through ``POST /pair`` (:mod:`cva.bridge`).

Both are a :class:`OneTimeCode`: 8 characters from a secure RNG
(:mod:`secrets`) over an alphabet without look-alike characters (about 40
bits), single use, valid for a limited time, compared in constant time, and
locked after :data:`MAX_ATTEMPTS` wrong tries. The code is never logged.
"""

import hmac
import secrets
import stat
import threading
import time
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Final

from cva.config import check_toml_settings
from cva.fsutil import atomic_write
from cva.setup.wizard import toml_set_key

# No 0/O, 1/I/L: the code is read from a terminal and typed on a phone.
ALPHABET: Final[str] = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH: Final[int] = 8
MAX_ATTEMPTS: Final[int] = 5
CLAIM_CODE_TTL_SECONDS: Final[int] = 30 * 60
PAIR_CODE_TTL_SECONDS: Final[int] = 10 * 60
OWNER_KEY: Final[str] = "owner_telegram_id"


def normalize(candidate: str) -> str:
    """What a typed code is compared as: upper case, without the blanks and
    dashes people add while copying it."""
    return "".join(ch for ch in candidate.upper() if ch not in " -\t")


def display(code: str) -> str:
    """``ABCDEFGH`` -> ``ABCD-EFGH`` (easier to read; the dash is ignored)."""
    half = len(code) // 2
    return f"{code[:half]}-{code[half:]}"


class OneTimeCode:
    """A single-use code with an expiry and an attempt limit (thread-safe:
    the bridge verifies from its server threads).

    :meth:`issue` makes a new code (and lifts a lock); :meth:`verify` is
    True exactly once, for the current code before it expires. Every wrong
    try counts; after ``max_attempts`` of them the code is dropped and
    :attr:`locked` stays True until the next :meth:`issue`.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float,
        max_attempts: int = MAX_ATTEMPTS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._max_attempts = max_attempts
        self._clock = clock
        self._lock = threading.Lock()
        self._code: str | None = None
        self._expires_at = 0.0
        self._failures = 0
        self._locked = False

    def issue(self) -> str:
        """A fresh code (the previous one stops working)."""
        code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
        with self._lock:
            self._code = code
            self._expires_at = self._clock() + self._ttl
            self._failures = 0
            self._locked = False
        return code

    @property
    def locked(self) -> bool:
        """True after too many wrong tries (until the next :meth:`issue`)."""
        with self._lock:
            return self._locked

    @property
    def active(self) -> bool:
        """True while a code can still be used."""
        with self._lock:
            return self._code is not None and self._clock() < self._expires_at

    def verify(self, candidate: str) -> bool:
        """True (once) when ``candidate`` is the current, unexpired code."""
        typed = normalize(candidate).encode("utf-8")
        with self._lock:
            if self._code is None:
                return False
            if self._clock() >= self._expires_at:
                self._code = None
                return False
            if hmac.compare_digest(typed, self._code.encode("utf-8")):
                self._code = None  # single use
                return True
            self._failures += 1
            if self._failures >= self._max_attempts:
                self._code = None
                self._locked = True
            return False


def save_owner_id(config_path: Path, user_id: int) -> None:
    """Set ``owner_telegram_id = <user_id>`` in ``config_path``
    (``config.local.toml``): only that line changes (the wizard's
    line-preserving writer), the result must load with the
    :class:`~cva.config.Settings` validators (merged over the ``config.toml``
    next to it), and the file is replaced atomically, its mode kept and a
    symlink written through. ``ValueError`` / ``OSError`` leave it as it
    was."""
    if user_id <= 0:
        raise ValueError("owner id must be a positive integer")
    base = config_path.read_bytes().decode("utf-8") if config_path.is_file() else ""
    text = toml_set_key(base, OWNER_KEY, user_id)
    shared = config_path.parent / "config.toml"
    documents = [tomllib.loads(shared.read_text("utf-8"))] if shared.is_file() else []
    check_toml_settings(*documents, tomllib.loads(text))
    mode = stat.S_IMODE(config_path.stat().st_mode) if config_path.is_file() else None
    target = config_path.resolve() if config_path.is_symlink() else config_path
    atomic_write(target, text, mode=mode)
