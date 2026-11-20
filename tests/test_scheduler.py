"""Scheduler tests (tasks 8b.3–8b.4): due rule, jitter, heartbeat, catch-up.

The heartbeat is a single PTB JobQueue job every 10 minutes whose first run is
the startup catch-up: due beacons run EXACTLY once (not once per missed slot)
because the sweep itself is the unit of catch-up. Zero network, zero real
Telegram: due/jitter are pure; registration and heartbeat use fake
app/context doubles; fetches go through an injected fake Fetcher.
"""

import asyncio
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import pytest

from cva.beacons import SweepResult
from cva.scheduler import (
    HEARTBEAT_INTERVAL_SECONDS,
    default_jitter,
    due,
    heartbeat,
    register,
)
from cva.service import JobService
from cva.tracker import BeaconRow, OfferInput, OfferRepository
from tests.demo import DEMO_CLAIMS, DEMO_SYNONYMS
from tests.fake_portal import FAKE_ID, FAKE_URL, register_fake_portal

FIXTURES = Path("tests/fixtures/beacons")
HEALTHY = (FIXTURES / "search_healthy.html").read_text(encoding="utf-8")
WALLED = (FIXTURES / "search_walled.html").read_text(encoding="utf-8")
SEARCH_URL = (
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPagePostings/search"
    "?keywords=ingeniero%20de%20ia&geoId=105088894"
)
NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
ZERO_JITTER: Callable[[], timedelta] = lambda: timedelta(0)


def beacon(**overrides: object) -> BeaconRow:
    base: dict[str, object] = {
        "id": 1,
        "name": "ia-espana",
        "url": SEARCH_URL,
        "next_check_after": None,
    }
    base.update(overrides)
    return BeaconRow.model_validate(base)


# --- due(): pure rule over the stored due time ---------------------------------


def test_never_checked_beacon_is_due() -> None:
    assert due(beacon(), NOW) is True


def test_due_boundary_now_equals_next_check_after() -> None:
    edge = beacon(next_check_after=NOW.isoformat())
    assert due(edge, NOW) is True
    assert due(edge, NOW - timedelta(seconds=1)) is False


def test_paused_and_disabled_beacons_are_never_due() -> None:
    past = (NOW - timedelta(hours=1)).isoformat()
    assert due(beacon(status="paused", next_check_after=past), NOW) is False
    assert due(beacon(enabled=False, next_check_after=past), NOW) is False


def test_degraded_beacon_becomes_due_after_backoff_window() -> None:
    past = (NOW - timedelta(minutes=1)).isoformat()
    assert due(beacon(status="degraded", next_check_after=past), NOW) is True


# --- default_jitter: ±30 min bounded randomness (design D11) --------------------


def test_default_jitter_stays_within_thirty_minutes_and_varies() -> None:
    span = timedelta(minutes=30)
    samples = [default_jitter() for _ in range(300)]
    assert all(-span <= sample <= span for sample in samples)
    assert len(set(samples)) > 2  # actually random, not a constant


# --- register(): one heartbeat job, first run = startup catch-up ----------------


class FakeJobQueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run_repeating(self, callback: object, **kwargs: object) -> None:
        self.calls.append({"callback": callback, **kwargs})


class _StubTrackerService:
    """Records the tracker wiring the bot's scheduler performs."""

    def __init__(self) -> None:
        self.deferred = False
        self.flushed = 0

    def enable_deferred_tracker_writes(self) -> None:
        self.deferred = True

    async def flush_tracker(self) -> None:
        self.flushed += 1


def test_register_defers_tracker_writes_and_flushes_them_on_shutdown() -> None:
    """Bot mode: mutations stop writing the xlsx on the loop, and PTB's
    post_shutdown writes whatever is still pending, after any earlier hook."""
    order: list[str] = []
    service = _StubTrackerService()

    async def previous(_app: object) -> None:
        order.append("previous")

    app = SimpleNamespace(bot_data={}, job_queue=FakeJobQueue(), post_shutdown=previous)
    register(app, service, owner_chat_id=5)  # type: ignore[arg-type]

    assert service.deferred is True
    asyncio.run(app.post_shutdown(app))
    assert order == ["previous"]
    assert service.flushed == 1


def test_register_schedules_exactly_one_heartbeat_starting_now() -> None:
    job_queue = FakeJobQueue()
    app = SimpleNamespace(bot_data={}, job_queue=job_queue)
    service = _StubTrackerService()  # register stores it + enables deferral
    register(app, service, owner_chat_id=5)  # type: ignore[arg-type]
    assert len(job_queue.calls) == 1
    call = job_queue.calls[0]
    assert call["callback"] is heartbeat
    assert call["interval"] == HEARTBEAT_INTERVAL_SECONDS == 600
    assert call["first"] == 0  # first tick = startup catch-up
    assert app.bot_data["owner_id"] == 5


# --- heartbeat callback: sweep → owner notifications/alerts (fakes) -------------


class RecordingFetcher:
    def __init__(self, body: str, status: int = 200) -> None:
        self.urls: list[str] = []
        self._body = body
        self._status = status

    async def get(self, url: str) -> object:
        self.urls.append(url)

        class Response:
            status = self._status
            body = self._body
            final_url = url

        return Response()


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, object]] = []

    async def send_message(
        self, chat_id: int, text: str, reply_markup: object = None
    ) -> None:
        self.sent.append((chat_id, text, reply_markup))


def make_service(
    conn: sqlite3.Connection,
    fetcher: RecordingFetcher,
    clock: Callable[[], datetime],
    ui_language: Literal["en", "es"] = "es",
) -> JobService:
    from cva.claimsdb import load_claims
    from cva.match import OfferExtraction
    from cva.match.extraction import ExtractionCache
    from cva.match.scorer import load_synonyms

    db = load_claims(DEMO_CLAIMS)

    class NoLlm:
        model = "test-model"

        async def json_completion(
            self, system: str, user: str, schema: type[object]
        ) -> object:
            return OfferExtraction(title=None, company=None)

    return JobService(
        repo=OfferRepository(conn),
        llm=NoLlm(),  # type: ignore[arg-type]
        fetcher=fetcher,
        db=db,
        syn=load_synonyms(DEMO_SYNONYMS, db),
        cache=ExtractionCache(conn),
        interval_minutes=480,
        checks_per_day=3,
        global_daily_cap=10,
        now_fn=clock,
        jitter_fn=ZERO_JITTER,
        ui_language=ui_language,
    )


def fake_context(service: JobService, owner: int, bot: FakeBot) -> SimpleNamespace:
    return SimpleNamespace(
        application=SimpleNamespace(bot_data={"service": service, "owner_id": owner}),
        bot=bot,
    )


def test_heartbeat_sends_new_job_cards_with_threshold_to_owner(tmp_path: Path) -> None:
    fetcher = RecordingFetcher(HEALTHY)
    service = make_service(sqlite3.connect(tmp_path / "hb.db"), fetcher, lambda: NOW)
    service.create_beacon("ia-espana", SEARCH_URL)
    bot = FakeBot()
    asyncio.run(heartbeat(fake_context(service, 5, bot)))
    assert len(bot.sent) == 3  # one card per new job
    chat_id, text, markup = bot.sent[0]
    assert chat_id == 5
    assert "Umbral Top-Choice: 65" in text  # service.threshold context
    assert "linkedin.com/jobs/view/4001122334" in text
    assert markup is not None  # [Analizar] inline keyboard


def test_heartbeat_catches_up_a_dirty_tracker_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetcher = RecordingFetcher(HEALTHY)
    service = make_service(sqlite3.connect(tmp_path / "hb4.db"), fetcher, lambda: NOW)
    calls: list[str] = []

    async def fake_retry() -> bool:
        calls.append("retry")
        return True

    monkeypatch.setattr(service, "retry_tracker_refresh", fake_retry)
    asyncio.run(heartbeat(fake_context(service, 5, FakeBot())))
    assert calls == ["retry"]


def test_heartbeat_pause_alert_sent_exactly_once_with_actions(
    tmp_path: Path,
) -> None:
    now = NOW
    fetcher = RecordingFetcher(WALLED, status=404)
    service = make_service(sqlite3.connect(tmp_path / "hb2.db"), fetcher, lambda: now)
    service.create_beacon("muro", SEARCH_URL)
    bot = FakeBot()
    for _ in range(3):
        asyncio.run(heartbeat(fake_context(service, 5, bot)))
        now += timedelta(hours=24)
    asyncio.run(heartbeat(fake_context(service, 5, bot)))  # paused: nothing more
    assert len(bot.sent) == 1  # exactly ONE alert ever
    _, text, markup = bot.sent[0]
    assert "pausado" in text
    payloads = [
        button.callback_data
        for row in markup.inline_keyboard  # type: ignore[union-attr]
        for button in row
    ]
    assert "beacon_resume:1" in payloads
    assert "beacon_disable:1" in payloads


def test_heartbeat_without_owner_sends_nothing(tmp_path: Path) -> None:
    fetcher = RecordingFetcher(HEALTHY)
    service = make_service(sqlite3.connect(tmp_path / "hb3.db"), fetcher, lambda: NOW)
    service.create_beacon("off", SEARCH_URL)
    bot = FakeBot()
    sweep = asyncio.run(heartbeat(fake_context(service, 0, bot)))
    assert bot.sent == []
    assert isinstance(sweep, SweepResult)  # sweep still ran


# --- catch-up: downtime across many slots → exactly one check per beacon --------


def test_catch_up_after_downtime_runs_each_due_beacon_once(tmp_path: Path) -> None:
    clock_now = NOW
    fetcher = RecordingFetcher(HEALTHY)
    service = make_service(
        sqlite3.connect(tmp_path / "cu.db"), fetcher, lambda: clock_now
    )
    service.create_beacon("uno", SEARCH_URL)
    service.create_beacon("dos", SEARCH_URL)
    asyncio.run(service.run_due_beacons())  # first-ever sweep: both checked
    clock_now += timedelta(days=2)  # ~6 missed 8h slots while "down"
    sweep = asyncio.run(service.run_due_beacons())
    assert len(fetcher.urls) == 4  # 2 + exactly ONE catch-up check per beacon
    assert len(sweep.notifications) == 0  # same page → diff dedupes to zero new
    clock_now += timedelta(minutes=1)
    asyncio.run(service.run_due_beacons())  # not due again: nothing extra
    assert len(fetcher.urls) == 4


# --- Heartbeat retry sweep (notification-pacing D4, post-beacon step) ----------


class FakeBridge:
    """Duck-typed bridge handle recording enqueue_auto_analysis calls."""

    def __init__(self) -> None:
        self.enqueued: list[tuple[int, str, dict[str, object], str]] = []

    def enqueue_auto_analysis(
        self,
        offer_id: int,
        job_id: str,
        job: dict[str, object],
        *,
        kind: str = "new",
    ) -> None:
        self.enqueued.append((offer_id, job_id, job, kind))


def _seed_pending(
    repo: OfferRepository,
    *,
    next_analyze_at: str,
    failures: int = 1,
    url: str = "https://linkedin.com/jobs/view/4123",
    title: str | None = "AI Engineer",
    company: str | None = "Acme",
) -> int:
    offer_id = repo.upsert_offer(
        OfferInput(
            job_id=url.rstrip("/").rsplit("/", 1)[-1],
            title=title,
            company=company,
            url=url,
            text="bridge: Test",
            source="link",
        )
    )
    for _ in range(failures):
        repo.record_analyze_failure(offer_id, next_analyze_at=next_analyze_at)
    return offer_id


def test_heartbeat_sweeps_due_retry_pending_into_bridge_queue(
    tmp_path: Path,
) -> None:
    """D4: post-beacon, the heartbeat re-enqueues ONLY due retry-pending rows
    (attempts ≤ max, next ≤ now) through the paced queue, kind="retry"."""
    conn = sqlite3.connect(tmp_path / "sweep.db", check_same_thread=False)
    service = make_service(conn, RecordingFetcher(HEALTHY), lambda: NOW)
    repo = OfferRepository(conn)
    # the heartbeat sweeps against the REAL wall clock — seed relative to it
    real_now = datetime.now(tz=UTC)
    past = (real_now - timedelta(minutes=1)).isoformat()
    future = (real_now + timedelta(hours=1)).isoformat()
    due_id = _seed_pending(repo, next_analyze_at=past)
    _seed_pending(
        repo, next_analyze_at=future, url="https://linkedin.com/jobs/view/4124"
    )  # not due yet
    _seed_pending(
        repo,
        next_analyze_at=past,
        failures=6,
        url="https://linkedin.com/jobs/view/4125",
    )  # exhausted (6 > max 5)

    bridge = FakeBridge()
    context = fake_context(service, 5, FakeBot())
    context.application.bot_data["bridge"] = bridge

    asyncio.run(heartbeat(context))

    assert [(offer_id, kind) for offer_id, _jid, _job, kind in bridge.enqueued] == [
        (due_id, "retry")
    ]
    offer_id, job_id, job, _kind = bridge.enqueued[0]
    assert job_id == "4123"
    assert job["url"] == "https://linkedin.com/jobs/view/4123"
    assert job["title"] == "AI Engineer"
    assert job["company"] == "Acme"
    assert "text" not in job  # LinkedIn rows keep the note→refetch lifecycle
    del offer_id


# --- D7 inline durability: sweep passes source_data only for infojobs rows -----


def _seed_infojobs_pending(
    repo: OfferRepository,
    *,
    next_analyze_at: str,
    source: Literal["link", "bridge-inline"] = "bridge-inline",
) -> int:
    """An inline InfoJobs row the bridge stored (D4): canonical normalized
    URL, real text in source_data, origen='infojobs'."""
    url = (
        "https://www.infojobs.net/barcelona/ingeniero-ia/of-"
        "0f8d7e6c5b4a39281706f5e4d3c2b1a0/"
    )
    offer_id = repo.upsert_offer(
        OfferInput(
            job_id="0f8d7e6c5b4a39281706f5e4d3c2b1a0",
            title="Ingeniero IA",
            company="Acme",
            url=url,
            text="Oferta completa inline para el reintento.",
            source=source,
        )
    )
    repo.record_analyze_failure(offer_id, next_analyze_at=next_analyze_at)
    return offer_id


def test_heartbeat_sweep_passes_inline_text_only_for_infojobs_rows(
    tmp_path: Path,
) -> None:
    """D7: the shutdown-deferred inline row re-analyzes WITHOUT the dead
    fetch — the sweep adds ``"text": row.source_data`` ONLY when
    ``origen == 'infojobs'``; LinkedIn rows never carry the key."""
    conn = sqlite3.connect(tmp_path / "sweep5.db", check_same_thread=False)
    service = make_service(conn, RecordingFetcher(HEALTHY), lambda: NOW)
    repo = OfferRepository(conn)
    real_now = datetime.now(tz=UTC)
    past = (real_now - timedelta(minutes=1)).isoformat()
    ij_id = _seed_infojobs_pending(repo, next_analyze_at=past)
    li_id = _seed_pending(repo, next_analyze_at=past)  # linkedin row

    bridge = FakeBridge()
    context = fake_context(service, 5, FakeBot())
    context.application.bot_data["bridge"] = bridge

    asyncio.run(heartbeat(context))

    jobs = {offer_id: job for offer_id, _jid, job, _k in bridge.enqueued}
    assert jobs[ij_id]["text"] == "Oferta completa inline para el reintento."
    assert "text" not in jobs[li_id]


def test_heartbeat_sweep_derives_infojobs_job_id_from_registry(
    tmp_path: Path,
) -> None:
    """Registry job_id (task 4.3): the enqueued id is the 32-char InfoJobs
    id, not the URL tail (``of-…``)."""
    conn = sqlite3.connect(tmp_path / "sweep6.db", check_same_thread=False)
    service = make_service(conn, RecordingFetcher(HEALTHY), lambda: NOW)
    repo = OfferRepository(conn)
    past = (datetime.now(tz=UTC) - timedelta(minutes=1)).isoformat()
    ij_id = _seed_infojobs_pending(repo, next_analyze_at=past)

    bridge = FakeBridge()
    context = fake_context(service, 5, FakeBot())
    context.application.bot_data["bridge"] = bridge

    asyncio.run(heartbeat(context))

    offer_id, job_id, job, kind = bridge.enqueued[0]
    assert offer_id == ij_id and kind == "retry"
    assert job_id == "0f8d7e6c5b4a39281706f5e4d3c2b1a0"
    assert job["url"] == (
        "https://www.infojobs.net/barcelona/ingeniero-ia/of-"
        "0f8d7e6c5b4a39281706f5e4d3c2b1a0/"
    )


def test_heartbeat_sweep_skips_sent_rows(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "sweep2.db", check_same_thread=False)
    service = make_service(conn, RecordingFetcher(HEALTHY), lambda: NOW)
    repo = OfferRepository(conn)
    past = (NOW - timedelta(minutes=1)).isoformat()
    sent_id = _seed_pending(repo, next_analyze_at=past)
    repo.set_estado(sent_id, "preparada")
    repo.mark_sent(sent_id, cv_path="adaptadas/x.pdf", cambios="c", match=70)

    bridge = FakeBridge()
    context = fake_context(service, 5, FakeBot())
    context.application.bot_data["bridge"] = bridge

    asyncio.run(heartbeat(context))
    assert bridge.enqueued == []


def test_heartbeat_without_bridge_is_a_noop_for_the_sweep(
    tmp_path: Path,
) -> None:
    """D9: no bridge in bot_data (endpoint disabled) → sweep step no-ops."""
    conn = sqlite3.connect(tmp_path / "sweep3.db", check_same_thread=False)
    service = make_service(conn, RecordingFetcher(HEALTHY), lambda: NOW)
    repo = OfferRepository(conn)
    _seed_pending(repo, next_analyze_at=(NOW - timedelta(minutes=1)).isoformat())
    context = fake_context(service, 5, FakeBot())  # no "bridge" key at all
    sweep = asyncio.run(heartbeat(context))
    assert isinstance(sweep, SweepResult)


def test_restart_redrive_never_reenqueues_cleared_rows(tmp_path: Path) -> None:
    """Durability: an offer whose final card already fired (state cleared) is
    invisible to the post-restart sweep — no duplicate cards, ever."""
    conn = sqlite3.connect(tmp_path / "sweep4.db", check_same_thread=False)
    service = make_service(conn, RecordingFetcher(HEALTHY), lambda: NOW)
    repo = OfferRepository(conn)
    past = (NOW - timedelta(minutes=1)).isoformat()
    done_id = _seed_pending(repo, next_analyze_at=past)
    repo.clear_analyze_retry(done_id)  # final card already sent before restart

    bridge = FakeBridge()
    context = fake_context(service, 5, FakeBot())
    context.application.bot_data["bridge"] = bridge

    asyncio.run(heartbeat(context))
    assert bridge.enqueued == []


def test_heartbeat_sweep_passes_inline_text_for_any_registered_portal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_fake_portal(monkeypatch)
    conn = sqlite3.connect(tmp_path / "sweep7.db", check_same_thread=False)
    service = make_service(conn, RecordingFetcher(HEALTHY), lambda: NOW)
    repo = OfferRepository(conn)
    past = (datetime.now(tz=UTC) - timedelta(minutes=1)).isoformat()
    offer_id = repo.upsert_offer(
        OfferInput(
            job_id=FAKE_ID,
            title="Ingeniero IA",
            company="Acme",
            url=FAKE_URL,
            text="Oferta de Otroportal para el reintento.",
            source="bridge-inline",
        )
    )
    repo.record_analyze_failure(offer_id, next_analyze_at=past)

    bridge = FakeBridge()
    context = fake_context(service, 5, FakeBot())
    context.application.bot_data["bridge"] = bridge

    asyncio.run(heartbeat(context))

    (queued,) = bridge.enqueued
    assert queued[1] == FAKE_ID
    assert queued[2]["text"] == "Oferta de Otroportal para el reintento."


def test_heartbeat_retries_link_ingested_infojobs_rows_by_refetching(
    tmp_path: Path,
) -> None:
    """Decided by the row's ingest source, not only origen: a row ingested via
    ``link`` is refetched (no inline ``text``) even for an inline-only portal."""
    conn = sqlite3.connect(tmp_path / "sweep7.db", check_same_thread=False)
    service = make_service(conn, RecordingFetcher(HEALTHY), lambda: NOW)
    repo = OfferRepository(conn)
    past = (datetime.now(tz=UTC) - timedelta(minutes=1)).isoformat()
    link_id = _seed_infojobs_pending(repo, next_analyze_at=past, source="link")

    bridge = FakeBridge()
    context = fake_context(service, 5, FakeBot())
    context.application.bot_data["bridge"] = bridge
    asyncio.run(heartbeat(context))

    jobs = {offer_id: job for offer_id, _jid, job, _k in bridge.enqueued}
    assert "text" not in jobs[link_id]


def test_heartbeat_legacy_rows_without_ingest_source_fall_back_to_origen(
    tmp_path: Path,
) -> None:
    conn = sqlite3.connect(tmp_path / "sweep8.db", check_same_thread=False)
    service = make_service(conn, RecordingFetcher(HEALTHY), lambda: NOW)
    repo = OfferRepository(conn)
    past = (datetime.now(tz=UTC) - timedelta(minutes=1)).isoformat()
    ij_id = _seed_infojobs_pending(repo, next_analyze_at=past)
    conn.execute("UPDATE offers SET ingest_source = NULL WHERE id = ?", (ij_id,))
    conn.commit()

    bridge = FakeBridge()
    context = fake_context(service, 5, FakeBot())
    context.application.bot_data["bridge"] = bridge
    asyncio.run(heartbeat(context))

    jobs = {offer_id: job for offer_id, _jid, job, _k in bridge.enqueued}
    assert jobs[ij_id]["text"] == "Oferta completa inline para el reintento."
