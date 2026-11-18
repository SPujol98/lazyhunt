"""Pure language inference: ES/EN/CA stopword-ratio heuristic (design D10)."""

import re
from typing import Final, Literal

Language = Literal["es", "en", "ca"]

_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[^\W\d_]+")

# "I+D" / "I+D+i" (R&D) is everyday Spanish; its "i" must not count as the
# Catalan conjunction, so the acronym is removed before tokenizing.
_RD_ACRONYM_RE: Final[re.Pattern[str]] = re.compile(r"\bi\s*\+\s*d(?:\s*\+\s*i)?\b")

# Catalan text still matches plenty of ES stopwords (shared function words):
# real Catalan scores markers >= 1/4 of its ES count (the AI_CA master: 49 vs
# 81), Spanish prose with a stray marker stays far below (AI_ES: 1 vs 144).
_CA_MIN_SHARE_OF_ES: Final[int] = 4

# Hand-curated disjoint stopword sets: a token scoring for one language
# never belongs to the other's set, so counts are unambiguous.
_ES_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "al",
        "algo",
        "ante",
        "como",
        "con",
        "contra",
        "cuando",
        "del",
        "de",
        "desde",
        "donde",
        "durante",
        "el",
        "ella",
        "ellas",
        "ellos",
        "en",
        "era",
        "es",
        "esa",
        "ese",
        "eso",
        "esta",
        "este",
        "estos",
        "fue",
        "fueron",
        "hasta",
        "la",
        "las",
        "le",
        "les",
        "lo",
        "los",
        "más",
        "muchos",
        "muy",
        "nada",
        "ni",
        "nos",
        "nuestro",
        "otros",
        "o",
        "para",
        "pero",
        "por",
        "porque",
        "que",
        "quien",
        "se",
        "ser",
        "sin",
        "sobre",
        "somos",
        "son",
        "su",
        "sus",
        "también",
        "tener",
        "tiene",
        "ti",
        "todo",
        "todos",
        "tú",
        "un",
        "una",
        "uno",
        "unos",
        "usted",
        "ya",
        "y",
    }
)

_EN_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "about",
        "all",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "but",
        "by",
        "can",
        "do",
        "for",
        "from",
        "go",
        "had",
        "has",
        "have",
        "he",
        "her",
        "him",
        "his",
        "how",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "like",
        "make",
        "many",
        "more",
        "my",
        "now",
        "of",
        "or",
        "other",
        "our",
        "out",
        "she",
        "so",
        "some",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "up",
        "was",
        "we",
        "were",
        "what",
        "when",
        "which",
        "will",
        "with",
        "would",
        "you",
        "your",
    }
)


# CA-specific markers (v3 part B): Catalan tokens that are NOT valid
# Spanish or English words, so their presence is unambiguous evidence.
# ES/CA share many function words (que, el, la, un...), which is exactly
# why CA election is marker-count based, not ratio based (see below).
_CA_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "així",
        "això",
        "amb",
        "cada",
        "cap",
        "dissabte",
        "divendres",
        "dilluns",
        "dijous",
        "dimarts",
        "dimecres",
        "ells",
        "elles",
        "faig",
        "fer",
        "hem",
        "hi",
        "i",
        "molt",
        "molta",
        "necessitem",
        "oferim",
        "per",
        "però",
        "plaça",
        "són",
        "som",
        "sota",
        "també",
        "valora",
        "volem",
    }
)


def infer_language(text: str) -> Language:
    """Infer ``"es"``, ``"en"`` or ``"ca"`` from stopword frequency.

    Pure and deterministic. CA election (ES/CA disambiguation, v3 part B):
    Catalan shares most function words with Spanish, so CA wins when the
    text carries TWO OR MORE CA-specific markers (tokens that are not valid
    ES/EN words — "i" included: the strict ``ca > en`` comparison keeps
    English first-person prose, rich in "I", from electing Catalan) AND
    strictly more markers than EN stopwords AND markers at least a quarter
    of the ES stopword count (so a few stray markers in Spanish prose never
    elect Catalan); the "I+D" acronym is ignored. Otherwise the legacy ES/EN
    stopword ratio applies: the higher count wins, and ties — including
    short texts or texts with zero stopwords on either side — resolve to
    ``"es"`` because the primary market of this tracker is Spanish. An
    explicit user override (bot toggle / CLI flag) always beats inference.
    """
    tokens = _TOKEN_RE.findall(_RD_ACRONYM_RE.sub(" ", text.casefold()))
    es = sum(1 for token in tokens if token in _ES_STOPWORDS)
    en = sum(1 for token in tokens if token in _EN_STOPWORDS)
    ca = sum(1 for token in tokens if token in _CA_MARKERS)
    if ca >= 2 and ca > en and ca * _CA_MIN_SHARE_OF_ES >= es:
        return "ca"
    return "es" if es >= en else "en"
