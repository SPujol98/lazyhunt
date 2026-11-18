"""Outcome calibration review follow-ups (R3-001..R3-004)."""

import asyncio
from pathlib import Path

import pytest

import bot
from cva.calibration import CalibrationExample
from cva.calibration_ui import CAL
from cva.claimsdb import load_claims
from cva.match.judge import build_system_prompt
from cva.notifier import OE
from tests.demo import DEMO_CLAIMS
from tests.test_bot import FakeContext, FakeQuery, FakeUpdate
from tests.test_outcome_calibration import _judgment, _sent
from tests.test_outcome_calibration_bot import _datas, _press, _questions

CLAIMS = load_claims(DEMO_CLAIMS)


def _backend_example(score: int = 60) -> CalibrationExample:
    return CalibrationExample(
        id=1,
        offer_id=101,
        outcome="entrevista_tecnica",
        polarity="positive",
        score_at_send=score,
        profile="backend",
        core_role="APIs",
        status="active",
        created_at="t",
        approved_at="t",
    )


# --- R3-001: anchors use their own profile's threshold -------------------------


def test_anchor_uses_its_own_profile_threshold() -> None:
    # 60 sits between the backend (55) and the AI (65) thresholds.
    prompt = build_system_prompt(
        CLAIMS,
        threshold=65,
        extra_anchors=[_backend_example(60)],
        anchor_thresholds={"ai": 65, "backend": 55},
    )
    assert (
        "you scored 60, it got a technical interview -> that score was right" in prompt
    )
    assert "deserve at least" not in prompt.split("Owner's real outcomes")[1]


def test_anchor_thresholds_default_keeps_the_judged_threshold() -> None:
    prompt = build_system_prompt(
        CLAIMS, threshold=65, extra_anchors=[_backend_example(60)]
    )
    assert "deserve at least 65" in prompt


def test_service_passes_per_profile_thresholds(tmp_path: Path) -> None:
    service, _repo, _offer_id = _sent(tmp_path)
    assert service._anchor_thresholds() == {
        "ai": service._threshold,
        "backend": service._backend_threshold,
    }


# --- R3-002: a failed send leaves a recoverable pending question -------------------


def test_send_failure_then_reask_positive(tmp_path: Path) -> None:
    service, repo, offer_id = _sent(tmp_path)
    context = FakeContext(service)
    query = FakeQuery(f"{OE}:{offer_id}:screening")
    real = query.message.reply_text
    calls = {"n": 0}

    async def flaky(
        text: str, reply_markup: object = None, parse_mode: str | None = None
    ):  # type: ignore[no-untyped-def]
        if reply_markup is not None and any(
            d.startswith(f"{CAL}:") for d in _datas(reply_markup)
        ):
            calls["n"] += 1
            raise RuntimeError("telegram down")
        return await real(text, reply_markup=reply_markup, parse_mode=parse_mode)

    query.message.reply_text = flaky  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        asyncio.run(bot.callback_router(FakeUpdate(query=query), context))
    pending = repo.calibration_for(offer_id, "positive")
    assert pending is not None and pending.status == "pending"

    again = _press(context, f"{OE}:{offer_id}:oferta")
    questions = _questions(again)
    assert len(questions) == 1
    assert _datas(questions[0][1]) == [f"{CAL}:y:{pending.id}", f"{CAL}:n:{pending.id}"]


def test_propose_returns_the_existing_pending_row(tmp_path: Path) -> None:
    service, _repo, offer_id = _sent(tmp_path)
    first = service.propose_calibration(offer_id, "screening")
    again = service.propose_calibration(offer_id, "oferta")
    assert first is not None and again is not None and again.id == first.id
    service.decline_calibration(first.id)
    assert service.propose_calibration(offer_id, "oferta") is None


def test_reasoned_rejection_pending_is_reasked(tmp_path: Path) -> None:
    service, _repo, offer_id = _sent(tmp_path)
    first = service.propose_calibration(offer_id, "rechazada", "stack")
    assert first is not None
    context = FakeContext(service)
    # the reason keyboard is stale now, but a repeated /outcome re-asks Sí/No
    again = _press(context, f"{OE}:{offer_id}:rechazada")
    questions = _questions(again)
    assert len(questions) == 1
    assert _datas(questions[0][1]) == [f"{CAL}:y:{first.id}", f"{CAL}:n:{first.id}"]
    # and pressing the reason button again re-asks it too
    press = _press(context, f"{CAL}:r:{offer_id}:stack")
    assert len(_questions(press)) == 1


# --- R3-003: never present a keyword score as the judge's -------------------------


def test_no_judge_score_means_no_question(tmp_path: Path) -> None:
    service, repo, offer_id = _sent(tmp_path, judge_score=None)
    assert repo.get_sent_judgment(offer_id) is None
    assert service.propose_calibration(offer_id, "screening") is None
    assert repo.calibration_for(offer_id, "positive") is None


def test_corrupt_judge_row_means_no_question(tmp_path: Path) -> None:
    service, repo, offer_id = _sent(tmp_path, judge_score=None)
    repo.save_judge_result(offer_id, score=61, verdict="apply", judgment_json="{bad")
    assert service.propose_calibration(offer_id, "screening") is None


def test_current_judge_row_is_used_for_legacy_sends(tmp_path: Path) -> None:
    service, repo, offer_id = _sent(tmp_path, judge_score=None)
    repo.save_judge_result(
        offer_id, score=61, verdict="apply_with_caveats", judgment_json=_judgment(61)
    )
    example = service.propose_calibration(offer_id, "screening")
    assert example is not None and example.score_at_send == 61


# --- R3-004: latest outcome wins per offer -----------------------------------------


def test_approving_the_other_polarity_deactivates_the_older_example(
    tmp_path: Path,
) -> None:
    service, _repo, offer_id = _sent(tmp_path)
    positive = service.propose_calibration(offer_id, "screening")
    assert positive is not None
    service.approve_calibration(positive.id)
    negative = service.propose_calibration(offer_id, "rechazada", "requisitos")
    assert negative is not None
    assert [e.id for e in service.active_calibrations()] == [positive.id]
    service.approve_calibration(negative.id)
    assert [e.id for e in service.active_calibrations()] == [negative.id]
    old = service.get_calibration(positive.id)
    assert old is not None and old.status == "inactive"
