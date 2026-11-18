"""Match a Gmail message to one of the owner's sent offers (pure, no I/O).

Signals, strongest first: the sender's registrable domain looks like the
company (3), the company name appears in the sender's display name or the
subject (2), or in the body (1). Applicant-tracking/job-board senders
(greenhouse, lever, linkedin, ...) never count as a domain signal because
their domain is not the employer's — for them the company name must appear
in the message. No match means ``None``: the caller ignores the message
silently.
"""

import re
import unicodedata
from collections.abc import Sequence
from typing import Final

from cva.gmail.client import GmailMessage
from cva.tracker import OfferRow

# Offers in these estados are closed for good: never candidates.
_CLOSED_ESTADOS: Final[frozenset[str]] = frozenset(
    {"rechazada", "retirada", "aceptada", "descartada"}
)
# Registrable-domain labels of ATS/job-board senders (not the employer).
ATS_LABELS: Final[frozenset[str]] = frozenset(
    {
        "greenhouse",
        "greenhouse-mail",
        "lever",
        "workable",
        "teamtailor",
        "teamtailor-mail",
        "personio",
        "smartrecruiters",
        "ashbyhq",
        "ashby",
        "recruitee",
        "infojobs",
        "linkedin",
        "indeed",
        "jobfluent",
        "tecnoempleo",
        "getmanfred",
        "manfred",
    }
)
_FREE_MAIL: Final[frozenset[str]] = frozenset(
    {"gmail", "googlemail", "outlook", "hotmail", "live", "yahoo", "icloud", "proton"}
)
_SECOND_LEVEL: Final[frozenset[str]] = frozenset(
    {"co", "com", "org", "net", "gov", "ac"}
)
_LEGAL_SUFFIXES: Final[tuple[tuple[str, ...], ...]] = (
    ("s", "l", "u"),
    ("s", "a", "u"),
    ("s", "l", "l"),
    ("s", "l"),
    ("s", "a"),
    ("sl",),
    ("sa",),
    ("slu",),
    ("sau",),
    ("inc",),
    ("ltd",),
    ("llc",),
    ("gmbh",),
    ("ag",),
    ("bv",),
    ("limited",),
    ("corp",),
)
# Removable only while a distinctive name remains ("Acme Group" -> "acme", but
# "Digital Group" stays whole because "digital" alone is a common word).
_BUSINESS_SUFFIXES: Final[frozenset[str]] = frozenset(
    {"group", "grupo", "spain", "espana"}
)
_GENERIC_NAMES: Final[frozenset[str]] = frozenset(
    {
        "confidencial",
        "empresa confidencial",
        "confidential",
        "empresa",
        "company",
        "no especificada",
        "unknown",
        "desconocida",
        "anonima",
        "digital",
        "data",
        "tech",
        "technology",
        "technologies",
        "solutions",
        "services",
        "consulting",
        "global",
        "international",
        "software",
        "systems",
        "labs",
        "media",
        "innovation",
        "engineering",
        "banco",
        "bank",
        "group",
        "grupo",
        "spain",
        "espana",
    }
)
_MIN_NAME_LENGTH: Final[int] = 3
_MIN_TOKEN_LABEL: Final[int] = 4
_EMAIL_IN_ANGLES = re.compile(r"<([^>]*)>")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

_SCORE_DOMAIN: Final[int] = 3
_SCORE_HEADER: Final[int] = 2
_SCORE_BODY: Final[int] = 1


def _fold(text: str) -> str:
    """Lowercase, strip accents, collapse non-alphanumerics to single spaces."""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _NON_ALNUM.sub(" ", ascii_text.lower()).strip()


def normalize_company(name: str) -> str:
    """Folded company name without trailing legal-form words (``S.L.``, ``Inc``)."""
    tokens = _fold(name).split()
    stripped = True
    while stripped and len(tokens) > 1:
        stripped = False
        for suffix in _LEGAL_SUFFIXES:
            size = len(suffix)
            if len(tokens) > size and tuple(tokens[-size:]) == suffix:
                tokens = tokens[:-size]
                stripped = True
                break
        if (
            not stripped
            and len(tokens) > 1
            and tokens[-1] in _BUSINESS_SUFFIXES
            and " ".join(tokens[:-1]) not in _GENERIC_NAMES
        ):
            tokens = tokens[:-1]
            stripped = True
    return " ".join(tokens)


def _split_sender(sender: str) -> tuple[str, str]:
    """``(display name, domain)`` of a ``From`` header value."""
    angles = _EMAIL_IN_ANGLES.search(sender)
    address = angles.group(1) if angles else sender
    display = _EMAIL_IN_ANGLES.sub(" ", sender) if angles else ""
    domain = address.rsplit("@", 1)[1].strip().lower() if "@" in address else ""
    return display.strip(), domain


def _registrable_label(domain: str) -> str:
    labels = [part for part in domain.split(".") if part]
    if len(labels) < 2:
        return labels[0] if labels else ""
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2:
        return labels[-3]
    return labels[-2]


def _mentions(folded_text: str, name: str) -> bool:
    return f" {name} " in f" {folded_text} "


def _domain_matches(label: str, name: str) -> bool:
    """The domain label is the company: equal to the whole name (hyphens and
    spaces ignored) or to one distinctive token of a multi-word name. No prefix
    matching ("meta" vs "metabase", "banco santander" vs "banco")."""
    compact = name.replace(" ", "")
    label = label.replace("-", "")
    if not label or not compact:
        return False
    if label == compact:
        return True
    tokens = name.split()
    return (
        len(tokens) > 1
        and len(label) >= _MIN_TOKEN_LABEL
        and label in tokens
        and label not in _GENERIC_NAMES
    )


def _is_generic(name: str) -> bool:
    """A name that is, or is made only of, common words matches anything."""
    return name in _GENERIC_NAMES or all(t in _GENERIC_NAMES for t in name.split())


def _score(message: GmailMessage, name: str) -> int:
    display, domain = _split_sender(message.sender)
    label = _registrable_label(domain)
    score = 0
    if (
        label not in ATS_LABELS
        and label not in _FREE_MAIL
        and _domain_matches(label, name)
    ):
        score += _SCORE_DOMAIN
    if _mentions(_fold(display), name) or _mentions(_fold(message.subject), name):
        score += _SCORE_HEADER
    if _mentions(_fold(message.text), name):
        score += _SCORE_BODY
    return score


def match_offer(message: GmailMessage, offers: Sequence[OfferRow]) -> OfferRow | None:
    """Best sent, still-open offer for ``message``; ``None`` when nothing matches.

    Among several matches the strongest signal wins, then the most recently
    sent, then the highest id.
    """
    best: tuple[tuple[int, str, int], OfferRow] | None = None
    for offer in offers:
        if offer.fecha_envio is None or offer.estado in _CLOSED_ESTADOS:
            continue
        name = normalize_company(offer.empresa or "")
        if len(name) < _MIN_NAME_LENGTH or _is_generic(name):
            continue
        score = _score(message, name)
        if score == 0:
            continue
        key = (score, offer.fecha_envio, offer.id)
        if best is None or key > best[0]:
            best = (key, offer)
    return None if best is None else best[1]
