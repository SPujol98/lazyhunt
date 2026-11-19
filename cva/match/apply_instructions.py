"""Detect extra application instructions inside an offer's text.

Pure, deterministic and regex-based (no LLM, no I/O). Spanish, English and
Catalan phrasing. Precision is preferred over recall: a skill list that says
"email marketing" or "Git/GitHub" must NOT warn, so every pattern needs an
asking verb / context, not just a keyword.

Matching runs on an accent- and case-folded copy of the text whose length
equals the original's (per-character folding), so match offsets index the
original text directly for snippets and extracted values.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Final, Literal

from cva.i18n import LEGACY_LANG, Lang, t

Kind = Literal[
    "email",
    "phone",
    "external_apply",
    "cover_letter",
    "salary_expectations",
    "start_date",
    "reference_code",
    "portfolio",
]

MAX_FINDINGS: Final[int] = 6
_SNIPPET_MAX: Final[int] = 120
_CONTEXT: Final[int] = 40

KIND_EMOJI: Final[dict[str, str]] = {
    "email": "📧",
    "phone": "📞",
    "external_apply": "🔗",
    "cover_letter": "✉️",
    "salary_expectations": "💶",
    "start_date": "📅",
    "reference_code": "🔖",
    "portfolio": "🗂️",
}


@dataclass(frozen=True)
class ApplyInstruction:
    """One extra application step found in the offer text."""

    kind: str
    label: str  # short explanation for the owner, in the UI language
    snippet: str  # verbatim excerpt, whitespace-collapsed, <= ~120 chars
    value: str | None = None  # extracted email / phone / reference


def _fold(text: str) -> str:
    """Lowercase + strip accents, one output char per input char."""
    out: list[str] = []
    for char in text:
        base = unicodedata.normalize("NFD", char)[0]
        lowered = base.lower()
        out.append(lowered if len(lowered) == 1 else base)
    return "".join(out)


def _snippet(text: str, start: int, end: int) -> str:
    """Verbatim window around a match, whitespace-collapsed, ellipsis-marked."""
    lo = max(0, start - _CONTEXT)
    hi = min(len(text), end + _CONTEXT)
    # Snap inward to word boundaries, never past the match itself.
    while 0 < lo < start and not text[lo - 1].isspace():
        lo += 1
    while end < hi < len(text) and not text[hi].isspace():
        hi -= 1
    piece = " ".join(text[lo:hi].split())
    truncated = len(piece) > _SNIPPET_MAX
    if truncated:
        piece = piece[:_SNIPPET_MAX].rstrip()
    prefix = "…" if lo > 0 else ""
    suffix = "…" if truncated or hi < len(text) else ""
    return f"{prefix}{piece}{suffix}"


# --- email ------------------------------------------------------------------
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Addresses that are never an application channel: data-protection / legal /
# automated mailboxes typically found in RGPD footers.
_EMAIL_IGNORED_LOCALS: Final[frozenset[str]] = frozenset(
    {
        "privacy",
        "privacidad",
        "rgpd",
        "gdpr",
        "lopd",
        "dpo",
        "dpd",
        "noreply",
        "no-reply",
        "donotreply",
        "do-not-reply",
        "unsubscribe",
        "legal",
        "abuse",
        "protecciodades",
        "protecciondedatos",
        "datos",
    }
)
_DATA_PROTECTION_RE = re.compile(
    r"proteccio[n]? de datos|rgpd|gdpr|lopd|privacy|privacidad|ejercer.{0,30}derechos"
)
_CV_VERB_RE = re.compile(
    # No bare "mail"/"contact": "Email: x@y" or "Contact: x@y" is only a
    # contact address, not an instruction to send the CV there.
    r"remit|envi[ae]|manda|send|escrib|write|reenvi|forward|adjunt|submit"
    r"|cv|curriculum|resume|candidatur"
)

# --- phone ------------------------------------------------------------------
_PHONE_RE = re.compile(
    r"(?<![\w.])(?:\+\d{2}[\s.-]?)?[6-9]\d{2}[\s.-]?\d{3}[\s.-]?\d{3}(?!\d)"
)
_PHONE_CONTEXT_RE = re.compile(
    r"llam|telefon|tel\b|tel\.|whatsapp|movil|phone|call|contact|mobile|\bwa\b"
)

# --- regex catalogue (applied to folded text) -------------------------------
_ATS = (
    r"myworkdayjobs\.com|workday\.com|greenhouse\.io|lever\.co|smartrecruiters\.com"
    r"|teamtailor\.com|personio\.(?:de|com)|recruitee\.com|workable\.com"
    r"|successfactors|taleo|bamboohr"
)
_APPLY_VERB = (
    r"\b(?:aplica|aplicar|aplicad|apply|inscribete|inscribirte|inscribid|postula"
    r"|postulate|solicita|solicitar|candidatate|registrate|registrarte|register"
    r"|submit|presenta|presentar|rellena|completa|fill)\b"
)
_APPLY_TARGET = (
    r"(?:nuestr[ao]s? (?:web|sitio|portal|pagina|formulario)"
    r"|(?:sitio|pagina) web (?:de la empresa|corporativ[ao])|web corporativa"
    r"|our (?:careers?|jobs?)(?: web)?(?: ?site| page| portal)?|our web ?site"
    r"|company(?:'s)? (?:web ?site|site|career)|careers? (?:page|site|portal)"
    r"|application (?:form|portal)|formulario de (?:inscripcion|solicitud|candidatura)"
    r"|portal de (?:empleo|candidat|talento)|infojobs|" + _ATS + r")"
)

# kind -> (label id, pattern); the owner-facing label is ``bot.apply.<id>``.
_PATTERNS: Final[dict[str, tuple[str, re.Pattern[str]]]] = {
    "external_apply": (
        "external_apply",
        re.compile(
            r"gestionadas fuera de linkedin"
            r"|(?:solicitar|aplicar|apply)(?: en| on| at)? (?:el )?(?:sitio web|the company web ?site|company web ?site)(?: de la empresa)?"
            r"|apply directly online"
            r"|"
            + _APPLY_VERB
            + r"[^.!?\n]{0,50}?"
            + _APPLY_TARGET
            + r"|"
            + r"\b(?:"
            + _ATS
            + r")"
        ),
    ),
    "cover_letter": (
        "cover_letter",
        re.compile(
            r"carta de (?:presentacion?|motivacio[n]?)|cover letter"
            r"|motivation(?:al)? letter|letter of motivation|lettre de motivation"
        ),
    ),
    "salary_expectations": (
        "salary_expectations",
        re.compile(
            r"pretensi(?:ones|ons) (?:salariales|economicas)|expectativas? (?:salariales?|economicas?)"
            r"|expectatives salarials|salary (?:expectations?|requirements?)"
            r"|(?:desired|expected) salary|salario deseado|salari esperat"
            r"|banda salarial deseada|remuneracion deseada"
        ),
    ),
    "start_date": (
        "start_date",
        re.compile(
            r"fecha de (?:incorporacion|inicio)"
            r"|disponibilidad (?:de |para )?(?:incorporacion|incorporarte|empezar|comenzar)"
            r"|(?:data d.incorporacio|disponibilitat d.incorporacio)"
            r"|(?:earliest|possible|desired|preferred|your|expected)(?: possible| available)? start(?:ing)? date"
            r"|notice period|periodo de preaviso"
            r"|when (?:can|could|would) you (?:start|join)|cuando podrias incorporarte"
        ),
    ),
    "portfolio": (
        "portfolio",
        re.compile(
            r"\b(?:adjunta|adjunt[ae]s?|incluye|incluir|envia|enviad|comparte|aporta|indica|send"
            r"|include|attach|share|provide|submit|add)\b[^.!?\n]{0,50}?"
            r"\b(?:tu|tus|your|un|una|a|an)\s+(?:\w+\s+)?"
            r"(?:portfolio|portafolio|github|gitlab|repositorios?|repositor(?:y|ies)"
            r"|code samples?|muestras de codigo)\b"
            r"|\b(?:cv|curriculum|resume)\s+(?:y|and|e|&|\+)\s+(?:tu\s+|your\s+)?"
            r"(?:portfolio|portafolio|github)\b"
        ),
    ),
}

_REFERENCE_RE = re.compile(
    r"\b(?:indica|indicando|indicar|incluye|incluyendo|menciona|mencionando|cita|citando"
    r"|include|including|quote|quoting|mention|mentioning|state|stating"
    r"|indiqueu|indicant)\b[^.!?\n]{0,25}?"
    r"\b(?:referencia|reference(?: number| code)?|ref\.|job code|codigo de oferta)"
    # "references" (people vouching for you) is not the offer's reference code.
    r"(?![a-z])"
)
_REFERENCE_VALUE_RE = re.compile(
    r"\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9_./-]*\d[A-Za-z0-9_./-]*)"
)

_MAX_PER_VALUE_KIND: Final[int] = 2

_NEGATION_RE = re.compile(r"(?:\bno |\bsin |\bwithout |\bnot |\bnon[- ])\S*\s*\S*\s*$")


def _label(label_id: str, lang: Lang) -> str:
    """The owner-facing label of a finding (``bot.apply.<id>``) in ``lang``."""
    return t(f"bot.apply.{label_id}", lang)


_ORDER: Final[tuple[str, ...]] = (
    "email",
    "phone",
    "external_apply",
    "cover_letter",
    "salary_expectations",
    "start_date",
    "reference_code",
    "portfolio",
)


# LinkedIn Premium UI chrome ("Create cover letter BETA") survives when the
# owner pastes the whole job page; a "create/crear" right before the match is
# that button, not the offer asking for anything.
_UI_CHROME_RE = re.compile(
    r"\b(?:create|crear|crea|generar|generate)\s+(?:una\s+|a\s+)?$"
)


def _negated(folded: str, start: int) -> bool:
    return bool(_NEGATION_RE.search(folded[max(0, start - 25) : start]))


def _ui_chrome(folded: str, start: int) -> bool:
    return bool(_UI_CHROME_RE.search(folded[max(0, start - 20) : start]))


def _emails(text: str, folded: str, lang: Lang) -> list[ApplyInstruction]:
    found: list[ApplyInstruction] = []
    seen: set[str] = set()
    for m in _EMAIL_RE.finditer(text):
        address = m.group(0).rstrip(".")
        key = address.lower()
        local = key.split("@", 1)[0]
        if key in seen or local in _EMAIL_IGNORED_LOCALS:
            continue
        before = folded[max(0, m.start() - 100) : m.start()]
        if _DATA_PROTECTION_RE.search(before) and not re.search(
            r"remit|envi|send|manda|cv|curriculum|resume", before
        ):
            continue
        seen.add(key)
        label = _label(
            "email_cv" if _CV_VERB_RE.search(before[-60:]) else "email_contact", lang
        )
        found.append(
            ApplyInstruction(
                "email", label, _snippet(text, m.start(), m.end()), address
            )
        )
    return found


def _phones(text: str, folded: str, lang: Lang) -> list[ApplyInstruction]:
    found: list[ApplyInstruction] = []
    seen: set[str] = set()
    for m in _PHONE_RE.finditer(text):
        window = folded[max(0, m.start() - 40) : m.start()]
        if not _PHONE_CONTEXT_RE.search(window):
            continue
        number = re.sub(r"[\s.-]", "", m.group(0))
        if number in seen:
            continue
        seen.add(number)
        found.append(
            ApplyInstruction(
                "phone",
                _label("phone", lang),
                _snippet(text, m.start(), m.end()),
                number,
            )
        )
    return found


def detect_apply_instructions(
    text: str, lang: Lang = LEGACY_LANG
) -> list[ApplyInstruction]:
    """Return the extra application steps the offer asks for (capped, deduped);
    labels in the UI language ``lang``."""
    if not text:
        return []
    folded = _fold(text)
    by_kind: dict[str, list[ApplyInstruction]] = {
        # Per-value kinds are capped so a footer full of addresses cannot
        # push the instruction kinds (start date, cover letter…) past the cap.
        "email": _emails(text, folded, lang)[:_MAX_PER_VALUE_KIND],
        "phone": _phones(text, folded, lang)[:_MAX_PER_VALUE_KIND],
    }
    for kind, (label_id, pattern) in _PATTERNS.items():
        for m in pattern.finditer(folded):
            if _negated(folded, m.start()) or _ui_chrome(folded, m.start()):
                continue
            by_kind[kind] = [
                ApplyInstruction(
                    kind, _label(label_id, lang), _snippet(text, m.start(), m.end())
                )
            ]
            break
    for m in _REFERENCE_RE.finditer(folded):
        if _negated(folded, m.start()) or _ui_chrome(folded, m.start()):
            continue
        value_match = _REFERENCE_VALUE_RE.match(text, m.end())
        by_kind["reference_code"] = [
            ApplyInstruction(
                "reference_code",
                _label("reference_code", lang),
                _snippet(
                    text, m.start(), value_match.end() if value_match else m.end()
                ),
                value_match.group(1).rstrip(".,;") if value_match else None,
            )
        ]
        break
    ordered = [item for kind in _ORDER for item in by_kind.get(kind, [])]
    return ordered[:MAX_FINDINGS]
