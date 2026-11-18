"""Deterministic search suggestions from the profile (no LLM)."""

import json

import pytest

from cva.claimsdb import Claim, ClaimsDB, load_claims
from cva.ingest.sources import SOURCES
from cva.suggest import (
    DEFAULT_PROFILE_TARGETS,
    ProfileTargets,
    suggest_searches,
    suggested_searches_payload,
)


def _db(*claims: Claim) -> ClaimsDB:
    return ClaimsDB(claims=list(claims), by_id={c.id: c for c in claims})


def _skill(name: str, tag: str) -> Claim:
    return Claim(id=f"skill-{tag}", text=name, category="skill", tags=[tag])


def _mention(idx: int, *tags: str) -> Claim:
    return Claim(id=f"proj-{idx}", text=f"p{idx}", category="project", tags=list(tags))


@pytest.fixture()
def claims() -> ClaimsDB:
    return _db(
        _skill("Python", "python"),
        _skill("LLMs", "llm"),
        _skill("RAG (Retrieval-Augmented Generation)", "rag"),
        _skill("Django", "django"),
        _skill("FastAPI", "fastapi"),
        _skill("Pygame", "pygame"),
        _skill("Python", "python"),  # duplicate (the *_en twin) must not repeat
        _mention(1, "llm", "rag", "python"),
        _mention(2, "llm", "python"),
        _mention(3, "django", "python"),
        _mention(4, "pygame"),
    )


def test_returns_between_six_and_eight_searches(claims: ClaimsDB) -> None:
    out = suggest_searches(claims, DEFAULT_PROFILE_TARGETS)
    assert 6 <= len(out) <= 8


def test_is_deterministic(claims: ClaimsDB) -> None:
    assert suggest_searches(claims, DEFAULT_PROFILE_TARGETS) == suggest_searches(
        claims, DEFAULT_PROFILE_TARGETS
    )


def test_splits_ai_and_backend_profiles(claims: ClaimsDB) -> None:
    out = suggest_searches(claims, DEFAULT_PROFILE_TARGETS)
    profiles = {s.filter_profile for s in out}
    assert profiles == {"ai", "backend"}
    ai = [s for s in out if s.filter_profile == "ai"]
    assert any(s.keywords == DEFAULT_PROFILE_TARGETS.ai_roles[0] for s in ai)
    backend = [s for s in out if s.filter_profile == "backend"]
    assert any(s.keywords == DEFAULT_PROFILE_TARGETS.backend_roles[0] for s in backend)


def test_locations_are_barcelona_and_remote(claims: ClaimsDB) -> None:
    out = suggest_searches(claims, DEFAULT_PROFILE_TARGETS)
    assert {s.location for s in out} == {"Barcelona", "remoto"}


def test_default_portals_skip_in_tab_only_and_keywordless_portals(
    claims: ClaimsDB,
) -> None:
    """Suggestions stay low-volume: no Tecnoempleo/JobFluent (in-tab only,
    owner reads them at low volume) and no Manfred (ignores keywords)."""
    out = suggest_searches(claims, DEFAULT_PROFILE_TARGETS)
    for s in out:
        assert s.portals == ("linkedin", "infojobs", "indeed")
        assert not {"tecnoempleo", "jobfluent", "manfred"} & set(s.portals)
        assert set(s.portals) <= set(SOURCES)


def test_skills_come_from_the_claims_ranked_by_use(claims: ClaimsDB) -> None:
    out = suggest_searches(claims, DEFAULT_PROFILE_TARGETS)
    skill_searches = [s for s in out if s.title_filter == "off"]
    kws = {s.keywords for s in skill_searches}
    # AI side: LLMs (2 mentions) before RAG (1); label drops the parenthetical.
    assert "LLMs RAG" in kws
    # Backend side: Python (3) before Django (1); unused Pygame never appears.
    assert "Python Django" in kws
    assert not any("Pygame" in s.keywords for s in out)


def test_role_searches_use_the_title_filter_and_skill_ones_do_not(
    claims: ClaimsDB,
) -> None:
    out = suggest_searches(claims, DEFAULT_PROFILE_TARGETS)
    for s in out:
        roles = DEFAULT_PROFILE_TARGETS.ai_roles + DEFAULT_PROFILE_TARGETS.backend_roles
        assert (s.title_filter == "on") == (s.keywords in roles)


def test_no_duplicates_by_keywords_and_location(claims: ClaimsDB) -> None:
    out = suggest_searches(claims, DEFAULT_PROFILE_TARGETS)
    keys = [(s.keywords.lower(), s.location.lower()) for s in out]
    assert len(keys) == len(set(keys))


def test_without_skills_only_role_searches(claims: ClaimsDB) -> None:
    out = suggest_searches(_db(), DEFAULT_PROFILE_TARGETS)
    assert out
    assert all(s.title_filter == "on" for s in out)


def test_custom_targets_and_portals(claims: ClaimsDB) -> None:
    targets = ProfileTargets(
        ai_roles=("ML Engineer",),
        backend_roles=("API Developer",),
        locations=("Madrid",),
    )
    out = suggest_searches(claims, targets, portals=("linkedin",))
    assert {s.location for s in out} == {"Madrid"}
    assert all(s.portals == ("linkedin",) for s in out)
    assert any(s.keywords == "ML Engineer" for s in out)


def test_names_are_readable_and_unique(claims: ClaimsDB) -> None:
    out = suggest_searches(claims, DEFAULT_PROFILE_TARGETS)
    names = [s.name for s in out]
    assert len(names) == len(set(names))
    assert "AI Engineer · Barcelona" in names


def test_real_claims_db_yields_valid_payload() -> None:
    payload = suggested_searches_payload()
    assert 6 <= len(payload) <= 8
    json.dumps(payload)  # JSON serializable
    for item in payload:
        assert set(item) == {
            "name",
            "keywords",
            "location",
            "filterProfile",
            "portals",
            "titleFilter",
        }
        assert item["filterProfile"] in ("ai", "backend")
        assert item["titleFilter"] in ("on", "off")
        assert isinstance(item["portals"], list)
    assert load_claims  # the payload reads the shipped claims.json


def test_default_portals_only_return_enabled_ones() -> None:
    from cva.suggest import default_portals

    assert default_portals(["infojobs"]) == ("infojobs",)
    assert default_portals(["indeed", "linkedin"]) == ("linkedin", "indeed")
    assert default_portals([]) == ()


def test_default_portals_unset_is_the_legacy_list() -> None:
    from cva.suggest import default_portals

    assert (
        default_portals()
        == default_portals(None)
        == (
            "linkedin",
            "infojobs",
            "indeed",
        )
    )


def test_no_enabled_default_portal_means_no_suggestions() -> None:
    assert suggested_searches_payload(enabled_portals=[]) == []
    assert all(
        s["portals"] == ["infojobs"]
        for s in suggested_searches_payload(enabled_portals=["infojobs"])
    )
