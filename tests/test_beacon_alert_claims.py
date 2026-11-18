"""Beacon health alert protocol (offer-gates-and-health T5b).

The once-per-episode alert is a two-phase protocol (claim under the lock, send
outside it, then confirm or release), the persisted episode state has one
reader, and the alert texts are built from the evaluator's thresholds.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from cva.beacons import health
from cva.beacons.health import (
    CONDITIONS,
    PARSER_BROKEN,
    UNCONFIRMED_STREAK,
    YIELD_DROP,
    AlertClaims,
    BeaconScanRow,
    load_alerted,
    store_alerted,
)
from cva.tracker import OfferRepository

STATE_KEY = "beacon_health_alerted"
NAME = "Barcelona AI"


def S(found: int = 5, empty: int = 0, status: str = "ok") -> BeaconScanRow:
    return BeaconScanRow(
        beacon_id="b1",
        beacon_name=NAME,
        at="2026-10-18T10:00:00+00:00",
        status=status,  # type: ignore[arg-type]
        jobs_found=found,
        empty_company=empty,
    )


DROP = [S(0), S(0), S(4), S(3), S(5)]  # yield_drop holds
HEALTHY = [S(4), *DROP]  # a healthy scan on top: the episode is over


def _repo(tmp_path: Path) -> OfferRepository:
    return OfferRepository(
        sqlite3.connect(tmp_path / "claims.db", check_same_thread=False),
        clock=lambda: "2026-10-18",
    )


# --- alert texts ------------------------------------------------------------------------


def test_alert_texts_follow_the_threshold_constants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health, "_ZERO_RUN", 3)
    monkeypatch.setattr(health, "_PARSER_EMPTY_RATIO", 0.75)
    assert "(3 escaneos seguidos)" in health._message(YIELD_DROP, NAME)
    assert "más del 75% de las ofertas" in health._message(PARSER_BROKEN, NAME)


def test_every_condition_has_an_alert_message() -> None:
    assert set(CONDITIONS) == {YIELD_DROP, PARSER_BROKEN, UNCONFIRMED_STREAK}
    for condition in CONDITIONS:
        assert NAME in health._message(condition, NAME)


def test_an_unknown_condition_has_no_message() -> None:
    with pytest.raises(ValueError, match="unknown beacon health condition"):
        health._message("nope", NAME)


# --- persisted episode state: one reader --------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("not json", set()),
        ("[1, 2]", set()),
        ('"yield_drop"', set()),
        ('{"b1": "yield_drop"}', set()),  # a bare string is not a list of conditions
        ('{"b1": {"yield_drop": 1}}', set()),
        ('{"b1": 7}', set()),
        ('{"b1": ["yield_drop", 5, null, ["x"], {"a": 1}]}', {"yield_drop"}),
        ('{"b1": ["parser_broken"], "b2": 7}', {"parser_broken"}),
    ],
)
def test_load_alerted_normalizes_corrupt_state(
    tmp_path: Path, raw: str, expected: set[str]
) -> None:
    repo = _repo(tmp_path)
    repo.set_state(STATE_KEY, raw)
    assert load_alerted(repo, "b1") == expected


def test_store_alerted_reads_state_like_load_and_keeps_valid_beacons(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    repo.set_state(
        STATE_KEY, '{"b2": ["parser_broken"], "b3": "junk", "b4": [1], "b5": 7}'
    )
    store_alerted(repo, "b1", {YIELD_DROP})
    assert json.loads(repo.get_state(STATE_KEY) or "") == {
        "b1": ["yield_drop"],
        "b2": ["parser_broken"],
    }
    store_alerted(repo, "b1", set())
    assert json.loads(repo.get_state(STATE_KEY) or "") == {"b2": ["parser_broken"]}


@pytest.mark.parametrize("raw", ["not json", "[1]", '"x"', "7"])
def test_store_alerted_replaces_unusable_state(tmp_path: Path, raw: str) -> None:
    repo = _repo(tmp_path)
    repo.set_state(STATE_KEY, raw)
    store_alerted(repo, "b1", {PARSER_BROKEN})
    assert load_alerted(repo, "b1") == {PARSER_BROKEN}


# --- claim / confirm / release ------------------------------------------------------------


def test_claim_returns_the_due_alert_and_holds_it(tmp_path: Path) -> None:
    claims = AlertClaims(_repo(tmp_path))
    (claim,) = claims.claim("b1", NAME, DROP)
    assert (claim.beacon_id, claim.alert.condition) == ("b1", YIELD_DROP)
    assert "El beacon «Barcelona AI»" in claim.alert.message
    # a concurrent report for the same episode finds it already claimed
    assert claims.claim("b1", NAME, DROP) == []


def test_a_claim_is_not_persisted_until_it_is_confirmed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    claims = AlertClaims(repo)
    claims.claim("b1", NAME, DROP)
    assert load_alerted(repo, "b1") == set()  # a crash now only means a retry


def test_confirm_marks_the_episode_alerted(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    claims = AlertClaims(repo)
    (claim,) = claims.claim("b1", NAME, DROP)
    claims.confirm(claim)
    assert load_alerted(repo, "b1") == {YIELD_DROP}
    assert claims.claim("b1", NAME, DROP) == []
    # a fresh AlertClaims (restart) still knows: the mark is persisted
    assert AlertClaims(repo).claim("b1", NAME, DROP) == []


def test_release_lets_the_next_scan_retry(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    claims = AlertClaims(repo)
    (claim,) = claims.claim("b1", NAME, DROP)
    claims.release(claim)
    assert load_alerted(repo, "b1") == set()
    (again,) = claims.claim("b1", NAME, DROP)
    assert again.alert == claim.alert


def test_release_after_confirm_changes_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    claims = AlertClaims(repo)
    (claim,) = claims.claim("b1", NAME, DROP)
    claims.confirm(claim)
    claims.release(claim)
    assert load_alerted(repo, "b1") == {YIELD_DROP}
    assert claims.claim("b1", NAME, DROP) == []


def test_a_recovered_episode_is_cleared_and_can_alert_again(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    claims = AlertClaims(repo)
    (claim,) = claims.claim("b1", NAME, DROP)
    claims.confirm(claim)
    assert claims.claim("b1", NAME, HEALTHY) == []
    assert load_alerted(repo, "b1") == set()
    assert len(claims.claim("b1", NAME, DROP)) == 1


def test_a_claim_cannot_confirm_after_its_episode_ended(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    claims = AlertClaims(repo)
    (claim,) = claims.claim("b1", NAME, DROP)
    assert (
        claims.claim("b1", NAME, HEALTHY) == []
    )  # recovery while the send is in flight
    claims.confirm(claim)
    assert load_alerted(repo, "b1") == set()  # no stale mark for the next episode


def test_an_old_claim_cannot_settle_a_newer_one(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    claims = AlertClaims(repo)
    (old,) = claims.claim("b1", NAME, DROP)
    claims.claim("b1", NAME, HEALTHY)  # the first episode ends ...
    (new,) = claims.claim("b1", NAME, DROP)  # ... and a second one starts
    claims.release(old)
    assert claims.claim("b1", NAME, DROP) == []  # the new claim is still held
    claims.confirm(old)
    assert load_alerted(repo, "b1") == set()
    claims.confirm(new)
    assert load_alerted(repo, "b1") == {YIELD_DROP}


def test_claims_are_independent_per_beacon(tmp_path: Path) -> None:
    claims = AlertClaims(_repo(tmp_path))
    (first,) = claims.claim("b1", NAME, DROP)
    (other,) = claims.claim("b2", "Madrid AI", DROP)
    assert (first.beacon_id, other.beacon_id) == ("b1", "b2")
    claims.release(first)
    assert claims.claim("b2", "Madrid AI", DROP) == []  # b2 is still held
    assert len(claims.claim("b1", NAME, DROP)) == 1  # b1 may retry
