"""Beacon health (offer-gates-and-health T5): scan history, evaluator, endpoint."""

import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, get_args

import httpx
import pytest

from cva import bridge
from cva.beacons.health import (
    PARSER_BROKEN,
    UNCONFIRMED_STREAK,
    YIELD_DROP,
    BeaconScanRow,
    new_alerts,
)
from cva.tracker import OfferRepository
from cva.tracker.repo import BEACON_SCANS_KEPT, SCHEMA_VERSION, ScanStatus

# --- evaluator (pure) -----------------------------------------------------------------


def S(found: int = 5, empty: int = 0, status: str = "ok") -> BeaconScanRow:
    return BeaconScanRow(
        beacon_id="b1",
        beacon_name="Barcelona AI",
        at="2026-10-18T10:00:00+00:00",
        status=status,  # type: ignore[arg-type]
        jobs_found=found,
        empty_company=empty,
    )


YIELD_MSG = (
    "⚠️ El beacon «Barcelona AI» devolvía ofertas y ahora da 0 (2 escaneos "
    "seguidos). Puede que el portal haya cambiado o la búsqueda esté rota."
)
PARSER_MSG = (
    "⚠️ El beacon «Barcelona AI»: más del 50% de las ofertas llegan sin "
    "empresa (¿cambió el HTML del portal?)."
)


UNCONFIRMED_MSG = (
    "⚠️ El beacon «Barcelona AI» lleva 3 escaneos vacíos sin confirmar. "
    "Puede ser un bloqueo temporal de LinkedIn o que el portal haya cambiado "
    "su HTML; ábrelo a mano para comprobarlo."
)


def U() -> BeaconScanRow:
    return S(0, status="empty_unconfirmed")


def _alerts(scans: list[BeaconScanRow], alerted: set[str] | None = None) -> Any:
    return new_alerts("Barcelona AI", scans, alerted or set())


def test_yield_drop_after_two_zero_scans_following_healthy_history() -> None:
    alerts, state = _alerts([S(0), S(0), S(4), S(3), S(5), S(0), S(0)])
    assert [a.message for a in alerts] == [YIELD_MSG]
    assert state == {YIELD_DROP}


@pytest.mark.parametrize(
    "scans",
    [
        [S(0), S(3), S(4), S(5), S(6), S(7)],  # a single zero
        [S(0), S(0), S(4), S(3), S(0), S(0), S(0)],  # only 2 of 5 were healthy
        [S(0), S(0)],  # no history at all
        [S(3), S(0), S(4), S(5), S(6), S(7)],  # latest is healthy
    ],
)
def test_no_yield_drop(scans: list[BeaconScanRow]) -> None:
    alerts, state = _alerts(scans)
    assert alerts == [] and state == set()


def test_yield_drop_needs_exactly_three_of_five_and_ignores_older_scans() -> None:
    three = [S(0), S(0), S(4), S(0), S(3), S(0), S(5)]
    assert _alerts(three)[0] != []
    older_only = [S(0), S(0), S(0), S(0), S(0), S(0), S(0), S(9), S(9), S(9)]
    assert _alerts(older_only)[0] == []


def test_yield_drop_with_short_history_counts_what_exists() -> None:
    assert _alerts([S(0), S(0), S(4), S(3), S(5)])[0] != []


def test_rate_limited_session_and_error_scans_are_ignored() -> None:
    noisy = [
        S(0, status="rate_limited"),
        S(0),
        S(0, status="session_expired"),
        S(0),
        S(0, status="error"),
        S(4),
        S(3),
        S(5),
    ]
    alerts, _ = _alerts(noisy)
    assert [a.message for a in alerts] == [YIELD_MSG]
    # failures alone never trigger anything
    assert _alerts([S(0, status="rate_limited")] * 3 + [S(4), S(5), S(6)])[0] == []


def test_failed_scans_neither_trigger_nor_reset_an_episode() -> None:
    scans = [S(0, status="rate_limited"), S(0), S(0), S(4), S(3), S(5)]
    alerts, state = _alerts(scans, {YIELD_DROP})
    assert alerts == [] and state == {YIELD_DROP}


def test_unconfirmed_empty_scans_never_count_as_breakage() -> None:
    # An empty page without the portal's "no results" copy may be a soft
    # throttle: a short run of them triggers nothing (a long one does, see the
    # unconfirmed-streak tests) ...
    assert _alerts([U(), U(), S(4), S(3), S(5), S(6), S(7)])[0] == []
    # ... nor hides a real drop that surrounds it ...
    scans = [S(0), S(0, status="empty_unconfirmed"), S(0), S(4), S(3), S(5)]
    assert [a.message for a in _alerts(scans)[0]] == [YIELD_MSG]
    # ... nor resets an episode that was already alerted.
    alerts, state = _alerts(
        [S(0, status="empty_unconfirmed"), S(0), S(0), S(4), S(3), S(5)], {YIELD_DROP}
    )
    assert alerts == [] and state == {YIELD_DROP}


def test_parser_broken_when_most_jobs_have_no_company() -> None:
    alerts, state = _alerts([S(4, empty=3), S(5)])
    assert [a.message for a in alerts] == [PARSER_MSG]
    assert state == {PARSER_BROKEN}


@pytest.mark.parametrize(
    "scan",
    [S(4, empty=2), S(3, empty=3), S(10, empty=5), S(6, empty=0)],
)
def test_parser_not_broken(scan: BeaconScanRow) -> None:
    assert _alerts([scan])[0] == []


def test_parser_broken_looks_at_latest_ok_scan_only() -> None:
    alerts, _ = _alerts([S(0, status="error"), S(8, empty=7)])
    assert [a.message for a in alerts] == [PARSER_MSG]
    assert _alerts([S(8, empty=0), S(8, empty=8)])[0] == []


def test_long_unconfirmed_streak_after_healthy_history_alerts() -> None:
    alerts, state = _alerts([U(), U(), U(), S(4), S(3), S(5), S(6), S(7)])
    assert [a.condition for a in alerts] == [UNCONFIRMED_STREAK]
    assert [a.message for a in alerts] == [UNCONFIRMED_MSG]
    assert state == {UNCONFIRMED_STREAK}


@pytest.mark.parametrize(
    "scans",
    [
        [U(), U(), S(4), S(3), S(5), S(6)],  # only 2 unconfirmed
        [U(), U(), U(), S(4), S(0), S(0), S(3), S(0), S(0)],  # 2 of 5 healthy
        [U(), U(), U()],  # no history: a portal that never listed anything
        [S(0), U(), U(), S(4), S(3), S(5)],  # a confirmed empty scan breaks it
        [S(3), U(), U(), U(), S(4), S(3), S(5)],  # latest scan found jobs
    ],
)
def test_no_unconfirmed_streak_alert(scans: list[BeaconScanRow]) -> None:
    assert _alerts(scans)[0] == []


def test_failed_scans_do_not_count_or_break_the_unconfirmed_streak() -> None:
    scans = [
        U(),
        S(0, status="rate_limited"),
        U(),
        S(0, status="session_expired"),
        U(),
        S(0, status="error"),
        S(4),
        S(3),
        S(5),
    ]
    assert [a.message for a in _alerts(scans)[0]] == [UNCONFIRMED_MSG]


def test_unconfirmed_streak_episode_alerts_once_and_clears_on_recovery() -> None:
    history = [S(4), S(3), S(5), S(6), S(7)]
    first, state = _alerts([U(), U(), U(), *history])
    again, state = _alerts([U(), U(), U(), U(), *history], state)
    assert len(first) == 1 and again == [] and state == {UNCONFIRMED_STREAK}
    # a scan with jobs ends the episode ...
    cleared, state = _alerts([S(4), U(), U(), U(), U(), *history], state)
    assert cleared == [] and state == set()
    # ... and a confirmed empty scan does too (it is a real answer)
    again2, state = _alerts([U(), U(), U(), *history], state)
    assert len(again2) == 1
    confirmed, state = _alerts([S(0), U(), U(), U(), *history], state)
    assert confirmed == [] and state == set()


def test_alert_once_per_episode_then_again_after_recovery() -> None:
    episode = [S(0), S(0), S(4), S(3), S(5)]
    first, state = _alerts(episode)
    assert len(first) == 1
    # a third zero scan: same episode, no repeat
    again, state = _alerts([S(0), *episode], state)
    assert again == [] and state == {YIELD_DROP}
    # healthy scan clears the episode ...
    recovered, state = _alerts([S(4), S(0), S(0), S(0), S(4), S(3), S(5)], state)
    assert recovered == [] and state == set()
    # ... and a new drop alerts again
    second, state = _alerts([S(0), S(0), S(4), S(4), S(5)], state)
    assert len(second) == 1 and state == {YIELD_DROP}


def test_parser_episode_alerts_once() -> None:
    first, state = _alerts([S(5, empty=4)])
    again, state = _alerts([S(6, empty=5), S(5, empty=4)], state)
    assert len(first) == 1 and again == []
    cleared, state = _alerts([S(6), S(6, empty=5)], state)
    assert cleared == [] and state == set()


# --- migration v12 and repository ------------------------------------------------------


def _repo(tmp_path: Path, name: str = "h.db") -> OfferRepository:
    return OfferRepository(
        sqlite3.connect(tmp_path / name, check_same_thread=False),
        clock=lambda: "2026-10-18",
    )


def test_fresh_db_is_v12_with_beacon_scans_table_and_index(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert SCHEMA_VERSION == 23
    cols = [r[1] for r in repo._conn.execute("PRAGMA table_info(beacon_scans)")]
    assert cols == [
        "id",
        "beacon_id",
        "beacon_name",
        "at",
        "status",
        "jobs_found",
        "empty_company",
    ]
    indexed = [
        [c[2] for c in repo._conn.execute(f"PRAGMA index_info({i[1]})")]
        for i in repo._conn.execute("PRAGMA index_list(beacon_scans)")
    ]
    assert ["beacon_id", "at"] in indexed


def test_v11_db_migrates_to_v12(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    repo = OfferRepository(sqlite3.connect(path))
    repo._conn.execute("DROP TABLE beacon_scans")
    repo._conn.execute("PRAGMA user_version = 11")
    repo._conn.commit()
    repo._conn.close()
    migrated = OfferRepository(sqlite3.connect(path))
    assert migrated._conn.execute("PRAGMA user_version").fetchone()[0] == 23
    assert migrated.recent_beacon_scans("b1") == []


def test_migration_v12_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cva.tracker.repo as repo_mod

    path = tmp_path / "old.db"
    repo = OfferRepository(sqlite3.connect(path))
    repo._conn.execute("DROP TABLE beacon_scans")
    repo._conn.execute("PRAGMA user_version = 11")
    repo._conn.commit()
    repo._conn.close()
    broken = repo_mod._MIGRATION_V12.replace(
        "PRAGMA user_version = 12;",
        "SELECT * FROM no_such_table;\nPRAGMA user_version = 12;",
    )
    assert broken != repo_mod._MIGRATION_V12
    monkeypatch.setattr(repo_mod, "_MIGRATION_V12", broken)
    conn = sqlite3.connect(path)
    with pytest.raises(sqlite3.OperationalError):
        OfferRepository(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 11
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert "beacon_scans" not in tables


def _record(repo: OfferRepository, n: int, beacon_id: str = "b1") -> None:
    repo.record_beacon_scan(
        beacon_id=beacon_id,
        beacon_name="Barcelona AI",
        at=f"2026-10-18T10:{n // 60:02d}:{n % 60:02d}+00:00",
        status="ok",
        jobs_found=n,
        empty_company=0,
    )


def test_recent_scans_are_newest_first(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    for n in range(3):
        _record(repo, n)
    assert [s.jobs_found for s in repo.recent_beacon_scans("b1")] == [2, 1, 0]
    assert [s.jobs_found for s in repo.recent_beacon_scans("b1", limit=2)] == [2, 1]
    assert repo.recent_beacon_scans("other") == []


def test_scans_are_pruned_to_the_last_fifty_per_beacon(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    for n in range(55):
        _record(repo, n)
    _record(repo, 1, beacon_id="b2")
    scans = repo.recent_beacon_scans("b1", limit=100)
    assert len(scans) == 50
    assert scans[0].jobs_found == 54 and scans[-1].jobs_found == 5
    assert len(repo.recent_beacon_scans("b2")) == 1


def _scan_at(repo: OfferRepository, at: str, found: int, beacon_id: str = "b1") -> None:
    repo.record_beacon_scan(
        beacon_id=beacon_id,
        beacon_name="Barcelona AI",
        at=at,
        status="ok",
        jobs_found=found,
        empty_company=0,
    )


def _minute(n: int) -> str:
    return f"2026-10-18T10:{n // 60:02d}:{n % 60:02d}.000+00:00"


def test_history_follows_insertion_order_not_the_reported_clock(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _scan_at(repo, "2026-10-18T12:00:00.000+00:00", 1)
    _scan_at(repo, "2026-10-18T09:00:00.000+00:00", 2)  # the client clock went back
    _scan_at(repo, "2099-01-01T00:00:00.000+00:00", 3)  # a skewed clock, far ahead
    _scan_at(repo, "2026-10-18T10:00:00.000+00:00", 4)
    assert [s.jobs_found for s in repo.recent_beacon_scans("b1")] == [4, 3, 2, 1]


def test_the_scan_just_recorded_is_never_pruned(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    for n in range(BEACON_SCANS_KEPT):
        _scan_at(repo, _minute(n), 100 + n)
    _scan_at(repo, "1999-01-01T00:00:00.000+00:00", 7)  # older than everything kept
    scans = repo.recent_beacon_scans("b1", limit=BEACON_SCANS_KEPT * 2)
    assert len(scans) == BEACON_SCANS_KEPT
    assert scans[0].jobs_found == 7  # the newest report survives its own prune
    assert scans[-1].jobs_found == 101  # the oldest RECORDED one made room


def test_a_far_future_scan_ages_out_instead_of_sticking_to_the_top(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _scan_at(repo, "2099-01-01T00:00:00.000+00:00", 999)
    for n in range(BEACON_SCANS_KEPT):
        _scan_at(repo, _minute(n), n)
    scans = repo.recent_beacon_scans("b1", limit=BEACON_SCANS_KEPT * 2)
    assert 999 not in [s.jobs_found for s in scans]
    assert scans[0].jobs_found == BEACON_SCANS_KEPT - 1


# --- endpoint --------------------------------------------------------------------------


class _Server:
    def __init__(
        self,
        repo: OfferRepository,
        fail_send: bool = False,
        hold: threading.Event | None = None,
    ) -> None:
        self.repo = repo
        self.sent: list[str] = []
        self.attempts = 0
        self.failed = 0
        self.fail_send = fail_send
        self.hold = hold

        def sender(message: str, keyboard: Any) -> None:
            self.attempts += 1
            if self.hold is not None:
                self.hold.wait(timeout=5)  # a slow Telegram send, in flight
            if self.fail_send:
                self.failed += 1
                raise RuntimeError("telegram down")
            self.sent.append(message)

        self.handler = bridge._make_handler(
            secret="s",
            repo=repo,
            sender=sender,
            threshold=65,
            lock=threading.Lock(),
        )
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self.handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def post(
        self, body: Any, secret: str | None = "s", path: str = "/scan-report"
    ) -> httpx.Response:
        port = self.httpd.server_address[1]
        headers = {"X-CVA-Bridge-Secret": secret} if secret else {}
        return httpx.post(f"http://127.0.0.1:{port}{path}", headers=headers, json=body)

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture()
def srv(tmp_path: Path) -> Any:
    made: list[_Server] = []

    def make(**kwargs: Any) -> _Server:
        server = _Server(_repo(tmp_path), **kwargs)
        made.append(server)
        return server

    yield make
    for server in made:
        server.close()


def _report(n: int = 0, found: int = 5, empty: int = 0, **over: Any) -> dict[str, Any]:
    body = {
        "beacon_id": "b1",
        "beacon_name": "Barcelona AI",
        "at": f"2026-10-18T11:{n:02d}:00.000Z",
        "status": "ok",
        "jobs_found": found,
        "empty_company": empty,
    }
    body.update(over)
    return body


def _wait(cond: Any) -> None:
    deadline = time.monotonic() + 3
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.01)


def test_scan_report_is_recorded(srv: Any) -> None:
    server = srv()
    assert server.post(_report(found=7, empty=2)).status_code == 200
    (scan,) = server.repo.recent_beacon_scans("b1")
    assert (scan.status, scan.jobs_found, scan.empty_company) == ("ok", 7, 2)
    assert scan.beacon_name == "Barcelona AI"


def test_scan_report_requires_the_bridge_secret(srv: Any) -> None:
    server = srv()
    assert server.post(_report(), secret="wrong").status_code == 401
    assert server.post(_report(), secret=None).status_code == 401
    assert server.repo.recent_beacon_scans("b1") == []


def test_scan_report_get_and_unknown_paths(srv: Any) -> None:
    server = srv()
    assert server.post(_report(), path="/nope").status_code == 404
    port = server.httpd.server_address[1]
    assert httpx.get(f"http://127.0.0.1:{port}/scan-report").status_code == 405


@pytest.mark.parametrize(
    "body",
    [
        [],
        "x",
        {},
        _report(beacon_id=""),
        _report(beacon_id=5),
        _report(beacon_name=""),
        _report(beacon_name="x" * 201),
        _report(at="yesterday"),
        _report(at=None),
        _report(status="weird"),
        _report(status=None),
        _report(jobs_found=-1),
        _report(jobs_found=1.5),
        _report(jobs_found=True),
        _report(jobs_found="3"),
        _report(found=3, empty=4),
        _report(empty=-1),
    ],
)
def test_scan_report_validation_rejects_bad_payloads(srv: Any, body: Any) -> None:
    server = srv()
    assert server.post(body).status_code == 400
    assert server.repo.recent_beacon_scans("b1") == []


@pytest.mark.parametrize(
    "body",
    [
        # wrong JSON types must be a 400, never an unhandled 500 (a list or dict
        # status used to blow up the set membership test)
        _report(status=["ok"]),
        _report(status={"ok": 1}),
        _report(status=1),
        _report(status=True),
        _report(beacon_id=["b1"]),
        _report(beacon_id={"id": "b1"}),
        _report(beacon_name=["Barcelona AI"]),
        _report(beacon_name=True),
        _report(at=["2026-10-18T10:00:00Z"]),
        _report(at={"iso": "2026-10-18T10:00:00Z"}),
        _report(at=1760788800),
        _report(at=True),
        _report(jobs_found=[1]),
        _report(jobs_found={"n": 1}),
        _report(jobs_found=None),
        _report(empty_company=True),
        _report(empty_company=[0]),
        _report(empty_company=0.0),
        # values that parse but cannot be stored
        _report(jobs_found=10**30),
        _report(jobs_found=10_001),
        _report(at="0001-01-01T00:00:00+05:00"),
    ],
)
def test_scan_report_wrong_types_are_400_never_500(srv: Any, body: Any) -> None:
    server = srv()
    assert server.post(body).status_code == 400
    assert server.repo.recent_beacon_scans("b1") == []


def test_scan_report_accepts_the_largest_plausible_counts(srv: Any) -> None:
    server = srv()
    assert server.post(_report(found=10_000, empty=10_000)).status_code == 200
    (scan,) = server.repo.recent_beacon_scans("b1")
    assert (scan.jobs_found, scan.empty_company) == (10_000, 10_000)


@pytest.mark.parametrize("status", get_args(ScanStatus))
def test_scan_report_accepts_every_scan_status(srv: Any, status: str) -> None:
    server = srv()
    assert server.post(_report(status=status)).status_code == 200
    (scan,) = server.repo.recent_beacon_scans("b1")
    assert scan.status == status


def test_bad_status_error_lists_the_statuses_the_model_defines(srv: Any) -> None:
    response = srv().post(_report(status="weird"))
    assert response.status_code == 400
    message = response.json()["error"]
    assert all(status in message for status in get_args(ScanStatus))


def test_unconfirmed_empty_is_a_known_scan_status() -> None:
    assert "empty_unconfirmed" in get_args(ScanStatus)


def test_unconfirmed_empty_scans_do_not_alert_through_the_endpoint(srv: Any) -> None:
    server = srv()
    for n, found in enumerate([4, 3, 5]):
        server.post(_report(n, found=found))
    for n in (10, 11):
        reply = server.post(_report(n, found=0, status="empty_unconfirmed"))
        assert reply.status_code == 200
    time.sleep(0.1)
    assert server.sent == []  # soft throttles are not a yield drop
    server.post(_report(12, found=0, status="empty_unconfirmed"))
    _wait(lambda: server.sent)  # but a long unconfirmed streak is reported
    assert server.sent == [UNCONFIRMED_MSG]
    for n in (13, 14):
        server.post(_report(n, found=0))  # two confirmed-empty scans are a drop
    _wait(lambda: len(server.sent) == 2)
    assert server.sent == [UNCONFIRMED_MSG, YIELD_MSG]
    statuses = [s.status for s in server.repo.recent_beacon_scans("b1")]
    assert statuses.count("empty_unconfirmed") == 3  # recorded, just not counted


NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def test_scan_time_up_to_a_day_ahead_is_kept() -> None:
    kept = bridge._validated_scan(_report(at="2026-10-19T12:00:00Z"), now=NOW)
    assert kept["at"] == "2026-10-19T12:00:00.000+00:00"
    past = bridge._validated_scan(_report(at="2026-10-18T07:00:00+02:00"), now=NOW)
    assert past["at"] == "2026-10-18T05:00:00.000+00:00"


@pytest.mark.parametrize(
    "at",
    [
        "2026-10-19T12:00:01Z",  # one second past the allowed skew
        "2999-01-01T00:00:00Z",
        "9999-12-31T23:59:59-05:00",  # cannot even be converted to UTC
    ],
)
def test_scan_time_far_in_the_future_becomes_server_time(at: str) -> None:
    clamped = bridge._validated_scan(_report(at=at), now=NOW)
    assert clamped["at"] == "2026-10-18T12:00:00.000+00:00"


def test_future_scan_time_is_stored_as_server_time(srv: Any) -> None:
    server = srv()
    before = datetime.now(UTC)
    assert server.post(_report(at="2999-01-01T00:00:00Z")).status_code == 200
    after = datetime.now(UTC)
    (scan,) = server.repo.recent_beacon_scans("b1")
    stored = datetime.fromisoformat(scan.at)
    assert before - timedelta(seconds=1) <= stored <= after + timedelta(seconds=1)


def test_oversized_scan_report_is_rejected(srv: Any) -> None:
    server = srv()
    big = _report(beacon_name="x" * (bridge.MAX_BODY_BYTES + 10))
    assert server.post(big).status_code == 413


def test_yield_drop_alerts_once_through_the_endpoint(srv: Any) -> None:
    server = srv()
    for n, found in enumerate([4, 3, 5, 0]):
        server.post(_report(n, found=found))
    assert server.sent == []
    server.post(_report(10, found=0))
    _wait(lambda: server.sent)
    assert server.sent == [YIELD_MSG]
    server.post(_report(11, found=0))
    server.post(_report(12, found=0, status="rate_limited"))
    time.sleep(0.1)
    assert server.sent == [YIELD_MSG]  # same episode: no repeat
    for n in (13, 14, 15):
        server.post(_report(n, found=4))  # recovery
    server.post(_report(16, found=0))
    server.post(_report(17, found=0))
    _wait(lambda: len(server.sent) == 2)
    assert server.sent == [YIELD_MSG, YIELD_MSG]


def test_parser_break_alerts_through_the_endpoint(srv: Any) -> None:
    server = srv()
    server.post(_report(found=6, empty=5))
    _wait(lambda: server.sent)
    assert server.sent == [PARSER_MSG]


def test_failed_alert_send_is_retried_on_the_next_scan(srv: Any) -> None:
    server = srv(fail_send=True)
    assert server.post(_report(found=6, empty=5)).status_code == 200
    time.sleep(0.1)
    assert server.sent == []
    server.fail_send = False
    server.post(_report(1, found=6, empty=5))
    _wait(lambda: server.sent)
    assert server.sent == [PARSER_MSG]


def test_concurrent_reports_alert_once(srv: Any) -> None:
    hold = threading.Event()
    server = srv(hold=hold)
    assert server.post(_report(found=6, empty=5)).status_code == 200
    _wait(lambda: server.attempts == 1)  # the first alert is in flight, unconfirmed
    for n in (1, 2):
        assert server.post(_report(n, found=6, empty=5)).status_code == 200
    time.sleep(0.1)
    assert server.attempts == 1  # the other reports did not start a second send
    hold.set()
    _wait(lambda: server.sent)
    time.sleep(0.1)
    assert server.sent == [PARSER_MSG]
    server.post(_report(3, found=6, empty=5))
    time.sleep(0.1)
    assert server.attempts == 1  # confirmed: the episode stays alerted


def test_failed_in_flight_alert_releases_the_claim(srv: Any) -> None:
    hold = threading.Event()
    server = srv(hold=hold, fail_send=True)
    server.post(_report(found=6, empty=5))
    _wait(lambda: server.attempts == 1)
    server.post(_report(1, found=6, empty=5))  # held back by the in-flight claim
    time.sleep(0.1)
    assert server.attempts == 1
    hold.set()  # the in-flight send fails now and gives its claim back
    _wait(lambda: server.failed == 1)
    server.fail_send = False
    # Reports keep arriving until the failed claim is released and one retries.
    deadline = time.monotonic() + 5
    n = 2
    while not server.sent and time.monotonic() < deadline:
        server.post(_report(n, found=6, empty=5))
        n += 1
        _wait(lambda: server.sent)
    assert server.sent == [PARSER_MSG]


def test_unanswered_report_does_not_leak_its_alert_claim(
    srv: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = srv()
    real = server.handler._send_json
    calls = {"n": 0}

    def flaky(self: Any, status: int, payload: dict[str, Any]) -> None:
        calls["n"] += 1
        if calls["n"] == 1:  # the client went away before reading the 200
            raise BrokenPipeError
        real(self, status, payload)

    monkeypatch.setattr(server.handler, "_send_json", flaky)
    assert server.post(_report(found=6, empty=5)).status_code == 500
    assert server.sent == []
    server.post(_report(1, found=6, empty=5))
    _wait(lambda: server.sent)
    assert server.sent == [PARSER_MSG]  # the claim was released, so it retried
