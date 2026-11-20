"""role-families T4: cover letters, gates, judge, salary, suggestions and panel
labels follow the profile's role families (odd/tasks/role-families.md S3).

Built-in behaviour (S4) is pinned here too: the built-in families keep today's
labels, thresholds, suggestions and emphasis.
"""

import asyncio
import dataclasses
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

import cva.profile_paths as profile_paths_module
from cva.calibration import CalibrationExample, anchor_line
from cva.claimsdb import load_claims
from cva.families import BUILTIN_REGISTRY
from cva.match.cover_letter import build_letter_parts_prompt
from cva.match.extraction import ExtractionCache, OfferExtraction, Requirement
from cva.match.judge import OfferJudgment, build_system_prompt
from cva.match.salary_reference import ReferenceRange, salary_line
from cva.match.scorer import load_synonyms
from cva.profile_prefs import DEFAULT_PREFS, ProfilePrefs, load_preferences
from cva.report.panel import DiscardedOffer, render_panel
from cva.service import JobService
from cva.suggest import suggest_searches
from cva.tracker import OfferInput, OfferRepository
from tests.demo import DEMO_CLAIMS, DEMO_MASTERS, DEMO_SYNONYMS
from tests.test_cover_letter_v2 import ES_BASE, ES_PARTS

FRONTEND = """
[families.ai]
[families.frontend]
label = "Frontend"
title_terms = ["Frontend", "React"]
search_roles = ["Frontend Developer", "React Developer"]
"""
TEXT = "Frontend Developer. Imprescindible Python y containers. Kubernetes valorable."


def _prefs(tmp_path: Path, body: str) -> ProfilePrefs:
    path = tmp_path / "preferences.toml"
    path.write_text(body, encoding="utf-8")
    return load_preferences(path)


# --- cover letters ---------------------------------------------------------------


def _letter_system(prefs: ProfilePrefs, branch: str) -> str:
    system, _ = build_letter_parts_prompt(
        offer_text="Buscamos frontend.",
        title="Frontend Developer",
        company="Acme",
        language="es",
        branch=branch,
        claims_digest="c1: x",
        apply_findings=(),
        voice_sample="v",
        base=ES_BASE,
        facts=(),
        persona=prefs.cover_letter,
        families=prefs.families,
    )
    return system


def test_family_cover_letter_emphasis_wins(tmp_path: Path) -> None:
    prefs = _prefs(
        tmp_path,
        FRONTEND
        + 'cover_letter_emphasis = "Frontend branch: emphasise React."\n'
        + '[cover_letter.branch_emphasis]\nFRONTEND = "From the table."\n',
    )
    assert _letter_system(prefs, "FRONTEND").endswith(
        "Branch emphasis: Frontend branch: emphasise React."
    )


def test_branch_emphasis_table_accepts_a_declared_family(tmp_path: Path) -> None:
    prefs = _prefs(
        tmp_path,
        FRONTEND + '[cover_letter.branch_emphasis]\nFRONTEND = "From the table."\n',
    )
    assert _letter_system(prefs, "FRONTEND").endswith(
        "Branch emphasis: From the table."
    )


def test_family_without_emphasis_uses_the_default_family_emphasis(
    tmp_path: Path,
) -> None:
    prefs = _prefs(tmp_path, FRONTEND)
    ai = DEFAULT_PREFS.cover_letter.branch_emphasis["AI"]
    assert _letter_system(prefs, "FRONTEND").endswith(f"Branch emphasis: {ai}")


def test_no_emphasis_anywhere_omits_the_line(tmp_path: Path) -> None:
    prefs = _prefs(tmp_path, "[families.frontend]\n[families.marketing]\n")
    system = _letter_system(prefs, "MARKETING")
    assert "Branch emphasis" not in system
    assert system.endswith("13. Write in the language requested by the user message.")


def test_builtin_emphasis_unchanged() -> None:
    for branch in ("AI", "BACKEND", "DS", "OTHER"):
        with_families = _letter_system(DEFAULT_PREFS, branch)
        legacy, _ = build_letter_parts_prompt(
            offer_text="Buscamos frontend.",
            title="Frontend Developer",
            company="Acme",
            language="es",
            branch=branch,
            claims_digest="c1: x",
            apply_findings=(),
            voice_sample="v",
            base=ES_BASE,
            facts=(),
        )
        assert with_families == legacy, branch


class _LetterLlm:
    model = "fake"

    def __init__(self) -> None:
        self.systems: list[str] = []

    async def json_completion(
        self, system: str, user: str, schema: type[BaseModel], *, model=None
    ):  # type: ignore[no-untyped-def]
        self.systems.append(system)
        return schema.model_validate(ES_PARTS)


class _ExtractionLlm:
    model = "m"

    def __init__(self, reply: OfferExtraction) -> None:
        self.reply = reply

    async def json_completion(
        self, system: str, user: str, schema: type[object]
    ) -> object:
        return self.reply


def _extraction() -> OfferExtraction:
    return OfferExtraction(
        title="Frontend Developer",
        company="Acme",
        requirements=[
            Requirement(
                name="Python", category="skill", must_have=True, evidence="Python"
            ),
            Requirement(
                name="containers",
                category="tool",
                must_have=False,
                evidence="containers",
            ),
        ],
    )


def _service(
    tmp_path: Path, prefs: str | None, llm: object, **kwargs: object
) -> tuple[JobService, OfferRepository]:
    # The conftest-patched resolver: the demo profile, never the owner's data/.
    paths = profile_paths_module.resolve_profile_paths()
    if prefs is not None:
        pref_path = tmp_path / "preferences.toml"
        pref_path.write_text(prefs, encoding="utf-8")
        paths = dataclasses.replace(paths, preferences=pref_path)
    conn = sqlite3.connect(tmp_path / "app.db", check_same_thread=False)
    repo = OfferRepository(conn)
    db = load_claims(DEMO_CLAIMS)
    svc = JobService(
        repo=repo,
        llm=llm,  # type: ignore[arg-type]
        fetcher=None,  # type: ignore[arg-type]
        db=db,
        syn=load_synonyms(DEMO_SYNONYMS, db),
        cache=ExtractionCache(conn),
        masters_dir=DEMO_MASTERS,
        profile_paths=paths,
        **kwargs,  # type: ignore[arg-type]
    )
    return svc, repo


def _offer(
    repo: OfferRepository, profile: str | None, title: str = "Frontend Developer"
) -> int:
    return repo.upsert_offer(
        OfferInput(
            title=title,
            company="Acme",
            url="https://x/1",
            text=TEXT,
            source="bridge-inline",
            profile=profile,
        )
    )


def test_service_cover_letter_uses_the_offer_family(tmp_path: Path) -> None:
    llm = _LetterLlm()
    svc, repo = _service(
        tmp_path, FRONTEND + 'cover_letter_emphasis = "Frontend emphasis."\n', llm
    )
    asyncio.run(svc.cover_letters(_offer(repo, "frontend")))
    assert llm.systems[0].endswith("Branch emphasis: Frontend emphasis.")


def test_manual_offer_profile_is_inferred_from_the_families(tmp_path: Path) -> None:
    svc, repo = _service(tmp_path, FRONTEND, _LetterLlm())
    oid = _offer(repo, None, title="React Developer")
    svc._ensure_profile(oid)
    assert repo.get_offer(oid).perfil == "frontend"


# --- thresholds ------------------------------------------------------------------


def _threshold(
    tmp_path: Path, prefs: str | None, profile: str | None, **kw: object
) -> int:
    svc, repo = _service(tmp_path, prefs, _ExtractionLlm(_extraction()), **kw)
    oid = _offer(repo, profile)
    asyncio.run(svc.analyze(oid))
    match = repo.latest_match(oid)
    assert match is not None
    return match.threshold


def test_family_threshold_overrides_the_settings_gate(tmp_path: Path) -> None:
    body = FRONTEND + "threshold = 42\n"
    assert _threshold(tmp_path, body, "frontend", threshold=70) == 42


def test_family_without_threshold_uses_the_settings_threshold(tmp_path: Path) -> None:
    assert (
        _threshold(tmp_path, FRONTEND, "frontend", threshold=70, backend_threshold=50)
        == 70
    )


def test_declared_backend_threshold_overrides_backend_threshold(tmp_path: Path) -> None:
    body = "[families.ai]\n[families.backend]\nthreshold = 33\n"
    assert _threshold(tmp_path, body, "backend", backend_threshold=50) == 33


@pytest.mark.parametrize(
    ("profile", "expected"), [("ai", 70), ("backend", 50), (None, 70)]
)
def test_builtin_thresholds_unchanged(
    tmp_path: Path, profile: str | None, expected: int
) -> None:
    assert (
        _threshold(tmp_path, None, profile, threshold=70, backend_threshold=50)
        == expected
    )


# --- judge -----------------------------------------------------------------------


def _judgment(cv_base: str) -> dict[str, object]:
    return {
        "score": 70,
        "verdict": "apply",
        "core_role": "Frontend",
        "cv_base": cv_base,
        "rationale": "Encaja.",
    }


@pytest.mark.parametrize(
    "cv_base", ["AI_ES", "AI_EN", "DS_ES", "DS_EN", "BACKEND_EN", "FRONTEND_ES"]
)
def test_judgment_accepts_any_family_cv_base(cv_base: str) -> None:
    assert OfferJudgment.model_validate(_judgment(cv_base)).cv_base == cv_base


@pytest.mark.parametrize("cv_base", ["frontend_es", "FRONTEND_DE", "FRONTEND", "_ES"])
def test_judgment_rejects_a_malformed_cv_base(cv_base: str) -> None:
    with pytest.raises(ValidationError):
        OfferJudgment.model_validate(_judgment(cv_base))


def _example(profile: str | None) -> CalibrationExample:
    return CalibrationExample(
        id=1,
        offer_id=1,
        polarity="positive",
        outcome="entrevista_tecnica",
        reason=None,
        score_at_send=50,
        profile=profile,
        core_role="React",
        status="active",
        created_at="2026-11-01",
    )


def test_anchor_line_uses_the_family_label(tmp_path: Path) -> None:
    families = _prefs(tmp_path, FRONTEND).families
    line = anchor_line(_example("frontend"), threshold=60, families=families)
    assert line.startswith("- Frontend role (core: React)")


@pytest.mark.parametrize(
    ("profile", "label"),
    [("ai", "AI"), ("backend", "Backend"), (None, "AI"), ("ds", "AI")],
)
def test_builtin_anchor_labels_unchanged(profile: str | None, label: str) -> None:
    assert anchor_line(_example(profile), threshold=60).startswith(f"- {label} role")


def test_judge_anchor_uses_its_family_threshold(tmp_path: Path) -> None:
    families = _prefs(tmp_path, FRONTEND).families
    system = build_system_prompt(
        load_claims(DEMO_CLAIMS),
        extra_anchors=[_example("frontend")],
        anchor_thresholds={"ai": 65, "frontend": 55},
        families=families,
    )
    assert system.endswith(
        "- Frontend role (core: React), you scored 50, it got a technical "
        "interview -> similar offers deserve at least 55."
    )


# --- salary ----------------------------------------------------------------------


def _row(branch: str) -> ReferenceRange:
    return ReferenceRange.model_validate(
        {
            "branch": branch,
            "level": "junior",
            "city": "barcelona",
            "low": 28000,
            "median": 31000,
            "high": 34000,
            "ask": 32000,
            "sources": ["src"],
            "confidence": "high",
            "as_of": "2026-10",
        }
    )


def test_salary_reference_for_a_declared_family(tmp_path: Path) -> None:
    families = _prefs(tmp_path, FRONTEND).families
    line = salary_line(
        title="Frontend Developer",
        text="Oficina en Barcelona.",
        perfil="frontend",
        table=[_row("AI"), _row("FRONTEND")],
        families=families,
    )
    assert "· Frontend junior · Barcelona ·" in line


def test_family_without_rows_has_no_salary_reference(tmp_path: Path) -> None:
    families = _prefs(tmp_path, FRONTEND).families
    line = salary_line(
        title="Frontend Developer",
        text="Oficina en Barcelona.",
        perfil="frontend",
        table=[_row("AI")],
        families=families,
    )
    assert line == ""


@pytest.mark.parametrize(
    ("perfil", "label"), [("ai", "AI"), ("backend", "Backend"), (None, "AI")]
)
def test_builtin_salary_labels_unchanged(perfil: str | None, label: str) -> None:
    line = salary_line(
        title="Developer",
        text="Oficina en Barcelona.",
        perfil=perfil,
        table=[_row("AI"), _row("BACKEND")],
    )
    assert f"· {label} junior · Barcelona ·" in line


# --- search suggestions ----------------------------------------------------------


def test_suggestions_follow_the_declared_families(tmp_path: Path) -> None:
    prefs = _prefs(tmp_path, FRONTEND)
    searches = suggest_searches(load_claims(DEMO_CLAIMS), prefs.search_targets)
    frontend = [s for s in searches if s.filter_profile == "frontend"]
    assert [s.keywords for s in frontend][:2] == [
        "Frontend Developer",
        "Frontend Developer",
    ]
    assert "React Developer" in [s.keywords for s in frontend]
    assert {s.filter_profile for s in searches} == {"ai", "frontend"}


def test_builtin_suggestions_unchanged(tmp_path: Path) -> None:
    claims = load_claims(DEMO_CLAIMS)
    targets = DEFAULT_PREFS.search_targets
    legacy = suggest_searches(claims, targets)
    explicit = suggest_searches(claims, targets, families=DEFAULT_PREFS.families)
    assert explicit == legacy
    declared = _prefs(tmp_path, "[families.ai]\n[families.backend]\n")
    assert suggest_searches(claims, declared.search_targets) == legacy


# --- panel -----------------------------------------------------------------------


def _discarded(profile: str) -> DiscardedOffer:
    return DiscardedOffer(
        offer_id=7,
        title="Dev",
        company="Acme",
        portal="LinkedIn",
        url=None,
        location=None,
        kind="below",
        score=40,
        threshold=60,
        profile=profile,
        reason="r",
        days_ago=1,
    )


NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def test_panel_uses_the_family_label(tmp_path: Path) -> None:
    families = _prefs(tmp_path, FRONTEND).families
    page = render_panel([], NOW, [_discarded("frontend")], families=families)
    assert "40 / 60 (Frontend)" in page


@pytest.mark.parametrize(("profile", "tag"), [("ai", "IA"), ("backend", "Backend")])
def test_builtin_panel_labels_unchanged(profile: str, tag: str) -> None:
    page = render_panel([], NOW, [_discarded(profile)])
    assert f"40 / 60 ({tag})" in page
    assert page == render_panel(
        [], NOW, [_discarded(profile)], families=BUILTIN_REGISTRY
    )
