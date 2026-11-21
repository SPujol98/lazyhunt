"""Bridge endpoint tests (beacon-bridge): real server, ephemeral loopback port.

Real ``ThreadingHTTPServer`` on port 0 + httpx + duck-typed PTB app/bot stubs;
no Telegram, no LLM, no network beyond loopback. The bot stub runs on a real
event loop in a helper thread because ``post_init`` captures that loop and
cards are scheduled onto it cross-thread (design D2) — exactly how PTB's
``run_polling`` loop behaves in production.
"""

import asyncio
import io
import logging
import socket
import sqlite3
import threading
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from cva import bridge
from cva.config import Settings
from cva.notifier import DiscardedOffer, canonical_job_url
from tests.demo import DEMO_CLAIMS
from tests.fake_portal import FAKE_URL, register_fake_portal
from tests.service_stub import service_stub

SECRET = "s3cret-bridge"
OWNER_ID = 123456
THRESHOLD = 65


class _RecordingBot:
    """Duck-typed PTB bot: records send_message calls (no telegram import).

    Accepts the optional ``parse_mode`` kwarg (match-calibration D4) so the
    HTML-mode round summary can be sent through the same fake without
    breaking every other card, which never passes it.
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
    async def send_message(
        self,
        chat_id: Any,
        text: str,
        reply_markup: Any = None,
        *,
        parse_mode: str | None = None,
    ) -> bool:
        raise RuntimeError("telegram exploded")


def _payload(job_id: str = "4200000000", beacon: str = "Barcelona AI") -> dict:
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


def _secret_header(value: str | None = SECRET) -> dict[str, str]:
    return {} if value is None else {"X-CVA-Bridge-Secret": value}


def _post(port: int, path: str = "/bridge", **kwargs: Any) -> httpx.Response:
    return httpx.post(f"http://127.0.0.1:{port}{path}", **kwargs)


def _rows(workdir: Any) -> list[sqlite3.Row]:
    conn = sqlite3.connect(workdir / "data" / "app.db")
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM offers ORDER BY id").fetchall()
    finally:
        conn.close()


@pytest.fixture()
def workdir(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Isolated cwd so the bridge's own sqlite file (DEFAULT_DB_PATH) is tmp.

    Pins ``auto_analyze=false``: this whole module is the flag-OFF regression
    golden (auto-triage 5.5) — legacy cards, zero LLM, no scheduled tasks.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CVA_AUTO_ANALYZE", "false")
    monkeypatch.setenv("CVA_TELEGRAM_TOKEN", "tok")
    monkeypatch.setenv("CVA_LLM_API_KEY", "sk")
    monkeypatch.setenv("CVA_OWNER_TELEGRAM_ID", str(OWNER_ID))
    monkeypatch.setenv("CVA_BRIDGE_SECRET", SECRET)
    monkeypatch.setenv("CVA_BRIDGE_PORT", "0")
    return tmp_path


@pytest.fixture()
def started(workdir: Any, clean_settings: Callable[..., Settings]):
    """Registered bridge with post_init run on a live loop (D2 rehearsal)."""
    bot = _RecordingBot()
    app = SimpleNamespace(post_init=None, bot=bot, bot_data={})
    handle = bridge.register(app, clean_settings(), service_stub(THRESHOLD))
    assert handle is not None
    loop = asyncio.new_event_loop()
    thread = threading.Thread(
        target=loop.run_forever, name="test-ptb-loop", daemon=True
    )
    thread.start()
    asyncio.run_coroutine_threadsafe(app.post_init(app), loop).result(timeout=5)
    yield SimpleNamespace(handle=handle, bot=bot, workdir=workdir)
    asyncio.run_coroutine_threadsafe(handle.shutdown(), loop).result(timeout=5)
    handle.stop()
    loop.call_soon_threadsafe(loop.stop)


# --- 3.1 auth / security ---------------------------------------------------


def test_wrong_secret_is_401(started: Any) -> None:
    resp = _post(started.handle.port, json=_payload(), headers=_secret_header("wrong"))
    assert resp.status_code == 401
    assert "error" in resp.json()


def test_absent_secret_is_401(started: Any) -> None:
    resp = _post(started.handle.port, json=_payload())
    assert resp.status_code == 401


def test_unknown_path_is_404(started: Any) -> None:
    resp = _post(
        started.handle.port, "/nope", json=_payload(), headers=_secret_header()
    )
    assert resp.status_code == 404


def test_wrong_method_is_405(started: Any) -> None:
    resp = httpx.get(
        f"http://127.0.0.1:{started.handle.port}/bridge", headers=_secret_header()
    )
    assert resp.status_code == 405


def test_get_unknown_path_is_404_before_method(started: Any) -> None:
    """Check order: path 404 wins over method 405 (design Interfaces)."""
    resp = httpx.get(f"http://127.0.0.1:{started.handle.port}/nope")
    assert resp.status_code == 404


def test_suggested_searches_requires_the_secret(started: Any) -> None:
    url = f"http://127.0.0.1:{started.handle.port}/searches/suggested"
    assert httpx.get(url).status_code == 401
    assert httpx.get(url, headers=_secret_header("wrong")).status_code == 401


def test_suggested_searches_returns_json(started: Any) -> None:
    url = f"http://127.0.0.1:{started.handle.port}/searches/suggested"
    resp = httpx.get(url, headers=_secret_header())
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json"
    searches = resp.json()["searches"]
    assert 6 <= len(searches) <= 8
    assert {
        "name",
        "keywords",
        "location",
        "filterProfile",
        "portals",
        "titleFilter",
    } == set(searches[0])


def test_suggested_searches_profile_without_title_filters_sends_none(
    workdir: Any, clean_settings: Callable[..., Settings]
) -> None:
    import shutil

    profile = workdir / "profile"
    profile.mkdir()
    shutil.copyfile(DEMO_CLAIMS, profile / "claims.json")
    (profile / "preferences.toml").write_text("", encoding="utf-8")
    app = SimpleNamespace(post_init=None, bot=_RecordingBot(), bot_data={})
    handle = bridge.register(
        app,
        clean_settings(profile_dir=profile),
        service_stub(THRESHOLD, profile),
    )
    assert handle is not None
    try:
        url = f"http://127.0.0.1:{handle.port}/searches/suggested"
        body = httpx.get(url, headers=_secret_header()).json()
    finally:
        handle.stop()
    assert set(body) == {"searches"}


def test_stop_returns_promptly_after_serving(
    workdir: Any, clean_settings: Callable[..., Settings]
) -> None:
    """Shutdown does not wait out a long ``serve_forever`` poll: five
    start/serve/stop cycles finish well under the stdlib 0.5 s poll default
    (expected ~1.25 s with it), so bot restarts and tests stay fast."""
    import time

    elapsed = 0.0
    for _ in range(5):
        app = SimpleNamespace(post_init=None, bot=_RecordingBot(), bot_data={})
        handle = bridge.register(app, clean_settings(), service_stub(THRESHOLD))
        assert handle is not None
        url = f"http://127.0.0.1:{handle.port}/searches/suggested"
        assert httpx.get(url, headers=_secret_header()).status_code == 200
        started = time.perf_counter()
        handle.stop()
        elapsed += time.perf_counter() - started
    assert elapsed < 0.5


def test_suggested_searches_serve_the_profile_preferences(
    workdir: Any, clean_settings: Callable[..., Settings]
) -> None:
    """A profile's search targets shape the suggestions and its title filters
    ride along for the extension (profile-extraction E3)."""
    import shutil

    profile = workdir / "profile"
    profile.mkdir()
    shutil.copyfile(DEMO_CLAIMS, profile / "claims.json")
    (profile / "preferences.toml").write_text(
        '[search_targets]\nai_roles = ["ML Engineer"]\nlocations = ["Madrid"]\n'
        '[title_filters.ai]\ninclude_groups = [["Data"]]\nexclude_terms = ["Intern"]\n',
        encoding="utf-8",
    )
    app = SimpleNamespace(post_init=None, bot=_RecordingBot(), bot_data={})
    handle = bridge.register(
        app,
        clean_settings(profile_dir=profile),
        service_stub(THRESHOLD, profile),
    )
    assert handle is not None
    try:
        url = f"http://127.0.0.1:{handle.port}/searches/suggested"
        body = httpx.get(url, headers=_secret_header()).json()
    finally:
        handle.stop()

    assert {s["location"] for s in body["searches"]} == {"Madrid"}
    assert "ML Engineer" in {s["keywords"] for s in body["searches"]}
    ai = body["titleFilters"]["ai"]
    assert ai["includeGroups"] == [["Data"]]
    assert ai["excludeTerms"][0] == "Intern"
    assert ai["excludeTerms"][-1] == "BJAK"  # the blocklist is appended
    assert body["titleFilters"]["backend"]["enabled"] is True


def test_suggested_searches_is_get_only(started: Any) -> None:
    url = f"http://127.0.0.1:{started.handle.port}/searches/suggested"
    assert httpx.post(url, json={}, headers=_secret_header()).status_code == 404


def test_binds_loopback_only(started: Any) -> None:
    """Off-loopback refused at bind time: the bound host IS 127.0.0.1."""
    assert started.handle._httpd is not None
    assert started.handle._httpd.server_address[0] == "127.0.0.1"


# --- 3.2 caps / validation -------------------------------------------------


def test_oversized_body_is_413(started: Any) -> None:
    body = b"x" * (256 * 1024 + 1)
    resp = _post(started.handle.port, content=body, headers=_secret_header())
    assert resp.status_code == 413


def test_malformed_json_is_400(started: Any) -> None:
    resp = _post(started.handle.port, content=b"{not json", headers=_secret_header())
    assert resp.status_code == 400


def test_drain_reads_exactly_n_bytes() -> None:
    """_drain consumes exactly the declared body length, nothing more."""
    buf = io.BytesIO(b"x" * 10)
    assert bridge._drain(buf, 10) is True
    assert buf.tell() == 10


def test_drain_is_bounded_by_max_drain_bytes() -> None:
    """An absurd Content-Length is capped: drain at most MAX_DRAIN_BYTES."""
    buf = io.BytesIO(b"x" * bridge.MAX_DRAIN_BYTES)
    assert bridge._drain(buf, bridge.MAX_DRAIN_BYTES + 1) is True
    assert buf.tell() == bridge.MAX_DRAIN_BYTES


def test_drain_returns_false_on_premature_eof() -> None:
    """Fewer bytes than declared + EOF → drain failed (no 413 can follow)."""
    assert bridge._drain(io.BytesIO(b"short"), 10) is False


def test_drain_returns_false_on_read_error() -> None:
    """A client hanging up mid-drain must not crash the handler."""

    class _BrokenReader:
        def read(self, size: int) -> bytes:
            raise OSError("client hung up mid-drain")

    assert bridge._drain(_BrokenReader(), 10) is False


@pytest.mark.parametrize(
    "payload",
    [
        {"beacon": 1, "jobs": []},
        {"beacon": "b", "jobs": "nope"},
        {"beacon": "b", "jobs": ["nope"]},
        {"beacon": "b", "jobs": [{"url": "https://example.com/no-job-id"}]},
        {
            "beacon": "b",
            "jobs": [{"title": 3, "url": "https://www.linkedin.com/jobs/view/1/"}],
        },
    ],
)
def test_wrong_schema_is_400(started: Any, payload: dict) -> None:
    resp = _post(started.handle.port, json=payload, headers=_secret_header())
    assert resp.status_code == 400


def test_bad_url_is_400_atomic_no_partial_ingest(started: Any) -> None:
    """Atomic validation: one bad job rejects the WHOLE payload (no ingest)."""
    payload = _payload()
    payload["jobs"].append({"title": "x", "url": "https://example.com/nope"})
    resp = _post(started.handle.port, json=payload, headers=_secret_header())
    assert resp.status_code == 400
    assert _rows(started.workdir) == []
    assert started.bot.calls == []


# --- Multi-source validation + optional inline text (infojobs-source D4/D5) --


def _infojobs_job(**over: object) -> dict:
    job: dict = {
        "title": "Ingeniero IA",
        "company": "Acme",
        "location": "Barcelona",
        "url": (
            "https://www.infojobs.net/barcelona/ingeniero-ia/of-"
            "0f8d7e6c5b4a39281706f5e4d3c2b1a0/"
        ),
        "text": "Oferta completa con los requisitos.",
    }
    job.update(over)
    return job


def test_validated_accepts_infojobs_url_with_text() -> None:
    beacon, jobs = bridge._validated({"beacon": "b", "jobs": [_infojobs_job()]})
    assert beacon == "b"
    assert len(jobs) == 1


def test_validated_strips_inline_text() -> None:
    _beacon, jobs = bridge._validated(
        {"beacon": "b", "jobs": [_infojobs_job(text="  Oferta con espacios  ")]}
    )
    assert jobs[0]["text"] == "Oferta con espacios"


def test_validated_accepts_linkedin_job_without_text() -> None:
    _beacon, jobs = bridge._validated({"beacon": "b", "jobs": [_payload()["jobs"][0]]})
    assert jobs[0].get("text") is None


def test_validated_accepts_and_ignores_optional_profile_field() -> None:
    """backend-profile T1: the extension now sends the beacon's filter
    profile; the bridge must accept it (T2 will consume it)."""
    for profile in ("ai", "backend"):
        beacon, jobs = bridge._validated(
            {"beacon": "b", "profile": profile, "jobs": [_payload()["jobs"][0]]}
        )
        assert beacon == "b"
        assert len(jobs) == 1


def test_validated_rejects_unknown_domain() -> None:
    with pytest.raises(bridge._BadRequest):
        bridge._validated(
            {
                "beacon": "b",
                "jobs": [_infojobs_job(url="https://example.com/of-abc")],
            }
        )


def test_validated_drops_an_inline_job_without_text_and_keeps_the_rest() -> None:
    """Server fetch is dead for infojobs (challenge shell): a text-less inline
    job cannot be processed, but it must not take the other jobs down."""
    other = _infojobs_job(
        url="https://www.infojobs.net/madrid/otra/of-1f8d7e6c5b4a39281706f5e4d3c2b1a0/"
    )
    dropped: list[str] = []
    _beacon, jobs = bridge._validated(
        {"beacon": "b", "jobs": [_infojobs_job(text=None), other]}, dropped
    )
    assert [j["url"] for j in jobs] == [other["url"]]
    assert dropped == [_infojobs_job()["url"]]


def test_bridge_answers_200_and_counts_the_dropped_text_less_jobs(
    started: Any,
) -> None:
    good = _infojobs_job()
    payload = {
        "beacon": "IA",
        "jobs": [
            _infojobs_job(
                text=None,
                url="https://www.infojobs.net/x/y/of-2f8d7e6c5b4a39281706f5e4d3c2b1a0/",
            ),
            good,
        ],
    }
    resp = _post(started.handle.port, json=payload, headers=_secret_header())
    assert resp.status_code == 200
    assert resp.json() == {"received": 1, "new": 1, "skipped": 0, "dropped": 1}
    assert len(_rows(started.workdir)) == 1


def test_bridge_payload_of_only_text_less_inline_jobs_is_a_200_with_nothing_new(
    started: Any,
) -> None:
    payload = {"beacon": "IA", "jobs": [_infojobs_job(text=None)]}
    resp = _post(started.handle.port, json=payload, headers=_secret_header())
    assert resp.status_code == 200
    assert resp.json() == {"received": 0, "new": 0, "skipped": 0, "dropped": 1}
    assert _rows(started.workdir) == []


@pytest.mark.parametrize(
    "text",
    [pytest.param("   ", id="blank"), pytest.param(123, id="non-string")],
)
def test_validated_rejects_invalid_text(text: object) -> None:
    with pytest.raises(bridge._BadRequest):
        bridge._validated({"beacon": "b", "jobs": [_infojobs_job(text=text)]})


def test_validated_rejects_text_over_20000_chars() -> None:
    with pytest.raises(bridge._BadRequest):
        bridge._validated({"beacon": "b", "jobs": [_infojobs_job(text="x" * 20001)]})


def test_validated_accepts_text_at_20000_chars() -> None:
    _beacon, jobs = bridge._validated(
        {"beacon": "b", "jobs": [_infojobs_job(text="x" * 20000)]}
    )
    assert len(jobs[0]["text"]) == 20000


def test_payload_with_another_violation_is_still_400_atomic(started: Any) -> None:
    """A text-less inline job is dropped alone, but any other violation keeps
    the whole payload a 400 with no partial ingest."""
    resp = _post(
        started.handle.port,
        json={
            "beacon": "ij",
            "jobs": [
                _infojobs_job(),
                _infojobs_job(url="https://example.com/of-abc"),
            ],
        },
        headers=_secret_header(),
    )
    assert resp.status_code == 400
    assert _rows(started.workdir) == []


# --- 3.3 dedupe ------------------------------------------------------------


def test_repost_is_200_new_zero_without_row_or_send(started: Any) -> None:
    first = _post(started.handle.port, json=_payload(), headers=_secret_header())
    assert first.status_code == 200
    assert first.json() == {"received": 1, "new": 1, "skipped": 0}

    second = _post(started.handle.port, json=_payload(), headers=_secret_header())
    assert second.status_code == 200
    assert second.json() == {"received": 1, "new": 0, "skipped": 1}

    assert len(_rows(started.workdir)) == 1
    assert len(started.bot.calls) == 1


# --- 3.4 ingest / notify / observability -----------------------------------


def test_new_jobs_ingested_and_notified(
    started: Any, caplog: pytest.LogCaptureFixture
) -> None:
    payload = _payload()
    payload["jobs"].append(
        {
            "title": None,
            "company": "Globex",
            "location": None,
            "url": "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/998877/",
        }
    )
    with caplog.at_level(logging.INFO, logger="cva.bridge"):
        resp = _post(started.handle.port, json=payload, headers=_secret_header())

    assert resp.status_code == 200
    assert resp.json() == {"received": 2, "new": 2, "skipped": 0}

    rows = _rows(started.workdir)
    assert [row["origen"] for row in rows] == ["linkedin", "linkedin"]
    by_url = {row["url_oferta"]: row for row in rows}
    assert by_url[canonical_job_url("4200000000")]["source_data"] == (
        "bridge: Barcelona AI\n" + canonical_job_url("4200000000")
    )
    assert by_url[canonical_job_url("998877")]["posicion"] is None

    # Cards: existing new-job card through the D2 cross-thread sender.
    assert len(started.bot.calls) == 2
    chat_id, text, keyboard = started.bot.calls[0]
    assert chat_id == OWNER_ID
    assert "ML Engineer" in text and "Acme" in text and "Umbral Top-Choice: 65" in text
    assert keyboard.inline_keyboard[0][0].callback_data == "an:4200000000"

    assert "Barcelona AI" in caplog.text and "received=2" in caplog.text
    assert "new=2" in caplog.text


def test_duplicate_skip_logged_at_debug(
    started: Any, caplog: pytest.LogCaptureFixture
) -> None:
    _post(started.handle.port, json=_payload(), headers=_secret_header())
    with caplog.at_level(logging.DEBUG, logger="cva.bridge"):
        _post(started.handle.port, json=_payload(), headers=_secret_header())
    assert "4200000000" in caplog.text
    assert "skip" in caplog.text.lower()


# --- Mixed-source payload (infojobs-source D2/D3/D4) ---------------------------

INFOJOBS_CANONICAL = (
    "https://www.infojobs.net/barcelona/ingeniero-ia/of-"
    "0f8d7e6c5b4a39281706f5e4d3c2b1a0/"
)
INFOJOBS_TEXT = "Oferta completa inline con los requisitos del puesto."


def test_mixed_payload_maps_source_canonical_and_inline_text(started: Any) -> None:
    """One linkedin job + one infojobs job with inline text: the linkedin row
    is unchanged (canonical + note), the infojobs row stores the REAL text
    under the normalized-original canonical with origen='infojobs', and the
    legacy card carries the InfoJobs canonical URL."""
    payload = _payload()
    payload["jobs"].append(
        {
            "title": "Ingeniero IA",
            "company": "Acme",
            "location": "Barcelona",
            "url": INFOJOBS_CANONICAL + "?utm_source=beacon",
            "text": f"  {INFOJOBS_TEXT}  ",
        }
    )
    resp = _post(started.handle.port, json=payload, headers=_secret_header())
    assert resp.status_code == 200
    assert resp.json() == {"received": 2, "new": 2, "skipped": 0}

    rows = _rows(started.workdir)
    by_url = {row["url_oferta"]: row for row in rows}
    linkedin_row = by_url[canonical_job_url("4200000000")]
    assert linkedin_row["origen"] == "linkedin"  # linkedin rows unchanged
    assert linkedin_row["source_data"] == (
        "bridge: Barcelona AI\n" + canonical_job_url("4200000000")
    )
    infojobs_row = by_url[INFOJOBS_CANONICAL]  # canonical = normalized original (D2)
    assert infojobs_row["origen"] == "infojobs"
    assert infojobs_row["source_data"] == INFOJOBS_TEXT  # stripped inline text

    # Legacy card for the infojobs job carries the InfoJobs canonical URL.
    texts = [text for _chat, text, _kb in started.bot.calls]
    assert any(INFOJOBS_CANONICAL in text for text in texts)


def test_mixed_payload_duplicate_infojobs_skipped_silently(started: Any) -> None:
    """Dedupe seam: re-POSTed infojobs job (same normalized canonical) →
    skipped, no second row, no second card."""
    job = {
        "title": "Ingeniero IA",
        "company": "Acme",
        "location": "Barcelona",
        "url": INFOJOBS_CANONICAL,
        "text": INFOJOBS_TEXT,
    }
    first = _post(
        started.handle.port,
        json={"beacon": "ij", "jobs": [job]},
        headers=_secret_header(),
    )
    second = _post(
        started.handle.port,
        json={"beacon": "ij", "jobs": [job]},
        headers=_secret_header(),
    )
    assert first.json() == {"received": 1, "new": 1, "skipped": 0}
    assert second.json() == {"received": 1, "new": 0, "skipped": 1}
    assert len(_rows(started.workdir)) == 1
    assert len(started.bot.calls) == 1


# --- 3.5 resilience / lifecycle --------------------------------------------


def test_handler_exception_is_500_and_server_survives(
    workdir: Any,
    clean_settings: Callable[..., Settings],
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = SimpleNamespace(post_init=None, bot=_ExplodingBot(), bot_data={})
    handle = bridge.register(app, clean_settings(), service_stub(THRESHOLD))
    assert handle is not None
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    asyncio.run_coroutine_threadsafe(app.post_init(app), loop).result(timeout=5)
    try:
        with caplog.at_level(logging.ERROR, logger="cva.bridge"):
            resp = _post(handle.port, json=_payload(), headers=_secret_header())
        assert resp.status_code == 500
        assert caplog.records  # error logged, not swallowed

        alive = _post(handle.port, json=_payload(), headers=_secret_header("bad"))
        assert alive.status_code == 401  # server still answering
    finally:
        asyncio.run_coroutine_threadsafe(handle.shutdown(), loop).result(timeout=5)
        handle.stop()
        loop.call_soon_threadsafe(loop.stop)


def test_secret_unset_disables_endpoint(
    workdir: Any,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("CVA_BRIDGE_SECRET")
    app = SimpleNamespace(post_init=None, bot=_RecordingBot())
    with caplog.at_level(logging.WARNING, logger="cva.bridge"):
        handle = bridge.register(app, clean_settings(), service_stub(THRESHOLD))
    assert handle is None
    assert app.post_init is None  # wiring untouched
    assert "CVA_BRIDGE_SECRET" in caplog.text


def test_owner_unset_disables_endpoint(
    workdir: Any,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("CVA_OWNER_TELEGRAM_ID", "0")
    app = SimpleNamespace(post_init=None, bot=_RecordingBot())
    with caplog.at_level(logging.WARNING, logger="cva.bridge"):
        handle = bridge.register(app, clean_settings(), service_stub(THRESHOLD))
    assert handle is None


def test_bind_failure_disables_with_warning(
    workdir: Any,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
    caplog: pytest.LogCaptureFixture,
) -> None:
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    busy_port = blocker.getsockname()[1]
    monkeypatch.setenv("CVA_BRIDGE_PORT", str(busy_port))
    try:
        with caplog.at_level(logging.WARNING, logger="cva.bridge"):
            handle = bridge.register(
                SimpleNamespace(post_init=None, bot=_RecordingBot()),
                clean_settings(),
                service_stub(THRESHOLD),
            )
        assert handle is None  # bot continues, no port bound
        assert "bind failed" in caplog.text
    finally:
        blocker.close()


def test_bridge_port_honored(
    workdir: Any,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
) -> None:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    chosen = probe.getsockname()[1]
    probe.close()
    monkeypatch.setenv("CVA_BRIDGE_PORT", str(chosen))

    app = SimpleNamespace(post_init=None, bot=_RecordingBot(), bot_data={})
    handle = bridge.register(app, clean_settings(), service_stub(THRESHOLD))
    assert handle is not None
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    asyncio.run_coroutine_threadsafe(app.post_init(app), loop).result(timeout=5)
    try:
        assert handle.port == chosen
        resp = _post(chosen, json=_payload(), headers=_secret_header())
        assert resp.status_code == 200
    finally:
        asyncio.run_coroutine_threadsafe(handle.shutdown(), loop).result(timeout=5)
        handle.stop()
        loop.call_soon_threadsafe(loop.stop)


# --- Paced auto-analysis queue (notification-pacing D1/D2/D9) ----------------
#
# The flag-OFF module above stays the legacy golden; this section drives the
# auto path through the paced queue: asyncio.Queue + ONE worker on the PTB
# loop (D1), sleep-after-item-when-non-empty pacing (D2), thread-safe enqueue
# that keeps the 200-first contract. Shared harness doubles come from
# test_auto_triage (same fake LLM/fetcher/service stack).

import time as _time

from test_auto_triage import (
    JSONLD_BODY,
    OFFER_URL,
    FakeFetcher,
    FakeLlmClient,
    _BlockingFetcher,
    _extraction,
    _service_on_default_db,
    _WallFetcher,
)

from cva.fetch import FetchResponse
from cva.tracker import OfferInput

JOB_IDS = ("4200000000", "6600022222", "7770003333")


def _job_id_of(url: str) -> str:
    """Last path segment of any canonical/guest job URL form."""
    return url.rstrip("/").rsplit("/", 1)[-1]


def _three_job_payload() -> dict:
    return {
        "beacon": "Barcelona AI",
        "jobs": [
            {
                "title": f"ML Engineer {i}",
                "company": "Acme",
                "location": "BCN",
                "url": f"https://www.linkedin.com/jobs/view/{job_id}/",
            }
            for i, job_id in enumerate(JOB_IDS)
        ],
    }


def _post_paced(port: int, payload: dict) -> httpx.Response:
    return _post(port, json=payload, headers=_secret_header())


def _wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        if predicate():
            return
        _time.sleep(0.01)
    raise AssertionError("condition not reached before timeout")


class _AsyncGateFetcher(FakeFetcher):
    """Holds its first fetch on an ASYNC wait: the loop stays responsive so
    enqueued puts land and shutdown/cancel work while the analysis hangs.
    Optionally records (kind, url) events for drain-order assertions."""

    def __init__(
        self,
        response: FetchResponse,
        gate: threading.Event,
        events: list[tuple[str, str]] | None = None,
    ) -> None:
        super().__init__(response)
        self._gate = gate
        self._events = events if events is not None else []

    async def get(self, url: str) -> FetchResponse:
        self.calls.append(url)
        self._events.append(("analyze", url))
        if len(self.calls) == 1:
            while not self._gate.is_set():
                await asyncio.sleep(0.01)
        return self._response


class _PerJobFetcher(FakeFetcher):
    """Canned 200 whose JobPosting description embeds the requested URL.

    Real guest fetches return job-distinct pages; the extraction cache keys
    on the stored source text (parsed from the JSON-LD), so without per-job
    bodies the whole payload collapses onto ONE cached extraction — mixed
    zone/round summaries become impossible.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get(self, url: str) -> FetchResponse:
        self.calls.append(url)
        body = JSONLD_BODY.replace(
            "Imprescindible experiencia con Python y Kubernetes.",
            f"Imprescindible experiencia con Python y Kubernetes. Ref {url}.",
        )
        return FetchResponse(200, body, url)


@pytest.fixture()
def paced_factory(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
) -> Callable[..., Any]:
    """Starts auto-mode bridges with the paced queue; shuts each down cleanly."""
    holders: list[Any] = []

    def _make(
        *,
        llm: Any,
        fetcher: Any,
        analyze_delay_seconds: int = 0,
        analyze_retry_max: int = 5,
        round_summary: bool = False,
        post_init: bool = True,
    ) -> Any:
        monkeypatch.chdir(tmp_path)
        settings = clean_settings(
            CVA_BRIDGE_SECRET=SECRET,
            bridge_port=0,
            owner_telegram_id=OWNER_ID,
            auto_analyze=True,
            analyze_delay_seconds=analyze_delay_seconds,
            analyze_retry_max=analyze_retry_max,
            round_summary=round_summary,
        )
        service, repo = _service_on_default_db(
            llm, fetcher, analyze_retry_max=analyze_retry_max
        )
        bot = _RecordingBot()
        app = SimpleNamespace(post_init=None, bot=bot, bot_data={})
        handle = bridge.register(app, settings, service)
        assert handle is not None
        loop = asyncio.new_event_loop()
        threading.Thread(
            target=loop.run_forever, name="paced-ptb-loop", daemon=True
        ).start()
        if post_init:
            asyncio.run_coroutine_threadsafe(app.post_init(app), loop).result(timeout=5)
        ns = SimpleNamespace(
            handle=handle,
            bot=bot,
            service=service,
            repo=repo,
            llm=llm,
            fetcher=fetcher,
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


def test_200_precedes_queued_analysis(paced_factory: Any) -> None:
    """Approval (preserved contract): the 200 lands while the analysis is
    still blocked in its first fetch — POSTs never wait for the worker."""
    fetcher = _BlockingFetcher()
    ns = paced_factory(llm=FakeLlmClient(_extraction()), fetcher=fetcher)
    try:
        resp = _post_paced(ns.handle.port, _three_job_payload())
        assert resp.status_code == 200
        assert resp.json() == {"received": 3, "new": 3, "skipped": 0}
        assert ns.repo.get_offer(1) is not None  # upserted before analysis
        assert ns.bot.calls == []  # worker still gated on the first fetch
    finally:
        fetcher.release.set()
    _wait_until(lambda: len(ns.bot.calls) == 3)


def test_pipeline_isolation_second_post_independent_of_first(
    paced_factory: Any,
) -> None:
    """Approval: N's analysis blocked → N+1's upsert + 200 still complete."""
    fetcher = _BlockingFetcher()
    ns = paced_factory(llm=FakeLlmClient(_extraction()), fetcher=fetcher)
    try:
        first = _post_paced(ns.handle.port, _three_job_payload())
        assert first.status_code == 200
        _wait_until(lambda: len(fetcher.calls) == 1)  # worker started N
        payload = _three_job_payload()
        payload["jobs"] = payload["jobs"][:1]
        payload["jobs"][0]["url"] = "https://www.linkedin.com/jobs/view/8800044444/"
        payload["jobs"][0]["title"] = "Distinct Role"  # not a cross-source dup
        second = _post_paced(ns.handle.port, payload)
        assert second.status_code == 200  # N still blocked; N+1 answered
        assert not fetcher.release.is_set()
    finally:
        fetcher.release.set()
    _wait_until(lambda: len(ns.bot.calls) == 4)


def test_hanging_analysis_never_times_out_post(paced_factory: Any) -> None:
    """Approval: a hanging analysis leaves the 200 already on the wire."""
    fetcher = _BlockingFetcher()
    ns = paced_factory(llm=FakeLlmClient(_extraction()), fetcher=fetcher)
    resp = _post_paced(ns.handle.port, _three_job_payload())
    assert resp.status_code == 200
    assert ns.bot.calls == []
    fetcher.release.set()
    _wait_until(lambda: len(ns.bot.calls) == 3)


def test_enqueue_auto_analysis_public_api_defaults_to_kind_new(
    paced_factory: Any,
) -> None:
    """D1 seam: the public enqueue the scheduler reuses (kind defaults new)
    drains through the worker and produces exactly the breakdown card."""
    llm = FakeLlmClient(_extraction())
    canonical = canonical_job_url(JOB_IDS[0])
    ns = paced_factory(
        llm=llm,
        fetcher=FakeFetcher(FetchResponse(200, JSONLD_BODY, canonical)),
    )
    offer_id = ns.repo.upsert_offer(
        OfferInput(
            job_id=JOB_IDS[0],
            title="ML Engineer",
            company="Acme",
            url=canonical,
            text=f"bridge: Test\n{canonical}",
            source="link",
        )
    )
    ns.handle.enqueue_auto_analysis(
        offer_id,
        JOB_IDS[0],
        {"title": "ML Engineer", "company": "Acme", "url": canonical},
    )
    _wait_until(lambda: len(ns.bot.calls) == 1)
    _chat, text, keyboard = ns.bot.calls[0]
    assert f"📊 Oferta #{offer_id}" in text
    assert keyboard.inline_keyboard[0][0].callback_data == f"cv:{offer_id}:AI:es"
    assert ns.repo.get_offer(offer_id).estado == "analizada"


def test_retry_kind_with_cleared_row_skips_silently(paced_factory: Any) -> None:
    """D-tree guard: kind="retry" + next_analyze_at NULL (resolved elsewhere:
    manual success / already carded) → skip with zero analysis, zero cards."""
    llm = FakeLlmClient(_extraction())
    ns = paced_factory(
        llm=llm,
        fetcher=FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
    )
    offer_id = ns.repo.upsert_offer(
        OfferInput(
            job_id=JOB_IDS[0],
            title="ML Engineer",
            company="Acme",
            url=OFFER_URL,
            text="bridge: Test",
            source="link",
        )
    )
    ns.handle.enqueue_auto_analysis(
        offer_id,
        JOB_IDS[0],
        {"title": "ML Engineer", "company": "Acme", "url": OFFER_URL},
        kind="retry",
    )
    _time.sleep(0.15)
    assert ns.bot.calls == []
    assert llm.calls == 0
    assert ns.fetcher.calls == []


def test_retry_kind_with_pending_row_runs_the_analysis(paced_factory: Any) -> None:
    """kind="retry" + still retry-pending → the worker analyzes it (sweep path)."""
    llm = FakeLlmClient(_extraction())
    canonical = canonical_job_url(JOB_IDS[0])
    ns = paced_factory(
        llm=llm,
        fetcher=FakeFetcher(FetchResponse(200, JSONLD_BODY, canonical)),
    )
    offer_id = ns.repo.upsert_offer(
        OfferInput(
            job_id=JOB_IDS[0],
            title="ML Engineer",
            company="Acme",
            url=canonical,
            text=f"bridge: Test\n{canonical}",
            source="link",
        )
    )
    ns.repo.record_analyze_failure(
        offer_id, next_analyze_at="2026-09-19T12:00:00+00:00"
    )
    ns.handle.enqueue_auto_analysis(
        offer_id,
        JOB_IDS[0],
        {"title": "ML Engineer", "company": "Acme", "url": canonical},
        kind="retry",
    )
    _wait_until(lambda: len(ns.bot.calls) == 1)
    assert llm.calls == 1


def test_single_worker_never_runs_analyses_in_parallel(paced_factory: Any) -> None:
    """D1: ONE worker → fetches strictly overlap-free even mid-burst."""
    events: list[tuple[str, str]] = []

    class _OverlapDetector(FakeFetcher):
        in_flight = 0
        max_in_flight = 0

        async def get(self, url: str) -> FetchResponse:
            type(self).in_flight += 1
            type(self).max_in_flight = max(type(self).max_in_flight, self.in_flight)
            events.append(("analyze", url))
            await asyncio.sleep(0.01)  # yield: a parallel run WOULD overlap here
            type(self).in_flight -= 1
            return self._response

    fetcher = _OverlapDetector(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    ns = paced_factory(llm=FakeLlmClient(_extraction()), fetcher=fetcher)
    resp = _post_paced(ns.handle.port, _three_job_payload())
    assert resp.status_code == 200
    _wait_until(lambda: len(ns.bot.calls) == 3)
    assert fetcher.max_in_flight == 1
    assert [_job_id_of(url) for _k, url in events] == list(JOB_IDS)


def test_burst_is_serialized_with_pacing_sleeps(
    paced_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D2: burst of 3, delay=45 → starts at t, t+45, t+90: sleep ONLY between
    items, never before the first, never after the last (queue empty)."""
    events: list[tuple[str, object]] = []
    gate = threading.Event()

    async def fake_sleep(seconds: float) -> None:
        events.append(("sleep", seconds))
        await asyncio.sleep(0)

    monkeypatch.setattr(bridge, "_pacing_sleep", fake_sleep)
    ns = paced_factory(
        llm=FakeLlmClient(_extraction()),
        fetcher=_AsyncGateFetcher(
            FetchResponse(200, JSONLD_BODY, OFFER_URL), gate, events
        ),
        analyze_delay_seconds=45,
    )
    resp = _post_paced(ns.handle.port, _three_job_payload())
    assert resp.status_code == 200
    _wait_until(lambda: len(events) == 1)  # first analysis started (gated)
    gate.set()  # items 2 and 3 are already queued (handler enqueued post-200)
    _wait_until(lambda: len(ns.bot.calls) == 3)

    assert [kind for kind, _ in events] == [
        "analyze",
        "sleep",
        "analyze",
        "sleep",
        "analyze",
    ]
    assert events[1][1] == 45 and events[3][1] == 45


def test_zero_delay_is_immediate_sequential_without_sleeps(
    paced_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Approval (spec: zero-equivalence): delay=0 → no sleep calls at all,
    sequential order preserved."""

    async def failing_sleep(seconds: float) -> None:
        raise AssertionError("no sleep may run when analyze_delay_seconds == 0")

    monkeypatch.setattr(bridge, "_pacing_sleep", failing_sleep)
    events: list[tuple[str, str]] = []
    gate = threading.Event()
    ns = paced_factory(
        llm=FakeLlmClient(_extraction()),
        fetcher=_AsyncGateFetcher(
            FetchResponse(200, JSONLD_BODY, OFFER_URL), gate, events
        ),
        analyze_delay_seconds=0,
    )
    resp = _post_paced(ns.handle.port, _three_job_payload())
    assert resp.status_code == 200
    _wait_until(lambda: len(events) == 1)
    gate.set()
    _wait_until(lambda: len(ns.bot.calls) == 3)
    assert [kind for kind, _ in events] == ["analyze", "analyze", "analyze"]


def test_shutdown_defers_in_flight_and_queued_rows(paced_factory: Any) -> None:
    """D9: post_shutdown cancels the worker and defers the in-flight + queued
    rows to retry-pending (next=now, attempts untouched) → sweep re-drives."""
    gate = threading.Event()
    fetcher = _AsyncGateFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL), gate)
    ns = paced_factory(llm=FakeLlmClient(_extraction()), fetcher=fetcher)
    resp = _post_paced(ns.handle.port, _three_job_payload())
    assert resp.status_code == 200
    # Offer 1 in flight AND offers 2-3 landed in the queue: the puts are
    # scheduled post-200, so under load they can trail the first fetch.
    _wait_until(
        lambda: (
            len(fetcher.calls) == 1
            and ns.handle._queue is not None
            and ns.handle._queue.qsize() == 2
        )
    )

    asyncio.run_coroutine_threadsafe(ns.handle.shutdown(), ns.loop).result(timeout=5)
    gate.set()  # too late: the worker was already cancelled
    _time.sleep(0.1)

    assert ns.bot.calls == []  # no card ever sent
    for offer_id in (1, 2, 3):
        row = ns.repo.get_offer(offer_id)
        assert row.next_analyze_at is not None  # deferred → sweep re-drives
        assert row.analyze_attempts == 0  # shutdown is NOT a failure


def test_post_shutdown_hook_takes_the_application_and_chains_the_previous(
    workdir: Any, clean_settings: Callable[..., Settings]
) -> None:
    """PTB calls ``post_shutdown(application)``: the bridge hook accepts it,
    runs its own deferral first, then the hook registered before it (the
    scheduler's tracker flush) — it no longer replaces that hook."""
    order: list[str] = []

    async def previous(application: Any) -> None:
        order.append("previous")

    app = SimpleNamespace(
        post_init=None, post_shutdown=previous, bot=_RecordingBot(), bot_data={}
    )
    handle = bridge.register(app, clean_settings(), service_stub(THRESHOLD))
    assert handle is not None
    real_shutdown = handle.shutdown

    async def recording_shutdown() -> None:
        order.append("bridge")
        await real_shutdown()

    handle.shutdown = recording_shutdown  # type: ignore[method-assign]
    try:
        asyncio.run(app.post_shutdown(app))
    finally:
        handle.stop()
    assert order == ["bridge", "previous"]


def test_enqueue_landing_after_shutdown_is_deferred_not_lost(
    paced_factory: Any,
) -> None:
    """A put scheduled before shutdown but landing after it (the post-200
    race) defers its row to retry-pending instead of dying in the queue."""
    ns = paced_factory(llm=FakeLlmClient(_extraction()), fetcher=_PerJobFetcher())
    offer_id = ns.repo.upsert_offer(
        OfferInput(title="T", company="C", url=OFFER_URL, text="x", source="link")
    )
    asyncio.run_coroutine_threadsafe(ns.handle.shutdown(), ns.loop).result(timeout=5)
    ns.handle.enqueue_auto_analysis(offer_id, JOB_IDS[0], {"url": OFFER_URL})
    _wait_until(lambda: ns.repo.get_offer(offer_id).next_analyze_at is not None)
    assert ns.repo.get_offer(offer_id).analyze_attempts == 0
    assert ns.bot.calls == []


def test_zero_equivalence_all_keys_zero_cards_immediately(
    paced_factory: Any,
) -> None:
    """Spec (zero-equivalence): delay=0 + retry_max=0 → pre-change behavior:
    analyses run immediately, and each failing analysis gets EXACTLY ONE
    final [Analizar] card right away (no sweep wait, attempts cleared)."""
    ns = paced_factory(
        llm=FakeLlmClient(_extraction()),
        fetcher=_WallFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
        analyze_delay_seconds=0,
        analyze_retry_max=0,
    )
    resp = _post_paced(ns.handle.port, _three_job_payload())
    assert resp.status_code == 200
    _wait_until(lambda: len(ns.bot.calls) == 3)
    _time.sleep(0.1)
    assert len(ns.bot.calls) == 3  # exactly one final card per job
    for i, (chat, text, keyboard) in enumerate(ns.bot.calls, start=1):
        assert chat == OWNER_ID
        assert "🔔" in text and "El análisis automático falló" in text
        assert keyboard.inline_keyboard[0][0].callback_data == f"an:{JOB_IDS[i - 1]}"
        row = ns.repo.get_offer(i)
        assert row.analyze_attempts == 0  # latch cleared
        assert row.next_analyze_at is None  # sweep never re-drives


# --- Restart durability via the REAL heartbeat sweep (D4) ---------------------


def _seed_bridge_row(repo: Any, job_id: str) -> int:
    canonical = canonical_job_url(job_id)
    return repo.upsert_offer(
        OfferInput(
            job_id=job_id,
            title="ML Engineer",
            company="Acme",
            url=canonical,
            text=f"bridge: Test\n{canonical}",
            source="link",
        )
    )


def _heartbeat_ns(ns: Any) -> Any:
    """The production heartbeat context over a started bridge namespace."""
    from cva.scheduler import heartbeat

    return SimpleNamespace(
        application=SimpleNamespace(
            bot_data={
                "service": ns.service,
                "owner_id": OWNER_ID,
                "bridge": ns.handle,
            }
        ),
        bot=ns.bot,
    ), heartbeat


def test_restart_redrives_retry_pending_to_one_late_card(
    paced_factory: Any,
) -> None:
    """Spec: retry-pending survives restart → the post-restart heartbeat sweep
    re-drives it through the queue → exactly ONE late breakdown card, state
    cleared."""
    gate = threading.Event()
    ns1 = paced_factory(
        llm=FakeLlmClient(_extraction()),
        fetcher=_AsyncGateFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL), gate),
        analyze_retry_max=5,
    )
    offer_id = _seed_bridge_row(ns1.repo, JOB_IDS[0])
    ns1.handle.enqueue_auto_analysis(
        offer_id, JOB_IDS[0], {"title": "ML", "company": "Acme", "url": OFFER_URL}
    )
    _wait_until(lambda: len(ns1.fetcher.calls) == 1)  # analysis mid-flight
    asyncio.run_coroutine_threadsafe(ns1.handle.shutdown(), ns1.loop).result(timeout=5)
    gate.set()
    row = ns1.repo.get_offer(offer_id)
    assert row.analyze_attempts == 0 and row.next_analyze_at is not None  # deferred

    # RESTART: a fresh bot over the SAME database, then the heartbeat's sweep
    ns2 = paced_factory(
        llm=FakeLlmClient(_extraction()),
        fetcher=FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
        analyze_retry_max=5,
    )
    context, heartbeat = _heartbeat_ns(ns2)
    asyncio.run(heartbeat(context))

    _wait_until(lambda: len(ns2.bot.calls) == 1)
    _time.sleep(0.1)
    assert len(ns2.bot.calls) == 1  # exactly ONE late card, never duplicated
    _chat, text, keyboard = ns2.bot.calls[0]
    assert f"📊 Oferta #{offer_id}" in text
    assert keyboard.inline_keyboard[0][0].callback_data == f"cv:{offer_id}:AI:es"
    row = ns2.repo.get_offer(offer_id)
    assert row.analyze_attempts == 0 and row.next_analyze_at is None  # cleared


def test_restart_after_exhausted_final_card_never_duplicates(
    paced_factory: Any,
) -> None:
    """Spec: the final fallback already fired pre-restart (state cleared) →
    the post-restart heartbeat sweep drives NOTHING."""
    ns1 = paced_factory(
        llm=FakeLlmClient(_extraction()),
        fetcher=_WallFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
        analyze_retry_max=0,  # first failure → immediate final card
    )
    offer_id = _seed_bridge_row(ns1.repo, JOB_IDS[0])
    ns1.handle.enqueue_auto_analysis(
        offer_id, JOB_IDS[0], {"title": "ML", "company": "Acme", "url": OFFER_URL}
    )
    _wait_until(lambda: len(ns1.bot.calls) == 1)  # final card sent pre-restart
    row = ns1.repo.get_offer(offer_id)
    assert row.analyze_attempts == 0 and row.next_analyze_at is None
    asyncio.run_coroutine_threadsafe(ns1.handle.shutdown(), ns1.loop).result(timeout=5)

    ns2 = paced_factory(
        llm=FakeLlmClient(_extraction()),
        fetcher=FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
        analyze_retry_max=0,
    )
    context, heartbeat = _heartbeat_ns(ns2)
    asyncio.run(heartbeat(context))
    _time.sleep(0.15)
    assert len(ns2.bot.calls) == 0  # no duplicate final card
    assert ns2.repo.get_offer(offer_id).estado == "pendiente_analisis"


# --- Per-payload round summary (round-summary amendment D11) -------------------
#
# After the paced queue finishes a payload's batch of NEW jobs, ONE compact
# Spanish summary goes out through the same notify channel (the recording
# bot). Individual cards keep arriving exactly as before — the summary is
# ADDITIONAL. Flag off / zero-new payloads → nothing.

from test_service import _extraction_scored


class _QueuedLlm(FakeLlmClient):
    """Pops one canned extraction per call: mixed-zone payload rounds."""

    def __init__(self, replies: list[Any]) -> None:
        super().__init__(replies[0])
        self._replies = list(replies)

    async def json_completion(
        self, system: str, user: str, schema: type[object]
    ) -> object:
        self.calls += 1
        return self._replies.pop(0)


def test_round_summary_after_mixed_payload_batch(paced_factory: Any) -> None:
    """3 new jobs (above/gray/below) → 2 individual cards, then ONE summary:
    header with the beacon, zone counts, and the discard line (score — título
    — empresa) for the below-gate job only."""
    llm = _QueuedLlm([_extraction(), _extraction_scored(64), _extraction_scored(49)])
    ns = paced_factory(
        llm=llm,
        fetcher=_PerJobFetcher(),
        round_summary=True,
    )
    resp = _post_paced(ns.handle.port, _three_job_payload())
    assert resp.status_code == 200
    assert resp.json() == {"received": 3, "new": 3, "skipped": 0}
    _wait_until(lambda: len(ns.bot.calls) == 3)  # breakdown + borderline + summary
    _time.sleep(0.1)
    assert len(ns.bot.calls) == 3  # no extra card of any kind

    _chat, breakdown, _kb = ns.bot.calls[0]
    assert breakdown.startswith("📊")  # above-gate card, unchanged
    _chat, borderline, _kb = ns.bot.calls[1]
    assert borderline.startswith("🟡")  # gray-zone card, unchanged

    _chat, summary, keyboard = ns.bot.calls[2]
    lines = summary.split("\n")
    assert lines[0] == "📊 Ronda 'Barcelona AI': 3 analizadas"
    assert (
        lines[1] == "🟢 1 sobre el umbral · 🟡 1 en zona gris · "
        "⚪ 1 descartadas por umbral"
    )
    assert lines[2] == "Descartadas (score — título — empresa):"
    assert (
        '· 49/100 — <a href="https://linkedin.com/jobs/view/7770003333">'
        "ML Engineer 2</a> @ Acme"
    ) in lines
    assert keyboard is None  # plain recap on the same notify channel


def test_round_summary_counts_failures_as_retry_pending(paced_factory: Any) -> None:
    """All 3 analyses wall → zero individual cards, ONE summary counting the
    batch as ♻️ pendientes de reintento (analyzed=0, no discard section)."""
    ns = paced_factory(
        llm=FakeLlmClient(_extraction()),
        fetcher=_WallFetcher(FetchResponse(200, "", OFFER_URL)),
        round_summary=True,
    )
    resp = _post_paced(ns.handle.port, _three_job_payload())
    assert resp.status_code == 200
    _wait_until(lambda: len(ns.bot.calls) == 1)
    _time.sleep(0.1)
    assert len(ns.bot.calls) == 1  # ONLY the summary — cards stay deferred
    _chat, summary, _kb = ns.bot.calls[0]
    assert summary.startswith("📊 Ronda 'Barcelona AI': 0 analizadas")
    assert "⚪ 0 descartadas por umbral" in summary
    assert "♻️ 3 pendientes de reintento" in summary
    assert "Descartadas" not in summary


def test_round_summary_zero_new_jobs_is_silent(paced_factory: Any) -> None:
    """Spec: a payload with 0 new jobs (all duplicates) → NO second summary,
    no extra analysis, nothing beyond the first round's 3 messages."""
    llm = _QueuedLlm([_extraction(), _extraction_scored(64), _extraction_scored(49)])
    ns = paced_factory(
        llm=llm,
        fetcher=_PerJobFetcher(),
        round_summary=True,
    )
    first = _post_paced(ns.handle.port, _three_job_payload())
    assert first.status_code == 200
    _wait_until(lambda: len(ns.bot.calls) == 3)  # 2 cards + round-1 summary
    _time.sleep(0.1)
    second = _post_paced(ns.handle.port, _three_job_payload())  # all duplicates
    assert second.status_code == 200
    assert second.json() == {"received": 3, "new": 0, "skipped": 3}
    _time.sleep(0.15)
    assert len(ns.bot.calls) == 3  # first round only — no second summary
    assert llm.calls == 3  # duplicates never re-analyzed


def test_round_summary_off_keeps_pre_amendment_silence(paced_factory: Any) -> None:
    """round_summary=false → EXACTLY the pre-amendment behavior: individual
    cards only, never a summary card."""
    ns = paced_factory(
        llm=_QueuedLlm([_extraction(), _extraction_scored(64), _extraction_scored(49)]),
        fetcher=_PerJobFetcher(),
        round_summary=False,
    )
    resp = _post_paced(ns.handle.port, _three_job_payload())
    assert resp.status_code == 200
    _wait_until(lambda: len(ns.bot.calls) == 2)  # breakdown + borderline only
    _time.sleep(0.15)
    assert len(ns.bot.calls) == 2
    assert all(not text.startswith("📊 Ronda") for _c, text, _k in ns.bot.calls)


def test_round_summary_after_last_item_pacing_delay(
    paced_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec: the summary lands only AFTER the payload's last job is processed
    INCLUDING its pacing delay. Payload A(3 jobs, all above) then B(1 job):
    during A3's inter-item sleep A's summary must NOT be sent yet; it lands
    right after the sleep, before B's card."""
    gate = threading.Event()
    events: list[tuple[str, str]] = []
    seen: list[tuple[str, float, int]] = []  # (kind, seconds, len(bot.calls))
    state: dict[str, Any] = {}

    async def fake_sleep(seconds: float) -> None:
        bot = state.get("bot")
        seen.append(("sleep", seconds, len(bot.calls) if bot is not None else -1))
        await asyncio.sleep(0)

    monkeypatch.setattr(bridge, "_pacing_sleep", fake_sleep)
    ns = paced_factory(
        llm=FakeLlmClient(_extraction()),  # every job scores 100 → all above-gate
        fetcher=_AsyncGateFetcher(
            FetchResponse(200, JSONLD_BODY, OFFER_URL), gate, events
        ),
        analyze_delay_seconds=45,
        round_summary=True,
    )
    state["bot"] = ns.bot
    resp = _post_paced(ns.handle.port, _three_job_payload())
    assert resp.status_code == 200
    solo = _three_job_payload()
    solo["jobs"] = [dict(solo["jobs"][0])]
    solo["jobs"][0]["url"] = "https://www.linkedin.com/jobs/view/8800044444/"
    solo["jobs"][0]["title"] = "Distinct Role"  # not a cross-source dup
    _post_paced(ns.handle.port, solo)
    _wait_until(lambda: len(events) == 1)  # A1 analysis gated mid-flight
    gate.set()
    # A: 3 cards + summary; B: 1 card + its own summary.
    _wait_until(lambda: len(ns.bot.calls) == 6)

    sleeps = [entry for entry in seen if entry[0] == "sleep"]
    assert len(sleeps) == 3  # after A1, A2, A3 (B1 is the tail: no sleep)
    assert sleeps[-1] == ("sleep", 45, 3)  # A3's delay ran BEFORE A's summary
    assert ns.bot.calls[3][1] == (
        "📊 Ronda 'Barcelona AI': 3 analizadas\n"
        "🟢 3 sobre el umbral · 🟡 0 en zona gris · ⚪ 0 descartadas por umbral"
    )
    assert "📊 Oferta #4" in ns.bot.calls[4][1]  # B's card AFTER A's summary
    assert ns.bot.calls[5][1] == (
        "📊 Ronda 'Barcelona AI': 1 analizadas\n"
        "🟢 1 sobre el umbral · 🟡 0 en zona gris · ⚪ 0 descartadas por umbral"
    )
    assert [_job_id_of(url) for _k, url in events] == [
        *JOB_IDS,
        "8800044444",
    ]


# --- Clickable round-summary offer links (match-calibration D4) -------------
#
# The round summary alone is sent with parse_mode="HTML"; every other card
# (breakdown, borderline, fallback) stays byte-identical. _RoundTally.record
# now also carries the canonical URL per discarded offer.


class _KwargsBot:
    """Fake bot capturing every send_message call as (args, kwargs) — used to
    assert exactly which kwargs (e.g. parse_mode) reach send_message."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def send_message(self, *args: Any, **kwargs: Any) -> bool:
        self.calls.append((args, kwargs))
        return True


def test_round_tally_record_stores_canonical_url_for_discarded_offers() -> None:
    """``_RoundTally.record`` accepts and forwards the canonical URL into a
    ``DiscardedOffer`` for every below-gate item."""
    tally = bridge._RoundTally("Barcelona AI", 1)

    tally.record("below", 42, "Title", "Company", "https://linkedin.com/jobs/view/1")

    assert tally.discarded == [
        DiscardedOffer(42, "Title", "Company", "https://linkedin.com/jobs/view/1")
    ]


def test_notify_card_forwards_parse_mode_only_when_set(paced_factory: Any) -> None:
    """``bridge._notify_card(message, keyboard, *, parse_mode=None)`` forwards
    ``parse_mode`` to ``send_message`` only when the caller sets it —
    unaffected cards keep an identical (no ``parse_mode`` kwarg) call."""
    ns = paced_factory(llm=FakeLlmClient(_extraction()), fetcher=_PerJobFetcher())
    kwargs_bot = _KwargsBot()
    ns.handle._bot = kwargs_bot

    asyncio.run_coroutine_threadsafe(
        ns.handle._notify_card("plain card", None), ns.loop
    ).result(timeout=5)
    asyncio.run_coroutine_threadsafe(
        ns.handle._notify_card("html card", None, parse_mode="HTML"), ns.loop
    ).result(timeout=5)

    assert len(kwargs_bot.calls) == 2
    _plain_args, plain_kwargs = kwargs_bot.calls[0]
    _html_args, html_kwargs = kwargs_bot.calls[1]
    assert "parse_mode" not in plain_kwargs
    assert html_kwargs["parse_mode"] == "HTML"


def test_settle_round_sends_summary_with_html_parse_mode(paced_factory: Any) -> None:
    """``_settle_round`` sends the round-summary message through
    ``_notify_card`` with ``parse_mode="HTML"`` — never for other cards."""
    ns = paced_factory(llm=FakeLlmClient(_extraction()), fetcher=_PerJobFetcher())
    kwargs_bot = _KwargsBot()
    ns.handle._bot = kwargs_bot
    tally = bridge._RoundTally("Barcelona AI", 1)
    tally.record("below", 40, "Title", "Company", "https://linkedin.com/jobs/view/1")

    asyncio.run_coroutine_threadsafe(ns.handle._settle_round(tally), ns.loop).result(
        timeout=5
    )

    assert len(kwargs_bot.calls) == 1
    _args, kwargs = kwargs_bot.calls[0]
    assert kwargs["parse_mode"] == "HTML"


def test_settle_round_is_silent_when_nothing_was_analyzed(paced_factory: Any) -> None:
    """A round with no analyzed offer and nothing pending (all closed) sends
    no summary at all."""
    ns = paced_factory(llm=FakeLlmClient(_extraction()), fetcher=_PerJobFetcher())
    kwargs_bot = _KwargsBot()
    ns.handle._bot = kwargs_bot
    tally = bridge._RoundTally("Barcelona AI", 1)
    tally.record("closed", None, "Title", "Company", "https://linkedin.com/jobs/view/1")

    asyncio.run_coroutine_threadsafe(ns.handle._settle_round(tally), ns.loop).result(
        timeout=5
    )

    assert kwargs_bot.calls == []


def test_settle_round_is_silent_when_every_offer_hit_a_wall(
    paced_factory: Any,
) -> None:
    """Walled offers (stack, eligibility, language) are discarded before any
    scoring: a round made only of them is as empty as a closed-only one."""
    ns = paced_factory(llm=FakeLlmClient(_extraction()), fetcher=_PerJobFetcher())
    kwargs_bot = _KwargsBot()
    ns.handle._bot = kwargs_bot
    tally = bridge._RoundTally("Barcelona AI", 2)
    tally.record("walled", None, "Title", "Company", None, wall_reason="idioma")
    tally.record("closed", None, "Other", "Company", None)

    for _ in range(2):
        asyncio.run_coroutine_threadsafe(
            ns.handle._settle_round(tally), ns.loop
        ).result(timeout=5)

    assert kwargs_bot.calls == []


def test_settle_round_still_sends_when_only_pending(paced_factory: Any) -> None:
    ns = paced_factory(llm=FakeLlmClient(_extraction()), fetcher=_PerJobFetcher())
    kwargs_bot = _KwargsBot()
    ns.handle._bot = kwargs_bot
    tally = bridge._RoundTally("Barcelona AI", 1)
    tally.record("failure", None, "Title", "Company", None)

    asyncio.run_coroutine_threadsafe(ns.handle._settle_round(tally), ns.loop).result(
        timeout=5
    )

    assert len(kwargs_bot.calls) == 1


@pytest.mark.parametrize(
    ("profile", "expected"),
    [("backend", "backend"), ("ai", "ai"), ("java", "ai"), (None, "ai"), (7, "ai")],
)
def test_bridge_persists_payload_profile(
    started: Any, profile: Any, expected: str
) -> None:
    """backend-profile T2: the beacon's profile is stored per offer; unknown or
    missing values fall back to 'ai'."""
    payload = _payload()
    if profile is not None:
        payload["profile"] = profile
    resp = _post(started.handle.port, json=payload, headers=_secret_header())
    assert resp.status_code == 200
    rows = _rows(started.workdir)
    assert [row["perfil"] for row in rows] == [expected]


@pytest.mark.parametrize(
    ("payload_profile", "title", "expected"),
    [
        ("backend", "AI Engineer Junior", "ai"),
        ("backend", "ingeniero de IA", "ai"),
        ("backend", "Ingeniero/a de Inteligencia Artificial", "ai"),
        ("backend", "senior LLM developer", "ai"),
        ("backend", "Backend Engineer", "backend"),
        ("backend", "Maintainer Engineer", "backend"),  # 'ai' only as a whole word
        ("backend", None, "backend"),
        # AI beacons already require an AI term in the extension filter, so
        # the payload wins when the title shows none.
        ("ai", "Software Engineer", "ai"),
        ("ai", "AI Engineer", "ai"),
    ],
)
def test_bridge_ai_title_overrides_the_beacon_profile(
    started: Any, payload_profile: str, title: str | None, expected: str
) -> None:
    payload = _payload()
    payload["profile"] = payload_profile
    payload["jobs"][0]["title"] = title
    resp = _post(started.handle.port, json=payload, headers=_secret_header())
    assert resp.status_code == 200
    assert [row["perfil"] for row in _rows(started.workdir)] == [expected]


# --- New portal through the bridge (portal-adapters T4b) ----------------------------


def test_validated_accepts_a_registered_portal_with_inline_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = {"title": "IA", "company": "Acme", "url": FAKE_URL, "text": "Oferta"}
    with pytest.raises(bridge._BadRequest):  # host not registered yet
        bridge._validated({"beacon": "b", "jobs": [dict(job)]})
    register_fake_portal(monkeypatch)
    _, jobs = bridge._validated({"beacon": "b", "jobs": [dict(job)]})
    assert jobs[0]["text"] == "Oferta"
    dropped: list[str] = []
    _, kept = bridge._validated(
        {"beacon": "b", "jobs": [{**job, "text": None}]}, dropped
    )
    assert kept == []
    assert dropped == [FAKE_URL]


def test_new_portal_inline_offer_is_stored_with_its_own_origen(
    started: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_fake_portal(monkeypatch)
    payload = {
        "beacon": "Otroportal IA",
        "jobs": [
            {
                "title": "Ingeniero IA",
                "company": "Acme",
                "location": "Madrid",
                "url": FAKE_URL + "?utm_source=beacon",
                "text": "Oferta completa de Otroportal con requisitos.",
            }
        ],
    }
    resp = _post(started.handle.port, json=payload, headers=_secret_header())
    assert resp.status_code == 200
    assert resp.json() == {"received": 1, "new": 1, "skipped": 0}
    (row,) = _rows(started.workdir)
    assert row["origen"] == "otroportal"
    assert row["ubicacion"] == "Madrid"  # the portal location is stored (v20)
    assert row["url_oferta"] == FAKE_URL.replace("www.", "")
    assert row["source_data"] == "Oferta completa de Otroportal con requisitos."


# --- safe-defaults: kill switch + enabled portals ------------------------------


def _paused_repo(workdir: Any) -> Any:
    from cva.tracker import OfferRepository

    return OfferRepository(sqlite3.connect(workdir / "data" / "app.db"))


def _config_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/config"


def test_bridge_post_is_503_while_paused_and_stores_nothing(started: Any) -> None:
    _paused_repo(started.workdir).set_paused(True)
    resp = _post(started.handle.port, json=_payload(), headers=_secret_header())
    assert resp.status_code == 503
    assert "pausa" in resp.json()["error"].lower()
    assert _rows(started.workdir) == []
    assert started.bot.calls == []


def test_bridge_post_resumes_after_the_pause_is_lifted(started: Any) -> None:
    repo = _paused_repo(started.workdir)
    repo.set_paused(True)
    repo.set_paused(False)
    resp = _post(started.handle.port, json=_payload(), headers=_secret_header())
    assert resp.status_code == 200
    assert len(_rows(started.workdir)) == 1


def test_bridge_pause_keeps_auth_first(started: Any) -> None:
    _paused_repo(started.workdir).set_paused(True)
    resp = _post(started.handle.port, json=_payload(), headers=_secret_header("bad"))
    assert resp.status_code == 401


def test_config_endpoint_requires_the_secret(started: Any) -> None:
    url = _config_url(started.handle.port)
    assert httpx.get(url).status_code == 401
    assert httpx.get(url, headers=_secret_header("wrong")).status_code == 401


def test_config_endpoint_reports_pause_and_all_portals_when_unset(
    started: Any,
) -> None:
    from cva.ingest.sources import SOURCES

    url = _config_url(started.handle.port)
    body = httpx.get(url, headers=_secret_header()).json()
    assert body == {"paused": False, "enabledPortals": list(SOURCES)}
    _paused_repo(started.workdir).set_paused(True)
    assert httpx.get(url, headers=_secret_header()).json()["paused"] is True


def test_config_endpoint_is_get_only(started: Any) -> None:
    resp = _post(started.handle.port, "/config", json={}, headers=_secret_header())
    assert resp.status_code == 404


def _register(
    workdir: Any, clean_settings: Callable[..., Settings], **over: Any
) -> Any:
    app = SimpleNamespace(post_init=None, bot=_RecordingBot(), bot_data={})
    handle = bridge.register(app, clean_settings(**over), service_stub(THRESHOLD))
    assert handle is not None
    return handle


def test_enabled_portals_reject_a_job_from_a_disabled_portal(
    workdir: Any, clean_settings: Callable[..., Settings]
) -> None:
    handle = _register(workdir, clean_settings, enabled_portals=["infojobs"])
    try:
        resp = _post(handle.port, json=_payload(), headers=_secret_header())
        config = httpx.get(_config_url(handle.port), headers=_secret_header()).json()
    finally:
        handle.stop()
    assert resp.status_code == 400
    assert "linkedin" in resp.json()["error"]
    assert "enabled_portals" in resp.json()["error"]
    assert _rows(workdir) == []
    assert config["enabledPortals"] == ["infojobs"]


def test_enabled_portals_accept_a_job_from_an_enabled_portal() -> None:
    _beacon, jobs = bridge._validated(
        _payload(), None, frozenset({"linkedin", "infojobs"})
    )
    assert len(jobs) == 1


def test_unset_enabled_portals_accept_every_registered_portal(
    started: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy (owner) behaviour: with enabled_portals unset nothing is rejected."""
    register_fake_portal(monkeypatch)
    payload = {
        "beacon": "x",
        "jobs": [{"title": "IA", "company": "A", "url": FAKE_URL, "text": "Oferta"}],
    }
    resp = _post(started.handle.port, json=payload, headers=_secret_header())
    assert resp.status_code == 200


def test_suggested_searches_only_use_enabled_portals(
    workdir: Any, clean_settings: Callable[..., Settings]
) -> None:
    handle = _register(workdir, clean_settings, enabled_portals=["infojobs"])
    try:
        url = f"http://127.0.0.1:{handle.port}/searches/suggested"
        body = httpx.get(url, headers=_secret_header()).json()
    finally:
        handle.stop()
    assert body["searches"]
    assert all(s["portals"] == ["infojobs"] for s in body["searches"])


def test_post_shutdown_hook_accepts_the_application_argument(
    workdir: Any, clean_settings: Callable[..., Settings]
) -> None:
    """PTB calls ``post_shutdown(application)``: the registered hook must take
    the app argument (it used to raise TypeError on every shutdown)."""
    app = SimpleNamespace(
        post_init=None, post_shutdown=None, bot=_RecordingBot(), bot_data={}
    )
    handle = bridge.register(app, clean_settings(), service_stub(THRESHOLD))
    assert handle is not None
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        asyncio.run_coroutine_threadsafe(app.post_init(app), loop).result(timeout=5)
        asyncio.run_coroutine_threadsafe(app.post_shutdown(app), loop).result(timeout=5)
        assert handle._worker is None and handle._closing is True
    finally:
        handle.stop()
        loop.call_soon_threadsafe(loop.stop)
