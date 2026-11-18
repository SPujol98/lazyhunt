"""Gates wired into JobService.analyze, the bridge zone and the cards."""

import asyncio
from pathlib import Path

from cva.fetch import FetchResponse
from cva.match.gates import GateResult
from cva.match.scorer import MatchResult
from cva.notifier import WalledOffer, judge_breakdown_message, round_summary_message
from tests.test_backend_profile import (
    PY_TEXT,
    _bridge_row,
    _Notify,
    _py_extraction,
    _run,
    _service,
)
from tests.test_service import (
    OFFER_URL,
    FakeFetcher,
    FakeLlmClient,
    _extraction,
    _judgment,
    make_service,
)

DE_TEXT = "AI Engineer en Acme. Imprescindible Python. Alemán imprescindible."
DE_PLUS_TEXT = "AI Engineer en Acme. Imprescindible Python. Alemán valorable."
US_TEXT = "AI Engineer at Acme. Python required. Must be a U.S. citizen."


def test_old_breakdown_json_without_gates_still_loads() -> None:
    raw = '{"score": 50, "threshold": 65, "breakdown": [], "stack_wall": null}'
    result = MatchResult.model_validate_json(raw)
    assert result.gates == []
    assert result.walled_reason is None


def test_gate_fail_forces_discard_with_hard_wall_in_hybrid(tmp_path: Path) -> None:
    llm = FakeLlmClient(_extraction(), judge_reply=_judgment(score=90, verdict="apply"))
    service, _repo = make_service(
        tmp_path,
        llm,
        FakeFetcher(FetchResponse(200, "", OFFER_URL)),
        match_mode="hybrid",
    )
    offer_id = asyncio.run(service.ingest(None, DE_TEXT))
    result = asyncio.run(service.analyze(offer_id))
    fails = [g for g in result.gates if g.verdict == "FAIL"]
    assert [g.gate for g in fails] == ["language"]
    assert result.walled_reason == fails[0].reason
    judgment, _fallback = service.latest_judgment(offer_id)
    assert judgment is not None
    assert judgment.verdict == "discard"
    assert any(w.hard and w.text == fails[0].reason for w in judgment.walls)


def test_gate_flag_adds_soft_wall_without_forcing_discard(tmp_path: Path) -> None:
    llm = FakeLlmClient(_extraction(), judge_reply=_judgment(score=90, verdict="apply"))
    service, _repo = make_service(
        tmp_path,
        llm,
        FakeFetcher(FetchResponse(200, "", OFFER_URL)),
        match_mode="hybrid",
    )
    offer_id = asyncio.run(service.ingest(None, DE_PLUS_TEXT))
    result = asyncio.run(service.analyze(offer_id))
    assert result.walled_reason is None
    judgment, _fallback = service.latest_judgment(offer_id)
    assert judgment is not None
    assert judgment.verdict == "apply"
    soft = [w for w in judgment.walls if not w.hard]
    assert len(soft) == 1 and "alemán" in soft[0].text


def test_gate_results_persist_in_stored_breakdown(tmp_path: Path) -> None:
    llm = FakeLlmClient(_extraction())
    service, repo = make_service(
        tmp_path, llm, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    offer_id = asyncio.run(service.ingest(None, US_TEXT))
    asyncio.run(service.analyze(offer_id))
    stored = repo.latest_match(offer_id)
    assert stored is not None
    loaded = MatchResult.model_validate_json(stored.breakdown_json)
    assert [g.gate for g in loaded.gates] == ["eligibility", "language", "location"]
    assert loaded.gates[0].verdict == "FAIL"


def test_gate_fail_takes_the_walled_zone_with_reason_and_no_card(
    tmp_path: Path,
) -> None:
    svc, repo = _service(tmp_path, _py_extraction(), threshold=0, gray_zone=0)
    text = f"{PY_TEXT} Alemán imprescindible."
    oid = _bridge_row(repo, "ai", text)
    notify = _Notify()
    outcome = _run(svc, oid, notify, text=text)
    assert outcome.zone == "walled"
    assert notify.calls == []
    assert outcome.reason is not None and "alemán" in outcome.reason


def test_clean_offer_is_not_walled_by_gates(tmp_path: Path) -> None:
    svc, repo = _service(tmp_path, _py_extraction(), threshold=0, gray_zone=0)
    oid = _bridge_row(repo, "ai")
    outcome = _run(svc, oid, _Notify())
    assert outcome.zone == "above" and outcome.reason is None


def test_keyword_card_shows_gate_fail_and_flag_lines() -> None:
    fail = GateResult(
        gate="language",
        verdict="FAIL",
        reason="Exige alemán",
        evidence="Alemán imprescindible",
    )
    flag = GateResult(
        gate="eligibility", verdict="FLAG", reason="Pide habilitación", evidence="HPS"
    )
    ok = GateResult(gate="language", verdict="PASS", reason="", evidence="")
    result = MatchResult(score=80, threshold=65, breakdown=[], gates=[fail, flag, ok])
    text = judge_breakdown_message(result, None, 7)
    assert "🚫 Muro: Exige alemán" in text
    assert "⚠️ Pide habilitación" in text
    assert "PASS" not in text


def test_round_summary_lists_walled_reasons_compactly() -> None:
    walled = [
        WalledOffer("Exige alemán", "Dev", "Acme", "https://x.test/1"),
        WalledOffer("stack principal Java, no Python", None, None, None),
    ]
    text = round_summary_message(
        beacon="b",
        analyzed=2,
        above=0,
        gray=0,
        below=0,
        pending=0,
        discarded=[],
        walled=2,
        walled_offers=walled,
    )
    assert "2 descartadas por muro" in text
    assert "Exige alemán" in text and '<a href="https://x.test/1">Dev</a>' in text
    assert "stack principal Java, no Python" in text


def test_round_summary_walled_lines_are_capped_and_escaped() -> None:
    walled = [WalledOffer(f"<r{i}>", f"T{i}", "C", None) for i in range(12)]
    text = round_summary_message(
        beacon="b",
        analyzed=12,
        above=0,
        gray=0,
        below=0,
        pending=0,
        discarded=[],
        walled=12,
        walled_offers=walled,
    )
    assert "&lt;r0&gt;" in text and "<r0>" not in text
    assert "… y 7 más" in text


def test_tally_records_walled_reason() -> None:
    from cva.bridge import _RoundTally

    tally = _RoundTally("b", 1)
    tally.record("walled", 40, "Dev", "Acme", "https://x.test/1", "Exige alemán")
    assert tally.walled == 1
    assert tally.walled_offers == [
        WalledOffer("Exige alemán", "Dev", "Acme", "https://x.test/1")
    ]


def test_location_fail_takes_the_walled_zone_with_reason_and_no_card(
    tmp_path: Path,
) -> None:
    svc, repo = _service(tmp_path, _py_extraction(), threshold=0, gray_zone=0)
    text = f"{PY_TEXT} Puesto presencial."
    oid = _bridge_row(repo, "ai", text, location="Madrid")
    notify = _Notify()
    outcome = _run(svc, oid, notify, text=text)
    assert outcome.zone == "walled"
    assert notify.calls == []
    assert outcome.reason == "Presencial fuera de Cataluña (Madrid)"


def test_location_in_catalonia_is_not_walled(tmp_path: Path) -> None:
    svc, repo = _service(tmp_path, _py_extraction(), threshold=0, gray_zone=0)
    text = f"{PY_TEXT} Puesto presencial."
    oid = _bridge_row(repo, "ai", text, location="Sant Andreu de la Barca")
    outcome = _run(svc, oid, _Notify(), text=text)
    assert outcome.zone == "above" and outcome.reason is None


def test_location_flag_is_a_soft_warning_not_a_wall(tmp_path: Path) -> None:
    svc, repo = _service(tmp_path, _py_extraction(), threshold=0, gray_zone=0)
    text = f"{PY_TEXT} Puesto presencial."
    oid = _bridge_row(repo, "ai", text, location="España")
    notify = _Notify()
    outcome = _run(svc, oid, notify, text=text)
    assert outcome.zone == "above"
    assert notify.calls  # the card goes out (with the warning line)


def test_keyword_card_shows_location_flag_line() -> None:
    flag = GateResult(
        gate="location",
        verdict="FLAG",
        reason="Presencial sin ubicación concreta: revisa",
        evidence="presencial",
    )
    result = MatchResult(score=80, threshold=65, breakdown=[], gates=[flag])
    assert "⚠️ Presencial sin ubicación concreta: revisa" in judge_breakdown_message(
        result, None, 7
    )


def test_location_is_kept_when_a_refresh_has_none(tmp_path: Path) -> None:
    _svc, repo = _service(tmp_path, _py_extraction())
    oid = _bridge_row(repo, "ai", location="Girona")
    _bridge_row(repo, "ai", "refreshed text")  # same URL, no location
    assert repo.get_offer(oid).ubicacion == "Girona"
    _bridge_row(repo, "ai", "again", location="Lleida")
    assert repo.get_offer(oid).ubicacion == "Lleida"
