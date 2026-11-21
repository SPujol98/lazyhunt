"""Minimal working profile from the ``/setup`` answers (onboarding-v2 T7, S7/S8).

Driven through the public function :func:`generate_minimal_profile` (and the
``CONFIRM_HOOKS`` adapter :func:`confirm_hook`), after the answers were written
by T4's writer (:func:`cva.onboarding.answers.write_answers`), exactly as the
Confirm button does.
"""

import hashlib
import json
import shutil
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from cva.claimsdb import load_claims
from cva.config import Settings
from cva.i18n import Lang, t
from cva.onboarding.answers import write_answers
from cva.onboarding.profile_gen import (
    GENERATED_FILES,
    GENERATED_MARKER,
    confirm_hook,
    generate_minimal_profile,
)
from cva.profile_identity import ProfileError, load_identity
from cva.profile_persona import RUBRIC_SLOTS, load_judge_persona
from cva.service import JobService
from cva.setup_mode import REQUIRED_PROFILE_FILES, missing_profile_files, needs_setup
from tests.portability import posix_modes

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_PROFILE = REPO_ROOT / "profiles" / "demo"


def _answers(**overrides: Any) -> dict[str, Any]:
    answers: dict[str, Any] = {
        "name": "Jane Roe",
        "email": "jane.roe@example.com",
        "phone": "+34 600 111 222",
        "city": "Valencia",
        "linkedin": "https://linkedin.com/in/jane-roe-example",
        "families": ["ai"],
        "custom_titles": ["Data Analyst", "BI Developer"],
        "level": "junior",
        "work_modes": ["remote", "hybrid"],
        "work_city": "Valencia",
        "country": "Spain",
        "min_salary": 28000,
        "currency": "EUR",
        "offer_langs": ["es", "en"],
        "portals": [],
    }
    answers.update(overrides)
    return answers


def _confirmed(
    tmp_path: Path, lang: Lang = "en", **overrides: Any
) -> tuple[Path, dict[str, Any]]:
    """An empty profile after Confirm: T4's files written, nothing else."""
    profile = tmp_path / "profiles" / "me"
    profile.mkdir(parents=True)
    answers = _answers(**overrides)
    write_answers(profile, answers, lang)
    return profile, answers


def _tree_digest(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# --- end to end: empty profile -> working bot -----------------------------------


@pytest.mark.parametrize("lang", ["en", "es"])
def test_empty_profile_leaves_setup_mode_and_the_service_loads(
    tmp_path: Path,
    lang: Lang,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, answers = _confirmed(tmp_path, lang)
    settings = clean_settings(profile_dir=str(profile), ui_language=lang)
    assert needs_setup(settings) is True

    result = generate_minimal_profile(profile, answers, lang)

    assert set(result.created) == set(REQUIRED_PROFILE_FILES) == set(GENERATED_FILES)
    assert result.skipped == ()
    assert missing_profile_files(profile) == ()
    assert needs_setup(settings) is False
    monkeypatch.chdir(tmp_path)
    service = JobService.from_settings(settings, db_path=tmp_path / "app.db")
    assert service.loaded_preferences.search.level == "junior"
    # The identity claim names the user: letters are signed with the stated name.
    claims = load_claims(profile / "claims.json")
    identity = load_identity(profile / "profile.toml")
    assert claims.by_id[identity.identity_claim_id].text.startswith("Jane Roe")


@posix_modes
def test_claims_file_is_private(tmp_path: Path) -> None:
    profile, answers = _confirmed(tmp_path)
    generate_minimal_profile(profile, answers, "en")
    mode = stat.S_IMODE((profile / "claims.json").stat().st_mode)
    assert mode == 0o600


# --- nothing the user did not state reaches claims -----------------------------


def _stated_values(answers: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for value in answers.values():
        items = value if isinstance(value, list) else [value]
        values.update(str(item).casefold() for item in items if item is not None)
    return values


# T8 (S9): the background questions of /setup.
_BACKGROUND: dict[str, Any] = {
    "spoken_langs": ["es", "en"],
    "spoken_other": ["Basque"],
    "spoken_levels": {"es": "native", "en": "C1", "Basque": "B1", "fr": "A2"},
    "skills": ["Python", "C++", "Node.js"],
    "exp_years": "3-5",
    "last_role": "Backend Developer",
    "last_company": "Acme",
    "education": "BSc Computer Science, UPV",
}


def _stated_language(text: str, answers: dict[str, Any]) -> bool:
    """``<Language> — <level>``: a chosen language (its catalogue name when it
    is a common one) at the level the user gave it."""
    name, _, level = text.partition(" — ")
    levels = {"native": "Native"}
    for key, given in answers["spoken_levels"].items():
        if key not in [*answers["spoken_langs"], *answers["spoken_other"]]:
            continue
        label = t(f"onboarding.olang.{key}", "en") if len(key) == 2 else key
        if (name, level) == (label, levels.get(given, given)):
            return True
    return False


@pytest.mark.parametrize("background", [False, True])
@pytest.mark.parametrize("phone", ["+34 600 111 222", None])
def test_every_claim_text_and_tag_comes_from_an_answer(
    tmp_path: Path, phone: str | None, background: bool
) -> None:
    extra = _BACKGROUND if background else {}
    profile, answers = _confirmed(tmp_path, phone=phone, **extra)
    generate_minimal_profile(profile, answers, "en")
    stated = _stated_values(answers)
    claims = load_claims(profile / "claims.json").claims
    assert claims, "the identity claim is always generated"
    for claim in claims:
        if claim.category == "language":  # tagged with the language's name
            assert _stated_language(claim.text, answers), claim.text
            assert claim.tags == [claim.text.partition(" — ")[0].casefold()]
            continue
        for tag in claim.tags:
            assert tag.casefold() in stated, (claim.id, tag)
        if claim.category in ("skill", "education"):
            assert claim.text.casefold() in stated, claim.text
            continue
        if claim.category == "experience":
            assert claim.text == (
                "3-5 years of professional experience"
                " · Last role: Backend Developer at Acme"
            )
            continue
        assert claim.category == "identity"
        for field in claim.text.split(" · "):
            assert field.casefold() in stated, (claim.id, field)
    ids = [c.id for c in claims if not c.id.startswith("setup-")]
    assert ids == (
        ["identity-contact", "identity-phone"] if phone else ["identity-contact"]
    )
    assert any(c.id.startswith("setup-") for c in claims) is background


def test_identity_claim_follows_the_profile_identity_claim_id(tmp_path: Path) -> None:
    profile, answers = _confirmed(tmp_path)
    toml = profile / "profile.toml"
    toml.write_text(
        toml.read_text("utf-8") + 'identity_claim_id = "me-contact"\n', "utf-8"
    )
    generate_minimal_profile(profile, answers, "en")
    ids = {c.id for c in load_claims(profile / "claims.json").claims}
    assert ids == {"me-contact", "me-phone"}


def test_spoken_languages_skills_and_experience_are_reported_as_gaps(
    tmp_path: Path,
) -> None:
    profile, answers = _confirmed(tmp_path)
    result = generate_minimal_profile(profile, answers, "en")
    categories = {c.category for c in load_claims(profile / "claims.json").claims}
    assert categories == {"identity"}  # offer languages are not spoken languages
    for gap in ("spoken_languages", "skills", "experience", "education"):
        assert gap in result.gaps
    for feature in ("cover_letter_base.toml", "voice.md", "salary_reference.toml"):
        assert feature in result.unavailable


# --- judge files: templates filled with stated facts only ----------------------


@pytest.mark.parametrize("lang", ["en", "es"])
@pytest.mark.parametrize(
    ("families", "titles"),
    [
        (["ai"], []),
        (["backend"], []),
        ([], ["Data Analyst"]),
        (["ai", "backend"], ["QA"]),
    ],
)
def test_judge_files_load_with_every_rubric_slot_filled(
    tmp_path: Path, lang: Lang, families: list[str], titles: list[str]
) -> None:
    profile, answers = _confirmed(
        tmp_path, lang, families=families, custom_titles=titles
    )
    generate_minimal_profile(profile, answers, lang)
    persona = load_judge_persona(profile / "judge")
    for slot in RUBRIC_SLOTS:
        assert getattr(persona.rubric, slot).strip(), slot
    for name in ("facts.md", "anchors.md", "rubric_persona.md"):
        assert GENERATED_MARKER[lang] in (profile / "judge" / name).read_text("utf-8")
    for title in titles:
        assert title in persona.facts


def test_facts_hold_the_stated_preferences(tmp_path: Path) -> None:
    profile, answers = _confirmed(tmp_path)
    generate_minimal_profile(profile, answers, "en")
    facts = (profile / "judge" / "facts.md").read_text("utf-8")
    for value in ("AI", "Data Analyst", "BI Developer", "junior", "remote", "hybrid"):
        assert value in facts
    assert "Valencia, Spain" in facts
    assert "28000 EUR" in facts


def test_facts_in_spanish_and_without_salary_when_skipped(tmp_path: Path) -> None:
    profile, answers = _confirmed(tmp_path, "es", min_salary=None, currency=None)
    generate_minimal_profile(profile, answers, "es")
    facts = (profile / "judge" / "facts.md").read_text("utf-8")
    assert "Nivel: junior" in facts
    assert "remoto" in facts and "híbrido" in facts
    assert "EUR" not in facts and "salario" not in facts.casefold()


# --- never overwrite; roll back on failure ------------------------------------


def test_existing_files_are_never_overwritten(tmp_path: Path) -> None:
    profile, answers = _confirmed(tmp_path)
    shutil.copy(DEMO_PROFILE / "claims.json", profile / "claims.json")
    (profile / "judge").mkdir()
    shutil.copy(DEMO_PROFILE / "judge" / "facts.md", profile / "judge" / "facts.md")
    before = _tree_digest(profile)

    result = generate_minimal_profile(profile, answers, "en")

    assert set(result.skipped) == {"claims.json", "judge/facts.md"}
    assert set(result.created) == {
        "synonyms.json",
        "judge/anchors.md",
        "judge/rubric_persona.md",
    }
    after = _tree_digest(profile)
    for rel in ("claims.json", "judge/facts.md"):
        assert after[rel] == before[rel]


def test_demo_profile_is_untouched(tmp_path: Path) -> None:
    repo_demo_before = _tree_digest(DEMO_PROFILE)
    profile = tmp_path / "demo"
    shutil.copytree(DEMO_PROFILE, profile)
    before = _tree_digest(profile)

    result = generate_minimal_profile(profile, _answers(), "en")

    assert result.created == ()
    assert set(result.skipped) == set(GENERATED_FILES)
    assert _tree_digest(profile) == before
    assert _tree_digest(DEMO_PROFILE) == repo_demo_before


def test_failed_validation_removes_only_the_files_it_created(tmp_path: Path) -> None:
    profile, answers = _confirmed(tmp_path)
    # A user's claims.json without the identity claim profile.toml names.
    (profile / "claims.json").write_text(
        json.dumps([{"id": "x", "text": "X", "category": "skill", "tags": []}]),
        "utf-8",
    )
    before = _tree_digest(profile)

    with pytest.raises(ProfileError, match="identity_claim_id"):
        generate_minimal_profile(profile, answers, "en")

    assert _tree_digest(profile) == before
    assert not (profile / "judge").exists()


# --- the Confirm hook -----------------------------------------------------------


def test_confirm_hook_uses_the_language_of_the_saved_answers(tmp_path: Path) -> None:
    profile, answers = _confirmed(tmp_path, "es")
    confirm_hook(profile, answers)
    assert missing_profile_files(profile) == ()
    facts = (profile / "judge" / "facts.md").read_text("utf-8")
    assert GENERATED_MARKER["es"] in facts


# --- T8 (S9): background answers -> setup-* claims ---------------------------------


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        (
            "en",
            [
                ("setup-lang-es", "language", "Spanish — Native"),
                ("setup-lang-en", "language", "English — C1"),
                ("setup-lang-basque", "language", "Basque — B1"),
                ("setup-skill-python", "skill", "Python"),
                ("setup-skill-cplusplus", "skill", "C++"),
                ("setup-skill-node-js", "skill", "Node.js"),
                (
                    "setup-experience",
                    "experience",
                    (
                        "3-5 years of professional experience · "
                        "Last role: Backend Developer at Acme"
                    ),
                ),
                (
                    "setup-education-bsc-computer-science-upv",
                    "education",
                    "BSc Computer Science, UPV",
                ),
            ],
        ),
        (
            "es",
            [
                ("setup-lang-es", "language", "Español — Nativo"),
                ("setup-lang-en", "language", "Inglés — C1"),
                ("setup-lang-basque", "language", "Basque — B1"),
                ("setup-skill-python", "skill", "Python"),
                ("setup-skill-cplusplus", "skill", "C++"),
                ("setup-skill-node-js", "skill", "Node.js"),
                (
                    "setup-experience",
                    "experience",
                    (
                        "3-5 años de experiencia profesional · "
                        "Último puesto: Backend Developer en Acme"
                    ),
                ),
                (
                    "setup-education-bsc-computer-science-upv",
                    "education",
                    "BSc Computer Science, UPV",
                ),
            ],
        ),
    ],
)
def test_background_answers_become_setup_claims(
    tmp_path: Path, lang: Lang, expected: list[tuple[str, str, str]]
) -> None:
    profile, answers = _confirmed(tmp_path, lang, **_BACKGROUND)
    result = generate_minimal_profile(profile, answers, lang)
    claims = load_claims(profile / "claims.json").claims
    assert [
        (c.id, c.category, c.text) for c in claims if c.id.startswith("setup-")
    ] == expected
    assert claims[0].id == "identity-contact"
    assert result.gaps == ("projects",)


@pytest.mark.parametrize(
    ("overrides", "text"),
    [
        ({"exp_years": "0", "last_role": None}, "No professional experience yet"),
        (
            {"exp_years": "10plus", "last_company": None},
            (
                "More than 10 years of professional experience · "
                "Last role: Backend Developer"
            ),
        ),
        (
            {"exp_years": "lt1", "last_role": None},  # a company alone is not used
            "Less than 1 year of professional experience",
        ),
    ],
)
def test_experience_claim_holds_only_what_was_given(
    tmp_path: Path, overrides: dict[str, Any], text: str
) -> None:
    profile, answers = _confirmed(tmp_path, **{**_BACKGROUND, **overrides})
    generate_minimal_profile(profile, answers, "en")
    claims = load_claims(profile / "claims.json").by_id
    assert claims["setup-experience"].text == text
    assert "setup-education-bsc-computer-science-upv" in claims


def test_skipped_education_and_no_experience_answer_give_no_claim(
    tmp_path: Path,
) -> None:
    profile, answers = _confirmed(
        tmp_path, **{**_BACKGROUND, "education": None, "exp_years": None}
    )
    result = generate_minimal_profile(profile, answers, "en")
    ids = set(load_claims(profile / "claims.json").by_id)
    assert (
        not any(i.startswith("setup-education") for i in ids)
        and "setup-experience" not in ids
    )
    assert {"education", "experience"} <= set(result.gaps)


def test_skill_ids_stay_unique(tmp_path: Path) -> None:
    profile, answers = _confirmed(
        tmp_path, **{**_BACKGROUND, "skills": ["C", "C#", "C++", "c-", "日本語"]}
    )
    generate_minimal_profile(profile, answers, "en")
    ids = [
        c.id
        for c in load_claims(profile / "claims.json").claims
        if c.category == "skill"
    ]
    assert ids == [
        "setup-skill-c",
        "setup-skill-csharp",
        "setup-skill-cplusplus",
        "setup-skill-c-2",
        "setup-skill-5",
    ]


def test_facts_hold_the_spoken_languages_and_skills(tmp_path: Path) -> None:
    profile, answers = _confirmed(tmp_path, **_BACKGROUND)
    generate_minimal_profile(profile, answers, "en")
    facts = (profile / "judge" / "facts.md").read_text("utf-8")
    assert "Spoken languages: Spanish (Native), English (C1), Basque (B1)" in facts
    assert "Main skills: Python, C++, Node.js" in facts


def _hand_written(tmp_path: Path) -> tuple[Path, dict[str, Any], str]:
    profile, answers = _confirmed(tmp_path, **_BACKGROUND)
    shutil.copy(DEMO_PROFILE / "claims.json", profile / "claims.json")
    return profile, answers, (profile / "claims.json").read_text("utf-8")


def _prefix(text: str) -> str:
    return text.rstrip()[:-1].rstrip()


def test_existing_claims_get_the_setup_claims_merged_and_keep_their_bytes(
    tmp_path: Path,
) -> None:
    profile, answers, original = _hand_written(tmp_path)
    mode = stat.S_IMODE((profile / "claims.json").stat().st_mode)

    result = generate_minimal_profile(profile, answers, "en")

    assert result.updated == ("claims.json",)
    assert "claims.json" not in result.created + result.skipped
    text = (profile / "claims.json").read_text("utf-8")
    assert text.startswith(_prefix(original))
    claims = json.loads(text)
    assert [c for c in claims if not c["id"].startswith("setup-")] == json.loads(
        original
    )
    assert len(claims) == len(json.loads(original)) + 8
    assert stat.S_IMODE((profile / "claims.json").stat().st_mode) == mode
    # Same answers again: nothing to change, the file is not rewritten.
    again = generate_minimal_profile(profile, answers, "en")
    assert "claims.json" in again.skipped and again.updated == ()
    assert (profile / "claims.json").read_text("utf-8") == text


def test_rerun_replaces_the_setup_claims_and_never_the_hand_written_ones(
    tmp_path: Path,
) -> None:
    profile, answers, original = _hand_written(tmp_path)
    digest = hashlib.sha256(_prefix(original).encode()).hexdigest()
    generate_minimal_profile(profile, answers, "en")

    changed = {
        **answers,
        "skills": ["Go"],
        "spoken_other": [],
        "education": None,
        "last_role": None,
    }
    generate_minimal_profile(profile, changed, "en")
    text = (profile / "claims.json").read_text("utf-8")
    head = text[: len(_prefix(original))]
    assert hashlib.sha256(head.encode()).hexdigest() == digest
    setup = {
        c["id"]: c["text"] for c in json.loads(text) if c["id"].startswith("setup-")
    }
    assert setup == {
        "setup-lang-es": "Spanish — Native",
        "setup-lang-en": "English — C1",
        "setup-skill-go": "Go",
        "setup-experience": "3-5 years of professional experience",
    }

    # Answers without background questions: the file is the original again.
    generate_minimal_profile(profile, _answers(), "en")
    assert (profile / "claims.json").read_text("utf-8") == original


def test_a_failed_validation_puts_the_merged_claims_back(tmp_path: Path) -> None:
    profile, answers, original = _hand_written(tmp_path)
    toml = profile / "profile.toml"
    toml.write_text(
        toml.read_text("utf-8") + 'identity_claim_id = "missing-contact"\n', "utf-8"
    )
    with pytest.raises(ProfileError, match="identity_claim_id"):
        generate_minimal_profile(profile, answers, "en")
    assert (profile / "claims.json").read_text("utf-8") == original


def test_a_failed_validation_puts_back_the_exact_bytes_of_a_crlf_claims_file(
    tmp_path: Path,
) -> None:
    profile, answers, _ = _hand_written(tmp_path)
    claims = profile / "claims.json"
    raw = claims.read_bytes().replace(b"\n", b"\r\n")
    claims.write_bytes(raw)
    toml = profile / "profile.toml"
    toml.write_text(
        toml.read_text("utf-8") + 'identity_claim_id = "missing-contact"\n', "utf-8"
    )
    with pytest.raises(ProfileError, match="identity_claim_id"):
        generate_minimal_profile(profile, answers, "en")
    assert claims.read_bytes() == raw
