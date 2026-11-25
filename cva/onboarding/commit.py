"""The one write path of the profile answers (onboarding-v2 T5/T10).

Both the Telegram Confirm (:mod:`cva.onboarding.flow`) and the extension's
Profile tab (``POST /profile`` on the bridge, :mod:`cva.onboarding.profile_edit`)
write through :func:`commit_all`: the validated writers of
:func:`cva.onboarding.answers.write_answers`, the base CV PDFs, then the
confirm hooks (the ``setup-*`` claims merge), all or nothing
(:mod:`cva.onboarding.snapshot`).

:data:`WRITE_LOCK` serializes those writes: the bot's event loop and the
bridge's server threads never interleave two of them. It is re-entrant, so a
caller that reads the current answers before writing can hold it around both.
"""

import json
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from cva.i18n import Lang
from cva.onboarding import answers as A
from cva.onboarding import cv_upload as U
from cva.onboarding import starter
from cva.onboarding.profile_gen import (
    GENERATED_FILES,
    IdentitySync,
    sync_identity_claims,
)
from cva.onboarding.snapshot import Snapshot
from cva.profile_identity import ProfileError

WRITE_LOCK: Final[threading.RLock] = threading.RLock()

Hook = Callable[[Path, dict[str, Any]], None]


@dataclass(frozen=True)
class Committed:
    """What a successful :func:`commit_all` did besides the profile files."""

    stored: list[str]  # base CV PDFs stored (relative names)
    identity: IdentitySync = "unchanged"  # the identity claim (contact answers)
    identity_claim: str = ""  # its id
    cover_letter: bool = False  # cover_letter_base.toml created (beta3-ux T4)


def _previous_contact(profile_dir: Path) -> dict[str, Any] | None:
    """``contact`` of the last Confirm's ``onboarding_answers.json``."""
    try:
        record = json.loads((profile_dir / A.ANSWERS_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    contact = record.get("contact") if isinstance(record, dict) else None
    return contact if isinstance(contact, dict) else None


class ConfirmFailed(Exception):
    """A commit failed (``cause``); ``undo`` is set when undoing it failed too."""

    def __init__(self, cause: Exception, undo: OSError | None) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.undo = undo


def commit_all(
    profile_dir: Path,
    answers: dict[str, Any],
    slots: list[U.Slot],
    got: dict[str, str],
    lang: Lang,
    section: str | None,
    hooks: Iterable[Hook],
    *,
    cover_letter: bool = False,
    declared: Sequence[tuple[str, str]] = (),
) -> Committed:
    """Write everything Confirm writes, or nothing: on any failure every file
    is put back as it was (:mod:`cva.onboarding.snapshot`) and
    :class:`ConfirmFailed` is raised. The pending uploads are left alone.
    Holds :data:`WRITE_LOCK` for the whole write.

    The full questionnaire and ``/setup contact`` also bring the identity
    claim of an existing ``claims.json`` in line with the new contact
    answers when ``/setup`` generated it, and keep it when it was edited by
    hand (:func:`cva.onboarding.profile_gen.sync_identity_claims`).

    Portals are not written here: they are enabled only in the extension
    (``config.local.toml`` ``enabled_portals`` is a manual advanced setting).

    With ``cover_letter`` a profile without ``cover_letter_base.toml`` gets
    one written from the answers (:func:`cva.onboarding.starter.write_cover_letter`;
    ``declared`` labels the kept non-built-in families), inside the same
    snapshot; an existing file is never touched."""
    with WRITE_LOCK:
        try:
            snap = Snapshot.take(
                [
                    *(
                        profile_dir / rel
                        for rel in (
                            "preferences.toml",
                            "preferences.toml.bak",
                            "profile.toml",
                            "profile.toml.bak",
                            A.ANSWERS_FILE,
                            *GENERATED_FILES,
                        )
                    ),
                    starter.letter_path(profile_dir),
                ],
                [U.base_dir(profile_dir)],
            )
        except (ProfileError, OSError) as exc:  # nothing written yet
            raise ConfirmFailed(exc, None) from exc
        previous = _previous_contact(profile_dir)
        identity: tuple[IdentitySync, str] = ("unchanged", "")
        letter = False
        try:
            A.write_answers(profile_dir, answers, lang, section)
            stored = U.commit(profile_dir, got, slots, str(answers.get("name") or ""))
            for hook in hooks:
                hook(profile_dir, answers)
            if cover_letter:
                letter = starter.write_cover_letter(
                    profile_dir, answers, list(declared)
                )
            if section in (None, "contact"):
                identity = sync_identity_claims(profile_dir, previous, answers)
        except BaseException as exc:
            undo: OSError | None = None
            try:
                snap.restore()
            except OSError as undo_exc:
                undo = undo_exc
            if not isinstance(exc, (ProfileError, OSError)):
                raise
            raise ConfirmFailed(exc, undo) from exc
        return Committed(stored, *identity, letter)
