"""On-demand salary web research (salary-estimate T3). No network, fake LLM."""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from openpyxl import load_workbook
from pydantic import BaseModel

import bot
from cva.claimsdb import load_claims
from cva.fetch import FetchResponse
from cva.llm import ClaudeCodeLlmClient, OpenAiCompatibleLlmClient, supports_web_search
from cva.match.extraction import ExtractionCache
from cva.match.salary_research import SalaryResearch, build_research_prompt, to_outcome
from cva.match.scorer import load_synonyms
from cva.service import JobService
from cva.tracker import OfferRepository
from cva.tracker.repo import SCHEMA_VERSION, OfferInput
from cva.tracker.xlsx import SYNCED_COLUMNS
from tests.demo import DEMO_CLAIMS, DEMO_MASTERS, DEMO_SYNONYMS

CLAIMS_PATH = DEMO_CLAIMS
SYNONYMS_PATH = DEMO_SYNONYMS
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)

GOOD = {
    "min": 30000,
    "max": 38000,
    "median": 34000,
    "currency": "EUR",
    "period": "annual",
    "sources": [
        {
            "publisher": "Fixture Survey",
            "url": "https://example.org/s",
            "date": "2026-03",
            "n": 120,
        }
    ],
    "confidence": "medium",
    "notes": "junior AI Barcelona",
}


# --- provider capability -------------------------------------------------------


class _Run:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def __call__(
        self, argv: list[str], stdin: bytes, timeout: float
    ) -> tuple[int, bytes, bytes]:
        self.calls.append(argv)
        return (
            0,
            json.dumps({"is_error": False, "structured_output": GOOD}).encode(),
            b"",
        )


def test_claude_code_web_completion_enables_only_web_tools() -> None:
    run = _Run()
    client = ClaudeCodeLlmClient(run=run)
    assert supports_web_search(client)
    result = asyncio.run(client.web_json_completion("sys", "user", SalaryResearch))
    assert result.median == 34000
    argv = run.calls[0]
    assert argv[argv.index("--tools") + 1] == "WebSearch,WebFetch"
    assert argv[argv.index("--allowedTools") + 1] == "WebSearch,WebFetch"
    assert "--strict-mcp-config" in argv


def test_plain_completion_still_has_no_tools() -> None:
    run = _Run()
    asyncio.run(ClaudeCodeLlmClient(run=run).json_completion("s", "u", SalaryResearch))
    argv = run.calls[0]
    assert argv[argv.index("--tools") + 1] == ""
    assert "--allowedTools" not in argv


def test_openai_compatible_client_cannot_search_the_web() -> None:
    client = OpenAiCompatibleLlmClient(base_url="http://x", api_key="k", model="m")
    assert not supports_web_search(client)
    assert not supports_web_search(object())


# --- validation ------------------------------------------------------------------


def _parsed(**override: object) -> SalaryResearch:
    return SalaryResearch.model_validate({**GOOD, **override})


def test_valid_research_is_ok() -> None:
    outcome = to_outcome(_parsed())
    assert outcome.status == "ok" and outcome.research is not None


@pytest.mark.parametrize(
    "override",
    [
        {"min": None, "max": None, "median": None},
        {"sources": []},
        {
            "sources": [
                {"publisher": "x", "url": "not-a-url", "date": "2026", "n": None}
            ]
        },
        {"confidence": "none"},
        {"currency": "USD"},
        {"min": 50000, "max": 30000},
        {"min": 500, "max": 900},
    ],
)
def test_unreliable_research_is_no_data(override: dict[str, object]) -> None:
    assert to_outcome(_parsed(**override)).status == "no_data"


def test_monthly_is_annualised_x12() -> None:
    outcome = to_outcome(_parsed(min=2500, max=3000, median=2750, period="monthly"))
    assert outcome.research is not None
    assert (outcome.research.min, outcome.research.max, outcome.research.median) == (
        30000,
        36000,
        33000,
    )


def test_prompt_names_company_position_city_level_and_forbids_fabrication() -> None:
    system, user = build_research_prompt(
        company="Acme", position="AI Engineer", city="Barcelona", level="junior"
    )
    assert "Acme" in user and "AI Engineer" in user and "Barcelona" in user
    assert "junior" in user
    assert "never invent" in system.lower() or "do not invent" in system.lower()
    assert "confidence" in system and "none" in system


# --- migration v8 ---------------------------------------------------------------------


def _old_db(path: Path, version: int = 7) -> None:
    repo = OfferRepository(sqlite3.connect(path))
    repo.upsert_offer(OfferInput(title="t", url="https://x/1", text="x", source="link"))
    repo._conn.execute("ALTER TABLE offers DROP COLUMN salario_investigado")
    repo._conn.execute("ALTER TABLE offers DROP COLUMN carta_presentacion")
    repo._conn.execute("ALTER TABLE offers DROP COLUMN fecha_alta")
    repo._conn.execute("ALTER TABLE offers DROP COLUMN fecha_publicacion")
    repo._conn.execute("ALTER TABLE offers DROP COLUMN fecha_limite")
    repo._conn.execute("DROP TABLE app_state")
    repo._conn.execute(f"PRAGMA user_version = {version}")
    repo._conn.commit()
    repo._conn.close()


def test_migration_v8_adds_nullable_column_to_old_db(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    _old_db(path)
    repo = OfferRepository(sqlite3.connect(path))
    assert SCHEMA_VERSION == 24
    assert repo._conn.execute("PRAGMA user_version").fetchone()[0] == 24
    assert repo.get_offer(1).salario_investigado is None
    repo.set_salary_research(1, '{"status": "ok"}')
    assert repo.get_offer(1).salario_investigado == '{"status": "ok"}'
    # reopening is a no-op
    assert OfferRepository(sqlite3.connect(path)).get_offer(1).salario_investigado


def test_migration_v8_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cva.tracker.repo as repo_mod

    path = tmp_path / "old.db"
    _old_db(path)
    broken = repo_mod._MIGRATION_V8.replace(
        "PRAGMA user_version = 8;",
        "SELECT * FROM no_such_table;\nPRAGMA user_version = 8;",
    )
    assert broken != repo_mod._MIGRATION_V8
    monkeypatch.setattr(repo_mod, "_MIGRATION_V8", broken)
    with pytest.raises(sqlite3.OperationalError):
        OfferRepository(sqlite3.connect(path))
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
    columns = [r[1] for r in conn.execute("PRAGMA table_info(offers)")]
    assert "salario_investigado" not in columns


def test_tracker_column_is_synced() -> None:
    assert "salario_investigado" in SYNCED_COLUMNS


# --- service + bot -----------------------------------------------------------------------


class _WebLlm:
    model = "fake"
    supports_web_search = True

    def __init__(
        self, payload: dict[str, object] | None = None, *, fail: bool = False
    ) -> None:
        self.payload = payload if payload is not None else GOOD
        self.fail = fail
        self.calls = 0
        self.last_user = ""

    async def json_completion(
        self, system: str, user: str, schema: type[BaseModel], *, model=None
    ):
        raise AssertionError("plain completion must not be used for research")

    async def web_json_completion(
        self, system: str, user: str, schema: type[BaseModel], *, model=None
    ):
        from cva.llm import LlmResponseError

        self.calls += 1
        self.last_user = user
        if self.fail:
            raise LlmResponseError("boom")
        return schema.model_validate(self.payload)


class _NoWebLlm:
    model = "fake"

    async def json_completion(self, *a, **k):
        raise AssertionError("must not be called")


class _Fetcher:
    async def get(self, url: str) -> FetchResponse:
        raise AssertionError("no fetch")


def _service(
    tmp_path: Path, llm: object, clock: list[datetime], xlsx: Path | None = None
):
    conn = sqlite3.connect(tmp_path / "app.db")
    repo = OfferRepository(conn)
    db = load_claims(CLAIMS_PATH)
    service = JobService(
        repo=repo,
        llm=llm,  # type: ignore[arg-type]
        fetcher=_Fetcher(),  # type: ignore[arg-type]
        db=db,
        syn=load_synonyms(SYNONYMS_PATH, db),
        cache=ExtractionCache(conn),
        masters_dir=DEMO_MASTERS,
        now_fn=lambda: clock[0],
        tracker_xlsx=xlsx,
    )
    oid = repo.upsert_offer(
        OfferInput(
            title="Junior AI Engineer",
            company="Acme",
            url="https://x/1",
            text="Oficina en Barcelona. Python.",
            source="link",
        )
    )
    return service, repo, oid


def test_research_persists_and_reuses_cache_same_day(tmp_path: Path) -> None:
    llm = _WebLlm()
    clock = [NOW]
    service, repo, oid = _service(tmp_path, llm, clock)
    first = asyncio.run(service.research_salary(oid))
    assert first.status == "ok" and not first.cached
    assert "Acme" in llm.last_user and "Barcelona" in llm.last_user
    stored = json.loads(repo.get_offer(oid).salario_investigado or "")
    assert stored["status"] == "ok" and stored["checked_at"] == "2026-10-10"
    second = asyncio.run(service.research_salary(oid))
    assert second.cached and second.research == first.research
    assert llm.calls == 1
    clock[0] = NOW + timedelta(days=1)
    third = asyncio.run(service.research_salary(oid))
    assert not third.cached and llm.calls == 2


def test_no_data_is_stored_and_rate_limited_too(tmp_path: Path) -> None:
    llm = _WebLlm(
        {**GOOD, "confidence": "none", "min": None, "max": None, "median": None}
    )
    service, repo, oid = _service(tmp_path, llm, [NOW])
    assert asyncio.run(service.research_salary(oid)).status == "no_data"
    assert asyncio.run(service.research_salary(oid)).cached
    assert llm.calls == 1
    assert (
        json.loads(repo.get_offer(oid).salario_investigado or "")["status"] == "no_data"
    )


def test_provider_without_web_search_is_unavailable_and_stores_nothing(
    tmp_path: Path,
) -> None:
    service, repo, oid = _service(tmp_path, _NoWebLlm(), [NOW])
    outcome = asyncio.run(service.research_salary(oid))
    assert outcome.status == "unavailable"
    assert repo.get_offer(oid).salario_investigado is None


def test_llm_failure_propagates_and_stores_nothing(tmp_path: Path) -> None:
    from cva.llm import LlmResponseError

    service, repo, oid = _service(tmp_path, _WebLlm(fail=True), [NOW])
    with pytest.raises(LlmResponseError):
        asyncio.run(service.research_salary(oid))
    assert repo.get_offer(oid).salario_investigado is None


def test_result_lands_in_synced_xlsx(tmp_path: Path) -> None:
    target = tmp_path / "tracker.xlsx"
    service, _repo, oid = _service(tmp_path, _WebLlm(), [NOW], xlsx=target)
    asyncio.run(service.research_salary(oid))
    sheet = load_workbook(target)["Todas"]  # full list tab
    header = [c.value for c in sheet[1]]
    assert "Fixture Survey" in str(
        sheet.cell(row=2, column=header.index("salario_investigado") + 1).value
    )


class _Msg:
    def __init__(self) -> None:
        self.chat_id = 5
        self.message_id = 11
        self.replies: list[str] = []

    async def reply_text(self, text: str, reply_markup: object = None) -> None:
        self.replies.append(text)


class _Query:
    def __init__(self, data: str) -> None:
        self.data = data
        self.message = _Msg()

    async def answer(self) -> None: ...


class _Update:
    def __init__(self, query: _Query) -> None:
        self.callback_query = query


class _Ctx:
    def __init__(self, service: JobService) -> None:
        self.bot_data = {bot._SERVICE_KEY: service, bot._OWNER_KEY: 5}
        self.user_data: dict[str, object] = {}


def _press(service: JobService, oid: int) -> list[str]:
    query = _Query(f"sal:{oid}")
    asyncio.run(bot.callback_router(_Update(query), _Ctx(service)))
    return query.message.replies


def test_button_replies_with_cited_range_then_cached(tmp_path: Path) -> None:
    llm = _WebLlm()
    service, _repo, oid = _service(tmp_path, llm, [NOW])
    replies = _press(service, oid)
    text = replies[-1]
    assert "30.000–38.000" in text and "Fixture Survey" in text
    assert "https://example.org/s" in text and "2026-03" in text and "n=120" in text
    assert "mediana 34.000" in text
    again = _press(service, oid)[-1]
    assert "guardado" in again and llm.calls == 1


def test_button_with_unsupported_provider_says_so_without_fabricating(
    tmp_path: Path,
) -> None:
    service, _repo, oid = _service(tmp_path, _NoWebLlm(), [NOW])
    assert (
        _press(service, oid)[-1]
        == "💶 Investigación salarial no disponible con este proveedor."
    )


def test_button_no_data_reply(tmp_path: Path) -> None:
    llm = _WebLlm({**GOOD, "confidence": "none", "sources": []})
    service, _repo, oid = _service(tmp_path, llm, [NOW])
    assert "sin datos fiables" in _press(service, oid)[-1].lower()


def test_button_llm_failure_replies_spanish_error(tmp_path: Path) -> None:
    service, _repo, oid = _service(tmp_path, _WebLlm(fail=True), [NOW])
    assert "falló" in _press(service, oid)[-1]


class _SlowWebLlm(_WebLlm):
    async def web_json_completion(self, system, user, schema, *, model=None):
        await asyncio.sleep(0.05)
        return await super().web_json_completion(system, user, schema, model=model)


def test_concurrent_presses_run_a_single_paid_research(tmp_path: Path) -> None:
    """Two presses before the first research finishes must not both call
    the web search: the second waits and reuses the stored result."""
    llm = _SlowWebLlm()
    service, _repo, oid = _service(tmp_path, llm, [NOW])

    async def both():
        return await asyncio.gather(
            service.research_salary(oid), service.research_salary(oid)
        )

    first, second = asyncio.run(both())
    assert llm.calls == 1
    assert {first.cached, second.cached} == {False, True}


def test_web_completion_gets_a_longer_timeout() -> None:
    from cva.llm import WEB_TIMEOUT_FLOOR_SECONDS, ClaudeCodeLlmClient

    client = ClaudeCodeLlmClient(timeout_seconds=30.0)
    assert client.web_timeout_seconds >= WEB_TIMEOUT_FLOOR_SECONDS > 30.0
