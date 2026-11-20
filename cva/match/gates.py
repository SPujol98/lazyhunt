"""Pre-scoring gates: eligibility, required languages and work location.

Pure, deterministic, regex/heuristic (no LLM). The language and location rules
follow the profile's preferences (:class:`GatePrefs`, ``preferences.toml``);
the defaults describe the original owner: a Spanish (EU) citizen living in
Barcelona, Spanish and Catalan native, English C1.

Precision over recall: a false FAIL silently hides a good offer, so FAIL is
reserved for strong evidence and anything weaker is a FLAG (a soft warning on
the card). Silence is always PASS.

- Eligibility: FAIL when a non-EU citizenship, residency or work authorization
  is required; FLAG for security clearances, a non-EU requirement next to
  EU/Spain wording, or one with a negation nearby; PASS only without any hit.
- Language: FAIL for a mandatory language outside ES/CA/EN; FLAG for a
  desirable, negated or ambiguously mentioned one, and for English above C1.
- Location: on-site/hybrid (or no remote wording) with positive evidence that
  the work place is outside Catalonia → FAIL; remote or any Catalan place →
  PASS; mixed or unclear signals (several places, remote with visits outside
  Catalonia, office work without a place) → FLAG. Unknown places never fail.
- Negation never erases a hit, it only downgrades it to FLAG (no false PASS).
- Generic home (``[location] city``/``country`` outside the home region): the
  same rules over the user's city (or country) matched by NAME, with no
  distance; a FAIL becomes a FLAG, because a name list cannot tell a suburb
  of the city from another city (see ``locations.generic_region``).
"""

import re
import unicodedata
from dataclasses import dataclass
from functools import cache
from string import Formatter
from typing import Final, Literal

from pydantic import BaseModel

from cva.i18n import CATALOGS, Lang, join_list, t
from cva.match.locations import (
    PlaceHit,
    Region,
    find_places,
    fold_place,
    generic_region,
    get_region,
)

Gate = Literal["eligibility", "language", "location"]
Verdict = Literal["PASS", "FLAG", "FAIL"]
# "mixed": remote next to hybrid/office wording; "unknown": no work-mode wording.
WorkMode = Literal["remote", "hybrid", "onsite", "mixed", "unknown"]

_EVIDENCE_RADIUS: Final[int] = 70
_EVIDENCE_MAX: Final[int] = 160


@dataclass(frozen=True)
class GatePrefs:
    """The profile preferences the language and location gates read.

    ``allowed_languages`` are ISO codes (see ``LANGUAGE_CODES``) of the
    languages the profile speaks: any other mandatory language FAILs.
    ``flag_english_above_c1`` only applies when ``en`` is allowed.
    ``home_region`` is a key of ``cva.match.locations.REGIONS``.
    ``city``/``country`` (``[location]``, by name) switch the location gate to
    the generic by-name home unless the city is inside ``home_region``.
    """

    allowed_languages: tuple[str, ...] = ("es", "ca", "en")
    flag_english_above_c1: bool = True
    home_region: str = "catalonia"
    city: str | None = None
    country: str | None = None


DEFAULT_GATE_PREFS: Final[GatePrefs] = GatePrefs()


# ``GateResult.reason`` is STORED (breakdown_json, judge walls): it is always the
# Spanish text, whatever ``ui_language`` is. Display code translates it with
# :func:`gate_reason`; nothing persisted changes.
_STORED_LANG: Final[Lang] = "es"


class GateResult(BaseModel):
    """One gate outcome. ``reason`` is Spanish (card text, stored as such; use
    :func:`gate_reason` to show it in the UI language); ``evidence`` quotes
    the offer. PASS carries an empty ``reason``."""

    gate: Gate
    verdict: Verdict
    reason: str = ""
    evidence: str = ""


def _reason(key: str, **fmt: str) -> str:
    """The stored (Spanish) reason text for ``key``."""
    return t(f"panel.gate.{key}", _STORED_LANG, **fmt)


def _fold(text: str) -> str:
    """Lowercase and strip accents WITHOUT changing the length, so indices into
    the folded text are valid indices into the original (for evidence)."""
    out: list[str] = []
    for char in text:
        base = unicodedata.normalize("NFD", char)[0]
        lowered = base.lower()
        out.append(lowered if len(lowered) == 1 else base)
    return "".join(out)


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _snippet(text: str, start: int, end: int) -> str:
    """Evidence window around ``text[start:end]``, at most ``_EVIDENCE_MAX``
    characters, trimmed symmetrically around the match (not the window)."""
    before = _collapse(text[max(0, start - _EVIDENCE_RADIUS) : start])
    match = _collapse(text[start:end])
    after = _collapse(text[end : min(len(text), end + _EVIDENCE_RADIUS)])
    if len(match) >= _EVIDENCE_MAX:
        return match[:_EVIDENCE_MAX].strip()
    room = (_EVIDENCE_MAX - len(match)) // 2
    before = before[-room:] if room else ""
    after = after[: _EVIDENCE_MAX - len(match) - len(before)]
    return (before + match + after).strip()


# --- eligibility ---------------------------------------------------------------

_COUNTRY: Final[str] = (
    r"(?:the\s+us\b|u\.s\.?a?(?![a-z])|usa\b|united\s+states|the\s+uk\b|u\.k\.|"
    r"uk\b|united\s+kingdom|great\s+britain|britain|canada|switzerland|swiss|"
    r"australia|singapore|uae\b|united\s+arab\s+emirates|india\b|brazil|japan|"
    r"israel|norway)"
)
_COUNTRY_ES: Final[str] = (
    r"(?:ee\.?\s?uu\.?|estados\s+unidos|reino\s+unido|suiza|canada|australia|"
    r"singapur|emiratos|india\b|brasil|japon|israel|noruega)"
)
_AUTH: Final[str] = (
    r"(?:authori[sz]ed|authori[sz]ation|eligible|legally|legal\s+right|permitted)"
)

_NON_EU_FAIL: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(p)
    for p in (
        r"\bgreen\s+card\b",
        r"\bindefinite\s+leave\s+to\s+remain\b",
        r"\b(?:united\s+states|american|british)\s+citizen",
        rf"{_AUTH}\s+(?:\w+\s+){{0,3}}?to\s+work\s+(?:in|within|for)\s+(?:the\s+)?{_COUNTRY}",
        rf"\bright\s+to\s+work\s+(?:in|within)\s+{_COUNTRY}",
        r"\b(?:uk|u\.k\.|united\s+kingdom|british)\s+right\s+to\s+work",
        rf"\bwork\s+(?:authori[sz]ation|permit|visa)\s+(?:in|for)\s+(?:the\s+)?{_COUNTRY}",
        r"\bswiss\s+(?:work\s+(?:permit|authori[sz]ation)|citizen)",
        r"\bpermiso\s+de\s+trabajo\s+suizo\b",
        (
            rf"\b(?:must|need\s+to|required\s+to|have\s+to|should)\s+(?:currently\s+)?"
            rf"(?:be\s+)?(?:resid(?:e|ing|ent)|liv(?:e|ing)|based|located)\s+"
            rf"(?:in|within)\s+(?:the\s+)?{_COUNTRY}"
        ),
        rf"\bresidir\s+en\s+(?:el\s+)?{_COUNTRY_ES}",
        rf"\bpermiso\s+de\s+trabajo\s+(?:en|para)\s+(?:el\s+)?{_COUNTRY_ES}",
        r"\bciudadan(?:ia|o|a)\s+(?:estadounidense|americana|norteamericana|britanica)",
        r"\bnacionalidad\s+(?:estadounidense|americana|britanica)",
    )
)
# Case-sensitive: "us" is a common word, only the capitalised forms count.
_US_CITIZEN: Final[re.Pattern[str]] = re.compile(r"\b(?:U\.S\.|US|USA)\s+[Cc]itizen")
_EU_WORDING: Final[re.Pattern[str]] = re.compile(
    r"\b(?:eu|ue|eea|eee|european\s+union|union\s+europea|europe|europa|spain|"
    r"espana|spanish|espanol[a]?|schengen)\b"
)
_EU_PASS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(p)
    for p in (
        r"\bnacionalidad\s+espanola\b",
        r"\bspanish\s+(?:nationality|citizen)",
        r"\beu\s+citizen",
        r"\bciudadan[oa]s?\s+(?:de\s+la\s+)?(?:ue|union\s+europea|europe[oa])",
        r"\bpermiso\s+de\s+trabajo\s+(?:en\s+)?(?:espana|la\s+ue|europa)",
        r"\b(?:authori[sz]ed|right)\s+(?:\w+\s+){0,3}?to\s+work\s+in\s+(?:the\s+)?(?:eu|spain|europe)",
    )
)
_CLEARANCE: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(p)
    for p in (
        r"\bsecurity\s+clearance\b",
        r"\bnato\s+secret\b",
        r"\bhabilitacion\s+(?:personal\s+de\s+)?(?:de\s+)?seguridad\b",
    )
)
_HPS: Final[re.Pattern[str]] = re.compile(r"\bHPS\b")
_NEGATION_BEFORE: Final[re.Pattern[str]] = re.compile(
    r"(?:\bnon[-\s]?|\bno\s+|\bnot\s+|\bwithout\s+|\bsin\s+|\bsense\s+)$"
)
# Any negation in the clause before a hit. A negation can only DOWNGRADE the
# hit to FLAG, never erase it: working out whether it really governs the
# requirement ("not required that you relocate since candidates are US
# citizens only") is exactly where regexes produce false PASSes.
_AUX: Final[str] = (
    r"(?:\b(?:do|does|did|is|are|was|were|will|would|can|could|should)\s*n['\u2019]?t\b"
    r"|\b(?:don|doesn|isn|aren|won|wouldn|needn|shouldn)['\u2019]t\b"
    r"|\b(?:do|does|will|would|is|are|should)\s+not\b)"
)
_NEGATION_CLAUSE: Final[re.Pattern[str]] = re.compile(
    rf"(?:{_AUX}"
    r"|\b(?:not|never)\s+(?:be\s+)?(?:required|needed|necessary|mandatory)\b"
    r"|\bno\s+(?:need|requirement)\b"
    r"|\bno\s+(?:es\s+(?:necesari[oa]|obligatori[oa]|imprescindible|requisito)"
    r"|(?:se\s+)?(?:requiere|exige|precisa)|hace\s+falta|necesitas?|necesitamos"
    r"|tienes?\s+que|hay\s+que|es\s+preciso)\b"
    r"|\bno\s+(?:cal|cap\s+necessitat|es\s+necessari|es\s+requereix)\b"
    r"|\bsense\s+(?:necessitat|haver)\b)"
)
# "unless you are / a menos que seas": the hit is a requirement, not negated.
_UNLESS: Final[re.Pattern[str]] = re.compile(
    r"\b(?:unless|except\s+(?:if|when|for)|a\s+menos\s+que|a\s+no\s+ser\s+que"
    r"|salvo\s+que|excepto\s+si|llevat\s+que|tret\s+que)\b"
)
_CLAUSE_LOOKBACK: Final[int] = 60
_CLAUSE_BREAK: Final[re.Pattern[str]] = re.compile(r"[.;!?\n]\s")


def _negated(folded: str, start: int) -> bool:
    """Is the hit at ``start`` preceded by a negation in its clause?

    True for an adjacent negator ("non-US", "no security clearance") or any
    negation phrase earlier in the same clause. An ``unless`` clause after the
    negation turns the hit back into a requirement (False).
    """
    if _NEGATION_BEFORE.search(folded[max(0, start - 12) : start]):
        return True
    clause = folded[max(0, start - _CLAUSE_LOOKBACK) : start]
    breaks = list(_CLAUSE_BREAK.finditer(clause))
    if breaks:
        clause = clause[breaks[-1].end() :]
    found = list(_NEGATION_CLAUSE.finditer(clause))
    return bool(found) and not _UNLESS.search(clause[found[-1].end() :])


def eligibility_gate(text: str) -> GateResult:
    """Non-EU eligibility requirement → FAIL; clearance or a negated mention
    → FLAG; else PASS."""
    folded = _fold(text)
    hits: list[tuple[int, int]] = []
    negated: list[tuple[int, int]] = []

    def classify(span: tuple[int, int]) -> None:
        (negated if _negated(folded, span[0]) else hits).append(span)

    for pattern in _NON_EU_FAIL:
        for m in pattern.finditer(folded):
            classify(m.span())
    for m in _US_CITIZEN.finditer(text):
        classify(m.span())
    if hits:
        start, end = min(hits)
        window = folded[max(0, start - 100) : end + 100]
        evidence = _snippet(text, start, end)
        if _EU_WORDING.search(window):
            return GateResult(
                gate="eligibility",
                verdict="FLAG",
                reason=_reason("eligibility_eu_wording"),
                evidence=evidence,
            )
        return GateResult(
            gate="eligibility",
            verdict="FAIL",
            reason=_reason("eligibility_fail"),
            evidence=evidence,
        )
    if negated:
        start, end = min(negated)
        return GateResult(
            gate="eligibility",
            verdict="FLAG",
            reason=_reason("eligibility_negated"),
            evidence=_snippet(text, start, end),
        )
    clearance = [m.span() for pattern in _CLEARANCE for m in pattern.finditer(folded)]
    clearance += [m.span() for m in _HPS.finditer(text)]
    if clearance:
        start, end = min(clearance)
        return GateResult(
            gate="eligibility",
            verdict="FLAG",
            reason=_reason("clearance"),
            evidence=_snippet(text, start, end),
        )
    for pattern in _EU_PASS:
        if eu_hit := pattern.search(folded):
            return GateResult(
                gate="eligibility",
                verdict="PASS",
                evidence=_snippet(text, *eu_hit.span()),
            )
    return GateResult(gate="eligibility", verdict="PASS")


# --- languages -----------------------------------------------------------------

# Spanish label -> accent-folded names in ES/EN/CA. "italia" (Catalan "italià")
# is deliberately absent: it would collide with the country name. Likewise
# "fines" (finés) is absent: it collides with "con fines de", "a fines de".
_LANGUAGES: Final[dict[str, tuple[str, ...]]] = {
    "alemán": ("aleman", "german", "alemany", "deutsch"),
    "francés": ("frances", "french"),
    "italiano": ("italiano", "italian"),
    "portugués": ("portugues", "portuguese"),
    "neerlandés": ("neerlandes", "holandes", "dutch", "nederlands"),
    "polaco": ("polaco", "polish", "polones"),
    "sueco": ("sueco", "swedish", "suec"),
    "danés": ("danes", "danish"),
    "noruego": ("noruego", "norwegian", "noruec"),
    "finés": ("finlandes", "finnish"),
    "árabe": ("arabe", "arabic", "arab"),
    "chino": ("chino", "chinese", "mandarin", "mandarino", "xines"),
    "japonés": ("japones", "japanese"),
    "ruso": ("ruso", "russian", "rus"),
    "turco": ("turco", "turkish", "turc"),
    "hebreo": ("hebreo", "hebrew", "hebreu"),
    "checo": ("checo", "czech", "txec"),
    "rumano": ("rumano", "romanian", "romanes"),
    "griego": ("griego", "greek", "grec"),
    "húngaro": ("hungaro", "hungarian", "hongares"),
}
# ISO code -> label (a key of ``_LANGUAGES``) for the languages checked by
# default, and the same for the languages the owner speaks; any code can be
# allowed or not through ``GatePrefs.allowed_languages``.
_FOREIGN_CODES: Final[dict[str, str]] = {
    "de": "alemán",
    "fr": "francés",
    "it": "italiano",
    "pt": "portugués",
    "nl": "neerlandés",
    "pl": "polaco",
    "sv": "sueco",
    "da": "danés",
    "no": "noruego",
    "fi": "finés",
    "ar": "árabe",
    "zh": "chino",
    "ja": "japonés",
    "ru": "ruso",
    "tr": "turco",
    "he": "hebreo",
    "cs": "checo",
    "ro": "rumano",
    "el": "griego",
    "hu": "húngaro",
}
_ENGLISH: Final[str] = r"(?:ingles|english|angles)"
_HOME_LANGUAGES: Final[dict[str, tuple[str, tuple[str, ...]]]] = {
    "es": ("español", ("espanol", "spanish", "castellano", "castella")),
    "ca": ("catalán", ("catalan", "catala")),
    "en": ("inglés", ("ingles", "english", "angles")),
}
LANGUAGE_CODES: Final[frozenset[str]] = frozenset(_FOREIGN_CODES) | frozenset(
    _HOME_LANGUAGES
)


@cache
def _language_patterns(allowed: tuple[str, ...]) -> dict[str, re.Pattern[str]]:
    """label -> whole-word pattern for every language NOT in ``allowed``."""
    by_label: dict[str, tuple[str, ...]] = {}
    for code, label in _FOREIGN_CODES.items():
        if code not in allowed:
            by_label[label] = _LANGUAGES[label]
    for code, (label, names) in _HOME_LANGUAGES.items():
        if code not in allowed:
            by_label[label] = names
    return {
        label: re.compile(r"\b(?:" + "|".join(names) + r")\b")
        for label, names in by_label.items()
    }


_CONTEXT: Final[re.Pattern[str]] = re.compile(
    r"\b(?:idiomas?|idiomes|language|languages|lengua|llengua|nivel|nivell|level|"
    r"speak\w*|hablar|habla|hablante|parlar|fluent|fluido|fluidez|fluency|native|"
    r"nativo|nadiu|written|spoken|oral|escrito|bilingue|bilingual|proficien\w*|"
    r"knowledge|conocimientos?|coneixements?|domini|dominio|mother\s+tongue|"
    r"[abc][12])\b"
)
_HEADING: Final[re.Pattern[str]] = re.compile(
    r"\b(?:idiomas?|idiomes|languages?|llengues)\b.*:\s*$"
)
_DESIRABLE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:valorable|valorables|se\s+valora\w*|es\s+valora\w*|valora\w*|deseable|"
    r"desitjable|nice\s+to\s+have|a\s+plus|plus|bonus|preferred|preferible|"
    r"ideal|an\s+advantage|ventaja|optional|opcional|apreciat|beneficial|"
    r"suma\s+puntos|es\s+un\s+plus)\b"
)
_MANDATORY: Final[re.Pattern[str]] = re.compile(
    r"\b(?:requerid[oa]s?|requerimos|se\s+requiere|requisito|imprescindibles?|"
    r"obligatori[oa]s?|indispensables?|necesari[oa]s?|required|requires?|must|"
    r"mandatory|essential|needed|necessary|fluent|fluido|nivel\s+nativo|"
    r"native\s+level|nativo|native|[bc][12]|c2)\b"
)
_NEGATED_REQ: Final[re.Pattern[str]] = re.compile(
    r"(?:\bno\b|\bnot\b|\bwithout\b|\bsin\b)\s+(?:\w+\s+){0,3}?"
    r"(?:required|needed|necessary|necesari[oa]|requerid[oa]|requiere|"
    r"imprescindible|obligatori[oa]|need|necessitat)"
)
_ENGLISH_ABOVE_C1: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(p)
    for p in (
        rf"\b{_ENGLISH}\b[^.\n]{{0,25}}\bc2\b",
        rf"\bc2\b[^.\n]{{0,25}}\b{_ENGLISH}\b",
        r"\bnative\s+english\b",
        r"\benglish\s+native\b",
        r"\bnative\s+speaker\s+of\s+english\b",
        r"\bnative(?:\s+or\s+near[-\s]native)?[-\s]level\s+english\b",
        r"\bbilingual\s+english\b",
        r"\benglish\s+bilingual\b",
        r"\bmother\s+tongue\s+english\b",
        r"\bingles\s+(?:nativo|bilingue)\b",
        r"\bnativo\s+de\s+ingles\b",
        r"\bnivell\s+c2\s+d.angles\b",
        r"\bangles\s+(?:natiu|nadiu|bilingue)\b",
    )
)
_PROXIMITY: Final[int] = 50
# A requirement marker this close to the language name is context by itself
# ("alemán imprescindible", "German is a plus").
_ADJACENT: Final[int] = 15
_SEGMENT_SPLIT: Final[re.Pattern[str]] = re.compile(r"[^\n.!?;]+")


def _gap(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Characters between two spans (0 when they touch or overlap)."""
    return max(0, max(a[0], b[0]) - min(a[1], b[1]))


def _nearest(span: tuple[int, int], pattern: re.Pattern[str], seg: str) -> int | None:
    gaps = [_gap(span, m.span()) for m in pattern.finditer(seg)]
    return min(gaps) if gaps else None


def _segments(folded: str) -> list[tuple[int, str, bool]]:
    """(offset, text, under_language_heading) for every sentence/line."""
    result: list[tuple[int, str, bool]] = []
    previous_heading = False
    for m in _SEGMENT_SPLIT.finditer(folded):
        seg = m.group()
        if not seg.strip():
            continue
        result.append((m.start(), seg, previous_heading))
        previous_heading = bool(_HEADING.search(seg.strip()))
    return result


def _classify_mention(seg: str, span: tuple[int, int], heading: bool) -> Verdict | None:
    """Verdict for one language mention, or ``None`` when it is not a
    language requirement (no context nearby). A negated mention is a FLAG."""
    near_context = _nearest(span, _CONTEXT, seg)
    near_marker = min(
        (
            gap
            for gap in (
                _nearest(span, _MANDATORY, seg),
                _nearest(span, _DESIRABLE, seg),
            )
            if gap is not None
        ),
        default=None,
    )
    has_context = (
        heading
        or (near_context is not None and near_context <= _PROXIMITY)
        or (near_marker is not None and near_marker <= _ADJACENT)
    )
    if not has_context:
        return None
    negated = _nearest(span, _NEGATED_REQ, seg)
    if negated is not None and negated <= _PROXIMITY:
        return "FLAG"
    desirable = _nearest(span, _DESIRABLE, seg)
    mandatory = _nearest(span, _MANDATORY, seg)
    if (
        desirable is not None
        and desirable <= _PROXIMITY
        and (mandatory is None or desirable <= mandatory)
    ):
        return "FLAG"
    if mandatory is not None and mandatory <= _PROXIMITY:
        return "FAIL"
    return "FLAG"


def _english_above_c1(text: str, folded: str) -> GateResult | None:
    for pattern in _ENGLISH_ABOVE_C1:
        if english_hit := pattern.search(folded):
            return GateResult(
                gate="language",
                verdict="FLAG",
                reason=_reason("english_above_c1"),
                evidence=_snippet(text, *english_hit.span()),
            )
    return None


def _join(labels: list[str]) -> str:
    return join_list(labels, _STORED_LANG)


def language_gate(text: str, prefs: GatePrefs = DEFAULT_GATE_PREFS) -> GateResult:
    """Required language outside the allowed ones (ES/CA/EN by default) →
    FAIL; desirable or ambiguous → FLAG; English above C1 → FLAG; else PASS.
    Worst verdict wins."""
    folded = _fold(text)
    patterns = _language_patterns(prefs.allowed_languages)
    found: dict[Verdict, dict[str, tuple[int, int]]] = {"FAIL": {}, "FLAG": {}}
    for offset, seg, heading in _segments(folded):
        for label, pattern in patterns.items():
            for m in pattern.finditer(seg):
                verdict = _classify_mention(seg, m.span(), heading)
                if verdict is None or verdict == "PASS":
                    continue
                span = (offset + m.start(), offset + m.end())
                found[verdict].setdefault(label, span)
    if found["FAIL"]:
        labels = list(found["FAIL"])
        allowed = "/".join(code.upper() for code in prefs.allowed_languages)
        return GateResult(
            gate="language",
            verdict="FAIL",
            reason=_reason("language_fail", labels=_join(labels), allowed=allowed),
            evidence=_snippet(text, *min(found["FAIL"].values())),
        )
    if found["FLAG"]:
        labels = list(found["FLAG"])
        return GateResult(
            gate="language",
            verdict="FLAG",
            reason=_reason("language_flag", labels=_join(labels)),
            evidence=_snippet(text, *min(found["FLAG"].values())),
        )
    if "en" in prefs.allowed_languages and prefs.flag_english_above_c1:
        flagged = _english_above_c1(text, folded)
        if flagged is not None:
            return flagged
    return GateResult(gate="language", verdict="PASS")


# --- location ------------------------------------------------------------------

_SEP: Final[str] = r"[\s-]+"
_NEG_BEFORE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:no|not|non|sin|sense|ni)(?:[\s-]+(?:es|is|hay|se|ofrece|ofrecemos|permite|"
    r"permitimos|ofrecen|admite|disponible|available|offer|offered|possible|posible|"
    r"tiene|tenemos|contamos|con|de|el|la|un|una|a|an|this|role|position|puesto|"
    r"opcion|option|full|fully|100%?|totalmente|completamente|ni))*[\s-]+$"
)
_REMOTE: Final[re.Pattern[str]] = re.compile(
    # Any inflection: remotos/remotas/remotamente/remotely, teletrabajar...
    r"\b(?:100\s*%?\s*)?(?:remot\w*|teletrabaj\w*|teletreball\w*|"
    r"telework\w*|work(?:ing)?\s+from\s+home|wfh|a\s+distancia|"
    r"trabajo\s+desde\s+casa|desde\s+casa)\b"
)
# Benefit words: a remote mention in the same clause as these is a perk
# ("vacaciones por puntos, teletrabajo y flexibilidad"), not the work mode.
_PERKS: Final[re.Pattern[str]] = re.compile(
    r"\b(?:beneficios?|ventajas|flexibilidad|vacaciones|seguro|"
    r"conciliaci\w*|retribuci\w*|wellbeing|benefits?|perks?|"
    r"holidays?|insurance|tickets?)\b"
)
# ...unless the clause states the work mode itself ("Modalidad de teletrabajo").
_MODE_WORDS: Final[re.Pattern[str]] = re.compile(
    r"\b(?:modalidad|puesto|posicion|position|role|work\s+mode)\b"
)
_CLAUSE_END: Final[re.Pattern[str]] = re.compile(r"[.;\n]")


def _clause(folded: str, start: int, end: int) -> str:
    """The sentence or list line around ``[start, end)``."""
    left = max((m.end() for m in _CLAUSE_END.finditer(folded, 0, start)), default=0)
    right_m = _CLAUSE_END.search(folded, end)
    return folded[left : right_m.start() if right_m else len(folded)]


_STRONG_REMOTE: Final[re.Pattern[str]] = re.compile(
    rf"\b(?:100\s*%?\s*(?:remot\w*|teletrabaj\w*)|full(?:y)?{_SEP}remote|"
    rf"(?:totalmente|completamente){_SEP}remot\w*|teletrabajo{_SEP}(?:total|completo)|"
    rf"remoto{_SEP}total|remote{_SEP}first|work{_SEP}from{_SEP}anywhere)\b"
)
_HYBRID: Final[re.Pattern[str]] = re.compile(
    # "hybrid search/cloud/RAG..." is a skill, not a work mode.
    rf"\b(?:(?:hibrid[oa]s?|hybrid)(?!{_SEP}(?:search|cloud|retrieval|rag|app|apps|"
    rf"model|models|approach|architecture|busqueda|nube|recuperacion|arquitectura))|"
    rf"semi{_SEP}?presencial|parcialmente{_SEP}remot\w*|"
    rf"(?:partially|partly){_SEP}remote|teletrabajo{_SEP}parcial|"
    r"d(?:ias|ays)\b[^.;\n]{0,25}\b(?:oficina|office))\b"
)
_OFFICE: Final[re.Pattern[str]] = re.compile(
    rf"\b(?:presencial(?:es|idad|ment)?|on{_SEP}?site|in{_SEP}?office|"
    rf"(?:in|at){_SEP}the{_SEP}office|en{_SEP}(?:la{_SEP})?oficina|"
    rf"office{_SEP}based|on{_SEP}premises?)\b"
)
# Wording that makes "presencial" something else than the work mode.
_NOT_MODE_CTX: Final[re.Pattern[str]] = re.compile(
    r"\b(?:entrevist\w*|formacion\w*|curso\w*|reunion\w*|jornada\w*|evento\w*|"
    r"taller\w*|onboarding|kick[\s-]*off|sesion\w*|prueba\w*|interview\w*|"
    r"training|meeting\w*)\b"
)
_VISIT: Final[re.Pattern[str]] = re.compile(
    r"\b(?:visitas?|visits?|desplazamientos?|viajes?|travel(?:l?ing)?|trips?|"
    r"mensual\w*|monthly|trimestral\w*|quarterly|puntual\w*|ocasional\w*|"
    r"occasional\w*|periodic\w*|eventual\w*|reuniones\s+presenciales|"
    r"(?:una|1|2|dos|\d)\s+(?:vez|veces|dia|dias|day|days|time|times)\s+"
    r"(?:al|por|a\s+la|cada|per|a|each)\s+(?:mes|semana|trimestre|month|week|quarter))\b"
)
_PLACE_IGNORED: Final[re.Pattern[str]] = re.compile(
    r"\b(?:fundad\w*|founded|cliente\w*|client\w*|matriz|filial\w*|subsidiar\w*|"
    r"parent\s+company|presencia\s+en|(?:con|with|tenemos|we\s+have)\s+oficinas?\s+"
    r"(?:en|in)|grupo|partners?)\b"
)
_PLACE_HQ: Final[re.Pattern[str]] = re.compile(
    r"\b(?:sede|headquarter\w*|hq|oficinas\s+centrales|head\s+office)\b"
)
_PLACE_CUE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:ubicaci\w*|ubicad[oa]\w*|lugar\s+de\s+trabajo|localizaci\w*|location|"
    r"based\s+in|located\s+in|work\s+location|centro\s+de\s+trabajo|oficinas?|"
    r"office|zona|ciudad|provincia|poblacion|lugar|incorporacion)\b"
)
_COUNTRY_ONLY: Final[re.Pattern[str]] = re.compile(
    r"\b(?:espana|spain|espanya|europe|europa)\b"
)
_CONNECTORS: Final[frozenset[str]] = frozenset(
    {"o", "or", "y", "and", "i", "e", "u", "ou"}
)
_BACK: Final[int] = 45
_SENTENCE: Final[re.Pattern[str]] = re.compile(r"[^.;!?\n]+")


def _non_negated(
    pattern: re.Pattern[str], folded: str, *, skip_ctx: bool = False
) -> list[re.Match[str]]:
    """Matches of ``pattern`` not directly negated ("no remoto", "sin
    presencialidad") nor, with ``skip_ctx``, part of an interview/training
    mention ("entrevista presencial")."""
    found: list[re.Match[str]] = []
    for m in pattern.finditer(folded):
        window = folded[max(0, m.start() - 30) : m.start()]
        if _NEG_BEFORE.search(window):
            continue
        if skip_ctx:
            seg_start = max((window.rfind(c) for c in ".;!?\n"), default=-1)
            if _NOT_MODE_CTX.search(window[seg_start + 1 :]):
                continue
        found.append(m)
    return found


def _spans(folded: str) -> list[tuple[int, int]]:
    return [m.span() for m in _SENTENCE.finditer(folded)]


def _span_of(segments: list[tuple[int, int]], pos: int) -> tuple[int, int]:
    return next((s for s in segments if s[0] <= pos < s[1]), (pos, pos))


class _Place:
    """A place mention: its span in the blob, display text and strength."""

    __slots__ = ("hit", "kind", "shown", "span", "structured")

    def __init__(
        self,
        hit: PlaceHit,
        span: tuple[int, int],
        shown: str,
        kind: str,
        structured: bool,
    ) -> None:
        self.hit = hit
        self.span = span
        self.shown = shown
        self.kind = kind  # "strong" | "weak"
        self.structured = structured


def _adjacent(gap: str) -> bool:
    words = re.findall(r"[a-z]+", gap)
    return len(gap) <= 25 and all(w in _CONNECTORS for w in words)


def _places(
    blob: str,
    folded: str,
    text_len: int,
    location: str | None,
    mode_segments: list[tuple[int, int]],
    region: Region,
) -> list[_Place]:
    """Place mentions from the structured location (always strong) and the
    offer text (strong only inside a work-mode/location sentence; company,
    client and founding mentions are dropped, headquarters are weak)."""
    places: list[_Place] = []
    offset = text_len + 1
    if location:
        for hit in find_places(fold_place(location), structured=True, region=region):
            span = (offset + hit.start, offset + hit.end)
            places.append(_Place(hit, span, blob[span[0] : span[1]], "strong", True))
    segments = _spans(folded)
    for hit in find_places(fold_place(blob[:text_len]), region=region):
        seg = _span_of(segments, hit.start)
        before = folded[max(seg[0], hit.start - _BACK) : hit.start]
        if _PLACE_IGNORED.search(before):
            continue
        kind = "weak"
        if _PLACE_HQ.search(before):
            kind = "weak"
        elif seg in mode_segments or _PLACE_CUE.search(folded[seg[0] : seg[1]]):
            kind = "strong"
        places.append(
            _Place(hit, (hit.start, hit.end), blob[hit.start : hit.end], kind, False)
        )
    return places


def _shown(places: list[_Place]) -> str:
    names: list[str] = []
    for p in places:
        label = re.sub(r"\s+", " ", p.shown).strip()
        if label.lower() not in {n.lower() for n in names}:
            names.append(label)
    return ", ".join(names[:3])


def _result(
    verdict: Verdict, reason: str, blob: str, span: tuple[int, int] | None
) -> GateResult:
    evidence = _snippet(blob, *span) if span else ""
    return GateResult(
        gate="location", verdict=verdict, reason=reason, evidence=evidence
    )


@dataclass(frozen=True)
class _Mode:
    """The work-mode reading of an offer (shared by the gate and the cards)."""

    mode: WorkMode
    blob: str
    folded: str
    office: list[re.Match[str]]
    mode_tokens: list[re.Match[str]]


def _read_mode(text: str, location: str | None) -> _Mode:
    blob = text if location is None else f"{text}\n{location}"
    folded = _fold(blob)
    remote = _non_negated(_REMOTE, folded)
    kept = {m.span() for m in remote}
    negated_remote = [m for m in _REMOTE.finditer(folded) if m.span() not in kept]
    # A plain remote mention among benefits is a perk (hybrid), not the mode.
    perk_remote = [
        m
        for m in remote
        if _PERKS.search(clause := _clause(folded, *m.span()))
        and not _STRONG_REMOTE.search(clause)
        and not _MODE_WORDS.search(clause)
    ]
    remote = [m for m in remote if m not in perk_remote]
    hybrid = _non_negated(_HYBRID, folded) + perk_remote
    office = _non_negated(_OFFICE, folded, skip_ctx=True)
    strong_remote = bool(_non_negated(_STRONG_REMOTE, folded))
    mode_tokens = [
        *hybrid,
        *office,
        *negated_remote,
    ]
    mode: WorkMode
    if remote and not (hybrid or office):
        mode = "remote"
    elif remote and strong_remote:
        # "100% remoto (o híbrido)": an explicit remote option always wins.
        mode = "remote"
    elif remote:
        # Remote next to hybrid/office wording: unclear, so FLAG at most.
        mode = "mixed"
    elif hybrid:
        mode = "hybrid"
    elif office or negated_remote:
        mode = "onsite"
    else:
        mode = "unknown"
    return _Mode(mode, blob, folded, office, mode_tokens)


def work_mode(text: str, location: str | None = None) -> WorkMode:
    """The offer's work mode as the location gate reads it (a remote mention
    among benefits is a perk, so the mode is hybrid; no wording: unknown)."""
    location = location.strip() if location and location.strip() else None
    return _read_mode(text, location).mode


def location_gate(
    text: str, location: str | None = None, prefs: GatePrefs = DEFAULT_GATE_PREFS
) -> GateResult:
    """On-site/hybrid outside the home region (Catalonia by default) → FAIL;
    remote or any place inside it → PASS; mixed or unclear signals → FLAG.

    ``location`` is the portal's structured location field (authoritative:
    its places are always strong evidence); the offer ``text`` supplies the
    work-mode wording and, as a fallback, places mentioned as the work place.
    With a generic home (``prefs.city``/``country`` outside the home region)
    the same rules run over that city or country, and never FAIL.
    """
    home = get_region(prefs.home_region)
    generic = generic_region(prefs.city, prefs.country, home)
    region = home if generic is None else generic
    location = location.strip() if location and location.strip() else None
    read = _read_mode(text, location)
    mode, blob, folded = read.mode, read.blob, read.folded
    office, mode_tokens = read.office, read.mode_tokens
    segments = _spans(folded)
    mode_segments = [_span_of(segments, m.start()) for m in mode_tokens]
    places = _places(blob, folded, len(text), location, mode_segments, region)
    inside = [p for p in places if p.hit.inside]
    outside = [p for p in places if not p.hit.inside]
    strong_out = [p for p in outside if p.kind == "strong"]

    if mode == "remote":
        # Remote: only visits/presence outside the home region are worth a FLAG.
        for seg in segments:
            if not (
                _VISIT.search(folded, *seg)
                or any(seg[0] <= m.start() < seg[1] for m in office)
            ):
                continue
            in_seg = [
                p
                for p in strong_out + [q for q in outside if q.kind == "weak"]
                if seg[0] <= p.span[0] < seg[1] and not p.structured
            ]
            if in_seg and not any(seg[0] <= p.span[0] < seg[1] for p in inside):
                return _result(
                    "FLAG",
                    _reason(
                        "remote_visits", region=region.label, places=_shown(in_seg)
                    ),
                    blob,
                    in_seg[0].span,
                )
        return GateResult(gate="location", verdict="PASS")

    label = _reason(f"mode.{mode}")
    if inside:
        if strong_out:
            gaps_ok = any(
                _adjacent(
                    fold_place(blob)[
                        min(i.span[1], o.span[1]) : max(i.span[0], o.span[0])
                    ]
                )
                for i in inside
                for o in strong_out
                if i.span[1] <= o.span[0] or o.span[1] <= i.span[0]
            )
            if not gaps_ok:
                return _result(
                    "FLAG",
                    _reason(
                        "several_places",
                        region=region.label,
                        places=_shown(strong_out),
                    ),
                    blob,
                    strong_out[0].span,
                )
        return GateResult(gate="location", verdict="PASS")
    if strong_out:
        structured_out = any(p.structured for p in strong_out)
        if generic is None and (
            mode in ("onsite", "hybrid") or (mode == "unknown" and structured_out)
        ):
            return _result(
                "FAIL",
                _reason(
                    "mode_outside_fail",
                    mode=label,
                    region=region.label,
                    places=_shown(strong_out),
                ),
                blob,
                strong_out[0].span,
            )
        return _result(
            "FLAG",
            _reason(
                "mode_outside_flag",
                mode=label,
                region=region.label,
                places=_shown(strong_out),
            ),
            blob,
            strong_out[0].span,
        )
    if outside:
        return _result(
            "FLAG",
            _reason("mentions_outside", places=_shown(outside), region=region.label),
            blob,
            outside[0].span,
        )
    if mode in ("onsite", "hybrid", "mixed"):
        country = _COUNTRY_ONLY.search(folded)
        return _result(
            "FLAG",
            _reason("no_place", mode=label),
            blob,
            country.span()
            if country
            else (mode_tokens[0].span() if mode_tokens else None),
        )
    return GateResult(gate="location", verdict="PASS")


def run_gates(
    text: str, location: str | None = None, prefs: GatePrefs = DEFAULT_GATE_PREFS
) -> list[GateResult]:
    """All gates, in fixed order (eligibility, language, location)."""
    return [
        eligibility_gate(text),
        language_gate(text, prefs),
        location_gate(text, location, prefs),
    ]


# --- display translation of the stored reasons -------------------------------

_REASON_KEYS: Final[tuple[str, ...]] = (
    "eligibility_eu_wording",
    "eligibility_fail",
    "eligibility_negated",
    "clearance",
    "english_above_c1",
    "language_fail",
    "language_flag",
    "remote_visits",
    "several_places",
    "mentions_outside",
    "no_place",
    "mode_outside_flag",
    "mode_outside_fail",
)


def _reverse(prefix: str) -> dict[str, str]:
    """Spanish text -> catalog key suffix for the ``panel.gate.<prefix>.*`` keys."""
    start = f"panel.gate.{prefix}."
    return {
        text: key[len(start) :]
        for key, text in CATALOGS[_STORED_LANG].items()
        if key.startswith(start)
    }


@cache
def _reason_patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    patterns = []
    for key in _REASON_KEYS:
        template = CATALOGS[_STORED_LANG][f"panel.gate.{key}"]
        parts = []
        for literal, field, _, _ in Formatter().parse(template):
            parts.append(re.escape(literal))
            if field:
                parts.append(f"(?P<{field}>.+?)")
        patterns.append((key, re.compile("^" + "".join(parts) + "$", re.DOTALL)))
    return tuple(patterns)


def gate_reason(reason: str, lang: Lang) -> str:
    """The stored (Spanish) gate/wall ``reason`` shown in ``lang``.

    Stored reasons stay Spanish; this is display only. Text that is not a gate
    reason (a stack wall, a judge rationale) is returned unchanged.
    """
    if lang == _STORED_LANG or not reason:
        return reason
    languages, modes, regions = _reverse("lang"), _reverse("mode"), _reverse("region")
    for key, pattern in _reason_patterns():
        found = pattern.match(reason)
        if found is None:
            continue
        fmt = found.groupdict()
        if "mode" in fmt:
            if fmt["mode"] not in modes:
                continue
            fmt["mode"] = t(f"panel.gate.mode.{modes[fmt['mode']]}", lang)
        if "region" in fmt and fmt["region"] in regions:
            fmt["region"] = t(f"panel.gate.region.{regions[fmt['region']]}", lang)
        if "labels" in fmt:
            names = re.split(r", | y ", fmt["labels"])
            fmt["labels"] = join_list(
                [
                    t(f"panel.gate.lang.{languages[n]}", lang) if n in languages else n
                    for n in names
                ],
                lang,
            )
        return t(f"panel.gate.{key}", lang, **fmt)
    return reason
