"""role-families T1: profile-defined role families (odd/tasks/role-families.md S2, S4, S5).

Every rule goes through the public interface: a ``preferences.toml`` written to
``tmp_path``, ``load_preferences`` and the registry it returns, and the
``cva/profile.py`` functions the bridge, service and repo already call.
"""

from pathlib import Path

import pytest

from cva.families import BUILTIN_REGISTRY, FamilyRegistry
from cva.profile import (
    AI_TITLE_TERMS,
    has_ai_title,
    infer_profile,
    normalize_profile,
    role_for_profile,
)
from cva.profile_identity import ProfileError
from cva.profile_prefs import DEFAULT_PREFS, load_preferences
from cva.title_filter import (
    BACKEND_TITLE_FILTER,
    DEFAULT_TITLE_FILTER,
    TitleFilter,
)

_THREE = """
[families.frontend]
label = "Frontend"
languages = ["es", "en"]
title_terms = ["Frontend", "React", "Vue"]
threshold = 55
search_roles = ["Frontend Developer", "React Developer"]
cover_letter_emphasis = "Frontend branch: emphasise UI work."
default = true
[families.frontend.title_filter]
include_groups = [["Frontend", "React"]]
exclude_terms = ["Senior"]

[families.marketing]
label = "Marketing"
languages = ["es", "ca"]
title_terms = ["Marketing", "SEO"]

[families.nursing]
label = "Enfermería"
languages = ["es"]
"""


def _load(tmp_path: Path, body: str) -> FamilyRegistry:
    path = tmp_path / "preferences.toml"
    path.write_text(body, encoding="utf-8")
    return load_preferences(path).families


# --- S4: no families declared -> today's built-ins ----------------------------


def test_no_preferences_means_the_builtin_families() -> None:
    reg = DEFAULT_PREFS.families
    assert reg.ids == BUILTIN_REGISTRY.ids
    assert reg.ids == ("ai", "backend")
    assert reg.default.id == "ai"
    ai, backend = reg.families
    assert (ai.label, ai.role, ai.languages) == ("AI", "AI", ("es", "en", "ca"))
    assert ai.title_terms == AI_TITLE_TERMS
    assert ai.title_filter == DEFAULT_TITLE_FILTER
    assert (backend.label, backend.role, backend.languages) == (
        "Backend",
        "BACKEND",
        ("es", "en"),
    )
    assert backend.title_terms == ()
    assert backend.title_filter == BACKEND_TITLE_FILTER
    # None = the Settings gates (threshold / backend_threshold), as today.
    assert ai.threshold is None and backend.threshold is None


def test_builtins_carry_todays_search_roles_and_emphasis() -> None:
    ai, backend = DEFAULT_PREFS.families.families
    targets = DEFAULT_PREFS.search_targets
    emphasis = DEFAULT_PREFS.cover_letter.branch_emphasis
    assert ai.search_roles == targets.ai_roles
    assert backend.search_roles == targets.backend_roles
    assert ai.cover_letter_emphasis == emphasis["AI"]
    assert backend.cover_letter_emphasis == emphasis["BACKEND"]


def test_file_without_families_keeps_the_builtins(tmp_path: Path) -> None:
    reg = _load(tmp_path, '[gates]\nallowed_languages = ["es"]\n')
    assert reg == DEFAULT_PREFS.families


def test_title_filters_backend_maps_onto_the_builtin_backend(tmp_path: Path) -> None:
    body = '[title_filters.backend]\ninclude_groups = [["Python"]]\n'
    path = tmp_path / "preferences.toml"
    path.write_text(body, encoding="utf-8")
    prefs = load_preferences(path)
    backend = prefs.families.get("backend")
    assert backend is not None
    assert backend.title_filter == prefs.title_filters.backend
    assert backend.title_filter.include_groups == [["Python"]]
    ai = prefs.families.get("ai")
    assert ai is not None and ai.title_filter == DEFAULT_TITLE_FILTER


def test_search_targets_and_emphasis_map_onto_the_builtins(tmp_path: Path) -> None:
    reg = _load(
        tmp_path,
        '[search_targets]\nbackend_roles = ["Django Developer"]\n'
        '[cover_letter.branch_emphasis]\nAI = "Mine."\n',
    )
    ai, backend = reg.families
    assert backend.search_roles == ("Django Developer",)
    assert ai.cover_letter_emphasis == "Mine."


# --- S2: declared families ----------------------------------------------------


def test_declared_families_in_order_with_every_field(tmp_path: Path) -> None:
    reg = _load(tmp_path, _THREE)
    assert reg.ids == ("frontend", "marketing", "nursing")
    front = reg.families[0]
    assert front.label == "Frontend"
    assert front.role == "FRONTEND"
    assert front.languages == ("es", "en")
    assert front.title_terms == ("Frontend", "React", "Vue")
    assert front.threshold == 55
    assert front.search_roles == ("Frontend Developer", "React Developer")
    assert front.cover_letter_emphasis == "Frontend branch: emphasise UI work."
    assert front.title_filter.enabled is True
    assert front.title_filter.include_groups == [["Frontend", "React"]]
    assert front.title_filter.exclude_terms[0] == "Senior"
    nursing = reg.get("nursing")
    assert nursing is not None and nursing.label == "Enfermería"
    assert nursing.languages == ("es",)


def test_declared_families_replace_the_builtins(tmp_path: Path) -> None:
    reg = _load(tmp_path, _THREE)
    assert reg.get("ai") is None and reg.get("backend") is None


def test_exactly_one_default_family(tmp_path: Path) -> None:
    assert _load(tmp_path, _THREE).default.id == "frontend"
    body = _THREE.replace("default = true\n", "") + "default = true\n"
    assert _load(tmp_path, body).default.id == "nursing"
    # None marked: the first declared family is the default.
    assert _load(tmp_path, _THREE.replace("default = true\n", "")).default.id == (
        "frontend"
    )
    with pytest.raises(ProfileError, match="one default"):
        _load(tmp_path, _THREE + "default = true\n")


def test_minimal_family_defaults(tmp_path: Path) -> None:
    reg = _load(tmp_path, "[families.data_eng]\n")
    fam = reg.default
    assert (fam.id, fam.role, fam.label) == ("data_eng", "DATA_ENG", "data_eng")
    assert fam.languages == ("es", "en")
    assert fam.title_terms == () and fam.threshold is None
    assert fam.search_roles == () and fam.cover_letter_emphasis is None
    # No include groups: only the company blocklist filters.
    assert fam.title_filter.include_groups == []
    assert fam.title_filter.enabled is True


def test_declared_builtin_id_inherits_the_builtin_values(tmp_path: Path) -> None:
    reg = _load(tmp_path, "[families.backend]\nthreshold = 40\n[families.ai]\n")
    backend = reg.get("backend")
    assert backend is not None
    assert backend.threshold == 40
    assert backend.label == "Backend" and backend.languages == ("es", "en")
    assert backend.title_filter == BACKEND_TITLE_FILTER
    assert reg.ids == ("backend", "ai")


def test_lookup_by_id_or_role_case_insensitive(tmp_path: Path) -> None:
    reg = _load(tmp_path, _THREE)
    for value in ("marketing", "MARKETING", "Marketing", " marketing "):
        fam = reg.get(value)
        assert fam is not None and fam.id == "marketing"
    assert reg.get("sales") is None
    assert reg.get(None) is None


def test_normalize_unknown_and_none_to_the_default(tmp_path: Path) -> None:
    reg = _load(tmp_path, _THREE)
    assert reg.normalize("marketing") == "marketing"
    for bad in (None, "", "sales", 3, ["marketing"]):
        assert reg.normalize(bad) == "frontend"


def test_normalize_legacy_ds_to_ai() -> None:
    reg = BUILTIN_REGISTRY
    assert reg.normalize("ds") == "ai"
    assert reg.normalize("DS") == "ai"
    fam = reg.get("DS")
    assert fam is not None and fam.id == "ai"


def test_legacy_ds_without_an_ai_family_is_the_default(tmp_path: Path) -> None:
    assert _load(tmp_path, _THREE).normalize("DS") == "frontend"


def test_role_for(tmp_path: Path) -> None:
    reg = _load(tmp_path, _THREE)
    assert reg.role_for("marketing") == "MARKETING"
    assert reg.role_for(None) == "FRONTEND"
    assert reg.role_for("unknown") == "FRONTEND"


def test_infer_first_matching_family_else_catch_all(tmp_path: Path) -> None:
    reg = _load(tmp_path, _THREE)
    assert reg.infer("Senior React Engineer") == "frontend"
    assert reg.infer("Responsable de Márketing digital") == "marketing"
    assert reg.infer("SEO and React specialist") == "frontend"  # first declared
    # No family matches: the first family without title_terms (nursing).
    assert reg.infer("Enfermera de quirófano") == "nursing"
    assert reg.infer(None) == "frontend"
    assert reg.infer("   ") == "frontend"


def test_infer_without_a_catch_all_is_the_default(tmp_path: Path) -> None:
    reg = _load(
        tmp_path,
        '[families.a]\ntitle_terms = ["Alpha"]\n'
        '[families.b]\ntitle_terms = ["Beta"]\ndefault = true\n',
    )
    assert reg.infer("Gamma role") == "b"


@pytest.mark.parametrize(
    "body",
    [
        "families = 3\n",
        "[families.Front]\n",
        "[families.1st]\n",
        "[families.front-end]\n",
        "[families.x]\ncolour = 'red'\n",
        "[families.x]\nlabel = ''\n",
        "[families.x]\nlanguages = ['fr']\n",
        "[families.x]\nlanguages = []\n",
        "[families.x]\ntitle_terms = 'React'\n",
        "[families.x]\nthreshold = 'high'\n",
        "[families.x]\nthreshold = 101\n",
        "[families.x]\nthreshold = true\n",
        "[families.x]\nsearch_roles = [1]\n",
        "[families.x]\ncover_letter_emphasis = 2\n",
        "[families.x]\ndefault = 'yes'\n",
        "[families.x]\ntitle_filter = 1\n",
        "[families.x.title_filter]\nmode = 'strict'\n",
        "[families.x.title_filter]\ninclude_groups = ['React']\n",
        "[families]\n",
    ],
)
def test_invalid_families_fail_loudly(tmp_path: Path, body: str) -> None:
    with pytest.raises(ProfileError, match=r"preferences\.toml \[families"):
        _load(tmp_path, body)


def test_unknown_key_message_names_the_family(tmp_path: Path) -> None:
    with pytest.raises(ProfileError) as exc:
        _load(tmp_path, "[families.x]\ncolour = 'red'\n")
    assert str(exc.value) == "preferences.toml [families.x] unknown key(s): colour"


def test_family_title_filter_gets_the_company_blocklist(tmp_path: Path) -> None:
    reg = _load(
        tmp_path,
        '[title_filters]\ncompany_blocklist = ["Acme Staffing"]\n'
        '[families.x.title_filter]\nexclude_terms = ["Intern"]\n',
    )
    assert reg.default.title_filter == TitleFilter(
        enabled=True, include_groups=[], exclude_terms=["Intern", "Acme Staffing"]
    )


# --- S4/S5: cva/profile.py keeps every existing output ------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("ai", "ai"),
        ("backend", "backend"),
        (None, "ai"),
        ("", "ai"),
        ("Backend", "ai"),
        ("java", "ai"),
        (3, "ai"),
        (["backend"], "ai"),
        ("ds", "ai"),
        ("DS", "ai"),
    ],
)
def test_normalize_profile_unchanged(value: object, expected: str) -> None:
    assert normalize_profile(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("ai", "AI"), ("backend", "BACKEND"), (None, "AI"), ("x", "AI"), ("DS", "AI")],
)
def test_role_for_profile_unchanged(value: object, expected: str) -> None:
    assert role_for_profile(value) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("AI Engineer", "ai"),
        ("Ingeniero de Inteligencia Artificial", "ai"),
        ("INGENIERO IA", "ai"),
        ("Programador RAG", "ai"),
        ("Backend Developer Python", "backend"),
        ("Engineer maintaining said media", "backend"),
        ("Software Developer", "backend"),
        (None, "ai"),
        ("", "ai"),
        ("   ", "ai"),
    ],
)
def test_infer_profile_unchanged(title: str | None, expected: str) -> None:
    assert infer_profile(title) == expected
    assert BUILTIN_REGISTRY.infer(title) == expected


def test_has_ai_title_unchanged() -> None:
    for term in AI_TITLE_TERMS:
        assert has_ai_title(f"Senior {term} Engineer")
    assert not has_ai_title("Engineer maintaining said media")
    assert not has_ai_title(None)
    assert not has_ai_title("")
