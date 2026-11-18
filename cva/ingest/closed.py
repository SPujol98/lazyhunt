"""Closed-offer detector: pure phrase matching on the offer text (ES/EN/CA).

A closed posting is not worth an LLM call. Only full phrase forms count, so
ordinary prose ("closed-loop", "we closed a funding round", "cerrada la
ronda", "close collaboration") never trips it. Returns the matched phrase as
evidence in folded form (lowercase, accents stripped), or ``None``. No I/O,
never raises.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

# Note appended to a closed offer (tracker notas: " · "-separated entries).
CLOSED_NOTE: Final[str] = "oferta cerrada"

_JOB_NOUN: Final[str] = (
    r"(?:job|position|role|posting|post|vacancy|listing|opening|offer|"
    r"oferta|puesto|vacante|posicio|lloc)"
)

_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(p)
    for p in (
        # English
        r"\bno\s+longer\s+accepting\s+(?:job\s+)?(?:applications?|applicants?|candidates?)\b",
        rf"\b{_JOB_NOUN}\s+(?:is|has\s+been|was)\s+no\s+longer\s+(?:available|open|active)\b",
        rf"\b(?:this|the)\s+{_JOB_NOUN}\s+(?:is\s+)?no\s+longer\s+(?:available|open|active)\b",
        r"\bapplications?\s+(?:are|is|have\s+been|has\s+been|were)\s+(?:now\s+)?closed\b",
        (
            rf"\b(?:this|the)\s+{_JOB_NOUN}\s+(?:is\s+now\s+|has\s+been\s+|has\s+)?"
            r"(?:closed|expired)\b"
        ),
        r"\bapplication\s+(?:period|window|process)\s+(?:is|has\s+been|has)\s+(?:now\s+)?closed\b",
        # Spanish
        r"\bya\s+no\s+(?:se\s+)?acepta(?:mos|n)?\s+(?:solicitudes|candidaturas|candidatos|inscripciones)\b",
        r"\boferta\s+(?:de\s+empleo\s+)?(?:cerrada|caducada|finalizada|expirada)\b",
        r"\b(?:esta|la)\s+oferta\s+(?:ya\s+no\s+esta\s+disponible|ha\s+(?:caducado|finalizado|expirado))\b",
        r"\bproceso\s+(?:de\s+seleccion\s+)?(?:cerrado|finalizado)\b",
        r"\binscripcion(?:es)?\s+cerrada(?:s)?\b",
        # Catalan
        r"\bja\s+no\s+(?:s'?)?accepta(?:m|n)?\s+(?:sol·licituds|solicituds|candidatures)\b",
        r"\boferta\s+(?:tancada|caducada|finalitzada)\b",
        r"\bproces\s+(?:de\s+seleccio\s+)?(?:tancat|finalitzat)\b",
        r"\binscripcio\s+tancada\b",
    )
)


def _fold(text: str) -> str:
    """Lowercase and strip accents (keeps the ``·`` of Catalan ``l·l``)."""
    decomposed = unicodedata.normalize("NFKD", text.lower().replace("’", "'"))
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def closed_marker(text: str) -> str | None:
    """The closed-offer phrase found in ``text`` in folded form, or ``None``.

    Folded means lowercase with accents stripped, not the original casing.
    """
    folded = _fold(text)
    # NFKD may change length (ligatures); positions only index the folded copy.
    best: re.Match[str] | None = None
    for pattern in _PATTERNS:
        m = pattern.search(folded)
        if m is not None and (best is None or m.start() < best.start()):
            best = m
    return best.group(0) if best is not None else None
