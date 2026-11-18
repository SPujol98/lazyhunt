"""Auto-triage tests: mirror hooks in JobService, analyze_new_bridge_job, and
the bridge card tree (offer-bridge + cv-mirror delta specs).

Layers: service-level integration over tmp SQLite + fake LLM/Fetcher (zero
network); bridge card-tree tests drive the REAL bridge endpoint on an
ephemeral loopback port with a recording bot on a live helper loop. The
mirror root lives under ``tmp_path``; mirror failures are warn-only.
"""

import asyncio
import logging
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from cva import bridge
from cva.claimsdb import load_claims
from cva.config import Settings
from cva.fetch import FetchResponse
from cva.ingest import job_id_from_url
from cva.llm import LlmResponseError
from cva.match import OfferExtraction, Requirement
from cva.match.extraction import ExtractionCache
from cva.match.scorer import load_synonyms
from cva.service import JobService
from cva.tracker import OfferInput, OfferRepository
from cva.wall import WallError, WallKind, WallVerdict
from tests.demo import DEMO_CLAIMS, DEMO_MASTERS, DEMO_SYNONYMS

_REPO_ROOT = Path(__file__).resolve().parent.parent
CLAIMS_PATH = DEMO_CLAIMS
SYNONYMS_PATH = DEMO_SYNONYMS
MASTERS_DIR = DEMO_MASTERS

OFFER_TEXT = (
    "AI Engineer en Acme (Barcelona). Imprescindible experiencia con Python. "
    "Se valoran conocimientos de PostgreSQL para las integraciones de datos. "
    "También gestionarás clusters de Kubernetes en producción. Ofrecemos "
    "formación continua y contrato indefinido."
)
OFFER_URL = "https://www.linkedin.com/jobs/view/4459804598/"
# Job-view URLs are fetched guest-endpoint-first (live production evidence):
# the fetch target for OFFER_URL, while the stored offer URL stays OFFER_URL.
GUEST_JOB_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4459804598"
JSONLD_BODY = (
    '<html><head><script type="application/ld+json">'
    '{"@type": "JobPosting", "title": "AI Engineer", '
    '"hiringOrganization": {"name": "Acme"}, '
    '"description": "Imprescindible experiencia con Python y Kubernetes."}'
    "</script></head></html>"
)

PDF_NAME = "Alex_Rivera_Ingeniero_IA.pdf"


class FakeLlmClient:
    """Call-counting stand-in for the LlmClient protocol (zero network)."""

    def __init__(self, reply: OfferExtraction) -> None:
        self.model = "test-model"
        self.calls = 0
        self._reply = reply

    async def json_completion(
        self, system: str, user: str, schema: type[object]
    ) -> object:
        self.calls += 1
        return self._reply


class FakeFetcher:
    """Canned fetcher counting calls — proves single-attempt semantics."""

    def __init__(self, response: FetchResponse) -> None:
        self._response = response
        self.calls: list[str] = []

    async def get(self, url: str) -> FetchResponse:
        self.calls.append(url)
        return self._response


def _extraction() -> OfferExtraction:
    return OfferExtraction(
        title="AI Engineer",
        company="Acme",
        requirements=[
            Requirement(
                name="Python",
                category="skill",
                must_have=True,
                evidence="experiencia con Python",
            ),
            Requirement(
                name="PostgreSQL",
                category="tool",
                must_have=False,
                evidence="conocimientos de PostgreSQL",
            ),
            Requirement(
                name="Kubernetes",
                category="tool",
                must_have=True,
                evidence="clusters de Kubernetes",
            ),
        ],
        keywords=["Python", "PostgreSQL", "Kubernetes", "Python"],
    )


def make_service(
    tmp_path: Path,
    llm: FakeLlmClient,
    fetcher: FakeFetcher,
    *,
    mirror_dir: Path | str | None = None,
    tracker_xlsx: Path | str | None = None,
) -> tuple[JobService, OfferRepository]:
    """Real JobService over tmp SQLite; check_same_thread=False so the
    bridge helper loop thread may run its use-cases (bridge tests)."""
    conn = sqlite3.connect(tmp_path / "app.db", check_same_thread=False)
    repo = OfferRepository(conn)
    db = load_claims(CLAIMS_PATH)
    syn = load_synonyms(SYNONYMS_PATH, db)
    service = JobService(
        repo=repo,
        llm=llm,
        fetcher=fetcher,
        db=db,
        syn=syn,
        cache=ExtractionCache(conn),
        masters_dir=MASTERS_DIR,
        mirror_dir=mirror_dir,
        tracker_xlsx=tracker_xlsx,
    )
    return service, repo


@pytest.fixture()
def hooked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Service + mirror plumbing: cwd isolated, mirror under tmp_path."""
    monkeypatch.chdir(tmp_path)
    llm = FakeLlmClient(_extraction())
    fetcher = FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    mirror_dir = tmp_path / "mirror"
    service, repo = make_service(tmp_path, llm, fetcher, mirror_dir=mirror_dir)
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    return {
        "tmp": tmp_path,
        "service": service,
        "repo": repo,
        "llm": llm,
        "fetcher": fetcher,
        "mirror_dir": mirror_dir,
        "offer_id": offer_id,
    }


# --- 3.x mirror hooks (cv-mirror spec) ---------------------------------------


def test_generate_cv_copies_pdf_to_nuevos(hooked: dict[str, Any]) -> None:
    """Spec: generate success → identical PDF in <mirror>/nuevos/; dirs made."""
    service = hooked["service"]
    pdf_path = asyncio.run(service.generate_cv(hooked["offer_id"], "AI"))
    mirrored = hooked["mirror_dir"] / "nuevos" / pdf_path.parent.name / pdf_path.name
    assert mirrored.is_file()
    assert mirrored.read_bytes() == pdf_path.read_bytes()
    assert pdf_path.is_file()  # adaptadas/ keeps the canonical original


def test_mark_sent_moves_mirror_pdf_to_enviados(hooked: dict[str, Any]) -> None:
    """Spec: mark_sent → file in enviados/, gone from nuevos/."""
    service = hooked["service"]
    offer_id = hooked["offer_id"]
    asyncio.run(service.analyze(offer_id))
    pdf_path = asyncio.run(service.generate_cv(offer_id, "AI"))
    folder = pdf_path.parent.name
    service.mark_sent(offer_id)
    assert (hooked["mirror_dir"] / "enviados" / folder / PDF_NAME).is_file()
    assert not (hooked["mirror_dir"] / "nuevos" / folder / PDF_NAME).exists()


def test_discard_removes_mirror_pdf_keeps_original(hooked: dict[str, Any]) -> None:
    """Spec: discard → gone from nuevos/; adaptadas/ original stays."""
    service = hooked["service"]
    offer_id = hooked["offer_id"]
    pdf_path = asyncio.run(service.generate_cv(offer_id, "AI"))
    service.discard(offer_id)
    assert not (
        hooked["mirror_dir"] / "nuevos" / pdf_path.parent.name / PDF_NAME
    ).exists()
    assert pdf_path.is_file()
    assert hooked["repo"].get_offer(offer_id).estado == "descartada"


def test_mirror_oserror_is_warn_only_action_completes(
    hooked: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """Spec: any mirror OSError → WARNING; every action keeps its result."""
    hooked["mirror_dir"].mkdir(parents=True)
    (hooked["mirror_dir"] / "nuevos").write_text("blocks mkdir", encoding="utf-8")
    service = hooked["service"]
    offer_id = hooked["offer_id"]
    with caplog.at_level(logging.WARNING, logger="cva.mirror"):
        pdf_path = asyncio.run(service.generate_cv(offer_id, "AI"))
        asyncio.run(service.analyze(offer_id))
        service.mark_sent(offer_id)
        other = asyncio.run(service.ingest(None, OFFER_TEXT))
        service.discard(other)  # separate offer: enviada refuses discard
    assert pdf_path.is_file()  # generate flow unaffected
    assert hooked["repo"].get_offer(offer_id).estado == "enviada"
    assert hooked["repo"].get_offer(other).estado == "descartada"
    row = hooked["repo"].get_offer(offer_id)
    assert row.match is not None and row.fecha_envio is not None
    assert caplog.records


def test_mirror_disabled_zero_fs_and_zero_warnings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Spec: cv_mirror_dir unset → zero mirror fs activity, zero warnings."""
    monkeypatch.chdir(tmp_path)
    llm = FakeLlmClient(_extraction())
    fetcher = FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    service, _repo = make_service(tmp_path, llm, fetcher, mirror_dir=None)
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    with caplog.at_level(logging.WARNING, logger="cva.mirror"):
        pdf_path = asyncio.run(service.generate_cv(offer_id, "AI"))
        asyncio.run(service.analyze(offer_id))
        service.mark_sent(offer_id)
        other = asyncio.run(service.ingest(None, OFFER_TEXT))
        service.discard(other)
    assert pdf_path.is_file()
    created = {p.name for p in tmp_path.iterdir()}
    assert "mirror" not in created
    assert caplog.records == []


def test_from_settings_wires_mirror_dir(tmp_path: Path) -> None:
    """D8: from_settings maps settings.cv_mirror_dir into the service seam."""
    settings = Settings(
        CVA_TELEGRAM_TOKEN="123456:TEST",
        CVA_LLM_API_KEY="sk-test",
        _env_file=None,
        toml_file=str(tmp_path / "absent.toml"),
        cv_mirror_dir=str(tmp_path / "wired"),
    )
    service = JobService.from_settings(settings, db_path=tmp_path / "s.db")
    assert service._mirror == tmp_path / "wired"


def test_from_settings_wires_analyze_retry_max_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CRITICAL-1: Settings default analyze_retry_max=5 reaches the service."""
    monkeypatch.chdir(tmp_path)  # no repo config.toml leaks into the test
    settings = Settings(
        CVA_TELEGRAM_TOKEN="123456:TEST",
        CVA_LLM_API_KEY="sk-test",
        _env_file=None,
    )
    service = JobService.from_settings(settings, db_path=tmp_path / "d.db")
    assert service.analyze_retry_max == 5


def test_from_settings_wires_analyze_retry_max_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Documented rollback: TOML analyze_retry_max = 0 disables the sweep."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text("analyze_retry_max = 0\n", encoding="utf-8")
    settings = Settings(
        CVA_TELEGRAM_TOKEN="123456:TEST",
        CVA_LLM_API_KEY="sk-test",
        _env_file=None,
    )
    service = JobService.from_settings(settings, db_path=tmp_path / "z.db")
    assert service.analyze_retry_max == 0


# --- 4.x analyze_new_bridge_job (offer-bridge delta, D1) ----------------------


class _RecordingNotify:
    """Async ``notify(text, keyboard)`` double recording every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def __call__(self, text: str, keyboard: Any) -> None:
        self.calls.append((text, keyboard))


class _WallFetcher(FakeFetcher):
    """Fetcher whose every get hits a LinkedIn login wall."""

    async def get(self, url: str) -> FetchResponse:
        self.calls.append(url)
        raise WallError(WallVerdict(WallKind.LOGIN_WALL, "auth wall detected"))


class _ExplodingLlm(FakeLlmClient):
    """LLM whose every completion fails (e.g. 429 after its single retry)."""

    def __init__(self, exc: Exception) -> None:
        super().__init__(_extraction())
        self._exc = exc

    async def json_completion(
        self, system: str, user: str, schema: type[object]
    ) -> object:
        self.calls += 1
        raise self._exc


def _bridged(
    tmp_path: Path,
    llm: FakeLlmClient,
    fetcher: FakeFetcher,
) -> tuple[JobService, OfferRepository, int]:
    """Service + a URL-only bridge row (exactly what _process_payload upserts)."""
    service, repo = make_service(tmp_path, llm, fetcher)
    offer_id = repo.upsert_offer(
        OfferInput(
            job_id=job_id_from_url(OFFER_URL),
            title="ML Engineer",
            company="Acme",
            url=OFFER_URL,
            text=f"bridge: Test\n{OFFER_URL}",
            source="link",
        )
    )
    return service, repo, offer_id


def test_analyze_new_bridge_job_success_sends_breakdown_once(
    tmp_path: Path,
) -> None:
    """Spec: ingest(url) refreshes the URL-only row, analyze scores it, and the
    injected notify receives breakdown_message + cv_keyboard exactly once."""
    llm = FakeLlmClient(_extraction())
    fetcher = FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    service, repo, offer_id = _bridged(tmp_path, llm, fetcher)
    notify = _RecordingNotify()

    asyncio.run(
        service.analyze_new_bridge_job(
            offer_id,
            url=OFFER_URL,
            title="ML Engineer",
            company="Acme",
            notify=notify,
        )
    )

    assert fetcher.calls == [GUEST_JOB_URL]  # bridge rows are URL-only: guest-first
    assert llm.calls == 1
    row = repo.get_offer(offer_id)
    assert row.estado == "analizada"
    assert "Imprescindible experiencia con Python" in (row.source_data or "")
    assert len(notify.calls) == 1
    text, keyboard = notify.calls[0]
    assert f"📊 Oferta #{offer_id}" in text
    assert "Python" in text
    assert keyboard.inline_keyboard[0][0].callback_data == f"cv:{offer_id}:AI:es"


def test_analyze_new_bridge_job_english_offer_preselects_english_cv(
    tmp_path: Path,
) -> None:
    """An English posting must arm [Generar CV] with EN: the button carries an
    explicit language, so a hardcoded ES default would override inference."""
    english_body = JSONLD_BODY.replace(
        "Imprescindible experiencia con Python y Kubernetes.",
        "You will own the AI flows in the product and the evaluation harness. "
        "We are looking for someone with real experience in Python and Kubernetes, "
        "and retrieval systems that worked with real users. "
        # The fake extraction's evidence quotes must stay verbatim in the text.
        "Imprescindible experiencia con Python y Kubernetes.",
    )
    llm = FakeLlmClient(_extraction())
    fetcher = FakeFetcher(FetchResponse(200, english_body, OFFER_URL))
    service, _repo, offer_id = _bridged(tmp_path, llm, fetcher)
    notify = _RecordingNotify()

    asyncio.run(
        service.analyze_new_bridge_job(
            offer_id,
            url=OFFER_URL,
            title="Founding AI Engineer",
            company="Acme",
            notify=notify,
        )
    )

    _text, keyboard = notify.calls[0]
    assert keyboard.inline_keyboard[0][0].callback_data == f"cv:{offer_id}:AI:en"


def test_analyze_new_bridge_job_wallerror_defers_without_card(
    tmp_path: Path,
) -> None:
    """Pacing D3: fetch failures (WallError) are SWALLOWED after retry
    bookkeeping — no exception escapes, the service never cards directly."""
    fetcher = _WallFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    service, repo, offer_id = _bridged(tmp_path, FakeLlmClient(_extraction()), fetcher)
    notify = _RecordingNotify()

    asyncio.run(  # must NOT raise
        service.analyze_new_bridge_job(
            offer_id,
            url=OFFER_URL,
            title="ML Engineer",
            company="Acme",
            notify=notify,
            min_score=None,
        )
    )

    # Guest attempt walls → single www-view fallback walls → WallError (cap 2).
    assert fetcher.calls == [GUEST_JOB_URL, OFFER_URL]
    assert notify.calls == []
    row = repo.get_offer(offer_id)
    assert row.analyze_attempts == 1
    assert row.next_analyze_at is not None


def test_analyze_new_bridge_job_llm_error_defers_without_card(
    tmp_path: Path,
) -> None:
    """Pacing D3: LLM failures (429 after retry) are swallowed after the
    fetch refresh; the row goes retry-pending with no card of any kind."""
    llm = _ExplodingLlm(LlmResponseError("LLM endpoint returned HTTP 429", status=429))
    fetcher = FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    service, repo, offer_id = _bridged(tmp_path, llm, fetcher)
    notify = _RecordingNotify()

    asyncio.run(  # must NOT raise
        service.analyze_new_bridge_job(
            offer_id,
            url=OFFER_URL,
            title="ML Engineer",
            company="Acme",
            notify=notify,
            min_score=None,
        )
    )

    assert fetcher.calls == [GUEST_JOB_URL]  # ingest ran; source_data is refreshed
    assert llm.calls == 1
    row = repo.get_offer(offer_id)
    assert row.source_data is not None
    assert row.analyze_attempts == 1
    assert row.next_analyze_at is not None
    assert notify.calls == []


# --- 5.3 bridge card tree (real endpoint + real JobService, auto_analyze) ------


AUTO_SECRET = "s3cret-auto"
AUTO_OWNER = 42


class _RecordingBot:
    """Duck-typed PTB bot recording send_message calls (no telegram import).

    Accepts the optional ``parse_mode`` kwarg (match-calibration D4) for
    consistency with the round-summary HTML send path — this module keeps
    ``round_summary=False`` throughout, so no call here ever sets it.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[Any, str, Any]] = []

    async def send_message(
        self,
        chat_id: Any,
        text: str,
        reply_markup: Any = None,
        *,
        parse_mode: str | None = None,
    ) -> bool:
        self.calls.append((chat_id, text, reply_markup))
        return True


class _ExplodingBot(_RecordingBot):
    """Bot whose every send fails — exercises the done-callback backstop."""

    async def send_message(
        self,
        chat_id: Any,
        text: str,
        reply_markup: Any = None,
        *,
        parse_mode: str | None = None,
    ) -> bool:
        raise RuntimeError("telegram exploded")


class _BlockingFetcher(FakeFetcher):
    """Fetcher that records the URL, then blocks until released."""

    def __init__(self) -> None:
        super().__init__(FetchResponse(200, JSONLD_BODY, OFFER_URL))
        self.release = threading.Event()

    async def get(self, url: str) -> FetchResponse:
        self.calls.append(url)
        self.release.wait(timeout=10)
        return self._response


class _BadLlm(FakeLlmClient):
    """LLM reply that fails schema parsing at the service boundary."""

    async def json_completion(
        self, system: str, user: str, schema: type[object]
    ) -> object:
        self.calls += 1
        return SimpleNamespace()  # no .requirements → extraction explodes


def _job_payload(job_id: str = "4200000000", beacon: str = "Barcelona AI") -> dict:
    return {
        "beacon": beacon,
        "jobs": [
            {
                "title": "ML Engineer",
                "company": "Acme",
                "location": "BCN",
                "url": f"https://www.linkedin.com/jobs/view/{job_id}/",
            }
        ],
    }


def _post_job(ns: Any, job_id: str = "4200000000") -> httpx.Response:
    return httpx.post(
        f"http://127.0.0.1:{ns.handle.port}/bridge",
        json=_job_payload(job_id),
        headers={"X-CVA-Bridge-Secret": AUTO_SECRET},
    )


def _wait_until(predicate: Any, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _service_on_default_db(
    llm: FakeLlmClient,
    fetcher: FakeFetcher,
    *,
    analyze_retry_max: int = 5,
) -> tuple[JobService, OfferRepository]:
    """JobService over ``data/app.db`` — the SAME file the bridge conn upserts."""
    default_db = Path("data") / "app.db"
    default_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(default_db, check_same_thread=False)
    repo = OfferRepository(conn)
    db = load_claims(CLAIMS_PATH)
    syn = load_synonyms(SYNONYMS_PATH, db)
    service = JobService(
        repo=repo,
        llm=llm,
        fetcher=fetcher,
        db=db,
        syn=syn,
        cache=ExtractionCache(conn),
        masters_dir=MASTERS_DIR,
        analyze_retry_max=analyze_retry_max,
    )
    return service, repo


@pytest.fixture()
def auto_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Any,
) -> Any:
    """Starts real bridges with a real JobService; tears every one down."""
    holders: list[Any] = []

    def _make(
        *,
        llm: FakeLlmClient,
        fetcher: FakeFetcher,
        auto_analyze: bool = True,
        post_init: bool = True,
        bot: _RecordingBot | None = None,
    ) -> Any:
        monkeypatch.chdir(tmp_path)
        settings = clean_settings(
            CVA_BRIDGE_SECRET=AUTO_SECRET,
            bridge_port=0,
            owner_telegram_id=AUTO_OWNER,
            auto_analyze=auto_analyze,
            analyze_delay_seconds=0,
            round_summary=False,  # pacing tested in test_bridge.py
        )
        service, repo = _service_on_default_db(llm, fetcher)
        bot = bot if bot is not None else _RecordingBot()
        app = SimpleNamespace(post_init=None, bot=bot, bot_data={})
        handle = bridge.register(app, settings, service)
        assert handle is not None
        loop = asyncio.new_event_loop()
        threading.Thread(
            target=loop.run_forever, name="auto-ptb-loop", daemon=True
        ).start()
        if post_init:
            asyncio.run_coroutine_threadsafe(app.post_init(app), loop).result(timeout=5)
        ns = SimpleNamespace(
            handle=handle,
            bot=bot,
            llm=llm,
            fetcher=fetcher,
            repo=repo,
            loop=loop,
            workdir=tmp_path,
        )
        holders.append(ns)
        return ns

    yield _make
    for ns in holders:
        asyncio.run_coroutine_threadsafe(ns.handle.shutdown(), ns.loop).result(
            timeout=5
        )
        ns.handle.stop()
        ns.loop.call_soon_threadsafe(ns.loop.stop)


def test_flag_on_sends_single_breakdown_card_no_analizar(
    auto_factory: Any,
) -> None:
    """Spec: flag on → 200 counters, one breakdown card, no [Analizar] card."""
    ns = auto_factory(
        llm=FakeLlmClient(_extraction()),
        fetcher=FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
    )
    resp = _post_job(ns)
    assert resp.status_code == 200
    assert resp.json() == {"received": 1, "new": 1, "skipped": 0}
    assert _wait_until(lambda: len(ns.bot.calls) == 1)
    _chat, text, keyboard = ns.bot.calls[0]
    assert "📊 Oferta #1" in text and "Python" in text
    assert "Analizar" not in text
    assert keyboard.inline_keyboard[0][0].callback_data == "cv:1:AI:es"
    assert ns.llm.calls == 1
    assert ns.repo.get_offer(1).estado == "analizada"


def test_200_precedes_analysis_blocking_fetcher(auto_factory: Any) -> None:
    """Spec: the 200 (with upsert counters) lands while the fetch is still
    blocked — the POST never waits for the analysis."""
    fetcher = _BlockingFetcher()
    ns = auto_factory(
        llm=FakeLlmClient(_extraction()),
        fetcher=fetcher,
    )
    try:
        resp = _post_job(ns, "4200000000")
        assert resp.status_code == 200
        assert resp.json() == {"received": 1, "new": 1, "skipped": 0}
        assert not fetcher.release.is_set()  # fetch incomplete; 200 already out
        assert ns.repo.get_offer(1) is not None  # row upserted before analysis
    finally:
        fetcher.release.set()
    assert _wait_until(lambda: len(ns.bot.calls) == 1)


def test_hanging_analysis_never_times_out_post(auto_factory: Any) -> None:
    """Spec: a hanging analysis leaves the 200 already delivered; no card yet."""
    fetcher = _BlockingFetcher()
    ns = auto_factory(
        llm=FakeLlmClient(_extraction()),
        fetcher=fetcher,
    )
    resp = _post_job(ns, "4200000000")  # default httpx timeouts apply
    assert resp.status_code == 200
    assert ns.bot.calls == []  # no card while the analysis hangs
    fetcher.release.set()  # let the loop drain for clean teardown
    assert _wait_until(lambda: len(ns.bot.calls) == 1)


def test_pipeline_isolation_n_plus_1_unaffected(auto_factory: Any) -> None:
    """Spec: N's analysis still running → N+1's upsert + 200 complete now."""
    fetcher = _BlockingFetcher()
    ns = auto_factory(
        llm=FakeLlmClient(_extraction()),
        fetcher=fetcher,
    )
    try:
        first = _post_job(ns, "4200000000")
        assert first.status_code == 200
        assert _wait_until(lambda: len(fetcher.calls) == 1)  # N's analysis started
        other = _job_payload("6600022222")
        other["jobs"][0]["title"] = "Distinct Role"  # same title would be a dup (T4)
        second = httpx.post(
            f"http://127.0.0.1:{ns.handle.port}/bridge",
            json=other,
            headers={"X-CVA-Bridge-Secret": AUTO_SECRET},
        )
        assert second.status_code == 200
        assert second.json() == {"received": 1, "new": 1, "skipped": 0}
        assert not fetcher.release.is_set()  # N STILL blocked; N+1 got its 200
    finally:
        fetcher.release.set()
    assert _wait_until(lambda: len(ns.bot.calls) == 2)


def test_wallerror_defers_card_row_is_retry_pending(
    auto_factory: Any,
) -> None:
    """Pacing spec: WallError → NO card yet; row retry-pending (offer never
    lost); a later [Analizar] press still completes manually."""
    ns = auto_factory(
        llm=FakeLlmClient(_extraction()),
        fetcher=_WallFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
    )
    resp = _post_job(ns, "5550001111")
    assert resp.status_code == 200
    asyncio.run_coroutine_threadsafe(asyncio.sleep(0.15), ns.loop).result(timeout=5)
    assert ns.bot.calls == []  # card DEFERRED to the sweep/final fallback
    row = ns.repo.get_offer(1)
    assert row.estado == "pendiente_analisis"  # offer never lost
    assert row.analyze_attempts == 1
    assert row.next_analyze_at is not None
    assert ns.llm.calls == 0  # fetch failed before any LLM call


def test_llm_429_exhausted_defers_card(auto_factory: Any) -> None:
    """Pacing spec: 429 after its retry → retry-pending; no breakdown card;
    no fallback card yet (the sweep drives the remaining attempts)."""
    ns = auto_factory(
        llm=_ExplodingLlm(
            LlmResponseError("LLM endpoint returned HTTP 429", status=429)
        ),
        fetcher=FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
    )
    resp = _post_job(ns)
    assert resp.status_code == 200
    asyncio.run_coroutine_threadsafe(asyncio.sleep(0.15), ns.loop).result(timeout=5)
    assert ns.bot.calls == []
    row = ns.repo.get_offer(1)
    assert row.estado == "pendiente_analisis"
    assert row.analyze_attempts == 1
    assert row.next_analyze_at is not None


def test_parse_error_defers_card_row_persists(auto_factory: Any) -> None:
    """Pacing spec: extraction parse failure → retry-pending, no card; row
    persists for the sweep."""
    ns = auto_factory(
        llm=_BadLlm(_extraction()),
        fetcher=FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
    )
    resp = _post_job(ns)
    assert resp.status_code == 200
    asyncio.run_coroutine_threadsafe(asyncio.sleep(0.15), ns.loop).result(timeout=5)
    assert ns.bot.calls == []
    row = ns.repo.get_offer(1)
    assert row is not None
    assert row.analyze_attempts == 1
    assert row.next_analyze_at is not None


def test_loop_not_captured_warns_offer_persists_no_card(
    auto_factory: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """D2: payload before post_init → warning, row persists, no card, no crash."""
    ns = auto_factory(
        llm=FakeLlmClient(_extraction()),
        fetcher=FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
        post_init=False,
    )
    with caplog.at_level(logging.WARNING, logger="cva.bridge"):
        resp = _post_job(ns)
    assert resp.status_code == 200
    time.sleep(0.05)
    assert ns.bot.calls == []
    assert ns.repo.get_offer(1) is not None
    assert "loop" in caplog.text.lower()


def test_duplicate_triggers_zero_extra_analysis(auto_factory: Any) -> None:
    """Spec: re-POSTed job_id → skipped silently; no analysis, no card."""
    ns = auto_factory(
        llm=FakeLlmClient(_extraction()),
        fetcher=FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
    )
    first = _post_job(ns)
    assert first.status_code == 200
    second = _post_job(ns)
    assert second.status_code == 200
    assert second.json() == {"received": 1, "new": 0, "skipped": 1}
    assert _wait_until(lambda: len(ns.bot.calls) == 1)
    asyncio.run_coroutine_threadsafe(asyncio.sleep(0.05), ns.loop).result(timeout=5)
    assert len(ns.bot.calls) == 1  # exactly the first job's breakdown card
    assert ns.llm.calls == 1
    assert len(ns.fetcher.calls) == 1


def test_card_send_failure_is_logged_by_done_callback(
    auto_factory: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """D2 backstop: a notify crash escapes the coroutine → future.exception()
    is WARNING-logged (task exceptions must never be silent)."""
    ns = auto_factory(
        llm=FakeLlmClient(_extraction()),
        fetcher=FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
        bot=_ExplodingBot(),
    )
    with caplog.at_level(logging.WARNING, logger="cva.bridge"):
        resp = _post_job(ns)
    assert resp.status_code == 200
    # the card-send escape is LOGGED (never silent); the worker survives
    assert _wait_until(lambda: "bridge auto-analysis failed" in caplog.text)


def test_flag_off_keeps_legacy_card_zero_analysis(auto_factory: Any) -> None:
    """Spec (regression guard): flag off → legacy [Analizar] card, zero fetch,
    zero LLM, no scheduled analysis — identical responses."""
    ns = auto_factory(
        llm=FakeLlmClient(_extraction()),
        fetcher=FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
        auto_analyze=False,
    )
    resp = _post_job(ns)
    assert resp.status_code == 200
    assert resp.json() == {"received": 1, "new": 1, "skipped": 0}
    assert len(ns.bot.calls) == 1
    _chat, text, keyboard = ns.bot.calls[0]
    assert "🔔 ML Engineer · Acme" in text
    assert keyboard.inline_keyboard[0][0].callback_data == "an:4200000000"
    assert ns.fetcher.calls == [] and ns.llm.calls == 0


def test_analyze_new_bridge_job_card_warns_about_extra_application_steps(
    tmp_path: Path,
) -> None:
    body = JSONLD_BODY.replace(
        "Imprescindible experiencia con Python y Kubernetes.",
        "Imprescindible experiencia con Python y Kubernetes. "
        "Si te encaja, remite tu CV a talent@acme-data.example.",
    )
    llm = FakeLlmClient(_extraction())
    fetcher = FakeFetcher(FetchResponse(200, body, OFFER_URL))
    service, _repo, offer_id = _bridged(tmp_path, llm, fetcher)
    notify = _RecordingNotify()

    asyncio.run(
        service.analyze_new_bridge_job(
            offer_id, url=OFFER_URL, title="ML Engineer", company="Acme", notify=notify
        )
    )

    text, _keyboard = notify.calls[0]
    assert "⚠️ Ojo: esta oferta pide algo más al aplicar" in text
    assert "talent@acme-data.example" in text


# --- "También en": card bookkeeping (quiet-rounds-also-on T2) -----------------------


class _IdNotify(_RecordingNotify):
    """Notify double that reports where the card landed, like the bridge."""

    async def __call__(self, text: str, keyboard: Any) -> Any:  # type: ignore[override]
        from cva.notifier import SentMessage

        self.calls.append((text, keyboard))
        return SentMessage(chat_id=9, message_id=100 + len(self.calls))


def _run_card(tmp_path: Path, notify: Any, *, before: Any = None) -> Any:
    llm = FakeLlmClient(_extraction())
    fetcher = FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    service, repo, offer_id = _bridged(tmp_path, llm, fetcher)
    if before is not None:
        before(repo, offer_id)
    asyncio.run(
        service.analyze_new_bridge_job(
            offer_id, url=OFFER_URL, title="ML Engineer", company="Acme", notify=notify
        )
    )
    return service, repo, offer_id


def test_sent_card_is_stored_with_its_message_id_text_and_keyboard(
    tmp_path: Path,
) -> None:
    notify = _IdNotify()
    _service, repo, offer_id = _run_card(tmp_path, notify)
    card = repo.get_card(offer_id)
    assert card is not None
    assert (card.chat_id, card.message_id) == (9, 101)
    assert card.text == notify.calls[0][0] and card.parse_mode is None
    assert card.keyboard is not None and f"cv:{offer_id}" in card.keyboard


def test_notify_without_a_message_id_stores_nothing(tmp_path: Path) -> None:
    _service, repo, offer_id = _run_card(tmp_path, _RecordingNotify())
    assert repo.get_card(offer_id) is None


def test_card_built_after_a_duplicate_is_known_includes_the_line(
    tmp_path: Path,
) -> None:
    def add_duplicate(repo: OfferRepository, offer_id: int) -> None:
        dup = repo.upsert_offer(
            OfferInput(
                title="ML Engineer",
                company="Acme",
                url="https://www.infojobs.net/x/y/of-0f8d7e6c5b4a39281706f5e4d3c2b1a0",
                text="x",
                source="link",
            )
        )
        repo.append_note(dup, f"duplicada de #{offer_id}")

    notify = _IdNotify()
    _service, repo, offer_id = _run_card(tmp_path, notify, before=add_duplicate)
    assert notify.calls[0][0].endswith("\n📍 También en: InfoJobs")
    card = repo.get_card(offer_id)
    assert card is not None and card.text.endswith("📍 También en: InfoJobs")


def test_service_also_on_and_forget_card(tmp_path: Path) -> None:
    service, repo, offer_id = _run_card(tmp_path, _IdNotify())
    assert service.also_on(offer_id) == []
    service.forget_card(9, 101)
    assert repo.get_card(offer_id) is None


def test_card_is_still_sent_when_the_also_on_lookup_fails(tmp_path: Path) -> None:
    def break_lookup(repo: OfferRepository, offer_id: int) -> None:
        def boom(_offer_id: int) -> list[str]:
            raise sqlite3.OperationalError("database is locked")

        repo.duplicate_portal_labels = boom  # type: ignore[method-assign]

    notify = _IdNotify()
    _service, _repo, _offer_id = _run_card(tmp_path, notify, before=break_lookup)
    assert len(notify.calls) == 1
    assert "También en" not in notify.calls[0][0]


def test_forget_card_never_raises_on_a_db_error(tmp_path: Path) -> None:
    service, repo, _offer_id = _run_card(tmp_path, _IdNotify())

    def boom(_chat_id: int, _message_id: int) -> None:
        raise sqlite3.OperationalError("database is locked")

    repo.forget_card = boom  # type: ignore[method-assign]
    service.forget_card(9, 101)  # logged, never propagated to the button press


# --- no-CV portals through the bridge auto path (cv-card-flow review) -------------


def _run_tecno_card(tmp_path: Path, *, min_score: int, gray_zone: int) -> Any:
    llm = FakeLlmClient(_extraction())
    fetcher = FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    service, repo = make_service(tmp_path, llm, fetcher)
    offer_text = (
        "ML Engineer en Acme. Imprescindible experiencia con Python y "
        "Kubernetes. Teletrabajo en España, contrato indefinido."
    )
    tecno_url = "https://www.tecnoempleo.com/ml-engineer/rf-a1b2c3d4e5f6a1b2c3d4"
    offer_id = repo.upsert_offer(
        OfferInput(
            job_id=job_id_from_url(OFFER_URL),
            title="ML Engineer",
            company="Acme",
            url=tecno_url,
            text=offer_text,
            source="link",
        )
    )
    service._gray_zone = gray_zone
    notify = _IdNotify()
    asyncio.run(
        service.analyze_new_bridge_job(
            offer_id,
            url=tecno_url,
            title="ML Engineer",
            company="Acme",
            notify=notify,
            min_score=min_score,
            text=offer_text,
        )
    )
    return notify


def _assert_no_cv_card(notify: Any) -> None:
    from cva.notifier import no_cv_line

    text, keyboard = notify.calls[0]
    assert text.count(no_cv_line("Tecnoempleo")) == 1
    kinds = [b.callback_data.split(":")[0] for r in keyboard.inline_keyboard for b in r]
    assert not {"cv", "lang", "variant"} & set(kinds)
    assert all("Generar CV" not in b.text for r in keyboard.inline_keyboard for b in r)


def test_tecnoempleo_judge_card_has_no_cv_line_or_button(tmp_path: Path) -> None:
    _assert_no_cv_card(_run_tecno_card(tmp_path, min_score=0, gray_zone=0))


def test_tecnoempleo_borderline_card_has_no_cv_line_or_button(tmp_path: Path) -> None:
    _assert_no_cv_card(_run_tecno_card(tmp_path, min_score=101, gray_zone=100))
