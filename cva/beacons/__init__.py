"""Beacon checks: fetch → classify → diff → D3 degradation (tasks 8b.1–8b.2).

One :func:`check_beacon` call is EXACTLY ONE fetch of the beacon's first results
page (``start=0`` forced; no in-loop retries, ever — design D1/D3). Every
response passes through the shared wall classifier:

- OK → parse the fragment (fixture-driven; live zero-ids is acceptable per the
  slice-1 spike gate), diff against ``seen_job_ids`` (only unseen ids are new),
  persist wall-state reset + next due time, and return the new-job
  notifications.
- Any non-OK wall → increment ``consecutive_walls``, apply the growing backoff
  floors 2h → 6h → 24h via ``next_check_after``, and after 3 consecutive walls
  transition to ``paused`` returning EXACTLY ONE pause alert (the transition can
  only happen once per degradation cycle because paused beacons are never
  checked again until resumed).

Timestamps are timezone-aware UTC ISO strings; ``now`` is injected so the whole
module is deterministic under test. User-facing copy (alert text built by the
notifier) stays Spanish per the slice-8a decision; code and identifiers are
English.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Final
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field

from cva.beacons.parse import JobCard, parse_search_fragment
from cva.fetch import Fetcher
from cva.tracker import BeaconRow, OfferRepository
from cva.wall import WallKind, classify

BACKOFF_FLOORS: Final[tuple[timedelta, ...]] = (
    timedelta(hours=2),
    timedelta(hours=6),
    timedelta(hours=24),
)


class NewJobNotification(BaseModel):
    """One new (never-seen) job found by a beacon check."""

    beacon_id: int
    beacon_name: str
    job_id: str
    title: str | None = None
    company: str | None = None
    age: str | None = None


class BeaconPausedAlert(BaseModel):
    """One-shot alert raised at the transition to ``paused`` (3rd wall)."""

    beacon_id: int
    beacon_name: str
    wall_kind: str
    consecutive_walls: int


class CheckResult(BaseModel):
    """Outcome of one beacon check: new-job notifications + optional pause alert."""

    notifications: list[NewJobNotification] = Field(default_factory=list)
    paused_alert: BeaconPausedAlert | None = None


class SweepResult(BaseModel):
    """Outcome of one heartbeat sweep over every due beacon (service seam)."""

    notifications: list[NewJobNotification] = Field(default_factory=list)
    paused_alerts: list[BeaconPausedAlert] = Field(default_factory=list)


def _first_page_url(url: str) -> str:
    """Force ``start=0`` on the search URL (first page only, design D11).

    Key/value pairs are spliced TEXTUALLY (no ``parse_qsl``/``urlencode`` round
    trip) so the saved query keeps its original percent-encoding verbatim.
    """
    parts = urlsplit(url)
    kept = [
        pair
        for pair in parts.query.split("&")
        if pair and pair.partition("=")[0] != "start"
    ]
    kept.append("start=0")
    return urlunsplit(parts._replace(query="&".join(kept)))


def _today(now: datetime) -> str:
    return now.date().isoformat()


def checks_done_today(beacon: BeaconRow, now: datetime) -> int:
    """Checks already made today; a stale ``checks_date`` counts as zero."""
    return beacon.checks_today if beacon.checks_date == _today(now) else 0


def _notifications(
    beacon: BeaconRow, cards: list[JobCard], new_ids: list[str]
) -> list[NewJobNotification]:
    by_id = {card.job_id: card for card in cards}
    return [
        NewJobNotification(
            beacon_id=beacon.id,
            beacon_name=beacon.name,
            job_id=job_id,
            title=by_id[job_id].title,
            company=by_id[job_id].company,
            age=by_id[job_id].age,
        )
        for job_id in new_ids
    ]


async def check_beacon(
    beacon: BeaconRow,
    fetcher: Fetcher,
    repo: OfferRepository,
    *,
    now: datetime,
    interval_minutes: int = 480,
    jitter: Callable[[], timedelta] | None = None,
) -> CheckResult:
    """Check one beacon: single fetch, wall-classified, diff-deduped.

    ``jitter`` randomizes the next due time (±30 min at each completion, design
    D11); ``None`` means zero jitter (deterministic tests).
    """
    response = await fetcher.get(_first_page_url(beacon.url))
    verdict = classify(response.status, response.body, response.final_url)
    done_today = checks_done_today(beacon, now)

    if verdict.kind is WallKind.OK:
        # Jitter (±30 min at each completing check, design D11) shifts the next
        # interval-based due time; wall backoff floors below are never jittered.
        offset = jitter() if jitter is not None else timedelta(0)
        cards = parse_search_fragment(response.body)
        new_ids = repo.diff_seen(beacon.id, [card.job_id for card in cards])
        next_due = now + timedelta(minutes=interval_minutes) + offset
        repo.record_beacon_check(
            beacon.id,
            checked_at=now.isoformat(),
            next_check_after=next_due.isoformat(),
            status="active",
            consecutive_walls=0,
            last_wall_kind=None,
            checks_today=done_today + 1,
            checks_date=_today(now),
        )
        return CheckResult(notifications=_notifications(beacon, cards, new_ids))

    walls = beacon.consecutive_walls + 1
    floor = BACKOFF_FLOORS[min(walls, len(BACKOFF_FLOORS)) - 1]
    paused = walls >= len(BACKOFF_FLOORS)
    repo.record_beacon_check(
        beacon.id,
        checked_at=now.isoformat(),
        next_check_after=(now + floor).isoformat(),
        status="paused" if paused else "degraded",
        consecutive_walls=walls,
        last_wall_kind=verdict.kind.value,
        checks_today=done_today + 1,
        checks_date=_today(now),
    )
    alert = (
        BeaconPausedAlert(
            beacon_id=beacon.id,
            beacon_name=beacon.name,
            wall_kind=verdict.kind.value,
            consecutive_walls=walls,
        )
        if paused
        else None
    )
    return CheckResult(paused_alert=alert)
