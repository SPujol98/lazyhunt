"""profile-extraction E3: ``<profile_dir>/preferences.toml`` drives the engine.

With no profile (or no file) every preference equals TODAY's hard-coded
constants (golden literals below, written independently of the module under
test). With a profile, each key overrides exactly its own preference; a
missing key keeps the engine default for that key only.
"""

import asyncio
import dataclasses
import sqlite3
from pathlib import Path

import pytest

from cva.claimsdb import load_claims
from cva.match import OfferExtraction, Requirement
from cva.match.extraction import ExtractionCache
from cva.match.gates import (
    DEFAULT_GATE_PREFS,
    GatePrefs,
    language_gate,
    location_gate,
    run_gates,
)
from cva.match.locations import REGIONS, UnknownRegionError, get_region, make_region
from cva.match.scorer import load_synonyms
from cva.match.stack_wall import (
    DEFAULT_STACK_WALL_PREFS,
    StackWallPrefs,
    stack_wall_reason,
)
from cva.profile_identity import ProfileError
from cva.profile_paths import ProfilePaths, resolve_profile_paths
from cva.profile_prefs import DEFAULT_PREFS, load_preferences
from cva.service import JobService
from cva.suggest import (
    DEFAULT_PROFILE_TARGETS,
    ProfileTargets,
    suggested_searches_payload,
)
from cva.title_filter import (
    BACKEND_TITLE_FILTER,
    DEFAULT_TITLE_FILTER,
    TitleFilter,
    passes_filter,
)
from tests.demo import DEMO_CLAIMS, DEMO_MASTERS, DEMO_SYNONYMS

_ROOT = Path(__file__).resolve().parent.parent

# --- golden literals: today's constants, copied independently ---------------

_WALL_LABELS = (
    "Java",
    "C#/.NET",
    "JavaScript/TypeScript",
    "Go",
    "PHP",
    "Ruby",
    "Scala",
    "Kotlin",
    "Swift",
    "C++",
)
_BLOCKLIST = (
    "Bending Spoons",
    "Crossing Hurdles",
    "Joinrs",
    "Evolve",
    "Hire Feed",
    "Quik Hire Staffing",
    "micro1",
    "BJAK",
)


def _prefs_file(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "preferences.toml"
    path.write_text(body, encoding="utf-8")
    return path


def _ext(*names: str) -> OfferExtraction:
    return OfferExtraction(
        title="Dev",
        company="A",
        requirements=[
            Requirement(name=n, category="skill", must_have=True, evidence=n)
            for n in names
        ],
    )


# --- defaults == today's constants -------------------------------------------


def test_default_prefs_equal_todays_constants() -> None:
    assert DEFAULT_PREFS.stack_wall.core_language == "Python"
    assert DEFAULT_PREFS.stack_wall.wall_languages is None
    assert DEFAULT_PREFS.gates.allowed_languages == ("es", "ca", "en")
    assert DEFAULT_PREFS.gates.flag_english_above_c1 is True
    assert DEFAULT_PREFS.gates.home_region == "catalonia"
    assert DEFAULT_PREFS.search_targets == ProfileTargets(
        ai_roles=("AI Engineer", "LLM Engineer"),
        backend_roles=("Backend Developer", "Python Developer"),
        locations=("Barcelona", "remoto"),
    )
    assert DEFAULT_PREFS.search_targets == DEFAULT_PROFILE_TARGETS
    assert DEFAULT_PREFS.title_filters.company_blocklist == _BLOCKLIST
    assert DEFAULT_PREFS.title_filters.ai == DEFAULT_TITLE_FILTER
    assert DEFAULT_PREFS.title_filters.backend == BACKEND_TITLE_FILTER
    assert DEFAULT_STACK_WALL_PREFS == DEFAULT_PREFS.stack_wall
    assert DEFAULT_GATE_PREFS == DEFAULT_PREFS.gates


def test_default_title_filters_keep_the_company_blocklist() -> None:
    for default in (DEFAULT_TITLE_FILTER, BACKEND_TITLE_FILTER):
        assert set(_BLOCKLIST) <= set(default.exclude_terms)
    assert DEFAULT_TITLE_FILTER.exclude_terms[-len(_BLOCKLIST) :] == list(_BLOCKLIST)


def test_unset_profile_has_no_preferences_file() -> None:
    assert resolve_profile_paths(None).preferences is None
    assert resolve_profile_paths(Path("/p")).preferences == Path("/p/preferences.toml")


def test_none_or_missing_file_means_defaults(tmp_path: Path) -> None:
    assert load_preferences(None) == DEFAULT_PREFS
    assert load_preferences(tmp_path / "preferences.toml") == DEFAULT_PREFS


def test_empty_file_means_defaults(tmp_path: Path) -> None:
    assert load_preferences(_prefs_file(tmp_path, "")) == DEFAULT_PREFS


# --- loader: per-key overrides, per-key defaults -----------------------------


def test_each_key_overrides_only_itself(tmp_path: Path) -> None:
    path = _prefs_file(
        tmp_path,
        """
[stack_wall]
core_language = "Java"
wall_languages = ["Python", "Go"]

[gates]
allowed_languages = ["es", "de"]
flag_english_above_c1 = false

[location]
home_region = "catalonia"

[search_targets]
ai_roles = ["ML Engineer"]
locations = ["Madrid"]

[title_filters]
company_blocklist = ["Acme Staffing"]
""",
    )

    prefs = load_preferences(path)

    assert prefs.stack_wall == StackWallPrefs(
        core_language="Java", wall_languages=("Python", "Go")
    )
    assert prefs.gates == GatePrefs(
        allowed_languages=("es", "de"),
        flag_english_above_c1=False,
        home_region="catalonia",
    )
    assert prefs.search_targets == ProfileTargets(
        ai_roles=("ML Engineer",),
        backend_roles=DEFAULT_PROFILE_TARGETS.backend_roles,  # missing key: default
        locations=("Madrid",),
    )
    assert prefs.title_filters.company_blocklist == ("Acme Staffing",)
    # Defaults swap their own blocklist terms for the profile's.
    for filt, default in (
        (prefs.title_filters.ai, DEFAULT_TITLE_FILTER),
        (prefs.title_filters.backend, BACKEND_TITLE_FILTER),
    ):
        assert "micro1" not in filt.exclude_terms
        assert "Acme Staffing" in filt.exclude_terms
        assert filt.include_groups == default.include_groups
        assert filt.enabled is True


def test_profile_title_filter_replaces_lists_and_gets_the_blocklist(
    tmp_path: Path,
) -> None:
    path = _prefs_file(
        tmp_path,
        """
[title_filters]
company_blocklist = ["Acme Staffing"]

[title_filters.ai]
include_groups = [["Data"], ["Engineer", "Scientist"]]
exclude_terms = ["Intern"]
""",
    )

    ai = load_preferences(path).title_filters.ai

    assert ai == TitleFilter(
        enabled=True,
        include_groups=[["Data"], ["Engineer", "Scientist"]],
        exclude_terms=["Intern", "Acme Staffing"],
    )
    # The other profile keeps the engine default (blocklist swapped).
    assert load_preferences(path).title_filters.backend.include_groups == (
        BACKEND_TITLE_FILTER.include_groups
    )


def test_disabled_profile_filter(tmp_path: Path) -> None:
    path = _prefs_file(tmp_path, "[title_filters.backend]\nenabled = false\n")

    assert load_preferences(path).title_filters.backend.enabled is False


@pytest.mark.parametrize(
    "body",
    [
        "not toml = [",
        "[nope]\nx = 1\n",
        "[stack_wall]\nbogus = 1\n",
        '[stack_wall]\ncore_language = "Cobol"\n',
        '[stack_wall]\nwall_languages = ["Cobol"]\n',
        "[stack_wall]\ncore_language = 3\n",
        '[gates]\nallowed_languages = ["xx"]\n',
        "[gates]\nallowed_languages = []\n",
        '[gates]\nflag_english_above_c1 = "yes"\n',
        '[location]\nhome_region = "atlantis"\n',
        '[search_targets]\nai_roles = "AI Engineer"\n',
        "[search_targets]\nlocations = []\n",
        "[title_filters]\ncompany_blocklist = [1]\n",
        '[title_filters.ai]\ninclude_groups = ["Engineer"]\n',
        "[title_filters.fullstack]\nenabled = true\n",
    ],
)
def test_invalid_preferences_fail_loudly(tmp_path: Path, body: str) -> None:
    with pytest.raises(ProfileError):
        load_preferences(_prefs_file(tmp_path, body))


# --- stack wall --------------------------------------------------------------

_JAVA_TEXT = "Backend Developer. Java 17 and Spring Boot. Java skills, Java services."
_PYTHON_TEXT = "Backend Developer. Python 3, Python services, Python tooling."


def test_stack_wall_defaults_unchanged() -> None:
    assert (
        stack_wall_reason(_ext("Java"), _JAVA_TEXT) == "stack principal Java, no Python"
    )
    assert stack_wall_reason(_ext("Python"), _PYTHON_TEXT) is None


def test_stack_wall_core_language_from_the_profile() -> None:
    prefs = StackWallPrefs(core_language="Java")

    assert stack_wall_reason(_ext("Java"), _JAVA_TEXT, prefs) is None
    assert (
        stack_wall_reason(_ext("Python"), _PYTHON_TEXT, prefs)
        == "stack principal Python, no Java"
    )


def test_stack_wall_only_the_listed_wall_languages() -> None:
    prefs = StackWallPrefs(core_language="Python", wall_languages=("Go",))

    assert stack_wall_reason(_ext("Java"), _JAVA_TEXT, prefs) is None
    go_text = "Backend. Go services in Go, more Go, Go everywhere."
    assert (
        stack_wall_reason(_ext("Go"), go_text, prefs) == "stack principal Go, no Python"
    )


# --- language gate -----------------------------------------------------------

_GERMAN = "Developer role. Alemán imprescindible."
_ENGLISH_REQ = "Developer role. English C1 imprescindible."
_C2 = "Developer role. English C2 required."


def test_language_gate_default_reason_text_unchanged() -> None:
    result = language_gate(_GERMAN)
    assert result.verdict == "FAIL"
    assert result.reason == "Exige alemán (idioma obligatorio fuera de ES/CA/EN)"
    assert (
        language_gate(_C2).reason == "Pide nivel de inglés por encima de C1 (C2/nativo)"
    )


def test_language_gate_allowed_languages_from_the_profile() -> None:
    prefs = GatePrefs(allowed_languages=("es", "ca", "en", "de"))

    assert language_gate(_GERMAN, prefs).verdict == "PASS"
    # French is still foreign; the reason lists the profile's own languages.
    fr = language_gate("Developer role. Francés imprescindible.", prefs)
    assert fr.verdict == "FAIL"
    assert fr.reason == "Exige francés (idioma obligatorio fuera de ES/CA/EN/DE)"


def test_language_gate_a_language_the_profile_does_not_speak_fails() -> None:
    prefs = GatePrefs(allowed_languages=("es", "en"))

    assert language_gate("Developer. Catalán imprescindible.").verdict == "PASS"
    result = language_gate("Developer. Catalán imprescindible.", prefs)
    assert result.verdict == "FAIL"
    assert "catalán" in result.reason
    assert result.reason.endswith("(idioma obligatorio fuera de ES/EN)")
    # English not spoken: required English fails.
    no_english = GatePrefs(allowed_languages=("es", "ca"))
    assert language_gate(_ENGLISH_REQ, no_english).verdict == "FAIL"
    assert language_gate(_ENGLISH_REQ).verdict == "PASS"


def test_language_gate_english_level_rule_can_be_switched_off() -> None:
    off = GatePrefs(flag_english_above_c1=False)

    assert language_gate(_C2).verdict == "FLAG"
    assert language_gate(_C2, off).verdict == "PASS"


def test_default_language_patterns_equal_the_old_table() -> None:
    from cva.match import gates

    assert set(gates._language_patterns(("es", "ca", "en"))) == {
        "alemán", "francés", "italiano", "portugués", "neerlandés", "polaco",
        "sueco", "danés", "noruego", "finés", "árabe", "chino", "japonés",
        "ruso", "turco", "hebreo", "checo", "rumano", "griego", "húngaro",
    }  # fmt: skip


# --- location: home region ---------------------------------------------------


def test_default_region_is_catalonia() -> None:
    assert location_gate("Puesto presencial.", "Madrid").reason == (
        "Presencial fuera de Cataluña (Madrid)"
    )
    assert location_gate("Puesto presencial.", "Girona").verdict == "PASS"


def test_unknown_home_region_is_a_clear_error() -> None:
    with pytest.raises(UnknownRegionError, match="atlantis.*catalonia"):
        get_region("atlantis")


def test_home_region_selects_the_place_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    madrid = make_region(
        "madrid-test",
        label="Madrid",
        inside="Madrid, Alcalá de Henares",
        outside="Barcelona, Girona, London",
    )
    monkeypatch.setitem(REGIONS, "madrid-test", madrid)
    prefs = GatePrefs(home_region="madrid-test")

    barcelona = location_gate("Puesto presencial.", "Barcelona", prefs)
    assert barcelona.verdict == "FAIL"
    assert barcelona.reason == "Presencial fuera de Madrid (Barcelona)"
    assert location_gate("Puesto presencial.", "Madrid", prefs).verdict == "PASS"
    assert location_gate("Puesto presencial.", "Barcelona").verdict == "PASS"
    assert [g.gate for g in run_gates("Presencial.", "Madrid", prefs)] == [
        "eligibility",
        "language",
        "location",
    ]


# --- search targets ----------------------------------------------------------


def test_suggested_searches_default_targets_unchanged() -> None:
    claims = DEMO_CLAIMS
    searches = suggested_searches_payload(claims)

    assert {s["location"] for s in searches} <= {"Barcelona", "remoto"}
    assert "AI Engineer" in {s["keywords"] for s in searches}


def test_suggested_searches_use_the_profile_targets(tmp_path: Path) -> None:
    prefs = load_preferences(
        _prefs_file(
            tmp_path,
            '[search_targets]\nai_roles = ["ML Engineer"]\n'
            'backend_roles = ["Data Engineer"]\nlocations = ["Madrid"]\n',
        )
    )
    claims = DEMO_CLAIMS

    searches = suggested_searches_payload(claims, prefs.search_targets)

    assert {s["location"] for s in searches} == {"Madrid"}
    keywords = {s["keywords"] for s in searches}
    assert {"ML Engineer", "Data Engineer"} <= keywords
    assert "AI Engineer" not in keywords


# --- title filter through the service (rejudge) ------------------------------


def _service(tmp_path: Path, preferences: Path | None) -> JobService:
    from cva.tracker import OfferRepository

    conn = sqlite3.connect(tmp_path / "app.db")
    db = load_claims(DEMO_CLAIMS)
    paths: ProfilePaths = dataclasses.replace(
        resolve_profile_paths(None), preferences=preferences
    )

    class _Llm:
        pass

    return JobService(
        repo=OfferRepository(conn),
        llm=_Llm(),  # type: ignore[arg-type]
        fetcher=None,  # type: ignore[arg-type]
        db=db,
        syn=load_synonyms(DEMO_SYNONYMS, db),
        cache=ExtractionCache(conn),
        masters_dir=DEMO_MASTERS,
        profile_paths=paths,
    )


def _seed(service: JobService, title: str, company: str, n: int) -> None:
    from cva.tracker import OfferInput

    service._repo.upsert_offer(
        OfferInput(
            title=title,
            company=company,
            url=f"https://x/{n}",
            text="Descripción del puesto. " * 40,
            source="link",
        )
    )


def test_rejudge_title_filter_uses_the_profile_filter(tmp_path: Path) -> None:
    prefs = _prefs_file(
        tmp_path,
        '[title_filters.ai]\ninclude_groups = [["Data"]]\nexclude_terms = ["Intern"]\n',
    )
    (tmp_path / "default").mkdir()
    (tmp_path / "custom").mkdir()
    default = _service(tmp_path / "default", None)
    custom = _service(tmp_path / "custom", prefs)
    for svc in (default, custom):
        _seed(svc, "Data Analyst", "Acme", 1)
        _seed(svc, "Data Intern", "Acme", 2)

    default_result = asyncio.run(
        default.rejudge_backlog(dry_run=True, title_filter=True)
    )
    custom_result = asyncio.run(custom.rejudge_backlog(dry_run=True, title_filter=True))

    assert default_result.total_candidates == 0  # no AI term: default filter drops both
    assert custom_result.total_candidates == 1  # "Data Analyst" only
    assert custom_result.skipped_title_filter == 1


def test_service_without_a_preferences_file_uses_defaults(tmp_path: Path) -> None:
    service = _service(tmp_path, None)

    # The families keep only the languages with a base CV in the profile's
    # manifest (cv-languages-from-manifest); everything else is the default.
    from dataclasses import replace

    from cva.cvgen.base_pdfs import available_registry

    expected = replace(
        DEFAULT_PREFS,
        families=available_registry(DEFAULT_PREFS.families, service._base_pdfs_dir),
    )
    assert service.preferences == expected


def test_passes_filter_backend_default_matches_the_extension_defaults() -> None:
    assert passes_filter("Backend Developer", "Acme", BACKEND_TITLE_FILTER) is True
    assert (
        passes_filter("Senior Backend Developer", "Acme", BACKEND_TITLE_FILTER) is False
    )
    assert passes_filter("Backend Developer", "micro1", BACKEND_TITLE_FILTER) is False
