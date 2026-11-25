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
