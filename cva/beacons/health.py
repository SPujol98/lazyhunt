"""Beacon health: alert when a beacon silently stops working.

Pure evaluator over a beacon's recent scans (newest first) plus the once-per-
episode alert protocol. Three conditions:

- ``yield_drop``: the last 2 ``ok`` scans found 0 jobs although at least 3 of
  the 5 ``ok`` scans before them found some.
- ``parser_broken``: the latest ``ok`` scan parsed at least 4 jobs and more
  than half of them have no company (the portal HTML probably changed).
- ``unconfirmed_streak``: the 3 latest ok-or-``empty_unconfirmed`` scans are all
  ``empty_unconfirmed`` (0 cards and no "no results" copy: a soft throttle or a
  total selector break) although at least 3 of the 5 ``ok`` scans before them
  found jobs.

Scans that were rate limited, session-expired or errored say nothing about the
beacon's health: they are skipped, so they neither trigger nor reset a
condition. ``yield_drop`` and ``parser_broken`` look only at ``ok`` scans (an
``empty_unconfirmed`` one may be a soft throttle). A condition alerts once per
episode; it can alert again only after it stopped holding (a scan with jobs or
a confirmed empty one ends the streak) and then re-occurred.

Alerting is a two-phase protocol so that concurrent reports cannot both send
the same alert and a failed send is retried (:class:`AlertClaims`): claim under
the caller's lock, send outside it, then confirm (persist the episode as
alerted) or release (nothing persisted; the next scan retries).
"""

import itertools
import json
from collections.abc import Sequence
from typing import Final, NamedTuple

from cva.tracker.repo import BeaconScanRow, OfferRepository

__all__ = [
    "CONDITIONS",
    "PARSER_BROKEN",
    "UNCONFIRMED_STREAK",
    "YIELD_DROP",
    "Alert",
    "AlertClaims",
    "BeaconScanRow",
    "Claim",
    "active_conditions",
    "load_alerted",
    "new_alerts",
    "store_alerted",
]

YIELD_DROP: Final[str] = "yield_drop"
PARSER_BROKEN: Final[str] = "parser_broken"
UNCONFIRMED_STREAK: Final[str] = "unconfirmed_streak"
CONDITIONS: Final[tuple[str, ...]] = (YIELD_DROP, PARSER_BROKEN, UNCONFIRMED_STREAK)

_ZERO_RUN: Final[int] = 2
_HISTORY: Final[int] = 5
_HISTORY_HEALTHY: Final[int] = 3
_UNCONFIRMED_RUN: Final[int] = 3
_PARSER_MIN_JOBS: Final[int] = 4
_PARSER_EMPTY_RATIO: Final[float] = 0.5
_STATE_KEY: Final[str] = "beacon_health_alerted"


class Alert(NamedTuple):
    condition: str
    message: str


def _yield_drop(ok: Sequence[BeaconScanRow]) -> bool:
    latest, earlier = ok[:_ZERO_RUN], ok[_ZERO_RUN : _ZERO_RUN + _HISTORY]
    if len(latest) < _ZERO_RUN or any(s.jobs_found != 0 for s in latest):
        return False
    return sum(1 for s in earlier if s.jobs_found > 0) >= _HISTORY_HEALTHY


def _parser_broken(ok: Sequence[BeaconScanRow]) -> bool:
    if not ok:
        return False
    latest = ok[0]
    return (
        latest.jobs_found >= _PARSER_MIN_JOBS
        and latest.empty_company / latest.jobs_found > _PARSER_EMPTY_RATIO
    )


def _unconfirmed_streak(scans: Sequence[BeaconScanRow]) -> bool:
    answered = [s for s in scans if s.status in ("ok", "empty_unconfirmed")]
    latest, rest = answered[:_UNCONFIRMED_RUN], answered[_UNCONFIRMED_RUN:]
    if len(latest) < _UNCONFIRMED_RUN or any(
        s.status != "empty_unconfirmed" for s in latest
    ):
        return False
    earlier = [s for s in rest if s.status == "ok"][:_HISTORY]
    return sum(1 for s in earlier if s.jobs_found > 0) >= _HISTORY_HEALTHY


def active_conditions(scans: Sequence[BeaconScanRow]) -> set[str]:
    """Conditions that hold right now (``scans`` newest first)."""
    ok = [s for s in scans if s.status == "ok"]
    active: set[str] = set()
    if _yield_drop(ok):
        active.add(YIELD_DROP)
    if _parser_broken(ok):
        active.add(PARSER_BROKEN)
    if _unconfirmed_streak(scans):
        active.add(UNCONFIRMED_STREAK)
    return active


def _message(condition: str, name: str) -> str:
    """The Telegram text for ``condition``; thresholds come from the constants."""
    if condition == YIELD_DROP:
        return (
            f"⚠️ El beacon «{name}» devolvía ofertas y ahora da 0 "
            f"({_ZERO_RUN} escaneos seguidos). "
            "Puede que el portal haya cambiado o la búsqueda esté rota."
        )
    if condition == PARSER_BROKEN:
        return (
            f"⚠️ El beacon «{name}»: más del {_PARSER_EMPTY_RATIO:.0%} de las "
            "ofertas llegan sin empresa (¿cambió el HTML del portal?)."
        )
    if condition == UNCONFIRMED_STREAK:
        return (
            f"⚠️ El beacon «{name}» lleva {_UNCONFIRMED_RUN} escaneos vacíos "
            "sin confirmar. Puede ser un bloqueo temporal de LinkedIn o que el "
            "portal haya cambiado su HTML; ábrelo a mano para comprobarlo."
        )
    raise ValueError(f"unknown beacon health condition: {condition!r}")


def new_alerts(
    name: str, scans: Sequence[BeaconScanRow], alerted: set[str]
) -> tuple[list[Alert], set[str]]:
    """Evaluate ``scans`` (newest first) against the conditions already alerted.

    Returns ``(fresh, standing)``:

    - ``fresh``: one alert for every active condition that is not in
      ``alerted``, in :data:`CONDITIONS` order;
    - ``standing``: the conditions that are active now and are either in
      ``alerted`` or fresh. A condition in ``alerted`` that no longer holds is
      absent from it: its episode ended, so it may alert again later.

    Pure: it persists nothing and does not know whether ``fresh`` gets
    delivered (see :class:`AlertClaims`).
    """
    active = active_conditions(scans)
    fresh = [
        Alert(c, _message(c, name))
        for c in CONDITIONS
        if c in active and c not in alerted
    ]
    return fresh, (alerted & active) | {a.condition for a in fresh}


def _read_state(repo: OfferRepository) -> dict[str, list[str]]:
    """The persisted alert state, normalized: ``{beacon_id: [condition, ...]}``.

    The single reader behind :func:`load_alerted` and :func:`store_alerted`.
    Anything unusable is discarded rather than coerced: a state that is not a
    JSON object is empty; a beacon whose value is not a list keeps nothing; a
    list keeps only its string items (so a bare ``"yield_drop"`` is never
    iterated into characters, nor ``5`` turned into ``"5"``).
    """
    try:
        data = json.loads(repo.get_state(_STATE_KEY) or "{}")
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    state: dict[str, list[str]] = {}
    for beacon_id, conditions in data.items():
        if isinstance(conditions, list):
            kept = [c for c in conditions if isinstance(c, str)]
            if kept:
                state[str(beacon_id)] = kept
    return state


def load_alerted(repo: OfferRepository, beacon_id: str) -> set[str]:
    """Conditions already alerted for this beacon (empty when none/corrupt)."""
    return set(_read_state(repo).get(beacon_id, []))


def store_alerted(repo: OfferRepository, beacon_id: str, alerted: set[str]) -> None:
    """Persist this beacon's alerted conditions (other beacons untouched)."""
    state = _read_state(repo)
    if alerted:
        state[beacon_id] = sorted(alerted)
    else:
        state.pop(beacon_id, None)
    repo.set_state(_STATE_KEY, json.dumps(state))


class Claim(NamedTuple):
    """One alert held by :class:`AlertClaims` until it is confirmed or released."""

    beacon_id: str
    alert: Alert
    token: int


class AlertClaims:
    """The claim, send, confirm-or-release protocol behind once-per-episode alerts.

    1. :meth:`claim` (under the caller's lock) evaluates the beacon's scans and
       claims every alert that is due: not yet alerted this episode and not
       already claimed by another report. The claim lives in memory only.
    2. The caller sends the messages OUTSIDE the lock (a slow Telegram send must
       not block the bridge).
    3. :meth:`confirm` persists the episode as alerted after a delivered
       message; :meth:`release` drops the claim after a failed one, so the next
       scan retries. A restart between 1 and 3 loses the claim and nothing was
       persisted: the alert is simply retried (at least once, never lost).

    Not thread-safe by itself: callers hold the repository lock around every
    method (they share its sqlite connection). A :class:`Claim` settles only
    itself: one whose episode ended while the message was in flight, or that a
    newer claim replaced, can no longer confirm or release anything.
    """

    def __init__(self, repo: OfferRepository) -> None:
        self._repo = repo
        self._pending: dict[tuple[str, str], int] = {}
        self._tokens = itertools.count(1)

    def claim(
        self, beacon_id: str, name: str, scans: Sequence[BeaconScanRow]
    ) -> list[Claim]:
        """Claim the alerts due for ``beacon_id`` given its ``scans`` (newest first)."""
        alerted = load_alerted(self._repo, beacon_id)
        held = {c for b, c in self._pending if b == beacon_id}
        fresh, standing = new_alerts(name, scans, alerted | held)
        # Episodes that ended are over: forget them, and any claim still in flight.
        for condition in held - standing:
            del self._pending[(beacon_id, condition)]
        if alerted - standing:
            store_alerted(self._repo, beacon_id, alerted & standing)
        claims: list[Claim] = []
        for alert in fresh:
            token = next(self._tokens)
            self._pending[(beacon_id, alert.condition)] = token
            claims.append(Claim(beacon_id, alert, token))
        return claims

    def confirm(self, claim: Claim) -> None:
        """The message was delivered: persist the episode as alerted."""
        if not self._holds(claim):
            return  # released, or its episode ended while the message was in flight
        del self._pending[(claim.beacon_id, claim.alert.condition)]
        alerted = load_alerted(self._repo, claim.beacon_id)
        store_alerted(self._repo, claim.beacon_id, alerted | {claim.alert.condition})

    def release(self, claim: Claim) -> None:
        """The message was not delivered: give the claim back (the next scan retries)."""
        if self._holds(claim):
            del self._pending[(claim.beacon_id, claim.alert.condition)]

    def _holds(self, claim: Claim) -> bool:
        return (
            self._pending.get((claim.beacon_id, claim.alert.condition)) == claim.token
        )
