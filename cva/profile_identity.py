"""Who the CVs and letters are for: ``profile.toml`` ``[identity]`` (profile-extraction).

``profile.toml`` lives in the profile directory (``cva/profile_paths.py``).
Every key is optional; the defaults are the values that used to be hard-coded,
so a profile without the file (or an unset ``profile_dir``) behaves as before::

    [identity]
    name = "Ana Demo"                 # signature override; default: the claim text
    file_slug = "Ana_Demo"            # prefix of every delivered file name
    identity_claim_id = "identity-contact"   # claim whose text starts with the name
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from cva.claimsdb import ClaimsDB

_FILE_SLUG_RE: Final[re.Pattern[str]] = re.compile(r"[0-9A-Za-z_]+")
_DEFAULT_FILE_SLUG: Final[str] = "CV"
_DEFAULT_CLAIM_ID: Final[str] = "identity-contact"


class ProfileError(ValueError):
    """``profile.toml`` is unreadable or violates the identity contract."""


@dataclass(frozen=True)
class Identity:
    """The profile owner's identity as the engine needs it."""

    # Signature override; ``None`` -> first field of the identity claim text.
    name: str | None = None
    file_slug: str = _DEFAULT_FILE_SLUG
    identity_claim_id: str = _DEFAULT_CLAIM_ID

    @property
    def contact_claim_ids(self) -> frozenset[str]:
        """Claim ids forming the CV contact block (ES + ``_en`` twins).

        Derived from the identity claim id: ``identity-contact`` ->
        ``identity-contact``/``-phone`` and their ``_en`` twins, exactly the
        ids the contact validator always checked.
        """
        family = self.identity_claim_id.rsplit("-", 1)[0]
        return frozenset(
            {
                self.identity_claim_id,
                f"{self.identity_claim_id}_en",
                f"{family}-phone",
                f"{family}-phone_en",
            }
        )


DEFAULT_IDENTITY: Final[Identity] = Identity()


def _text(table: dict[str, Any], key: str) -> str | None:
    """The stripped string at ``key``; ``None`` when the key is absent."""
    if key not in table:
        return None
    value = table[key]
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"profile.toml [identity] {key} must be a non-empty string")
    return value.strip()


def load_identity(profile_toml: Path | None) -> Identity:
    """The identity in ``profile_toml``; defaults when it is ``None`` or absent."""
    if profile_toml is None or not profile_toml.is_file():
        return DEFAULT_IDENTITY
    try:
        data = tomllib.loads(profile_toml.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProfileError(f"profile.toml unreadable ({profile_toml}): {exc}") from exc
    table = data.get("identity", {})
    if not isinstance(table, dict):
        raise ProfileError("profile.toml [identity] must be a table")
    file_slug = _text(table, "file_slug") or DEFAULT_IDENTITY.file_slug
    if _FILE_SLUG_RE.fullmatch(file_slug) is None:
        raise ProfileError(
            f"profile.toml [identity] file_slug must be letters, digits or _: {file_slug!r}"
        )
    return Identity(
        name=_text(table, "name"),
        file_slug=file_slug,
        identity_claim_id=_text(table, "identity_claim_id")
        or DEFAULT_IDENTITY.identity_claim_id,
    )


def require_identity_claim(identity: Identity, claims: ClaimsDB) -> None:
    """Fail loudly when the identity claim is not in the claims DB.

    A typo in ``identity_claim_id`` would otherwise give an empty owner name
    in every cover letter and change which contact claims are validated.
    """
    if identity.identity_claim_id not in claims.by_id:
        raise ProfileError(
            f"profile.toml [identity] identity_claim_id "
            f"{identity.identity_claim_id!r} is not a claim in the claims DB"
        )
