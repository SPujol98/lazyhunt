"""The persona text the prompts take from a profile (profile-extraction E4).

Plain data only. The original owner's text is in ``persona_defaults.py``; a
profile's own text is read by ``cva/profile_persona.py`` (judge files) and
``cva/profile_prefs.py`` (``[cover_letter]``).
"""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class RubricPersona:
    """The persona-bound parts of the judge rubric, each at its own position.

    Every slot is plain text without surrounding blank lines; the engine
    supplies the generic parts (score definition, truth constraint, verdict)
    and puts the slots between them (``cva/match/judge.py`` ``_RUBRIC``).
    """

    # Clause of the opening sentence: "... evaluating ONE job offer <this>: ...".
    manual_analyses: str
    experience_rule: str
    degree_rule: str
    lacking_core_rule: str
    hard_walls: str
    soft_walls: str
    positive_signals: str


@dataclass(frozen=True)
class JudgePersona:
    """Candidate facts, named calibration anchors and rubric persona text."""

    facts: str
    # Company-named calibration anchors, one "- ..." line each; the generic
    # anchors stay in the engine. May be empty.
    named_anchors: str
    rubric: RubricPersona


@dataclass(frozen=True)
class CoverLetterPersona:
    """Cover-letter text that depends on who the candidate is."""

    # "AI" / "BACKEND" -> the sentence appended after "Branch emphasis: ".
    branch_emphasis: Mapping[str, str]
    # Rule 7 of the system prompt, without its "7. " prefix.
    career_change: str
