"""backend-profile T2: offer profile persistence, title inference, backend gate
and the non-Python-stack wall (odd/tasks/backend-profile.md)."""

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook

from cva.claimsdb import load_claims
from cva.config import Settings
from cva.fetch import FetchResponse
from cva.match import OfferExtraction, Requirement
from cva.match.extraction import ExtractionCache
from cva.match.scorer import load_synonyms
from cva.profile import AI_TITLE_TERMS, has_ai_title, infer_profile, normalize_profile
from cva.service import JobService
from cva.tracker import OfferInput, OfferRepository
from cva.tracker.repo import _MIGRATION_V2, _SCHEMA_SQL, SCHEMA_VERSION
from cva.tracker.xlsx import export_all_xlsx
from tests.demo import DEMO_CLAIMS, DEMO_MASTERS, DEMO_SYNONYMS

_ROOT = Path(__file__).resolve().parent.parent
URL = "https://www.linkedin.com/jobs/view/4459804598/"


# --- profile helpers ----------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("AI Engineer", "ai"),
        ("Ingeniero de Inteligencia Artificial", "ai"),
        ("Software Engineer – AI", "ai"),
        ("ML Engineer (LLM)", "ai"),
        ("Machine Learning Developer", "ai"),
        ("INGENIERO IA", "ai"),
        ("Backend Developer Python", "backend"),
        ("Desarrollador Backend", "backend"),
        ("Software Developer", "backend"),
        ("Engineer maintaining said media", "backend"),  # ai/ia are whole words only
        ("Programador RAG", "ai"),
        ("Ingeniero Generative AI", "ai"),
        (None, "ai"),
        ("", "ai"),
        ("   ", "ai"),
    ],
)
def test_infer_profile_from_title(title: str | None, expected: str) -> None:
    assert infer_profile(title) == expected


def test_normalize_profile_defaults_unknown_to_ai() -> None:
    assert normalize_profile("backend") == "backend"
    assert normalize_profile("ai") == "ai"
    for bad in (None, "", "Backend", "java", 3, ["backend"]):
        assert normalize_profile(bad) == "ai"


def test_ai_terms_mirror_the_extension_ai_group() -> None:
    js = (_ROOT / "extension" / "filter.js").read_text(encoding="utf-8")
    for term in AI_TITLE_TERMS:
        assert f"'{term}'" in js


# --- migration ---------------------------------------------------------------


def test_migration_adds_nullable_perfil_to_an_old_schema_db(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(_SCHEMA_SQL)
    legacy.executescript(_MIGRATION_V2)
    legacy.execute(
        "INSERT INTO offers (source_data, estado) VALUES ('old text', 'analizada')"
    )
    legacy.execute("PRAGMA user_version = 2")
    legacy.commit()
    legacy.close()

    repo = OfferRepository(sqlite3.connect(path))
    assert SCHEMA_VERSION == 23
    assert repo._conn.execute("PRAGMA user_version").fetchone()[0] == 23
    assert repo.get_offer(1).perfil is None  # legacy rows stay NULL

    # Idempotent: reopening a migrated DB changes nothing and does not raise.
    repo2 = OfferRepository(sqlite3.connect(path))
    assert repo2.get_offer(1).perfil is None
    columns = [r[1] for r in repo2._conn.execute("PRAGMA table_info(offers)")]
    assert columns.count("perfil") == 1


def test_perfil_check_rejects_unknown_values(tmp_path: Path) -> None:
    repo = OfferRepository(sqlite3.connect(tmp_path / "a.db"))
    with pytest.raises(sqlite3.IntegrityError):
        repo._conn.execute(
            "INSERT INTO offers (source_data, perfil) VALUES ('x', 'Java')"
        )


def test_upsert_stores_profile_and_refresh_keeps_it(tmp_path: Path) -> None:
    repo = OfferRepository(sqlite3.connect(tmp_path / "a.db"))
    offer = OfferInput(
        title="Dev", company="A", url=URL, text="t", source="link", profile="backend"
    )
    offer_id = repo.upsert_offer(offer)
    assert repo.get_offer(offer_id).perfil == "backend"
    # A later refresh without a profile (re-ingest) must not wipe it.
    repo.upsert_offer(offer.model_copy(update={"profile": None, "text": "t2"}))
    row = repo.get_offer(offer_id)
    assert row.perfil == "backend" and row.source_data == "t2"


def test_set_profile_if_unset_never_overwrites(tmp_path: Path) -> None:
    repo = OfferRepository(sqlite3.connect(tmp_path / "a.db"))
    offer_id = repo.upsert_offer(
        OfferInput(title="Dev", url=URL, text="t", source="link")
    )
    repo.set_profile_if_unset(offer_id, "backend")
    repo.set_profile_if_unset(offer_id, "ai")
    assert repo.get_offer(offer_id).perfil == "backend"


def test_tracker_xlsx_has_profile_column(tmp_path: Path) -> None:
    repo = OfferRepository(sqlite3.connect(tmp_path / "a.db"))
    repo.upsert_offer(
        OfferInput(title="Dev", url=URL, text="t", source="link", profile="backend")
    )
    path = export_all_xlsx(repo, tmp_path / "t.xlsx")
    sheet = load_workbook(path)["Todas"]  # full list tab
    assert sheet is not None
    header = [c.value for c in sheet[1]]
    assert "perfil" in header and "source_data" not in header
    assert sheet.cell(row=2, column=header.index("perfil") + 1).value == "backend"


def test_offer_status_line_shows_profile() -> None:
    from types import SimpleNamespace

    from cva.notifier import offer_status_line

    base = {"id": 3, "estado": "analizada", "match": 70, "posicion": "Dev"}
    assert "[backend]" in offer_status_line(SimpleNamespace(**base, perfil="backend"))
    assert "[" not in offer_status_line(SimpleNamespace(**base, perfil="ai"))
    assert "[" not in offer_status_line(SimpleNamespace(**base, perfil=None))


# --- config ------------------------------------------------------------------


def test_config_defaults(clean_settings: Any) -> None:
    s: Settings = clean_settings()
    assert s.backend_threshold == 50
    assert s.backend_gray_zone is None  # None -> follows notify_gray_zone
    s2: Settings = clean_settings(notify_gray_zone=7)
    assert s2.backend_gray_zone is None


# --- stack wall --------------------------------------------------------------


def _req(name: str, must: bool = True) -> Requirement:
    return Requirement(name=name, category="skill", must_have=must, evidence=name)


def _ext(*reqs: Requirement) -> OfferExtraction:
    return OfferExtraction(title="Dev", company="A", requirements=list(reqs))


JAVA_TEXT = (
    "Backend Developer. Java 17 and Spring Boot. Strong Java skills, Java "
    "microservices, some scripting."
)


def test_wall_java_must_have_and_java_dominant_text() -> None:
    from cva.match.stack_wall import stack_wall_reason

    reason = stack_wall_reason(_ext(_req("Java"), _req("Spring Boot")), JAVA_TEXT)
    assert reason == "stack principal Java, no Python"


def test_wall_not_applied_when_text_mentions_python_a_lot() -> None:
    from cva.match.stack_wall import stack_wall_reason

    text = JAVA_TEXT + " Python Python automation with Python tooling."
    assert stack_wall_reason(_ext(_req("Java")), text) is None


def test_wall_never_when_python_is_must_have() -> None:
    from cva.match.stack_wall import stack_wall_reason

    assert stack_wall_reason(_ext(_req("Java"), _req("Python")), JAVA_TEXT) is None
    assert (
        stack_wall_reason(_ext(_req("Python/Django"), _req("Java")), JAVA_TEXT) is None
    )


def test_wall_needs_both_signals() -> None:
    from cva.match.stack_wall import stack_wall_reason

    # LLM says Java but the text barely mentions it.
    assert (
        stack_wall_reason(_ext(_req("Java")), "Backend developer, one Java note.")
        is None
    )
    # Text is Java-heavy but the LLM did not list a language as must-have.
    assert stack_wall_reason(_ext(_req("Spring Boot")), JAVA_TEXT) is None
    # Nice-to-have language is not a must-have.
    assert stack_wall_reason(_ext(_req("Java", must=False)), JAVA_TEXT) is None


def test_wall_full_stack_python_with_react_not_walled() -> None:
    from cva.match.stack_wall import stack_wall_reason

    text = (
        "Full Stack Python developer. Python, Django, React and TypeScript on the UI."
    )
    ext = _ext(_req("Python"), _req("React"), _req("TypeScript"))
    assert stack_wall_reason(ext, text) is None


def test_wall_other_languages_and_labels() -> None:
    from cva.match.stack_wall import stack_wall_reason

    cases = {
        ("C#", "We build in C# and .NET. C# C# daily; .NET core."): "C#/.NET",
        (
            "Node.js",
            "Node.js services. Node.js and JavaScript APIs, JavaScript.",
        ): "JavaScript/TypeScript",
        ("Golang", "Go services in Golang. We love Golang and Go."): "Go",
        ("PHP", "PHP and Laravel. Modern PHP, PHP 8."): "PHP",
    }
    for (req, text), label in cases.items():
        assert (
            stack_wall_reason(_ext(_req(req)), text)
            == f"stack principal {label}, no Python"
        )


def test_wall_java_does_not_match_javascript() -> None:
    from cva.match.stack_wall import stack_wall_reason

    text = "JavaScript JavaScript JavaScript developer"
    assert stack_wall_reason(_ext(_req("Java")), text) is None


# --- service: gate + wall ----------------------------------------------------


class _Llm:
    def __init__(self, reply: OfferExtraction) -> None:
        self.model = "m"
        self.reply = reply

    async def json_completion(
        self, system: str, user: str, schema: type[object]
    ) -> object:
        return self.reply


class _Fetcher:
    async def get(self, url: str) -> FetchResponse:  # pragma: no cover
        raise AssertionError("no fetch expected")


class _Notify:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def __call__(self, text: str, keyboard: Any) -> None:
        self.calls.append((text, keyboard))


def _service(
    tmp_path: Path, extraction: OfferExtraction, **kwargs: Any
) -> tuple[JobService, OfferRepository]:
    conn = sqlite3.connect(tmp_path / "app.db", check_same_thread=False)
    repo = OfferRepository(conn)
    db = load_claims(DEMO_CLAIMS)
    syn = load_synonyms(DEMO_SYNONYMS, db)
    svc = JobService(
        repo=repo,
        llm=_Llm(extraction),  # type: ignore[arg-type]
        fetcher=_Fetcher(),  # type: ignore[arg-type]
        db=db,
        syn=syn,
        cache=ExtractionCache(conn),
        masters_dir=DEMO_MASTERS,
        **kwargs,
    )
    return svc, repo


PY_TEXT = "Backend Developer. Imprescindible Python y containers. Kubernetes valorable."


def _py_extraction() -> OfferExtraction:
    return OfferExtraction(
        title="Backend Developer",
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


def _bridge_row(
    repo: OfferRepository,
    profile: str | None,
    text: str = PY_TEXT,
    location: str | None = None,
) -> int:
    return repo.upsert_offer(
        OfferInput(
            location=location,
            title="Backend Developer",
            company="Acme",
            url=URL,
            text=text,
            source="bridge-inline",
            profile=profile,
        )
    )


def _run(svc: JobService, offer_id: int, notify: _Notify, **kw: Any) -> Any:
    return asyncio.run(
        svc.analyze_new_bridge_job(
            offer_id,
            url=URL,
            title="Backend Developer",
            company="Acme",
            notify=notify,
            text=kw.pop("text", PY_TEXT),
            **kw,
        )
    )


def _probe_score(tmp_path: Path) -> int:
    svc, repo = _service(tmp_path / "probe", _py_extraction())
    oid = _bridge_row(repo, "ai")
    return asyncio.run(svc.analyze(oid)).score


@pytest.fixture()
def tmp_probe(tmp_path: Path) -> Path:
    (tmp_path / "probe").mkdir()
    return tmp_path


def test_backend_offer_uses_backend_gate_ai_offer_keeps_ai_gate(
    tmp_probe: Path,
) -> None:
    s = _probe_score(tmp_probe)
    assert 0 < s < 100
    # backend gate == score -> card; ai gate one above -> below/gray.
    (tmp_probe / "b").mkdir()
    svc, repo = _service(
        tmp_probe / "b",
        _py_extraction(),
        threshold=s + 1,
        backend_threshold=s,
        gray_zone=0,
    )
    oid = _bridge_row(repo, "backend")
    notify = _Notify()
    outcome = _run(svc, oid, notify)
    assert outcome.zone == "above" and len(notify.calls) == 1
    assert repo.latest_match(oid).threshold == s  # type: ignore[union-attr]

    (tmp_probe / "a").mkdir()
    svc2, repo2 = _service(
        tmp_probe / "a",
        _py_extraction(),
        threshold=s + 1,
        backend_threshold=s,
        gray_zone=0,
    )
    oid2 = _bridge_row(repo2, "ai")
    notify2 = _Notify()
    assert _run(svc2, oid2, notify2).zone == "below"
    assert notify2.calls == []


def test_legacy_null_profile_is_treated_as_ai(tmp_probe: Path) -> None:
    s = _probe_score(tmp_probe)
    (tmp_probe / "n").mkdir()
    svc, repo = _service(
        tmp_probe / "n",
        _py_extraction(),
        threshold=s + 1,
        backend_threshold=s,
        gray_zone=0,
    )
    oid = _bridge_row(repo, None)
    assert _run(svc, oid, _Notify()).zone == "below"


def test_backend_gray_zone_uses_backend_gray_zone(tmp_probe: Path) -> None:
    s = _probe_score(tmp_probe)
    (tmp_probe / "g").mkdir()
    svc, repo = _service(
        tmp_probe / "g",
        _py_extraction(),
        threshold=s + 20,
        backend_threshold=s + 3,
        gray_zone=0,
        backend_gray_zone=5,
    )
    oid = _bridge_row(repo, "backend")
    notify = _Notify()
    assert _run(svc, oid, notify).zone == "gray"
    assert len(notify.calls) == 1


def test_backend_gray_zone_defaults_to_gray_zone(tmp_probe: Path) -> None:
    s = _probe_score(tmp_probe)
    (tmp_probe / "d").mkdir()
    svc, repo = _service(
        tmp_probe / "d",
        _py_extraction(),
        threshold=s + 20,
        backend_threshold=s + 3,
        gray_zone=5,
    )
    oid = _bridge_row(repo, "backend")
    assert _run(svc, oid, _Notify()).zone == "gray"


def test_explicit_min_score_beats_backend_gate(tmp_probe: Path) -> None:
    s = _probe_score(tmp_probe)
    (tmp_probe / "m").mkdir()
    svc, repo = _service(
        tmp_probe / "m", _py_extraction(), backend_threshold=s, gray_zone=0
    )
    oid = _bridge_row(repo, "backend")
    assert _run(svc, oid, _Notify(), min_score=s + 1).zone == "below"


def _java_extraction() -> OfferExtraction:
    return OfferExtraction(
        title="Backend Developer",
        company="Acme",
        requirements=[
            Requirement(name="Java", category="skill", must_have=True, evidence="Java"),
            Requirement(
                name="PostgreSQL",
                category="tool",
                must_have=True,
                evidence="PostgreSQL",
            ),
        ],
    )


JAVA_OFFER = "Backend Developer. Java 17, Java Spring, Java microservices. PostgreSQL."


def test_walled_backend_offer_sends_no_card_and_records_reason(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    svc, repo = _service(tmp_path, _java_extraction(), backend_threshold=0, gray_zone=0)
    oid = _bridge_row(repo, "backend", JAVA_OFFER)
    notify = _Notify()
    with caplog.at_level("INFO", logger="cva.service"):
        outcome = _run(svc, oid, notify, text=JAVA_OFFER)
    assert outcome.zone == "walled" and notify.calls == []
    wall_logs = [
        r for r in caplog.records if r.levelname == "INFO" and "wall" in r.getMessage()
    ]
    assert any(
        f"#{oid}" in r.getMessage()
        and "stack principal Java, no Python" in r.getMessage()
        for r in wall_logs
    )
    result = asyncio.run(svc.analyze(oid))
    assert result.stack_wall == "stack principal Java, no Python"


def test_ai_profile_java_offer_is_not_wall_evaluated(tmp_path: Path) -> None:
    svc, repo = _service(tmp_path, _java_extraction(), threshold=0, gray_zone=0)
    oid = _bridge_row(repo, "ai", JAVA_OFFER)
    notify = _Notify()
    outcome = _run(svc, oid, notify, text=JAVA_OFFER)
    assert outcome.zone == "above" and len(notify.calls) == 1
    assert asyncio.run(svc.analyze(oid)).stack_wall is None


def test_manual_card_shows_wall_line(tmp_path: Path) -> None:
    from cva.notifier import judge_breakdown_message

    svc, repo = _service(tmp_path, _java_extraction())
    oid = _bridge_row(repo, "backend", JAVA_OFFER)
    result = asyncio.run(svc.analyze(oid))
    text = judge_breakdown_message(result, None, oid)
    assert "stack principal Java, no Python" in text


# --- ingest inference --------------------------------------------------------


def test_manual_paste_without_title_defaults_to_ai(tmp_path: Path) -> None:
    svc, repo = _service(tmp_path, _py_extraction())
    offer_id = asyncio.run(svc.ingest(None, "Backend Developer Python\nAcme\nPython"))
    assert repo.get_offer(offer_id).perfil == "ai"  # paste carries no title


def test_ingest_sets_profile_when_unset_only(tmp_path: Path) -> None:
    svc, repo = _service(tmp_path, _py_extraction())
    oid = _bridge_row(repo, "backend")  # bridge row with a payload profile
    repo._conn.execute(
        "UPDATE offers SET posicion = 'AI Engineer' WHERE id = ?", (oid,)
    )
    svc._ensure_profile(oid)
    assert repo.get_offer(oid).perfil == "backend"  # existing value wins
    other = repo.upsert_offer(
        OfferInput(title="AI Engineer", url=URL + "x", text="t", source="paste")
    )
    svc._ensure_profile(other)
    assert repo.get_offer(other).perfil == "ai"
    third = repo.upsert_offer(
        OfferInput(
            title="Backend Developer Python", url=URL + "y", text="t", source="paste"
        )
    )
    svc._ensure_profile(third)
    assert repo.get_offer(third).perfil == "backend"


def test_hybrid_wall_adds_hard_wall_and_discard_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cva.match.judge import OfferJudgment

    async def fake_judge(*_a: Any, **kw: Any) -> OfferJudgment:
        assert kw["threshold"] == 50  # backend threshold reaches the judge
        return OfferJudgment(
            score=80,
            verdict="apply",
            core_role="Backend Java",
            walls=[],
            cv_base="AI_ES",
            rationale="ok",
        )

    monkeypatch.setattr("cva.service.judge_offer", fake_judge)
    svc, repo = _service(tmp_path, _java_extraction(), match_mode="hybrid")
    oid = _bridge_row(repo, "backend", JAVA_OFFER)
    result = asyncio.run(svc.analyze(oid))
    assert result.stack_wall == "stack principal Java, no Python"
    judgment, _ = svc.latest_judgment(oid)
    assert judgment is not None and judgment.verdict == "discard"
    assert [(w.text, w.hard) for w in judgment.walls] == [
        ("stack principal Java, no Python", True)
    ]


# --- T3 carried advisories ---------------------------------------------------


def test_round_tally_counts_walled_offers_separately() -> None:
    from cva.bridge import _RoundTally
    from cva.notifier import round_summary_message

    tally = _RoundTally("backend-es", 3)
    tally.record("walled", 40, "Java Dev", "Acme", URL)
    tally.record("below", 20, "Dev", "Beta", URL)
    tally.record("above", 80, "Dev", "Gamma", URL)
    assert (tally.walled, tally.below, tally.above) == (1, 1, 1)
    assert [d.company for d in tally.discarded] == ["Beta"]  # walled are not "below"
    text = round_summary_message(
        beacon="backend-es",
        analyzed=3,
        above=1,
        gray=0,
        below=1,
        pending=0,
        discarded=tally.discarded,
        walled=1,
    )
    assert "1 descartadas por muro" in text
    assert "1 descartadas por umbral" in text


def test_round_summary_without_walled_is_unchanged() -> None:
    from cva.notifier import round_summary_message

    text = round_summary_message(
        beacon="b", analyzed=1, above=1, gray=0, below=0, pending=0, discarded=[]
    )
    assert "muro" not in text


@pytest.mark.parametrize(
    "python", ["Python3", "python3.11", "Python 3", "Python 3.12", "python"]
)
def test_wall_counts_python_version_spellings_as_python(python: str) -> None:
    from cva.match.stack_wall import stack_wall_reason

    # Python mentioned twice in the text -> not walled, even without a must-have.
    text = f"{JAVA_TEXT} We also use {python} and {python} for scripts."
    assert stack_wall_reason(_ext(_req("Java")), text) is None
    # As a must-have name it never walls.
    assert stack_wall_reason(_ext(_req(python), _req("Java")), JAVA_TEXT) is None


def test_wall_does_not_treat_python_lookalikes_as_python() -> None:
    from cva.match.stack_wall import stack_wall_reason

    text = f"{JAVA_TEXT} Also python35 and pythonic."
    assert (
        stack_wall_reason(_ext(_req("Java")), text) == "stack principal Java, no Python"
    )


def test_backend_threshold_default_has_a_single_source() -> None:
    import inspect

    from cva.config import DEFAULT_BACKEND_THRESHOLD

    assert DEFAULT_BACKEND_THRESHOLD == 50
    assert (
        Settings.model_fields["backend_threshold"].default == DEFAULT_BACKEND_THRESHOLD
    )
    param = inspect.signature(JobService.__init__).parameters["backend_threshold"]
    assert param.default == DEFAULT_BACKEND_THRESHOLD


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("AI Engineer Junior", True),
        ("ingeniero de ia", True),
        ("Ingeniero de Inteligencia Artificial", True),
        ("Machine-Learning Engineer", True),
        ("Backend Engineer", False),
        ("Maintainer", False),
        ("", False),
        ("   ", False),
        (None, False),  # unlike infer_profile, no title is NOT an AI title
    ],
)
def test_has_ai_title(title: str | None, expected: bool) -> None:
    assert has_ai_title(title) is expected
