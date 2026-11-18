"""Match engine: LLM extraction, deterministic scorer, synonym map, language."""

from cva.match.extraction import OfferExtraction, Requirement
from cva.match.language import infer_language
from cva.match.scorer import (
    MatchResult,
    RequirementMatch,
    SynonymEntry,
    SynonymError,
    SynonymMap,
    classify_keyword,
    load_synonyms,
    score,
)

__all__ = [
    "MatchResult",
    "OfferExtraction",
    "Requirement",
    "RequirementMatch",
    "SynonymEntry",
    "SynonymError",
    "SynonymMap",
    "classify_keyword",
    "infer_language",
    "load_synonyms",
    "score",
]
