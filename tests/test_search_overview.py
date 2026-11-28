"""``/searches``: what each watched search found and what was new in 14 days.

The bridge appends the NEW count of every extension payload to one
``app_state`` JSON value (``beacon_new_counts``, pruned to 14 days); the
command joins it with ``beacon_scans``.
"""

import asyncio
import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

import bot
from cva import bridge
from cva.commands import COMMANDS, command_name, help_text
from cva.i18n import Lang
from cva.tracker import OfferRepository
from tests.test_bot import (
    OFFER_URL,
    FakeContext,
    FakeFetcher,
    FakeMessage,
    FakeUpdate,
    FetchResponse,
    make_service,
)

STATE_KEY = "beacon_new_counts"


class _Bridge:
    def __init__(self, tmp_path: Path) -> None:
        self.today = "2026-10-18"
        self.repo = OfferRepository(
            sqlite3.connect(tmp_path / "o.db", check_same_thread=False),
            clock=lambda: self.today,
        )
        handler = bridge._make_handler(
            secret="s",
            repo=self.repo,
            sender=lambda message, keyboard: None,
            threshold=65,
            lock=threading.Lock(),
        )
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(
            target=self.httpd.serve_forever,
            kwargs={"poll_interval": bridge.SERVE_POLL_INTERVAL},
            daemon=True,
        ).start()

    def post(self, beacon: str, ids: list[str]) -> httpx.Response:
        jobs = [
            {
                "title": f"ML Engineer {i}",
                "company": f"Acme {i}",
                "url": f"https://www.linkedin.com/jobs/view/{i}/",
            }
            for i in ids
        ]
        port = self.httpd.server_address[1]
        return httpx.post(
            f"http://127.0.0.1:{port}/bridge",
            headers={"X-CVA-Bridge-Secret": "s"},
            json={"beacon": beacon, "jobs": jobs},
        )

    def counts(self) -> dict[str, Any]:
        raw = self.repo.get_state(STATE_KEY)
        return {} if raw is None else json.loads(raw)


@pytest.fixture()
def srv(tmp_path: Path) -> Any:
    server = _Bridge(tmp_path)
    yield server
    server.httpd.shutdown()
    server.httpd.server_close()


def test_bridge_persists_new_counts_and_keeps_its_reply(srv: _Bridge) -> None:
    first = srv.post("Barcelona AI", ["4200000001", "4200000002"])
    assert first.status_code == 200
    assert first.json() == {"received": 2, "new": 2, "skipped": 0}
    again = srv.post("Barcelona AI", ["4200000002", "4200000003"])
    assert again.json() == {"received": 2, "new": 1, "skipped": 1}
    assert srv.counts() == {"Barcelona AI": [["2026-10-18", 3]]}


def test_bridge_keeps_a_row_per_day_and_prunes_after_14_days(srv: _Bridge) -> None:
    srv.post("A", ["4200000001"])
    srv.post("B", ["4200000002"])
    srv.today = "2026-10-25"
    srv.post("A", ["4200000003"])
    assert srv.counts()["A"] == [["2026-10-18", 1], ["2026-10-25", 1]]
    srv.today = "2026-11-01"  # 2026-10-18 is now 14 days old: out
    srv.post("A", ["4200000004"])
    counts = srv.counts()
    assert counts["A"] == [["2026-10-25", 1], ["2026-11-01", 1]]
    assert "B" not in counts  # nothing left in its window


def test_bridge_survives_a_corrupt_counts_value(srv: _Bridge) -> None:
    srv.repo.set_state(STATE_KEY, "not json")
    reply = srv.post("A", ["4200000001"])
    assert reply.json() == {"received": 1, "new": 1, "skipped": 0}
    assert srv.counts() == {"A": [["2026-10-18", 1]]}


# --- the command -------------------------------------------------------------


def _scan(
    service: Any, name: str, found: int, *, days_ago: int = 0, status: str = "ok"
) -> None:
    at = (datetime.now(tz=UTC) - timedelta(days=days_ago)).isoformat()
    service._repo.record_beacon_scan(
        beacon_id=f"id-{name}",
        beacon_name=name,
        at=at,
        status=status,  # type: ignore[arg-type]
        jobs_found=found,
        empty_company=0,
    )


def _new(service: Any, name: str, count: int, days_ago: int = 0) -> None:
    day = (datetime.now(tz=UTC) - timedelta(days=days_ago)).date().isoformat()
    raw = service._repo.get_state(STATE_KEY)
    data = {} if raw is None else json.loads(raw)
    data.setdefault(name, []).append([day, count])
    service._repo.set_state(STATE_KEY, json.dumps(data))


def _run(service: Any, lang: Lang = "en") -> FakeMessage:
    message = FakeMessage()
    context = FakeContext(service)
    context.bot_data[bot._LANG_KEY] = lang
    asyncio.run(bot.searches_command(FakeUpdate(message=message), context))
    return message


def _service(tmp_path: Path) -> Any:
    service, _ = make_service(tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL)))
    return service


def test_command_is_registered_and_help_still_fits() -> None:
    (command,) = [c for c in COMMANDS if c.name == "busquedas"]
    assert command.names == ("busquedas", "searches")
    assert bot._COMMAND_HANDLERS["busquedas"] is bot.searches_command
    for lang in ("en", "es"):
        assert len(help_text(lang=lang)) < 4096  # type: ignore[arg-type]
    assert command_name("busquedas", "en") == "/searches"
    assert command_name("busquedas", "es") == "/busquedas"


def test_lists_zero_new_first_with_rounds_found_new_and_failure(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for _ in range(3):
        _scan(service, "dead", 4)
    _scan(service, "dead", 0, status="rate_limited")
    for _ in range(2):
        _scan(service, "busy", 10)
    _new(service, "busy", 5)
    _new(service, "busy", 2, days_ago=3)
    _scan(service, "old", 9, days_ago=20)  # outside the 14 days
    _new(service, "old", 7, days_ago=20)
    message = _run(service)
    (text,) = message.replies
    lines = text.splitlines()
    dead = next(i for i, ln in enumerate(lines) if "dead" in ln)
    busy = next(i for i, ln in enumerate(lines) if "busy" in ln)
    assert dead < busy
    assert "old" not in text
    assert "rounds 4 · found 12 · new 0 · last failure: rate_limited" in lines[dead]
    assert "rounds 2 · found 20 · new 7" in lines[busy]
    assert "failure" not in lines[busy]
    assert "candidates to remove (Options › Searches)" in text


def test_spanish_output_and_little_data_note(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _scan(service, "solo", 3)
    text = _run(service, "es").replies[0]
    assert "rondas 1 · encontradas 3 · nuevas 0" in text
    assert "candidatas a quitar (Opciones › Búsquedas)" in text
    assert "Pocos datos" in text


def test_no_data_says_so(tmp_path: Path) -> None:
    text = _run(_service(tmp_path)).replies[0]
    assert "No scans" in text


def test_long_lists_are_split_under_the_telegram_limit(tmp_path: Path) -> None:
    service = _service(tmp_path)
    for n in range(81):
        _scan(service, f"search-{n:02d}-" + "x" * 60, 3)
    replies = _run(service).replies
    assert len(replies) > 1
    assert all(len(r) < 4096 for r in replies)
    joined = "\n".join(replies)
    assert all(f"search-{n:02d}-" in joined for n in range(81))


# --- the last failure's reason (beta10 S2) -------------------------------------

ERRORS_KEY = "beacon_last_errors"


def _scan_report(
    srv: _Bridge, status: str, minute: int, day: str = "2026-10-18", **extra: Any
) -> httpx.Response:
    port = srv.httpd.server_address[1]
    body = {
        "beacon_id": "b1",
        "beacon_name": "Barcelona AI",
        "at": f"{day}T11:{minute:02d}:00.000Z",
        "status": status,
        "jobs_found": 0,
        "empty_company": 0,
        **extra,
    }
    return httpx.post(
        f"http://127.0.0.1:{port}/scan-report",
        headers={"X-CVA-Bridge-Secret": "s"},
        json=body,
    )


def test_failed_scan_reason_is_kept_and_shown_in_the_overview(srv: _Bridge) -> None:
    assert _scan_report(srv, "ok", 0).status_code == 200
    assert _scan_report(srv, "error", 1, reason="timeout").status_code == 200
    (row,) = srv.repo.search_overview()
    assert row.last_failure == "error (2026-10-18)"
    assert row.last_failure_reason == "timeout"
    stored = json.loads(srv.repo.get_state(ERRORS_KEY) or "{}")
    assert stored == {
        "Barcelona AI": {
            "at": "2026-10-18T11:01:00.000+00:00",
            "status": "error",
            "reason": "timeout",
        }
    }


def test_a_later_failure_without_reason_never_shows_the_old_one(
    srv: _Bridge,
) -> None:
    _scan_report(srv, "error", 1, reason="offline")
    assert _scan_report(srv, "error", 2).status_code == 200  # older extension
    (row,) = srv.repo.search_overview()
    assert row.last_failure == "error (2026-10-18)"
    assert row.last_failure_reason is None


def test_unknown_or_malformed_reasons_never_reject_the_report(srv: _Bridge) -> None:
    assert _scan_report(srv, "error", 1, reason="cosmic_rays").status_code == 200
    assert srv.repo.search_overview()[0].last_failure_reason == "unknown"
    assert _scan_report(srv, "error", 2, reason=42).status_code == 200
    assert srv.repo.search_overview()[0].last_failure_reason is None
    assert _scan_report(srv, "rate_limited", 3, reason="timeout").status_code == 200
    assert srv.repo.search_overview()[0].last_failure_reason is None
    assert len(srv.repo.recent_beacon_scans("b1")) == 3


def test_stored_reasons_are_pruned_after_14_days(srv: _Bridge) -> None:
    _scan_report(srv, "error", 1, reason="timeout")
    srv.today = "2026-11-01"
    port = srv.httpd.server_address[1]
    httpx.post(
        f"http://127.0.0.1:{port}/scan-report",
        headers={"X-CVA-Bridge-Secret": "s"},
        json={
            "beacon_id": "b2",
            "beacon_name": "Madrid AI",
            "at": "2026-11-01T09:00:00.000Z",
            "status": "error",
            "jobs_found": 0,
            "empty_company": 0,
            "reason": "tab_closed",
        },
    )
    stored = json.loads(srv.repo.get_state(ERRORS_KEY) or "{}")
    assert list(stored) == ["Madrid AI"]


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        (
            "en",
            "last failure: error (2026-10-18) — the page did not load in time",
        ),
        (
            "es",
            "último fallo: error (2026-10-18) — la página no cargó a tiempo",
        ),
    ],
)
def test_command_shows_the_failure_reason_in_both_languages(
    srv: _Bridge, lang: Lang, expected: str
) -> None:
    from cva.notifier import searches_overview_messages

    _scan_report(srv, "error", 1, reason="timeout")
    (text,) = searches_overview_messages(srv.repo.search_overview(), lang=lang)
    assert expected in text
