"""Deterministic cover-letter quality score (telegram-improvements I3/I4).

ATS systems parse CVs, they do not score cover letters, and public AI
detectors are unreliable, so the letter is measured with plain deterministic
checks instead, each one a penalty on a 0-100 score:

- florid: a phrase of ``BANNED_PHRASES`` or ``FLORID_PHRASES``
  (``cva/match/cover_letter.py``, the one shared list), 10 points each, max 40;
- intensifiers: more than one intensifier/hype adjective ("muy", "truly",
  "increíble"...) per five sentences, up to 20 points;
- avg_sentence / long_sentence: average above 20 words (up to 15 points) and
  any sentence above 32 words (8 points);
- coverage: share of the offer's must-have requirements missing from the
  letter, up to 20 points;
- length: word count outside the limits (the assembled letter's 250-350 by
  default), 10 points;
- dash: an em dash or an en dash used as punctuation, 10 points;
- ungrounded: a figure that appears nowhere in the grounding sources,
  10 points each, max 20.

``exempt`` text (the owner's fixed base letter) is never policed for style
but still counts for coverage and length. The findings are English: they are
fed back to the model in one rewrite and summarised by the caller.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from cva.match.cover_letter import (
    _EM_DASH,
    _EN_DASH_PUNCT,
    BANNED_PHRASES,
    FLORID_PHRASES,
    LETTER_MAX_WORDS,
    LETTER_MIN_WORDS,
    _fold,
    split_sentences,
)
from cva.match.interview_prep import unbacked_figures

QUALITY_THRESHOLD: Final[int] = 70

_FLORID_POINTS: Final[int] = 10
_FLORID_CAP: Final[int] = 40
_INTENSIFIER_RATE: Final[float] = 0.2  # tolerated intensifiers per sentence
_INTENSIFIER_CAP: Final[int] = 20
_AVG_SENTENCE_WORDS: Final[int] = 20
_AVG_SENTENCE_CAP: Final[int] = 15
_MAX_SENTENCE_WORDS: Final[int] = 32
_LONG_SENTENCE_POINTS: Final[int] = 8
_COVERAGE_POINTS: Final[int] = 20
_LENGTH_POINTS: Final[int] = 10
_DASH_POINTS: Final[int] = 10
_UNGROUNDED_POINTS: Final[int] = 10
_UNGROUNDED_CAP: Final[int] = 20

# Intensifiers and hype adjectives, folded (no accents); "\w*" allows inflection.
_INTENSIFIERS: Final[re.Pattern[str]] = re.compile(
    r"(?<!\w)(?:"
    # Spanish
    r"muy|realmente|sumamente|altamente|enormemente|extremadamente|"
    r"verdaderamente|profundamente|tremendamente|increibl\w*|enorme\w*|"
    r"excepcional\w*|extraordinari\w*|fascinante\w*|inspirador\w*|"
    r"perfectamente|"
    # English
    r"very|really|truly|deeply|highly|incredibly|extremely|greatly|genuinely|"
    r"exceptional\w*|outstanding|remarkable|amazing|exciting|fascinating|"
    r"vibrant|unique|incredible|extraordinary|"
    # Catalan
    r"molt|realment"
    r")(?!\w)"
)


@dataclass(frozen=True)
class QualityFinding:
    """One failed check: a stable ``code``, an English ``detail`` and its penalty."""

    code: str
    detail: str
    penalty: int


@dataclass(frozen=True)
class LetterQuality:
    """Score (0-100), findings and the measurements behind them."""

    score: int
    findings: tuple[QualityFinding, ...] = ()
    florid: tuple[str, ...] = ()
    covered: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    intensifiers_per_sentence: float = 0.0
    avg_sentence_words: float = 0.0
    max_sentence_words: int = 0
    words: int = 0
    ungrounded: tuple[str, ...] = field(default=())

    @property
    def passed(self) -> bool:
        return self.score >= QUALITY_THRESHOLD

    def feedback(self) -> list[str]:
        """One line per finding, for the rewrite prompt."""
        return [finding.detail for finding in self.findings]


def _florid_phrases(folded: str) -> tuple[str, ...]:
    matched = [
        phrase
        for phrase in (*BANNED_PHRASES, *FLORID_PHRASES)
        if re.search(rf"(?<!\w){re.escape(_fold(phrase))}", folded)
    ]
    # "passionate about" already counts "passionate" and "passion".
    return tuple(
        phrase
        for phrase in matched
        if not any(
            other != phrase and _fold(phrase) in _fold(other) for other in matched
        )
    )


def _mentions(folded: str, requirement: str) -> bool:
    name = _fold(requirement).strip()
    return (
        bool(name) and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", folded) is not None
    )


def _style_text(text: str, exempt: Sequence[str]) -> str:
    for piece in exempt:
        if piece.strip():
            text = text.replace(piece, "\n")
    return text


def letter_quality(
    text: str,
    *,
    offer_requirements: Sequence[str],
    language: str,
    grounding: str | None = None,
    exempt: Sequence[str] = (),
    min_words: int = LETTER_MIN_WORDS,
    max_words: int = LETTER_MAX_WORDS,
) -> LetterQuality:
    """Score ``text`` (see the module docstring for the checks and penalties).

    ``offer_requirements`` are the offer's must-have requirement names (an
    empty list skips coverage); ``grounding`` is every source the letter may
    take figures from (``None`` skips that check). ``language`` is accepted
    for symmetry with the validators: every phrase list is checked in every
    language, because a letter can mix them.
    """
    del language  # the phrase lists are multilingual; kept for the call contract
    findings: list[QualityFinding] = []
    style = _style_text(text, exempt)
    folded_style = _fold(style)

    florid = _florid_phrases(folded_style)
    if florid:
        findings.append(
            QualityFinding(
                "florid",
                "florid or AI-sounding phrases, write plainly instead: "
                + ", ".join(f'"{p}"' for p in florid),
                min(_FLORID_CAP, _FLORID_POINTS * len(florid)),
            )
        )

    sentences = split_sentences(style)
    counts = [len(sentence.split()) for sentence in sentences]
    intensifiers = _INTENSIFIERS.findall(folded_style)
    rate = len(intensifiers) / len(sentences) if sentences else 0.0
    if rate > _INTENSIFIER_RATE and len(intensifiers) > 1:
        findings.append(
            QualityFinding(
                "intensifiers",
                f"{len(intensifiers)} intensifiers or hype adjectives in "
                f"{len(sentences)} sentences ("
                + ", ".join(sorted(set(intensifiers)))
                + "): state the fact without them",
                min(_INTENSIFIER_CAP, 5 + round(50 * (rate - _INTENSIFIER_RATE))),
            )
        )
    average = sum(counts) / len(counts) if counts else 0.0
    longest = max(counts, default=0)
    if average > _AVG_SENTENCE_WORDS:
        findings.append(
            QualityFinding(
                "avg_sentence",
                f"sentences average {average:.0f} words; keep them under "
                f"{_AVG_SENTENCE_WORDS}",
                min(
                    _AVG_SENTENCE_CAP, round(1.5 * (average - _AVG_SENTENCE_WORDS)) + 1
                ),
            )
        )
    if longest > _MAX_SENTENCE_WORDS:
        findings.append(
            QualityFinding(
                "long_sentence",
                f"a sentence has {longest} words; split anything over "
                f"{_MAX_SENTENCE_WORDS}",
                _LONG_SENTENCE_POINTS,
            )
        )

    folded_all = _fold(text)
    requirements = [r for r in offer_requirements if r.strip()]
    covered = tuple(r for r in requirements if _mentions(folded_all, r))
    missing = tuple(r for r in requirements if not _mentions(folded_all, r))
    if missing:
        findings.append(
            QualityFinding(
                "coverage",
                "must-have requirements of the offer not mentioned: "
                + ", ".join(missing)
                + " (name each one only if the claims back it)",
                round(_COVERAGE_POINTS * len(missing) / len(requirements)),
            )
        )

    words = len(text.split())
    if not min_words <= words <= max_words:
        findings.append(
            QualityFinding(
                "length",
                f"the letter has {words} words; keep it within {min_words}-{max_words}",
                _LENGTH_POINTS,
            )
        )

    if _EM_DASH in style or _EN_DASH_PUNCT.search(style):
        findings.append(
            QualityFinding(
                "dash",
                "em dash or en dash used as punctuation; use a comma or a full stop",
                _DASH_POINTS,
            )
        )

    ungrounded: tuple[str, ...] = ()
    if grounding is not None:
        ungrounded = tuple(unbacked_figures(style, grounding))
        if ungrounded:
            findings.append(
                QualityFinding(
                    "ungrounded",
                    "figures not found in the claims, the offer or the facts: "
                    + ", ".join(ungrounded),
                    min(_UNGROUNDED_CAP, _UNGROUNDED_POINTS * len(ungrounded)),
                )
            )

    score = max(0, 100 - sum(finding.penalty for finding in findings))
    return LetterQuality(
        score=score,
        findings=tuple(findings),
        florid=florid,
        covered=covered,
        missing=missing,
        intensifiers_per_sentence=rate,
        avg_sentence_words=average,
        max_sentence_words=longest,
        words=words,
        ungrounded=ungrounded,
    )
