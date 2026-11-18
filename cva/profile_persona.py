"""The candidate-specific prompt text of a profile (profile-extraction E4).

``<profile_dir>/judge/`` holds the judge's persona, one plain file each:

- ``facts.md``: the candidate-facts paragraph ("Candidate timeline ...").
- ``anchors.md``: the company-named calibration anchors, one ``- ...`` line
  each (may be empty; the generic anchors stay in the engine).
- ``rubric_persona.md``: the persona-bound parts of the rubric, one
  ``## <slot>`` section per slot (all of :data:`RUBRIC_SLOTS`, none empty). Text
  before the first heading is ignored, so the file can start with notes.

Unlike ``preferences.toml`` the files are NOT optional once a ``profile_dir`` is
configured: their defaults describe the original owner, so a missing file fails
loudly instead of silently mixing two personas (E1's rule for persona files).
With no ``profile_dir`` the owner's text (``cva/match/persona_defaults.py``)
is used. The cover-letter persona is in ``preferences.toml`` ``[cover_letter]``.

Content rules (enforced when loading and when rendering): ``facts.md`` and every
rubric slot must be non-empty (``anchors.md`` alone may be empty), and a rubric
slot body must NOT contain a line starting with ``## `` because that marks the
next slot; use ``###`` or deeper for sub-headings inside a slot.
"""

import dataclasses
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from cva.match.persona import JudgePersona, RubricPersona
from cva.match.persona_defaults import OWNER_JUDGE_PERSONA
from cva.profile_identity import ProfileError

RUBRIC_SLOTS: Final[tuple[str, ...]] = tuple(
    field.name for field in dataclasses.fields(RubricPersona)
)
JUDGE_FILES: Final[tuple[str, ...]] = ("facts.md", "anchors.md", "rubric_persona.md")
_HEADING: Final[re.Pattern[str]] = re.compile(r"^## (.*?)\s*$")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ProfileError(f"judge persona file unreadable ({path}): {exc}") from exc


def parse_rubric_persona(text: str, where: str = "rubric_persona.md") -> RubricPersona:
    """The slots of a ``rubric_persona.md`` text; strict about the slot set."""
    slots: dict[str, list[str]] = {}
    rule = "slot bodies must not contain '## ' headings (use '###' for sub-headings)"
    current: list[str] | None = None
    for line in text.splitlines():
        heading = _HEADING.match(line)
        if heading is not None:
            name = heading.group(1)
            if name not in RUBRIC_SLOTS:
                raise ProfileError(
                    f"{where}: unknown slot {name!r} (slots: "
                    f"{', '.join(RUBRIC_SLOTS)}); {rule}"
                )
            if name in slots:
                raise ProfileError(f"{where}: slot {name!r} appears twice; {rule}")
            current = slots[name] = []
        elif current is not None:
            current.append(line)
    values = {name: "\n".join(lines).strip() for name, lines in slots.items()}
    problems = [
        f"{name} is missing" if name not in values else f"{name} is empty"
        for name in RUBRIC_SLOTS
        if not values.get(name)
    ]
    if problems:
        raise ProfileError(f"{where}: {'; '.join(problems)}")
    return RubricPersona(**values)


def load_judge_persona(judge_dir: Path | None) -> JudgePersona:
    """The persona in ``judge_dir``; the owner's text when it is ``None``."""
    if judge_dir is None:
        return OWNER_JUDGE_PERSONA
    missing = [name for name in JUDGE_FILES if not (judge_dir / name).is_file()]
    if missing:
        raise ProfileError(
            f"judge persona file(s) missing in {judge_dir}: {', '.join(missing)}. "
            "A configured profile must define its own judge persona (the defaults "
            "are the original owner's text, never mixed in). Create them with "
            "cva.profile_persona.render_judge_persona_files(persona), which "
            "returns the three file contents (OWNER_JUDGE_PERSONA from "
            "cva.match.persona_defaults is a starting point to edit)."
        )
    facts = _read(judge_dir / "facts.md")
    if not facts:
        raise ProfileError(f"{judge_dir / 'facts.md'}: facts is empty")
    return JudgePersona(
        facts=facts,
        named_anchors=_read(judge_dir / "anchors.md"),
        rubric=parse_rubric_persona(
            _read(judge_dir / "rubric_persona.md"), str(judge_dir / "rubric_persona.md")
        ),
    )


def render_judge_persona_files(persona: JudgePersona) -> dict[str, str]:
    """``persona`` as the file contents of a ``judge/`` directory (name -> text).

    The inverse of :func:`load_judge_persona`; used to write the owner's text
    into a private profile and by the tests.
    """
    if not persona.facts.strip():
        raise ProfileError("persona facts is empty: facts.md must not be empty")
    for slot in RUBRIC_SLOTS:
        body = getattr(persona.rubric, slot)
        if any(line.startswith("## ") for line in body.splitlines()):
            raise ProfileError(
                f"rubric slot {slot!r} contains a '## ' heading, which would be "
                "read back as another slot (use '###' for sub-headings)"
            )
    rubric = "\n".join(
        f"## {slot}\n{getattr(persona.rubric, slot)}\n" for slot in RUBRIC_SLOTS
    )
    return {
        "facts.md": persona.facts + "\n",
        "anchors.md": persona.named_anchors + "\n",
        "rubric_persona.md": rubric,
    }


# --- base cover letter (cover-letter-v2) --------------------------------------

LETTER_LANGUAGES: Final[tuple[str, ...]] = ("es", "en", "ca")
_LETTER_REQUIRED: Final[tuple[str, ...]] = (
    "salutation",
    "salutation_generic",
    "paragraph1",
    "closing",
    "signoff",
    "open_salutation",
    "open_paragraph",
)
_LETTER_OPTIONAL: Final[tuple[str, ...]] = (
    "salutation_formal",
    "salutation_formal_generic",
    "closing_formal",
)


@dataclass(frozen=True)
class LetterBase:
    """The fixed text of the cover letter in one language.

    ``salutation*`` are templates with ``{company}`` (the name) and
    ``{company_de}`` (``"de X"``; Catalan ``"d'X"`` before a vowel). The
    ``*_formal`` variants serve clearly corporate companies and default to
    the plain text. ``open_salutation`` and ``open_paragraph`` build the open
    letter (``/cartabase``): no company, no generated text, a fixed middle
    paragraph in place of the per-offer paragraphs 2 and 3.
    """

    salutation: str
    salutation_generic: str
    paragraph1: str
    closing: str
    signoff: str
    open_salutation: str
    open_paragraph: str
    salutation_formal: str = ""
    salutation_formal_generic: str = ""
    closing_formal: str = ""

    def pick(self, plain: str, formal: str, register: str) -> str:
        return formal if register == "formal" and formal else plain


def load_letter_bases(path: Path) -> dict[str, LetterBase]:
    """The per-language base letters of ``cover_letter_base.toml``.

    Like the judge persona, the file is not optional: its defaults would
    describe another person, so a missing file or language fails loudly.
    """
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProfileError(f"base cover letter unreadable ({path}): {exc}") from exc
    unknown = sorted(set(data) - set(LETTER_LANGUAGES))
    if unknown:
        raise ProfileError(f"{path}: unknown language table(s) {', '.join(unknown)}")
    bases: dict[str, LetterBase] = {}
    for language in LETTER_LANGUAGES:
        table = data.get(language)
        if not isinstance(table, dict):
            raise ProfileError(f"{path}: missing [{language}] table")
        extra = sorted(set(table) - set(_LETTER_REQUIRED) - set(_LETTER_OPTIONAL))
        if extra:
            raise ProfileError(
                f"{path} [{language}]: unknown key(s) {', '.join(extra)}"
            )
        values: dict[str, str] = {}
        for key in (*_LETTER_REQUIRED, *_LETTER_OPTIONAL):
            raw = table.get(key, "")
            if not isinstance(raw, str):
                raise ProfileError(f"{path} [{language}] {key} must be a string")
            values[key] = raw.strip()
        missing = [key for key in _LETTER_REQUIRED if not values[key]]
        if missing:
            raise ProfileError(
                f"{path} [{language}]: missing or empty {', '.join(missing)}"
            )
        bases[language] = LetterBase(**values)
    return bases
