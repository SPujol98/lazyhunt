"""Outcome calibration (K2): post-outcome questions, Sí/No, /calibracion."""

from pathlib import Path

import bot
from cva.calibration import SentJudgment
from cva.calibration_ui import (
    CAL,
    list_keyboard,
    list_message,
    question_keyboard,
    reason_keyboard,
)
from cva.commands import COMMANDS, menu_commands
from cva.notifier import OE, help_message, outcome_note_prompt
from cva.service import JobService
from cva.tracker import OfferInput, OfferRepository
from tests.helpers import run_coro
from tests.test_bot import (
    FakeContext,
    FakeFetcher,
    FakeMessage,
    FakeQuery,
    FakeUpdate,
    FetchResponse,
    make_service,
)


def _sent_offer(
    tmp_path: Path, *, match: int = 52
) -> tuple[JobService, OfferRepository, int]:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", "https://x/1"))
    )
    repo = service._repo
    offer_id = repo.upsert_offer(
        OfferInput(
            job_id="1",
            title="T",
            company="C",
            url="https://x/1",
            text="t",
            source="link",
        )
    )
    repo.mark_sent(
        offer_id,
        cv_path="cv.pdf",
        cambios="c",
        match=match,
        judgment=SentJudgment(score=match),
    )
    return service, repo, offer_id


def _press(context: FakeContext, data: str) -> FakeQuery:
    query = FakeQuery(data)
    run_coro(bot.callback_router(FakeUpdate(query=query), context))
    return query


def _datas(markup: object) -> list[str]:
    return [b.callback_data for r in markup.inline_keyboard for b in r]  # type: ignore[attr-defined]


def _questions(query: FakeQuery) -> list[tuple[str, object]]:
    return [
        (text, markup)
        for text, markup in zip(
            query.message.replies, query.message.markups, strict=True
        )
        if markup is not None and any(d.startswith(f"{CAL}:") for d in _datas(markup))
    ]


# --- after /outcome -------------------------------------------------------------


def test_positive_outcome_asks_after_the_note_prompt_without_consuming_it(
    tmp_path: Path,
) -> None:
    service, _repo, offer_id = _sent_offer(tmp_path)
    context = FakeContext(service)
    query = _press(context, f"{OE}:{offer_id}:entrevista_tecnica")

    assert query.message.replies[-1] == outcome_note_prompt("es")  # prompt stays last
    questions = _questions(query)
    assert len(questions) == 1
    text, markup = questions[0]
    assert "calibrar" in text and "52/100" in text and "entrevista técnica" in text
    example = service.active_calibrations()
    assert example == []  # nothing is active before the owner answers
    pending = service._repo.calibration_for(offer_id, "positive")
    assert pending is not None
    assert _datas(markup) == [f"{CAL}:y:{pending.id}", f"{CAL}:n:{pending.id}"]

    # the optional note capture is still armed and still works
    assert context.user_data[bot._NOTE_KEY] == offer_id
    note = FakeMessage("Pidieron disponibilidad")
    run_coro(bot.paste_handler(FakeUpdate(message=note), context))
    assert [e.note for e in service.outcome_events(offer_id) if e.kind == "note"] == [
        "Pidieron disponibilidad"
    ]


def test_second_positive_outcome_does_not_ask_again(tmp_path: Path) -> None:
    service, _repo, offer_id = _sent_offer(tmp_path)
    context = FakeContext(service)
    first = _press(context, f"{OE}:{offer_id}:screening")
    _press(context, _datas(_questions(first)[0][1])[1])  # answer "No"
    again = _press(context, f"{OE}:{offer_id}:oferta")
    assert _questions(again) == []


def test_ignored_outcomes_ask_nothing(tmp_path: Path) -> None:
    service, _repo, offer_id = _sent_offer(tmp_path)
    context = FakeContext(service)
    for estado in ("sin_respuesta", "retirada"):
        assert _questions(_press(context, f"{OE}:{offer_id}:{estado}")) == []


def test_rejection_asks_the_reason_first(tmp_path: Path) -> None:
    service, _repo, offer_id = _sent_offer(tmp_path)
    context = FakeContext(service)
    query = _press(context, f"{OE}:{offer_id}:rechazada")
    questions = _questions(query)
    assert len(questions) == 1
    assert _datas(questions[0][1]) == [
        f"{CAL}:r:{offer_id}:requisitos",
        f"{CAL}:r:{offer_id}:stack",
        f"{CAL}:r:{offer_id}:idioma",
        f"{CAL}:r:{offer_id}:otro",
    ]
    assert service.active_calibrations() == []


def test_fit_reason_leads_to_the_yes_no_question(tmp_path: Path) -> None:
    service, repo, offer_id = _sent_offer(tmp_path)
    context = FakeContext(service)
    _press(context, f"{OE}:{offer_id}:rechazada")
    query = _press(context, f"{CAL}:r:{offer_id}:requisitos")
    questions = _questions(query)
    assert len(questions) == 1
    assert "rechazada" in questions[0][0] and "52/100" in questions[0][0]
    example = repo.calibration_for(offer_id, "negative")
    assert example is not None and example.reason == "requisitos"
    assert _datas(questions[0][1]) == [f"{CAL}:y:{example.id}", f"{CAL}:n:{example.id}"]


def test_other_reason_records_nothing_and_stale_reason_press_is_polite(
    tmp_path: Path,
) -> None:
    service, _repo, offer_id = _sent_offer(tmp_path)
    context = FakeContext(service)
    _press(context, f"{OE}:{offer_id}:rechazada")
    query = _press(context, f"{CAL}:r:{offer_id}:otro")
    assert _questions(query) == []
    assert "no se usará" in query.message.replies[-1].lower()
    stale = _press(context, f"{CAL}:r:{offer_id}:stack")
    assert _questions(stale) == []
    assert "ya" in stale.message.replies[-1].lower()
    assert service.active_calibrations() == []


def test_invalid_reason_or_unknown_offer_changes_nothing(tmp_path: Path) -> None:
    service, repo, offer_id = _sent_offer(tmp_path)
    context = FakeContext(service)
    _press(context, f"{CAL}:r:{offer_id}:inventado")
    _press(context, f"{CAL}:r:999:stack")
    _press(context, f"{CAL}:zz:1")
    _press(context, f"{CAL}:y:abc")
    assert repo.calibration_for(offer_id, "negative") is None


# --- Sí / No ----------------------------------------------------------------------


def test_yes_activates_and_a_double_press_is_polite(tmp_path: Path) -> None:
    service, repo, offer_id = _sent_offer(tmp_path)
    context = FakeContext(service)
    _press(context, f"{OE}:{offer_id}:screening")
    example = repo.calibration_for(offer_id, "positive")
    assert example is not None
    yes = _press(context, f"{CAL}:y:{example.id}")
    assert [e.id for e in service.active_calibrations()] == [example.id]
    assert yes.edited and "calibr" in yes.edited[-1].lower()
    assert yes.edited_markups[-1] is None
    again = _press(context, f"{CAL}:y:{example.id}")
    assert "ya" in again.message.replies[-1].lower()
    assert len(service.active_calibrations()) == 1
    stale_no = _press(context, f"{CAL}:n:{example.id}")
    assert "ya" in stale_no.message.replies[-1].lower()
    assert len(service.active_calibrations()) == 1


def test_no_declines_and_never_asks_again(tmp_path: Path) -> None:
    service, repo, offer_id = _sent_offer(tmp_path)
    context = FakeContext(service)
    _press(context, f"{OE}:{offer_id}:screening")
    example = repo.calibration_for(offer_id, "positive")
    assert example is not None
    no = _press(context, f"{CAL}:n:{example.id}")
    assert no.edited and service.active_calibrations() == []
    assert _questions(_press(context, f"{OE}:{offer_id}:oferta")) == []


def test_unknown_example_button_is_polite(tmp_path: Path) -> None:
    service, _repo, _offer_id = _sent_offer(tmp_path)
    query = _press(FakeContext(service), f"{CAL}:y:999")
    assert "ya" in query.message.replies[-1].lower()


# --- /calibracion -----------------------------------------------------------------


def test_calibracion_empty(tmp_path: Path) -> None:
    service, _repo, _offer_id = _sent_offer(tmp_path)
    message = FakeMessage()
    run_coro(bot.calibracion_command(FakeUpdate(message=message), FakeContext(service)))
    assert "No hay" in message.replies[0]


def test_calibracion_lists_examples_summary_and_deactivates(tmp_path: Path) -> None:
    service, repo, offer_id = _sent_offer(tmp_path, match=40)  # below threshold 65
    context = FakeContext(service)
    _press(context, f"{OE}:{offer_id}:entrevista_tecnica")
    example = repo.calibration_for(offer_id, "positive")
    assert example is not None
    _press(context, f"{CAL}:y:{example.id}")

    message = FakeMessage()
    run_coro(bot.calibracion_command(FakeUpdate(message=message), context))
    text = message.replies[0]
    assert f"#{offer_id}" in text and "40" in text
    assert "1 con nota por debajo" in text  # positive below threshold
    assert _datas(message.markups[0]) == [f"{CAL}:d:{example.id}"]

    off = _press(context, f"{CAL}:d:{example.id}")
    assert service.active_calibrations() == []
    assert off.message.replies and "desactiv" in off.message.replies[-1].lower()
    again = _press(context, f"{CAL}:d:{example.id}")
    assert "ya" in again.message.replies[-1].lower()


def test_summary_counts_negatives_above_threshold(tmp_path: Path) -> None:
    service, repo, offer_id = _sent_offer(tmp_path, match=80)
    context = FakeContext(service)
    _press(context, f"{OE}:{offer_id}:rechazada")
    _press(context, f"{CAL}:r:{offer_id}:stack")
    example = repo.calibration_for(offer_id, "negative")
    assert example is not None
    _press(context, f"{CAL}:y:{example.id}")
    summary = service.calibration_summary()
    assert (summary.total, summary.positives_below, summary.negatives_above) == (
        1,
        0,
        1,
    )
    assert "1 con nota por encima" in list_message(
        service.active_calibrations(), summary
    )


# --- keyboards / registration -----------------------------------------------------


def test_keyboards_payloads() -> None:
    assert _datas(question_keyboard(7)) == [f"{CAL}:y:7", f"{CAL}:n:7"]
    assert _datas(reason_keyboard(3))[0] == f"{CAL}:r:3:requisitos"
    assert list_keyboard([]).inline_keyboard == ()


def test_command_is_registered_in_help_and_menu() -> None:
    assert "calibracion" in {c.name for c in COMMANDS}
    assert "calibracion" in {name for name, _ in menu_commands()}
    assert "/calibracion" in help_message()
