"""Beacon tests (tasks 8b.1–8b.2): parser, check_beacon, D3 degradation, sweep.

SPIKE GATE (docs/spike_guest_evidence.md): the live guest search endpoint is
WALLED (404 + JS shell, reproduced twice). Consequence: the job-id parser is
tested against SAVED HTML FIXTURES ONLY; a live check returning zero ids is an
acceptable outcome; there are ZERO live-network tests here — every fetch goes
through an injected fake Fetcher and every body through a fixture file.
"""

import asyncio
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cva.beacons import (
    BeaconPausedAlert,
    NewJobNotification,
    check_beacon,
)
from cva.beacons.parse import JobCard, parse_search_fragment
from cva.fetch import FetchResponse
from cva.service import JobService
from cva.tracker import BeaconRow, OfferRepository, RepositoryError
from tests.demo import DEMO_CLAIMS, DEMO_SYNONYMS

FIXTURES = Path("tests/fixtures/beacons")
HEALTHY = (FIXTURES / "search_healthy.html").read_text(encoding="utf-8")
WALLED = (FIXTURES / "search_walled.html").read_text(encoding="utf-8")
MALFORMED = (FIXTURES / "search_malformed.html").read_text(encoding="utf-8")

NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
ZERO_JITTER: Callable[[], timedelta] = lambda: timedelta(0)
SEARCH_URL = (
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPagePostings/search"
    "?keywords=ingeniero%20de%20ia&geoId=105088894"
)
OK_BODY = '<html><body><ul class="jobs-search__results-list"></ul></body></html>'
JSONLD_OK = (
    '<html><head><script type="application/ld+json">'
    '{"@type": "JobPosting", "title": "x"}</script></head><body>ok</body></html>'
)


class RecordingFetcher:
    """Fake Fetcher: canned response + requested-URL log (zero network)."""

    def __init__(self, body: str, status: int = 200) -> None:
        self.urls: list[str] = []
        self._response = FetchResponse(status=status, body=body, final_url="")

    async def get(self, url: str) -> FetchResponse:
        self.urls.append(url)
        return FetchResponse(
            status=self._response.status,
            body=self._response.body,
            final_url=url,
        )


def make_repo(tmp_path: Path) -> OfferRepository:
    return OfferRepository(sqlite3.connect(tmp_path / "app.db"))


def make_beacon(repo: OfferRepository, url: str = SEARCH_URL) -> BeaconRow:
    beacon_id = repo.create_beacon("ia-espana", url)
    return repo.get_beacon(beacon_id)


def check(
    beacon: BeaconRow,
    repo: OfferRepository,
    fetcher: RecordingFetcher,
    **kwargs: object,
) -> object:
    return asyncio.run(
        check_beacon(
            beacon,
            fetcher,
            repo,
            now=NOW,
            interval_minutes=480,
            jitter=ZERO_JITTER,
            **kwargs,
        )
    )


# --- Parser (pure, fixture-only per the walled-search gate) --------------------


def test_healthy_fixture_yields_three_cards_in_order() -> None:
    cards = parse_search_fragment(HEALTHY)
    assert [card.job_id for card in cards] == ["4001122334", "4005566778", "4012345678"]


def test_card_fields_title_company_age_extracted() -> None:
    first, second, third = parse_search_fragment(HEALTHY)
    assert first == JobCard(
        job_id="4001122334", title="Ingeniero de IA", company="Acme", age="2 hours ago"
    )
    assert second.company == "Beta Research"
    assert third.company is None  # card without subtitle degrades gracefully


def test_walled_shell_fixture_parses_to_zero_cards() -> None:
    # The spike's real live shape: <title>LinkedIn</title> shell, zero urns.
    assert parse_search_fragment(WALLED) == []


def test_malformed_fragment_parses_to_zero_cards() -> None:
    assert parse_search_fragment(MALFORMED) == []


# --- Beacon CRUD on the repository ---------------------------------------------


def test_create_beacon_persists_enabled_and_appears_in_list(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    beacon = make_beacon(repo)
    rows = repo.list_beacons()
    assert [row.id for row in rows] == [beacon.id]
    assert rows[0].enabled is True
    assert rows[0].status == "active"
    assert rows[0].consecutive_walls == 0
    assert rows[0].checks_today == 0


def test_get_beacon_fails_fast_on_unknown_id(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    with pytest.raises(RepositoryError):
        repo.get_beacon(999)


def test_v1_database_migrates_to_v2_daily_cap_columns(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    from cva.tracker.repo import _SCHEMA_SQL

    legacy.executescript(_SCHEMA_SQL)  # v1 DDL: beacons without daily-cap columns
    legacy.execute("PRAGMA user_version = 1")
    legacy.commit()
    legacy.close()

    repo = OfferRepository(sqlite3.connect(path))
    assert repo._conn.execute("PRAGMA user_version").fetchone()[0] == 23
    columns = {
        row[1] for row in repo._conn.execute("PRAGMA table_info(beacons)").fetchall()
    }
    assert {"checks_today", "checks_date"} <= columns


# --- check_beacon: OK path (fetch start=0, diff, persistence) -------------------


def test_ok_check_fetches_first_page_only_start_zero(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    beacon = make_beacon(repo)
    fetcher = RecordingFetcher(HEALTHY)
    check(beacon, repo, fetcher)
    assert fetcher.urls == [
        SEARCH_URL + "&start=0"
    ]  # exactly one fetch, start forced to 0


def test_ok_check_normalizes_existing_start_param(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    beacon = make_beacon(repo, SEARCH_URL + "&start=120")
    fetcher = RecordingFetcher(HEALTHY)
    check(beacon, repo, fetcher)
    assert "start=120" not in fetcher.urls[0]
    assert fetcher.urls[0].endswith("start=0")


def test_ok_check_reports_unseen_ids_and_marks_seen(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    beacon = make_beacon(repo)
    result = check(beacon, repo, RecordingFetcher(HEALTHY))
    assert result is not None
    notifications = result.notifications
    assert [n.job_id for n in notifications] == [
        "4001122334",
        "4005566778",
        "4012345678",
    ]
    first = notifications[0]
    assert isinstance(first, NewJobNotification)
    assert first.beacon_id == beacon.id
    assert first.beacon_name == "ia-espana"
    assert first.title == "Ingeniero de IA"
    assert first.company == "Acme"
    assert first.age == "2 hours ago"


def test_recheck_same_page_reports_zero_new(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    beacon = make_beacon(repo)
    fetcher = RecordingFetcher(HEALTHY)
    check(beacon, repo, fetcher)
    result = check(repo.get_beacon(beacon.id), repo, fetcher)
    assert result is not None
    assert result.notifications == []
    assert len(fetcher.urls) == 2  # both fetches happened; diff dedupes


def test_ok_check_resets_wall_state_and_schedules_next(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    beacon = make_beacon(repo)
    check(beacon, repo, RecordingFetcher(HEALTHY))
    row = repo.get_beacon(beacon.id)
    assert row.status == "active"
    assert row.consecutive_walls == 0
    assert row.last_checked_at == NOW.isoformat()
    assert row.next_check_after == (NOW + timedelta(minutes=480)).isoformat()
    assert row.checks_today == 1
    assert row.checks_date == NOW.date().isoformat()


# --- check_beacon: D3 wall degradation (backoff floors → pause + one alert) -----


def wall_case(tmp_path: Path, body: str, status: int = 200) -> tuple:
    repo = make_repo(tmp_path)
    beacon = make_beacon(repo)
    return repo, beacon, check(beacon, repo, RecordingFetcher(body, status=status))


def test_wall_increments_counter_and_applies_2h_floor(tmp_path: Path) -> None:
    repo, beacon, result = wall_case(tmp_path, WALLED, status=404)
    row = repo.get_beacon(beacon.id)
    assert row.consecutive_walls == 1
    assert row.status == "degraded"
    assert row.last_wall_kind == "js_shell"
    assert row.next_check_after == (NOW + timedelta(hours=2)).isoformat()
    assert result is not None
    assert result.notifications == []
    assert result.paused_alert is None


def test_second_consecutive_wall_applies_6h_floor(tmp_path: Path) -> None:
    repo, beacon, _ = wall_case(tmp_path, WALLED, status=404)
    check(repo.get_beacon(beacon.id), repo, RecordingFetcher(WALLED, status=404))
    row = repo.get_beacon(beacon.id)
    assert row.consecutive_walls == 2
    assert row.next_check_after == (NOW + timedelta(hours=6)).isoformat()


def test_third_wall_pauses_with_exactly_one_alert(tmp_path: Path) -> None:
    repo, beacon, _ = wall_case(tmp_path, WALLED, status=404)
    check(repo.get_beacon(beacon.id), repo, RecordingFetcher(WALLED, status=404))
    result = check(
        repo.get_beacon(beacon.id), repo, RecordingFetcher(WALLED, status=404)
    )
    row = repo.get_beacon(beacon.id)
    assert row.status == "paused"
    assert row.consecutive_walls == 3
    assert row.next_check_after == (NOW + timedelta(hours=24)).isoformat()
    assert result is not None
    alert = result.paused_alert
    assert isinstance(alert, BeaconPausedAlert)
    assert alert.beacon_id == beacon.id
    assert alert.wall_kind == "js_shell"
    assert alert.consecutive_walls == 3


def test_ok_check_after_wall_resets_counter(tmp_path: Path) -> None:
    repo, beacon, _ = wall_case(tmp_path, WALLED, status=404)
    check(repo.get_beacon(beacon.id), repo, RecordingFetcher(HEALTHY))
    row = repo.get_beacon(beacon.id)
    assert row.consecutive_walls == 0
    assert row.status == "active"
    assert row.last_wall_kind is None


def test_rate_limited_counts_as_wall(tmp_path: Path) -> None:
    repo, beacon, _ = wall_case(tmp_path, "slow down", status=429)
    row = repo.get_beacon(beacon.id)
    assert row.consecutive_walls == 1
    assert row.last_wall_kind == "rate_limited"


def test_not_found_counts_as_wall(tmp_path: Path) -> None:
    repo, beacon, _ = wall_case(tmp_path, "plain 404 page", status=404)
    row = repo.get_beacon(beacon.id)
    assert row.consecutive_walls == 1
    assert row.last_wall_kind == "not_found"


def test_resume_clears_degradation_state(tmp_path: Path) -> None:
    repo, beacon, _ = wall_case(tmp_path, WALLED, status=404)
    check(repo.get_beacon(beacon.id), repo, RecordingFetcher(WALLED, status=404))
    check(repo.get_beacon(beacon.id), repo, RecordingFetcher(WALLED, status=404))
    repo.resume_beacon(beacon.id)
    row = repo.get_beacon(beacon.id)
    assert row.status == "active"
    assert row.consecutive_walls == 0
    assert row.next_check_after is None  # due again immediately


def test_disable_beacon_persists_enabled_false(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    beacon = make_beacon(repo)
    repo.disable_beacon(beacon.id)
    assert repo.get_beacon(beacon.id).enabled is False


def test_service_resume_or_disable_unknown_id_fails_fast(tmp_path: Path) -> None:
    fetcher = RecordingFetcher(HEALTHY)
    service = make_service(tmp_path, fetcher, Clock(NOW))
    with pytest.raises(RepositoryError):
        service.resume_beacon(999)
    with pytest.raises(RepositoryError):
        service.disable_beacon(999)


# --- JobService.run_due_beacons composition (due gating, caps, sweep result) ----


class Clock:
    """Mutable injected clock: sweeps can advance past backoff/cap windows."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now = self.now + delta


def _service_with(
    conn: sqlite3.Connection,
    fetcher: RecordingFetcher,
    clock: Clock,
    *,
    interval_minutes: int = 480,
    checks_per_day: int = 3,
    global_daily_cap: int = 10,
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
        interval_minutes=interval_minutes,
        checks_per_day=checks_per_day,
        global_daily_cap=global_daily_cap,
        now_fn=clock,
        jitter_fn=ZERO_JITTER,
    )


def make_service(tmp_path: Path, fetcher: RecordingFetcher, clock: Clock) -> JobService:
    conn = sqlite3.connect(tmp_path / "service.db")
    return _service_with(conn, fetcher, clock)


def test_sweep_checks_due_beacons_sequentially(tmp_path: Path) -> None:
    fetcher = RecordingFetcher(HEALTHY)
    service = make_service(tmp_path, fetcher, Clock(NOW))
    first = service._repo.create_beacon("uno", SEARCH_URL)
    second = service._repo.create_beacon("dos", SEARCH_URL)
    sweep = asyncio.run(service.run_due_beacons())
    assert [n.beacon_id for n in sweep.notifications] == [first] * 3 + [second] * 3
    assert fetcher.urls == [SEARCH_URL + "&start=0"] * 2  # strict order, one page each


def test_sweep_skips_beacons_not_yet_due(tmp_path: Path) -> None:
    fetcher = RecordingFetcher(HEALTHY)
    clock = Clock(NOW)
    service = make_service(tmp_path, fetcher, clock)
    service._repo.create_beacon("uno", SEARCH_URL)
    asyncio.run(service.run_due_beacons())
    clock.advance(timedelta(minutes=1))  # next_check_after is NOW + 8h: not due
    asyncio.run(service.run_due_beacons())
    assert len(fetcher.urls) == 1  # second sweep made no fetch


def test_sweep_pause_alert_flows_to_result(tmp_path: Path) -> None:
    fetcher = RecordingFetcher(WALLED, status=404)
    clock = Clock(NOW)
    service = make_service(tmp_path, fetcher, clock)
    beacon_id = service._repo.create_beacon("muro", SEARCH_URL)
    asyncio.run(service.run_due_beacons())  # wall 1 → 2h floor
    clock.advance(timedelta(hours=2, minutes=1))
    asyncio.run(service.run_due_beacons())  # wall 2 → 6h floor
    clock.advance(timedelta(hours=6, minutes=1))
    sweep = asyncio.run(service.run_due_beacons())  # wall 3 → paused
    assert sweep.notifications == []
    assert len(sweep.paused_alerts) == 1
    assert sweep.paused_alerts[0].beacon_id == beacon_id
    assert service._repo.get_beacon(beacon_id).status == "paused"


def test_paused_beacon_is_not_checked_again(tmp_path: Path) -> None:
    fetcher = RecordingFetcher(WALLED, status=404)
    clock = Clock(NOW)
    service = make_service(tmp_path, fetcher, clock)
    service._repo.create_beacon("muro", SEARCH_URL)
    for _ in range(3):
        clock.advance(timedelta(hours=24))
        asyncio.run(service.run_due_beacons())
    fetcher.urls.clear()
    clock.advance(timedelta(hours=24))
    sweep = asyncio.run(service.run_due_beacons())
    assert fetcher.urls == []
    assert sweep.notifications == []
    assert sweep.paused_alerts == []  # exactly one alert ever, at the transition


def test_sweep_respects_per_beacon_daily_cap(tmp_path: Path) -> None:
    fetcher = RecordingFetcher(HEALTHY)
    clock = Clock(NOW)
    conn = sqlite3.connect(tmp_path / "cap.db")
    service = _service_with(conn, fetcher, clock, checks_per_day=1)
    service._repo.create_beacon("cap", SEARCH_URL)
    asyncio.run(service.run_due_beacons())  # check #1 (cap 1 reached)
    clock.advance(timedelta(hours=8))  # due again, same day
    asyncio.run(service.run_due_beacons())
    assert len(fetcher.urls) == 1  # skipped until the next day
    clock.advance(timedelta(hours=16))  # next day: stale checks_date resets count
    asyncio.run(service.run_due_beacons())
    assert len(fetcher.urls) == 2


def test_sweep_enforces_global_daily_cap(tmp_path: Path) -> None:
    fetcher = RecordingFetcher(HEALTHY)
    conn = sqlite3.connect(tmp_path / "global.db")
    service = _service_with(conn, fetcher, Clock(NOW), global_daily_cap=1)
    first = service._repo.create_beacon("uno", SEARCH_URL)
    service._repo.create_beacon("dos", SEARCH_URL)
    sweep = asyncio.run(service.run_due_beacons())
    assert len(fetcher.urls) == 1  # second beacon blocked by the global cap
    assert [n.beacon_id for n in sweep.notifications] == [first] * 3


def test_sweep_skips_disabled_beacon(tmp_path: Path) -> None:
    fetcher = RecordingFetcher(HEALTHY)
    service = make_service(tmp_path, fetcher, Clock(NOW))
    beacon_id = service._repo.create_beacon("off", SEARCH_URL)
    service._repo.disable_beacon(beacon_id)
    sweep = asyncio.run(service.run_due_beacons())
    assert fetcher.urls == []
    assert sweep.notifications == []


def test_sweep_daily_count_resets_after_date_rollover(tmp_path: Path) -> None:
    fetcher = RecordingFetcher(HEALTHY)
    service = make_service(tmp_path, fetcher, Clock(NOW))
    beacon_id = service._repo.create_beacon("viejo", SEARCH_URL)
    yesterday = (NOW - timedelta(days=1)).isoformat()
    service._repo.record_beacon_check(
        beacon_id,
        checked_at=yesterday,
        next_check_after=yesterday,  # due again
        status="active",
        consecutive_walls=0,
        last_wall_kind=None,
        checks_today=3,  # cap reached... yesterday
        checks_date=(NOW - timedelta(days=1)).date().isoformat(),
    )
    sweep = asyncio.run(service.run_due_beacons())
    assert len(fetcher.urls) == 1  # stale checks_date counts as zero today
    assert len(sweep.notifications) == 3
