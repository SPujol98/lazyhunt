"""Offer profile (backend-profile): 'ai' vs 'backend'.

Beacon offers carry the profile of the beacon that found them (the extension
sends it in the bridge payload). Manual offers (/analyze URL or paste) infer
it from the title: no AI term -> 'backend'. A beacon offer whose title shows an
AI term is 'ai' whatever the beacon profile (the backend filter lets any
"Engineer" through). NULL in the DB means a legacy row
and is treated as 'ai' everywhere.
"""

import re
import unicodedata
from typing import Final, Literal

Profile = Literal["ai", "backend"]
DEFAULT_PROFILE: Final[Profile] = "ai"

# MUST mirror the first include group of DEFAULT_TITLE_FILTER in
# extension/filter.js (the extension `ai` profile); a test pins the two together.
AI_TITLE_TERMS: Final[tuple[str, ...]] = (
    "AI", "IA", "Artificial Intelligence", "Inteligencia Artificial", "GenAI",
    "Generative AI", "LLM", "LLMOps", "MLOps", "RAG", "Agentic", "AI Agent",
    "Machine Learning", "NLP", "Prompt",
)  # fmt: skip


def _normalize(text: str) -> str:
    """Lowercase, strip diacritics, collapse non-alphanumerics (as filter.js)."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", stripped).strip()


_AI_TERM_RE: Final[re.Pattern[str]] = re.compile(
    "(?:^|\\s)(?:" + "|".join(re.escape(_normalize(t)) for t in AI_TITLE_TERMS) + ")(?:$|\\s)"
)


def normalize_profile(value: object) -> Profile:
    """'backend' only when explicitly so; anything else is 'ai'."""
    return "backend" if value == "backend" else "ai"


def role_for_profile(profile: object) -> str:
    """CV role a card starts on: 'BACKEND' for backend offers, else 'AI'."""
    return "BACKEND" if normalize_profile(profile) == "backend" else "AI"


def has_ai_title(title: str | None) -> bool:
    """True when the title contains an AI term (whole word, case/accent-blind)."""
    return bool(title) and _AI_TERM_RE.search(_normalize(title or "")) is not None


def infer_profile(title: str | None) -> Profile:
    """Profile of a manual offer from its title; unknown title -> 'ai'."""
    if title is None or not title.strip():
        return DEFAULT_PROFILE
    return "ai" if has_ai_title(title) else "backend"
