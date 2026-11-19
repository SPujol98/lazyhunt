"""Backend profile wall: the offer's core programming language is not the
profile's core language (Python by default).

Precision first (owner decision): an offer is walled only when TWO independent
signals agree on the same non-Python language, so an LLM extraction mistake
alone never drops a good offer.

1. LLM extraction: a must-have requirement naming that language.
2. Text count: the language dominates ``source_data`` (>= 2 mentions, more
   than Python, with Python mentioned at most once).

A must-have requirement for the core language never walls. Only evaluated for
backend-profile offers (the caller decides). The core language and the walled
languages come from ``[stack_wall]`` in ``preferences.toml``
(:class:`StackWallPrefs`); the defaults are the owner's original rule.
"""

import re
from dataclasses import dataclass
from typing import Final

from cva.i18n import Lang, t
from cva.match.extraction import OfferExtraction

# label -> whole-word pattern. "Go" is ambiguous in prose ("go to market"), so
# only the capitalised word or "Golang" counts.
_WALL_LANGUAGES: Final[dict[str, re.Pattern[str]]] = {
    "Java": re.compile(r"(?<![\w#+.])java(?![\w#+])", re.IGNORECASE),
    "C#/.NET": re.compile(r"(?<!\w)c#|\.net(?!\w)|\bdotnet\b", re.IGNORECASE),
    "JavaScript/TypeScript": re.compile(
        r"\b(?:javascript|typescript|node\.?js|node js)\b", re.IGNORECASE
    ),
    "Go": re.compile(r"(?i:\bgolang\b)|\bGo\b(?!-)"),
    "PHP": re.compile(r"\bphp\b", re.IGNORECASE),
    "Ruby": re.compile(r"\bruby\b", re.IGNORECASE),
    "Scala": re.compile(r"\bscala\b", re.IGNORECASE),
    "Kotlin": re.compile(r"\bkotlin\b", re.IGNORECASE),
    "Swift": re.compile(r"\bswift\b", re.IGNORECASE),
    "C++": re.compile(r"(?<!\w)c\+\+", re.IGNORECASE),
}
# "Python", "Python3", "python3.11", "Python 3" all count as Python.
_PYTHON: Final[re.Pattern[str]] = re.compile(
    r"\bpython(?:\s?3(?:\.\d+)*)?\b", re.IGNORECASE
)
# Every language the wall knows, core candidates included: label -> pattern.
LANGUAGE_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    "Python": _PYTHON,
    **_WALL_LANGUAGES,
}
_MIN_DOMINANT_MENTIONS: Final[int] = 2
_MAX_CORE_MENTIONS: Final[int] = 1


@dataclass(frozen=True)
class StackWallPrefs:
    """The profile's core language and which other languages wall an offer."""

    core_language: str = "Python"
    # ``None`` -> every known language except the core one.
    wall_languages: tuple[str, ...] | None = None


DEFAULT_STACK_WALL_PREFS: Final[StackWallPrefs] = StackWallPrefs()

_STORED_LANG: Final[Lang] = "es"
# The stored template turned into a parser: "stack principal (label), no (core)".
_STORED_RE: Final[re.Pattern[str]] = re.compile(
    "^"
    + "(?P<label>.+?)".join(
        re.escape(part)
        for part in t("bot.stack_wall", _STORED_LANG, label="\x00", core="\x01").split(
            "\x00"
        )
    ).replace(re.escape("\x01"), "(?P<core>.+)")
    + "$"
)


def stack_wall_reason(
    extraction: OfferExtraction,
    text: str,
    prefs: StackWallPrefs = DEFAULT_STACK_WALL_PREFS,
) -> str | None:
    """Short wall reason, or ``None`` when the offer is not walled.

    The reason is STORED (``MatchResult.stack_wall``, judge walls), so it is
    always the Spanish text; :func:`stack_wall_display` shows it in the UI
    language.
    """
    core = prefs.core_language
    core_pattern = LANGUAGE_PATTERNS[core]
    walled = {
        label: pattern
        for label, pattern in LANGUAGE_PATTERNS.items()
        if label != core
        and (prefs.wall_languages is None or label in prefs.wall_languages)
    }
    must_have = [r.name for r in extraction.requirements if r.must_have]
    if any(core_pattern.search(name) for name in must_have):
        return None
    core_mentions = len(core_pattern.findall(text))
    if core_mentions > _MAX_CORE_MENTIONS:
        return None
    llm_languages = {
        label
        for label, pattern in walled.items()
        if any(pattern.search(name) for name in must_have)
    }
    dominant = [
        (count, label)
        for label in llm_languages
        if (count := len(walled[label].findall(text))) >= _MIN_DOMINANT_MENTIONS
        and count > core_mentions
    ]
    if not dominant:
        return None
    _, label = max(dominant)
    return t("bot.stack_wall", _STORED_LANG, label=label, core=core)


def stack_wall_display(reason: str, lang: Lang) -> str:
    """The stored (Spanish) stack-wall ``reason`` shown in ``lang``; any other
    text is returned unchanged."""
    if lang == _STORED_LANG:
        return reason
    found = _STORED_RE.match(reason)
    if found is None:
        return reason
    return t("bot.stack_wall", lang, label=found["label"], core=found["core"])
