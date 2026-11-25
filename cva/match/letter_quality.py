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
- length: word count outside the limits (about 120-250 by default: the reader
  skims), 10 points, plus one point per 5 words over the maximum, max 40;
- dash: an em dash or an en dash used as punctuation, 10 points;
- ungrounded: a figure that appears nowhere in the grounding sources,
  10 points each, max 20;
- definition: a sentence explaining a term of the stack to a technical reader
  ("RAG is a technique that...", "tool calling, which lets..."), 10 points
  each, max 20;
- gap: a confessed missing skill ("I have not used", "aún no he usado"),
  15 points each, max 30;
- company: the letter names another employer that the offer names as the
  parent group, owner or recruiter of ``company`` (the card's), or never names
  ``company`` at all, 40 points.

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
    _fold,
    known_company,
    normalize_company,
    split_sentences,
)
from cva.match.interview_prep import unbacked_figures

QUALITY_THRESHOLD: Final[int] = 70
# Soft length window of the score: shorter than the validator's 150 is still
# readable, longer than 250 is skimmed (beta.8 owner feedback).
QUALITY_MIN_WORDS: Final[int] = 120
QUALITY_MAX_WORDS: Final[int] = LETTER_MAX_WORDS

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
_LENGTH_OVER_STEP: Final[int] = 5  # one more point per this many words over
_LENGTH_CAP: Final[int] = 40
_DEFINITION_POINTS: Final[int] = 10
_DEFINITION_CAP: Final[int] = 20
_GAP_POINTS: Final[int] = 15
_GAP_CAP: Final[int] = 30
_COMPANY_POINTS: Final[int] = 40
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


# Terms of the candidate's stack a technical reader already knows (folded).
_STACK: Final[str] = (
    r"(?:rag|retrieval[- ]augmented generation|tool calling|function calling|"
    r"llms?|large language models?|embeddings?|vector (?:databases?|stores?)|"
    r"agents?|agentes?|fine[- ]tuning|mcp|langchain|langgraph|"
    r"transformers?|reranking|re-ranking|kubernetes|docker)"
)
_DEFINITION_NOUNS: Final[str] = (
    r"(?:technique|method|approach|way|framework|pattern|architecture|process|"
    r"paradigm|tecnica|metodo|enfoque|forma|manera|arquitectura|patron|proceso|"
    r"herramienta|metode|enfocament|patro|proces|eina)"
)
# Conservative: only sentences that define a stack term, in EN/ES/CA (folded,
# so "és"/"es" and "técnica"/"tecnica" are the same).
_DEFINITIONS: Final[tuple[re.Pattern[str], ...]] = (
    # "RAG is a technique that", "RAG es una técnica que", "el RAG és una tècnica"
    re.compile(
        rf"(?<!\w){_STACK}(?:\s*\([^)]*\))?\s+(?:is|are|es|son|so)\s+"
        rf"(?:a|an|the|una?|el|la)\s+(?:\w+\s+){{0,3}}?{_DEFINITION_NOUNS}(?!\w)"
    ),
    # "tool calling, which lets", "embeddings, which allows", "..., que permite"
    re.compile(
        rf"(?<!\w){_STACK}\s*,\s*(?:which|that|que)\s+(?:\w+\s+)?"
        r"(?:allows?|lets|enables?|means|permite|permiten|permet|permeten|"
        r"consiste|consisteix)(?!\w)"
    ),
    # "RAG refers to", "RAG stands for", "RAG significa", "RAG vol dir"
    re.compile(
        rf"(?<!\w){_STACK}\s+(?:refers to|stands for|means|se refiere a|"
        r"significa|vol dir)(?!\w)"
    ),
    # A gloss in brackets: "RAG (un buscador con IA)", "RAG (an AI search)"
    re.compile(rf"(?<!\w){_STACK}\s*\((?:a|an|the|un|una|el|la)\s"),
)
_APOS: Final[str] = "['\u2019]"
# Confessed gaps, folded (EN/ES/CA); each needs an explicit negation.
_GAPS: Final[re.Pattern[str]] = re.compile(
    r"(?<!\w)(?:"
    # English
    rf"i (?:have not|haven{_APOS}t|have never|did not|didn{_APOS}t) (?:yet )?"
    r"(?:used|worked (?:with|on)|tried|touched)|"
    r"i (?:have|had) no (?:\w+ )?(?:experience|exposure)|"
    rf"i (?:do not|don{_APOS}t) have (?:\w+ )?(?:experience|exposure)|"
    r"(?:although|while|even though) i (?:lack|am new to)|i lack|"
    r"not yet (?:used|worked)|yet to (?:use|work)|"
    # Spanish
    r"(?:aun|todavia) no (?:he|tengo|domino|conozco)|"
    r"no he (?:usado|utilizado|trabajado (?:con|en)|tocado|probado)|"
    r"nunca he (?:usado|utilizado|trabajado)|"
    r"no tengo (?:\w+ )?experiencia|aunque no tengo|me falta experiencia|"
    # Catalan
    r"encara no (?:he|tinc|domino|conec)|"
    r"no he (?:fet servir|utilitzat|treballat (?:amb|en))|"
    r"no tinc (?:\w+ )?experiencia|tot i que no tinc|em falta experiencia"
    r")(?!\w)"
)
# A capitalised name of one or more words ("Crédit Mutuel Arkéa", "Grupo
# Initech", "Banco de Sabadell"); lowercase connectors only between capitals.
_CAP_WORD: Final[str] = r"[A-ZÀ-ÖØ-Þ][\w&.'\u2019-]*"
_NAME: Final[str] = (
    rf"({_CAP_WORD}(?:[ \t]+(?:(?:de|del|du|des|of|y|et|and|&)[ \t]+)?"
    rf"{_CAP_WORD})*)"
)
# Phrases that introduce a parent group, owner or recruiter (case-insensitive).
_RELATED: Final[re.Pattern[str]] = re.compile(
    r"(?i:(?<!\w)(?:a part of|part of|a member of|member of|a subsidiary of|"
    r"subsidiary of|a division of|division of|owned by|acquired by|"
    r"on behalf of|forma parte del grupo|forma parte de|parte del grupo|"
    r"parte de|pertenece al grupo|pertenece a|perteneciente al grupo|"
    r"filial del grupo|filial de|en nombre de|forma part del grup|"
    r"forma part de|part del grup|filial del grup|en nom de))"
    r"[ \t]+(?i:(?:the|el|la|los|les)[ \t]+)?" + _NAME
)
_GROUP_SUFFIX: Final[re.Pattern[str]] = re.compile(r"[ \t]+(?:Group|Grupo|Grup)$")
_NOT_A_NAME: Final[frozenset[str]] = frozenset(
    {"our", "the", "we", "you", "your", "nuestro", "nuestra", "el", "la"}
)


def related_companies(offer_text: str, company: str) -> tuple[str, ...]:
    """Employer names the offer introduces as the parent group, owner or
    recruiter of the hiring company ("Powens, part of the Crédit Mutuel
    Arkéa group" gives ``("Crédit Mutuel Arkéa",)``).

    Names that are ``company`` itself (or contain it, or are contained in it)
    are left out, so a card that already names the group guards nothing.
    """
    own = normalize_company(company) if known_company(company) else ""
    found: list[str] = []
    for match in _RELATED.finditer(offer_text):
        name = _GROUP_SUFFIX.sub("", match.group(1).rstrip(".'\u2019-")).strip()
        key = normalize_company(name)
        if not key or key in _NOT_A_NAME or name in found:
            continue
        if own and (own in key or key in own):
            continue
        found.append(name)
    return tuple(found)


def _contains(folded: str, name: str) -> bool:
    return any(
        re.search(rf"(?<!\w){re.escape(form)}(?!\w)", folded) is not None
        for form in {_fold(name).strip(), normalize_company(name)}
        if form
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
    definitions: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    # Other employers the letter names (the card's parent group, recruiter...).
    wrong_company: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.score >= QUALITY_THRESHOLD

    @property
    def company_mismatch(self) -> bool:
        """The letter names another employer, or never names the card's."""
        return any(finding.code == "company" for finding in self.findings)

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
    min_words: int = QUALITY_MIN_WORDS,
    max_words: int = QUALITY_MAX_WORDS,
    company: str = "",
    offer_text: str = "",
) -> LetterQuality:
    """Score ``text`` (see the module docstring for the checks and penalties).

    ``offer_requirements`` are the offer's must-have requirement names (an
    empty list skips coverage); ``grounding`` is every source the letter may
    take figures from (``None`` skips that check). ``company`` is the offer's
    company as stored on the card and ``offer_text`` the text that may name its
    parent group or recruiter; an unknown company skips that check. ``language`` is accepted
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
                + " (name each one only if the claims back it; otherwise leave it "
                "out, never say the candidate lacks it)",
                round(_COVERAGE_POINTS * len(missing) / len(requirements)),
            )
        )

    words = len(text.split())
    if words > max_words:
        findings.append(
            QualityFinding(
                "length",
                f"the letter has {words} words; cut it to at most {max_words}: "
                "drop filler and explanations, keep the strongest facts",
                min(
                    _LENGTH_CAP,
                    _LENGTH_POINTS + (words - max_words) // _LENGTH_OVER_STEP,
                ),
            )
        )
    elif words < min_words:
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

    definitions = tuple(
        match.group(0)
        for pattern in _DEFINITIONS
        for match in pattern.finditer(folded_style)
    )
    if definitions:
        findings.append(
            QualityFinding(
                "definition",
                "explains a technical term to a technical reader, drop it: "
                + ", ".join(f'"{d}"' for d in definitions),
                min(_DEFINITION_CAP, _DEFINITION_POINTS * len(definitions)),
            )
        )

    gaps = tuple(match.group(0) for match in _GAPS.finditer(folded_style))
    if gaps:
        findings.append(
            QualityFinding(
                "gap",
                "states a missing skill ("
                + ", ".join(f'"{g}"' for g in gaps)
                + "): do not mention tools or skills the candidate lacks; "
                "highlight the closest real experience instead",
                min(_GAP_CAP, _GAP_POINTS * len(gaps)),
            )
        )

    wrong: tuple[str, ...] = ()
    name = known_company(company)
    if name:
        wrong = tuple(
            other
            for other in related_companies(offer_text, name)
            if _contains(folded_style, other)
        )
        if wrong:
            others = ", ".join(wrong)
            findings.append(
                QualityFinding(
                    "company",
                    f"the letter names {others}, but the offer is from {name}: "
                    f"address {name} only and do not name {others}",
                    _COMPANY_POINTS,
                )
            )
        elif not _contains(folded_all, name):
            findings.append(
                QualityFinding(
                    "company",
                    f"the letter never names {name}, the company that is hiring: "
                    f"address {name}",
                    _COMPANY_POINTS,
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
        definitions=definitions,
        gaps=gaps,
        wrong_company=wrong,
    )
