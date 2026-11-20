"""profile-extraction E4: the prompts' persona text comes from the profile.

With a profile, each file/key replaces exactly its own text. The byte-identical
guard of the owner's built-in prompts lives in
``tests/owner/test_prompt_persona_owner.py``.
"""

import asyncio
import dataclasses
import sqlite3
from pathlib import Path

import pytest
from pydantic import BaseModel

from cva.claimsdb import Claim, ClaimsDB, load_claims
from cva.match import cover_letter as cl
from cva.match.extraction import ExtractionCache
from cva.match.judge import build_system_prompt
from cva.match.persona import CoverLetterPersona, JudgePersona, RubricPersona
from cva.match.persona_defaults import (
    OWNER_COVER_LETTER_PERSONA,
    OWNER_JUDGE_PERSONA,
)
from cva.match.scorer import load_synonyms
from cva.profile_identity import ProfileError
from cva.profile_paths import resolve_profile_paths
from cva.profile_persona import (
    RUBRIC_SLOTS,
    load_judge_persona,
    render_judge_persona_files,
)
from cva.profile_prefs import DEFAULT_PREFS, load_preferences
from cva.service import JobService
from cva.tracker import OfferInput, OfferRepository
from tests.demo import DEMO_CLAIMS, DEMO_MASTERS, DEMO_SYNONYMS


def _claims() -> ClaimsDB:
    claims = [
        Claim(id="skill-python", text="Python", category="skill", tags=["python"]),
        Claim(
            id="skill-python_en", text="Python (en)", category="skill", tags=["python"]
        ),
        Claim(
            id="skill-rag",
            text="Pipelines RAG con recall medido",
            category="skill",
            tags=["rag"],
        ),
        Claim(
            id="edu-coding-school",
            text="Coding school, FP-equivalente",
            category="education",
            tags=["42"],
        ),
    ]
    return ClaimsDB(claims=claims, by_id={c.id: c for c in claims})


_LETTER_ARGS = {
    "offer_text": "Offer body.",
    "title": "AI Engineer",
    "company": "Acme",
    "language": "es",
    "claims_digest": "skill-python: Python",
    "apply_findings": [],
    "voice_sample": "Voz.",
    "owner_name": "Ana Demo",
}

# Owner-only words of the persona files. Not listed: "FP Superior", which one
# GENERIC engine anchor (the "degree 'o similar'" one) still names.
_OWNER_ONLY = (
    "data consultancy",
    "media group",
    "IT services firm",
    "IT integrator",
    "careful human reviewer",
    "coding school",
)


def _write_judge_dir(root: Path, persona: JudgePersona) -> Path:
    judge = root / "judge"
    judge.mkdir(parents=True, exist_ok=True)
    for name, text in render_judge_persona_files(persona).items():
        (judge / name).write_text(text, encoding="utf-8")
    return judge


def _custom_persona() -> JudgePersona:
    slots = {slot: f"CUSTOM {slot} text." for slot in RUBRIC_SLOTS}
    return JudgePersona(
        facts="Candidate timeline: Acme Corp since 2020.",
        named_anchors="- Foo Inc Engineer -> score 50, apply_with_caveats (demo).",
        rubric=RubricPersona(**slots),
    )


def test_default_personas_are_what_the_service_uses_without_a_profile() -> None:
    assert load_judge_persona(None) == OWNER_JUDGE_PERSONA
    assert DEFAULT_PREFS.cover_letter == OWNER_COVER_LETTER_PERSONA


# --- the file format round-trips the owner's text -----------------------------


# --- profile files replace their own text -------------------------------------


def test_profile_persona_replaces_every_persona_text(tmp_path: Path) -> None:
    judge = _write_judge_dir(tmp_path, _custom_persona())

    prompt = build_system_prompt(_claims(), persona=load_judge_persona(judge))

    assert "Candidate timeline: Acme Corp since 2020." in prompt
    assert "- Foo Inc Engineer -> score 50" in prompt
    for slot in RUBRIC_SLOTS:
        assert f"CUSTOM {slot} text." in prompt
    for owner_text in _OWNER_ONLY:
        assert owner_text not in prompt, owner_text
    # The engine keeps the generic parts of the rubric and the generic anchors.
    assert "Truth constraint:" in prompt
    assert "Verdict: state your own best call" in prompt
    assert "A GenAI/RAG engineer role requiring only 1-2 years" in prompt
    assert "skill-rag: Pipelines RAG con recall medido" in prompt


def test_rubric_persona_is_injected_at_the_same_positions() -> None:
    prompt = build_system_prompt(_claims(), persona=_custom_persona())
    order = [
        "CUSTOM manual_analyses text.",
        "Score (0-100)",
        *(f"CUSTOM {slot} text." for slot in RUBRIC_SLOTS[1:]),
        "Truth constraint:",
        "Verdict:",
        "Calibration anchors (owner-reviewed):",
        "- Foo Inc Engineer",
        "A GenAI/RAG engineer role",
        "Candidate timeline:",
        "Candidate claims (id: text)",
    ]
    positions = [prompt.index(part) for part in order]
    assert positions == sorted(positions)
    assert RUBRIC_SLOTS[0] == "manual_analyses"


def test_empty_named_anchors_leave_only_the_generic_ones() -> None:
    persona = dataclasses.replace(_custom_persona(), named_anchors="")

    prompt = build_system_prompt(_claims(), persona=persona)

    assert "Calibration anchors (owner-reviewed):\n- A GenAI/RAG engineer" in prompt


def test_missing_judge_file_in_a_configured_profile_fails_loudly(
    tmp_path: Path,
) -> None:
    judge = _write_judge_dir(tmp_path, _custom_persona())
    (judge / "anchors.md").unlink()

    with pytest.raises(ProfileError, match="anchors.md"):
        load_judge_persona(judge)


@pytest.mark.parametrize(
    "body",
    [
        "no headings at all",
        "## nonsense\ntext\n",
        "## manual_analyses\n\n## experience_rule\ntext\n",  # empty slot
        "## manual_analyses\na\n## manual_analyses\nb\n",  # duplicate
    ],
)
def test_invalid_rubric_persona_fails_loudly(tmp_path: Path, body: str) -> None:
    judge = _write_judge_dir(tmp_path, _custom_persona())
    (judge / "rubric_persona.md").write_text(body, encoding="utf-8")

    with pytest.raises(ProfileError, match="rubric_persona.md"):
        load_judge_persona(judge)


def test_rubric_persona_with_a_missing_slot_fails_loudly(tmp_path: Path) -> None:
    judge = _write_judge_dir(tmp_path, _custom_persona())
    text = (judge / "rubric_persona.md").read_text(encoding="utf-8")
    (judge / "rubric_persona.md").write_text(
        text.replace("## hard_walls", "## not_a_slot"), encoding="utf-8"
    )

    with pytest.raises(ProfileError, match="hard_walls|not_a_slot"):
        load_judge_persona(judge)


def test_rubric_persona_preamble_before_the_first_slot_is_ignored(
    tmp_path: Path,
) -> None:
    judge = _write_judge_dir(tmp_path, _custom_persona())
    path = judge / "rubric_persona.md"
    path.write_text(
        "# My rubric\nNotes for me.\n\n" + path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    assert load_judge_persona(judge) == _custom_persona()


# --- paths -------------------------------------------------------------------


def test_judge_dir_resolves_under_the_profile() -> None:
    assert resolve_profile_paths(None).judge_dir is None
    assert resolve_profile_paths(Path("/p")).judge_dir == Path("/p/judge")


# --- cover letter: preferences.toml [cover_letter] -----------------------------


def _prefs(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "preferences.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_cover_letter_persona_from_preferences(tmp_path: Path) -> None:
    prefs = load_preferences(
        _prefs(
            tmp_path,
            '[cover_letter]\ncareer_change = "The move from teaching to data takes ONE sentence."\n'
            '[cover_letter.branch_emphasis]\nAI = "AI branch: demo emphasis."\n',
        )
    )

    persona = prefs.cover_letter
    assert persona.career_change == "The move from teaching to data takes ONE sentence."
    assert persona.branch_emphasis["AI"] == "AI branch: demo emphasis."
    # A missing branch keeps the engine default for that key only.
    assert (
        persona.branch_emphasis["BACKEND"]
        == (OWNER_COVER_LETTER_PERSONA.branch_emphasis["BACKEND"])
    )
    system, _ = cl.build_cover_letter_prompt(
        branch="AI", persona=persona, **_LETTER_ARGS
    )
    assert "7. The move from teaching to data takes ONE sentence.\n" in system
    assert system.endswith("Branch emphasis: AI branch: demo emphasis.")
    assert "career change (software development" not in system
    assert "learning speed" not in system


def test_cover_letter_persona_fallback_branch_uses_ai(tmp_path: Path) -> None:
    persona = CoverLetterPersona(
        branch_emphasis={"AI": "A.", "BACKEND": "B."}, career_change="C."
    )
    system, _ = cl.build_cover_letter_prompt(
        branch="OTHER", persona=persona, **_LETTER_ARGS
    )
    assert system.endswith("Branch emphasis: A.")


@pytest.mark.parametrize(
    "body",
    [
        "[cover_letter]\nbogus = 1\n",
        "[cover_letter]\ncareer_change = 3\n",
        '[cover_letter.branch_emphasis]\nFRONTEND = "x"\n',
        "[cover_letter.branch_emphasis]\nAI = 3\n",
    ],
)
def test_invalid_cover_letter_preferences_fail_loudly(
    tmp_path: Path, body: str
) -> None:
    with pytest.raises(ProfileError):
        load_preferences(_prefs(tmp_path, body))


# --- the service uses the profile's persona ------------------------------------


class _Llm:
    model = "fake"

    def __init__(self) -> None:
        self.systems: list[str] = []

    async def json_completion(
        self, system: str, user: str, schema: type[BaseModel], *, model=None
    ):
        self.systems.append(system)
        from tests.test_cover_letter import ES_BODY, ES_CERCANA

        return schema.model_validate({"formal": ES_BODY, "cercana": ES_CERCANA})


def _service(tmp_path: Path, llm: _Llm, judge_dir: Path | None, prefs: Path | None):
    conn = sqlite3.connect(tmp_path / "app.db")
    repo = OfferRepository(conn)
    db = load_claims(DEMO_CLAIMS)
    paths = dataclasses.replace(
        resolve_profile_paths(None), judge_dir=judge_dir, preferences=prefs
    )
    service = JobService(
        repo=repo,
        llm=llm,  # type: ignore[arg-type]
        fetcher=None,  # type: ignore[arg-type]
        db=db,
        syn=load_synonyms(DEMO_SYNONYMS, db),
        cache=ExtractionCache(conn),
        masters_dir=DEMO_MASTERS,
        profile_paths=paths,
    )
    offer_id = repo.upsert_offer(
        OfferInput(
            title="AI Engineer",
            company="Acme",
            url="https://x/1",
            text="Buscamos AI Engineer en Valencia. Python y RAG.",
            source="link",
            profile="ai",
        )
    )
    return service, offer_id


@pytest.mark.owner  # the default (no profile) side reads the owner base letter
def test_service_cover_letters_use_the_profile_persona(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    prefs = _prefs(
        tmp_path / "b",
        '[cover_letter]\ncareer_change = "The move from teaching to data takes ONE sentence."\n',
    )
    default_llm, custom_llm = _Llm(), _Llm()
    default, default_id = _service(tmp_path / "a", default_llm, None, None)
    custom, custom_id = _service(tmp_path / "b", custom_llm, None, prefs)

    asyncio.run(default.cover_letters(default_id))
    asyncio.run(custom.cover_letters(custom_id))

    assert "career change (software development" in default_llm.systems[0]
    assert "The move from teaching" not in default_llm.systems[0]
    assert "The move from teaching to data takes ONE sentence." in custom_llm.systems[0]
    assert "career change (software development" not in custom_llm.systems[0]


def test_service_judge_uses_the_profile_persona(tmp_path: Path) -> None:
    from tests.test_service import (
        _judgment,
        _long_text,
        _noop_sleep,
        _RejudgeLlm,
        _seed_offer,
    )

    class _RecordingLlm(_RejudgeLlm):
        def __init__(self, outcomes):  # type: ignore[no-untyped-def]
            super().__init__(outcomes)
            self.systems: list[str] = []

        async def json_completion(self, system, user, schema, *, model=None):  # type: ignore[no-untyped-def]
            self.systems.append(system)
            return await super().json_completion(system, user, schema, model=model)

    judge = _write_judge_dir(tmp_path, _custom_persona())
    llm = _RecordingLlm([_judgment(score=70)])
    service, _ = _service(tmp_path, llm, judge, None)  # type: ignore[arg-type]
    assert service.judge_persona == _custom_persona()
    _seed_offer(service._repo, url="https://x/9", text=_long_text())

    asyncio.run(
        service.rejudge_backlog(pace_seconds=0, sleep_fn=_noop_sleep, min_score=0)
    )

    assert "Candidate timeline: Acme Corp since 2020." in llm.systems[0]
    absent = list(_OWNER_ONLY)
    for owner_text in absent:
        assert owner_text not in llm.systems[0], owner_text


# --- review follow-ups ---------------------------------------------------------


@pytest.mark.parametrize("empty", ['""', '"   "'])
@pytest.mark.parametrize(
    "table",
    [
        "[cover_letter]\ncareer_change = {v}\n",
        "[cover_letter.branch_emphasis]\nAI = {v}\n",
    ],
)
def test_empty_cover_letter_strings_are_rejected(
    tmp_path: Path, table: str, empty: str
) -> None:
    with pytest.raises(ProfileError, match="non-empty"):
        load_preferences(_prefs(tmp_path, table.format(v=empty)))


def test_rubric_slot_body_with_a_heading_is_rejected_on_render() -> None:
    persona = _custom_persona()
    bad = dataclasses.replace(persona.rubric, hard_walls="Intro\n## Sub heading\nmore")
    with pytest.raises(ProfileError, match="hard_walls.*##"):
        render_judge_persona_files(dataclasses.replace(persona, rubric=bad))


def test_slot_heading_collision_error_explains_the_rule(tmp_path: Path) -> None:
    judge = _write_judge_dir(tmp_path, _custom_persona())
    path = judge / "rubric_persona.md"
    path.write_text(
        path.read_text(encoding="utf-8") + "\n## Extra notes\nx\n", encoding="utf-8"
    )
    with pytest.raises(ProfileError, match="must not contain '## '"):
        load_judge_persona(judge)


def test_empty_facts_are_rejected_like_the_slots(tmp_path: Path) -> None:
    judge = _write_judge_dir(tmp_path, _custom_persona())
    (judge / "facts.md").write_text("  \n", encoding="utf-8")
    with pytest.raises(ProfileError, match="facts.md.*empty"):
        load_judge_persona(judge)
    with pytest.raises(ProfileError, match="facts.*empty"):
        render_judge_persona_files(dataclasses.replace(_custom_persona(), facts=""))


def test_missing_judge_files_error_lists_them_and_how_to_create(
    tmp_path: Path,
) -> None:
    judge = tmp_path / "judge"
    judge.mkdir()
    (judge / "anchors.md").write_text("", encoding="utf-8")
    with pytest.raises(ProfileError) as info:
        load_judge_persona(judge)
    message = str(info.value)
    assert "facts.md" in message and "rubric_persona.md" in message
    assert "anchors.md" not in message.split("render_judge_persona_files")[0]
    assert "render_judge_persona_files" in message


def test_service_startup_fails_without_the_judge_dir_in_a_profile(
    tmp_path: Path,
) -> None:
    with pytest.raises(ProfileError, match="facts.md.*anchors.md.*rubric_persona.md"):
        _service(tmp_path, _Llm(), tmp_path / "no-judge", None)
