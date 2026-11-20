"""Global kill switch (safe-defaults S2): persistent pause, bot side.

The pause lives in ``app_state`` (durable, survives restarts); the Telegram
toggle is the source of truth at runtime and ``Settings.paused`` is only the
default used until someone toggles it.
"""

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cva import bridge
from cva.scheduler import GMAIL_KEY, heartbeat
from cva.tracker import OfferInput, OfferRepository
from tests.service_stub import service_stub
from tests.test_scheduler import (
    HEALTHY,
    NOW,
    SEARCH_URL,
    FakeBot,
    FakeBridge,
    RecordingFetcher,
    _seed_pending,
    fake_context,
    make_service,
)


def _repo(path: Path) -> OfferRepository:
    return OfferRepository(sqlite3.connect(path, check_same_thread=False))


# --- persistence: app_state, default, toggle wins ----------------------------


def test_pause_defaults_to_not_paused(tmp_path: Path) -> None:
    assert _repo(tmp_path / "a.db").is_paused() is False


def test_pause_survives_a_new_connection(tmp_path: Path) -> None:
    _repo(tmp_path / "a.db").set_paused(True)
    assert _repo(tmp_path / "a.db").is_paused() is True  # "restart"
    _repo(tmp_path / "a.db").set_paused(False)
    assert _repo(tmp_path / "a.db").is_paused() is False


def test_config_default_applies_until_the_toggle_is_used(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "a.db")
    assert repo.is_paused(default=True) is True  # nothing stored: config default
    repo.set_paused(False)
    assert repo.is_paused(default=True) is False  # Telegram toggle wins


def test_service_exposes_the_persistent_pause(tmp_path: Path) -> None:
    service = make_service(
        sqlite3.connect(tmp_path / "s.db"), RecordingFetcher(HEALTHY), lambda: NOW
    )
    assert service.is_paused() is False
    service.set_paused(True)
    assert service.is_paused() is True


# --- heartbeat: beacons, retry sweep and Gmail do nothing while paused --------


class _Sync:
    def __init__(self) -> None:
        self.runs = 0

    def due(self) -> bool:
        return True

    async def run(self, *_args: Any) -> None:
        self.runs += 1


def _paused_context(tmp_path: Path, *, paused: bool) -> tuple[Any, Any, Any, Any]:
    fetcher = RecordingFetcher(HEALTHY)
    conn = sqlite3.connect(tmp_path / "hb.db", check_same_thread=False)
    service = make_service(conn, fetcher, lambda: NOW)
    service.create_beacon("ia-espana", SEARCH_URL)
    _seed_pending(OfferRepository(conn), next_analyze_at="2000-01-01T00:00:00+00:00")
    service.set_paused(paused)
    bot, bridge_, sync = FakeBot(), FakeBridge(), _Sync()
    context = fake_context(service, 5, bot)
    context.application.bot_data["bridge"] = bridge_
    context.application.bot_data[GMAIL_KEY] = sync
    return context, fetcher, bridge_, sync


def test_heartbeat_does_nothing_while_paused(tmp_path: Path) -> None:
    context, fetcher, bridge_, sync = _paused_context(tmp_path, paused=True)
    asyncio.run(heartbeat(context))
    assert fetcher.urls == []  # no beacon sweep
    assert bridge_.enqueued == []  # no retry sweep
    assert sync.runs == 0  # no Gmail poll
    assert context.bot.sent == []


def test_heartbeat_runs_everything_when_not_paused(tmp_path: Path) -> None:
    context, fetcher, bridge_, sync = _paused_context(tmp_path, paused=False)
    asyncio.run(heartbeat(context))
    assert fetcher.urls  # previous behaviour untouched
    assert len(bridge_.enqueued) == 1
    assert sync.runs == 1


def test_heartbeat_resumes_after_reanudar(tmp_path: Path) -> None:
    context, fetcher, _bridge, sync = _paused_context(tmp_path, paused=True)
    asyncio.run(heartbeat(context))
    context.application.bot_data["service"].set_paused(False)
    asyncio.run(heartbeat(context))
    assert fetcher.urls and sync.runs == 1


# --- paced auto-analysis queue: queued items defer while paused ---------------


class _ExplodingService:
    async def analyze_new_bridge_job(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("analysis must not run while paused")


class _CountingService:
    def __init__(self) -> None:
        self.calls = 0

    async def analyze_new_bridge_job(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return bridge.BridgeJobOutcome(zone="below", score=1)


@pytest.fixture()
def queue_bridge(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.chdir(tmp_path)

    def make(service: Any) -> Any:
        return bridge._Bridge(
            secret="x",
            port=0,
            owner_id=1,
            threshold=65,
            auto_analyze=True,
            service=service,
        )

    return make


def _offer(handle: Any) -> int:
    return handle._repo.upsert_offer(
        OfferInput(
            job_id="4300000000",
            title="ML",
            company="Acme",
            url="https://www.linkedin.com/jobs/view/4300000000/",
            text="t",
            source="link",
        )
    )


def test_queued_item_is_deferred_not_analyzed_while_paused(queue_bridge: Any) -> None:
    handle = queue_bridge(_ExplodingService())
    offer_id = _offer(handle)
    handle._repo.set_paused(True)
    asyncio.run(handle._process_item(offer_id, "4300000000", {"url": "u"}, "new", None))
    # kept for the retry sweep once the owner resumes (attempts untouched)
    assert handle._repo.get_offer(offer_id).next_analyze_at is not None


def test_queued_item_is_analyzed_when_not_paused(queue_bridge: Any) -> None:
    service = _CountingService()
    handle = queue_bridge(service)
    offer_id = _offer(handle)
    asyncio.run(
        handle._process_item(
            offer_id,
            "4300000000",
            {"url": "https://www.linkedin.com/jobs/view/4300000000/"},
            "new",
            None,
        )
    )
    assert service.calls == 1


def test_register_passes_the_config_default_pause(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, clean_settings: Any
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CVA_BRIDGE_SECRET", "s")
    app = SimpleNamespace(post_init=None, bot_data={})
    handle = bridge.register(
        app,
        clean_settings(
            paused=True, owner_telegram_id=7, bridge_port=0, CVA_BRIDGE_SECRET="s"
        ),
        service_stub(65),
    )
    assert handle is not None
    try:
        assert handle._repo.is_paused(default=handle._paused_default) is True
    finally:
        handle.stop()
