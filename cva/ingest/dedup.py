"""Cross-source duplicate key: ``<company-slug>_<title-slug>`` (pure, no I/O).

The same role is often posted on LinkedIn and InfoJobs under different URLs.
The key folds accents and case, drops legal suffixes from the company
("Acme S.L.U." == "Acme"), drops gender/inclusion and work-mode tails from the
title ("(m/f/d)", "- Remote") and keeps everything else, so seniority words
(Junior/Senior/Lead) still tell two roles apart.

Python port of the idea in MadsLorentzen/ai-job-search ``tools/job_key.py``
(MIT licence), adapted to ES/CA/EN offers.
"""

from __future__ import annotations

import hashlib
import re
from typing import Final

from cva.title_filter import normalize

MAX_KEY_LENGTH: Final[int] = 100
_HASH_LENGTH: Final[int] = 8

# Legal-form suffixes as normalized token sequences ("S.L.U." -> s l u).
_COMPANY_SUFFIXES: Final[tuple[tuple[str, ...], ...]] = (
    ("s", "l", "u"),
    ("s", "a", "u"),
    ("s", "a", "s"),
    ("s", "p", "a"),
    ("s", "l"),
    ("s", "a"),
    ("b", "v"),
    ("sl",),
    ("slu",),
    ("sa",),
    ("sau",),
    ("sas",),
    ("spa",),
    ("bv",),
    ("inc",),
    ("ltd",),
    ("limited",),
    ("gmbh",),
    ("llc",),
)
# Descriptive suffixes: stripped only when something else is left.
_COMPANY_WORDS: Final[tuple[tuple[str, ...], ...]] = (("group",), ("grupo",))
# Work-mode tails, only ever stripped from the END of a title ("Remote Sensing
# Engineer" keeps its leading word).
_TITLE_TAILS: Final[tuple[tuple[str, ...], ...]] = (
    ("all", "genders"),
    ("all", "gender"),
    ("on", "site"),
    ("remoto",),
    ("remote",),
    ("hibrido",),
    ("hybrid",),
    ("presencial",),
    ("teletrabajo",),
    ("onsite",),
)
# "(m/f/d)", "h/m", "f/h/x": 2-3 single gender letters joined by slashes.
_GENDER_TAG: Final[re.Pattern[str]] = re.compile(
    r"(?<![a-z0-9])[mwfhdx](?:\s*/\s*[mwfhdx]){1,2}(?![a-z0-9])", re.IGNORECASE
)


def _strip_tail(
    tokens: list[str], tails: tuple[tuple[str, ...], ...], *, keep: int = 0
) -> list[str]:
    """Repeatedly drop any trailing token sequence in ``tails``.

    ``keep`` tokens must remain, so a company called just "Group" survives.
    """
    changed = True
    while changed:
        changed = False
        for tail in tails:
            n = len(tail)
            if len(tokens) - n >= keep and tuple(tokens[-n:]) == tail:
                tokens = tokens[:-n]
                changed = True
    return tokens


def _slug(tokens: list[str]) -> str:
    return "-".join(tokens)


def dedup_key(company: str | None, title: str | None) -> str | None:
    """Stable duplicate key, or ``None`` when company or title is unusable.

    No company means no dedup (two unnamed "Developer" posts are unrelated).
    """
    company_tokens = _strip_tail(normalize(company).split(), _COMPANY_SUFFIXES)
    company_tokens = _strip_tail(company_tokens, _COMPANY_WORDS, keep=1)
    company_tokens = _strip_tail(company_tokens, _COMPANY_SUFFIXES)
    title_tokens = _strip_tail(
        normalize(_GENDER_TAG.sub(" ", title or "")).split(), _TITLE_TAILS
    )
    if not company_tokens or not title_tokens:
        return None
    key = f"{_slug(company_tokens)}_{_slug(title_tokens)}"
    if len(key) <= MAX_KEY_LENGTH:
        return key
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:_HASH_LENGTH]
    return f"{key[: MAX_KEY_LENGTH - _HASH_LENGTH - 1].rstrip('-')}-{digest}"
