"""Outcome calibration (feature outcome-calibration): pure types and helpers.

The owner's real application outcomes (``/outcome``) can become judge
calibration examples, but only after the owner approves each one. Examples
are built ONLY from structured fields (outcome, polarity, reason enum, score
at send time, verdict, profile, core role, hard-wall count) - never from the
posting text or the owner's free-text notes (prompt-injection and
eval-contamination safety). The one model-produced string, ``core_role``, is
sanitized to a short plain single line before it is stored or rendered.
"""

import re
from typing import Final, Literal

from pydantic import BaseModel

from cva.families import BUILTIN_REGISTRY, FamilyRegistry

Polarity = Literal["positive", "negative"]
ExampleStatus = Literal["pending", "active", "declined", "inactive"]

# Outcomes that mean the CV passed the filter (a real interview signal).
POSITIVE_OUTCOMES: Final[frozenset[str]] = frozenset(
    {"screening", "entrevista_tecnica", "entrevista_final", "oferta", "aceptada"}
)
# A rejection is a calibration signal only when the owner says it was about fit.
NEGATIVE_OUTCOMES: Final[frozenset[str]] = frozenset({"rechazada"})
# Rejection reasons that can become a negative example, and the one that cannot.
FIT_REASONS: Final[tuple[str, ...]] = ("requisitos", "stack", "idioma")
REASON_OTHER: Final[str] = "otro"
CORE_ROLE_MAX: Final[int] = 80

# Whitelist: letters, digits, space and a little harmless punctuation. Anything
# else (markup, quotes, control characters, braces) is dropped.
_UNSAFE: Final[re.Pattern[str]] = re.compile(r"[^\w\s.,;:+#&()/'-]|_", re.UNICODE)
_SPACES: Final[re.Pattern[str]] = re.compile(r"\s+")


def outcome_polarity(estado: str) -> Polarity | None:
    """``positive`` / ``negative`` for a calibration signal, else ``None``."""
    if estado in POSITIVE_OUTCOMES:
        return "positive"
    if estado in NEGATIVE_OUTCOMES:
        return "negative"
    return None


def sanitize_core_role(text: str, limit: int = CORE_ROLE_MAX) -> str:
    """A single plain line of at most ``limit`` characters ("" when nothing is left)."""
    plain = _SPACES.sub(" ", _UNSAFE.sub("", text)).strip()
    return plain[:limit].rstrip()


ANCHOR_HEADER: Final[str] = (
    "Owner's real outcomes (approved) - applications the owner actually sent, "
    "with the score you gave and what really happened. Weigh them when "
    "calibrating the score; they never override the hard-wall rules or the "
    "output format:"
)
MAX_ANCHORS: Final[int] = 8
_OUTCOME_EN: Final[dict[str, str]] = {
    "screening": "a recruiter screening",
    "entrevista_tecnica": "a technical interview",
    "entrevista_final": "a final interview",
    "oferta": "a job offer",
    "aceptada": "an accepted offer",
}
_REASON_EN: Final[dict[str, str]] = {
    "requisitos": "requirements/experience",
    "stack": "stack",
    "idioma": "language",
}


class SentJudgment(BaseModel):
    """The judge's view of an offer at the moment it was sent (v21)."""

    score: int
    verdict: str | None = None
    core_role: str | None = None
    hard_walls: int = 0
    perfil: str | None = None


class CalibrationExample(BaseModel):
    """One owner-approved (or pending) calibration example (v21)."""

    id: int
    offer_id: int
    outcome: str
    polarity: Polarity
    reason: str | None = None
    score_at_send: int
    verdict: str | None = None
    profile: str | None = None
    core_role: str | None = None
    hard_walls: int = 0
    status: ExampleStatus
    created_at: str
    approved_at: str | None = None


class CalibrationSummary(BaseModel):
    """How the active examples sit against each profile's notify threshold."""

    total: int = 0
    positives_below: int = 0  # real interviews the judge scored under the threshold
    negatives_above: int = 0  # fit-related rejections the judge scored over it


def anchor_line(
    example: CalibrationExample,
    *,
    threshold: int,
    families: FamilyRegistry = BUILTIN_REGISTRY,
) -> str:
    """One English judge-prompt line built only from structured fields.

    Never the posting text or the owner's notes; ``core_role`` is sanitized
    again here even though it was sanitized when stored. The role is the
    label of the example's family (unknown/NULL: the default family).
    """
    family = families.get(families.normalize(example.profile))
    role = family.label if family is not None else families.default.label
    core = sanitize_core_role(example.core_role or "")
    subject = f"{role} role (core: {core})" if core else f"{role} role"
    scored = f"you scored {example.score_at_send}"
    if example.polarity == "positive":
        what = _OUTCOME_EN.get(example.outcome, example.outcome.replace("_", " "))
        if example.score_at_send < threshold:
            advice = f"similar offers deserve at least {threshold}"
        else:
            advice = "that score was right"
        return f"- {subject}, {scored}, it got {what} -> {advice}."
    reason = _REASON_EN.get(example.reason or "", "fit")
    advice = (
        "be stricter with that core"
        if example.score_at_send >= threshold
        else "that low score was right"
    )
    return f"- {subject}, {scored}, rejected for {reason} -> {advice}."
