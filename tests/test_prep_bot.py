"""/prep command and "Preparar entrevista" buttons (bot wiring)."""

import asyncio
import io
from pathlib import Path

import bot
from cva.notifier import outcome_keyboard
from cva.report.prep_pack import prep_filename
from tests.test_bot import FakeContext, FakeMessage, FakeQuery, FakeUpdate
from tests.test_interview_prep import FakeLlm, make


class CapturingMessage(FakeMessage):
    def __init__(self, text: str | None = None) -> None:
        super().__init__(text)
        self.payloads: list[bytes] = []

    async def reply_document(
        self, document: object, filename: str | None = None
    ) -> None:
        assert isinstance(document, io.BytesIO)
        self.payloads.append(document.getvalue())
        await super().reply_document(document, filename)


def setup(tmp_path: Path, llm: FakeLlm | None = None, estado: str | None = None):
    service, repo, oid = make(tmp_path, llm or FakeLlm())
    if estado:
        repo.mark_sent(oid, cv_path="out/cv.pdf", cambios="c", match=70)
        repo.record_estado_event(oid, estado)
    return service, repo, oid, FakeContext(service)


def run_prep(context: FakeContext, *args: str) -> FakeMessage:
    context.args = list(args)
    message = FakeMessage("/prep")
    asyncio.run(bot.prep_command(FakeUpdate(message=message), context))
    return message


def press(context: FakeContext, data: str) -> FakeQuery:
    query = FakeQuery(data)
    query.message = CapturingMessage()
    asyncio.run(bot.callback_router(FakeUpdate(query=query), context))
    return query


def data(markup: object) -> list[str]:
    return [b.callback_data for r in markup.inline_keyboard for b in r]  # type: ignore[attr-defined]


def labels(markup: object) -> list[str]:
    return [b.text for r in markup.inline_keyboard for b in r]  # type: ignore[attr-defined]


def test_prep_without_a_valid_id_shows_usage(tmp_path: Path) -> None:
    _s, _r, _o, context = setup(tmp_path)
    assert run_prep(context).replies == ["Uso: /prep <id>"]
    assert run_prep(context, "abc").replies == ["Uso: /prep <id>"]


def test_prep_unknown_offer_is_a_clear_error(tmp_path: Path) -> None:
    _s, _r, _o, context = setup(tmp_path)
    assert run_prep(context, "999").replies[0].startswith("⚠️")


def test_prep_offers_three_stages_with_the_default_from_the_estado(
    tmp_path: Path,
) -> None:
    _s, _r, oid, context = setup(tmp_path, estado="entrevista_tecnica")
    message = run_prep(context, str(oid))
    assert data(message.markups[0]) == [
        f"prep:{oid}:rrhh",
        f"prep:{oid}:tecnica",
        f"prep:{oid}:final",
    ]
    assert "Técnica" in message.replies[0]
    assert labels(message.markups[0])[1].startswith("⭐")
    assert not labels(message.markups[0])[0].startswith("⭐")


def test_prep_default_is_rrhh_for_other_estados(tmp_path: Path) -> None:
    _s, _r, oid, context = setup(tmp_path)
    message = run_prep(context, str(oid))
    assert labels(message.markups[0])[0].startswith("⭐")


def test_button_opens_the_stage_choice(tmp_path: Path) -> None:
    _s, _r, oid, context = setup(tmp_path, estado="screening")
    query = press(context, f"prep:{oid}")
    assert data(query.message.markups[0])[0] == f"prep:{oid}:rrhh"


def test_stage_press_shows_progress_then_document_and_summary(
    tmp_path: Path,
) -> None:
    llm = FakeLlm()
    _s, repo, oid, context = setup(tmp_path, llm, estado="entrevista_tecnica")
    query = press(context, f"prep:{oid}:tecnica")
    message = query.message
    assert message.replies[0] == "⏳ Preparando…"
    assert message.documents == [prep_filename(oid, "tecnica")]
    assert b"Python as\xc3\xadncrono" in message.payloads[0]
    assert (
        "Temas clave" in message.replies[-1]
        and "Python asíncrono" in message.replies[-1]
    )
    assert repo.latest_prep_pack(oid, "tecnica") is not None
    assert len(llm.pack_calls) == 1


def test_llm_failure_gives_a_clear_error_and_no_document(tmp_path: Path) -> None:
    _s, repo, oid, context = setup(tmp_path, FakeLlm(fail_pack=True))
    message = press(context, f"prep:{oid}:rrhh").message
    assert message.replies[0] == "⏳ Preparando…"
    assert message.replies[-1].startswith("⚠️ No pude preparar la entrevista")
    assert message.documents == []
    assert repo.latest_prep_pack(oid, "rrhh") is None


def test_invalid_pack_gives_a_clear_error_and_no_document(tmp_path: Path) -> None:
    _s, _r, oid, context = setup(tmp_path, FakeLlm(pack={"topics": []}))
    message = press(context, f"prep:{oid}:final").message
    assert "formato" in message.replies[-1]
    assert message.documents == []


def test_stale_or_bad_prep_callbacks_are_harmless(tmp_path: Path) -> None:
    _s, _r, oid, context = setup(tmp_path)
    assert press(context, "prep:999:rrhh").message.replies[-1].startswith("⚠️")
    assert press(context, f"prep:{oid}:bogus").message.replies == [
        "Acción no reconocida."
    ]
    assert press(context, "prep:x").message.replies == ["Acción no reconocida."]


def test_a_second_press_while_preparing_does_not_start_another(
    tmp_path: Path,
) -> None:
    llm = FakeLlm()
    service, _r, oid, context = setup(tmp_path, llm)

    async def scenario() -> list[str]:
        assert service.claim_prep(oid, "rrhh")
        try:
            query = FakeQuery(f"prep:{oid}:rrhh")
            await bot.callback_router(FakeUpdate(query=query), context)
        finally:
            service.release_prep(oid, "rrhh")
        return query.message.replies

    replies = asyncio.run(scenario())
    assert replies == ["⏳ Ya estoy preparando esa entrevista…"]
    assert llm.pack_calls == []


class SlowLlm(FakeLlm):
    async def json_completion(self, system: str, user: str, schema: type) -> object:
        await asyncio.sleep(0.01)  # a real await point: presses interleave here
        return await super().json_completion(system, user, schema)


def test_interleaved_presses_start_exactly_one_preparation(tmp_path: Path) -> None:
    llm = SlowLlm()
    service, _r, oid, context = setup(tmp_path, llm)

    async def scenario() -> list[list[str]]:
        queries = [FakeQuery(f"prep:{oid}:rrhh") for _ in range(2)]
        for q in queries:
            q.message = CapturingMessage()
        await asyncio.gather(
            *(bot.callback_router(FakeUpdate(query=q), context) for q in queries)
        )
        return [q.message.replies for q in queries]

    first, second = asyncio.run(scenario())
    all_replies = first + second
    assert sum("⏳ Preparando…" in r for r in all_replies) == 1
    assert all_replies.count("⏳ Ya estoy preparando esa entrevista…") == 1
    assert len(llm.pack_calls) == 1
    assert not service.prep_in_progress(oid, "rrhh")  # released after use


def test_prep_claims_do_not_accumulate(tmp_path: Path) -> None:
    service, _r, oid, context = setup(tmp_path)
    press(context, f"prep:{oid}:rrhh")
    assert not service.prep_in_progress(oid, "rrhh")
    assert not service._prep_claims
    assert service.claim_prep(oid, "rrhh") is True
    assert service.claim_prep(oid, "rrhh") is False
    service.release_prep(oid, "rrhh")
    assert not service._prep_claims


def test_claim_is_released_when_generation_fails(tmp_path: Path) -> None:
    service, _r, oid, context = setup(tmp_path, FakeLlm(fail_pack=True))
    press(context, f"prep:{oid}:rrhh")
    assert not service._prep_claims


def test_outcome_view_has_the_prep_button_only_for_interview_estados() -> None:
    for estado in ("screening", "entrevista_tecnica", "entrevista_final"):
        assert "prep:7" in data(outcome_keyboard(7, estado))
    for estado in ("enviada", "oferta", "rechazada", None):
        assert "prep:7" not in data(outcome_keyboard(7, estado))
    assert "prep:7" not in data(outcome_keyboard(7))


def test_outcome_command_for_an_interview_offer_shows_the_button(
    tmp_path: Path,
) -> None:
    _s, _r, oid, context = setup(tmp_path, estado="screening")
    context.args = [str(oid)]
    message = FakeMessage("/outcome")
    asyncio.run(bot.outcome_command(FakeUpdate(message=message), context))
    assert f"prep:{oid}" in data(message.markups[-1])


def test_recording_an_interview_estado_offers_the_prep_button(tmp_path: Path) -> None:
    _s, _r, oid, context = setup(tmp_path, estado="enviada")
    query = press(context, f"oe:{oid}:entrevista_final")
    markups = [m for m in query.message.markups if m is not None]
    assert markups and f"prep:{oid}" in data(markups[0])
    other = press(context, f"oe:{oid}:oferta")
    assert all(m is None for m in other.message.markups)


# --- help / menu / BUTTONS parity -----------------------------------------------


def test_prep_is_in_the_command_table_menu_and_help() -> None:
    from cva.commands import COMMANDS, menu_commands
    from cva.notifier import help_message

    assert "prep" in {c.name for c in COMMANDS}
    assert "prep" in {name for name, _ in menu_commands()}
    assert "/prep <id>" in help_message()
    assert len(help_message()) <= 4096


def test_prep_keyboard_labels_are_documented_in_help() -> None:
    import re

    from cva.notifier import help_message, prep_keyboard, prep_stage_keyboard

    text = help_message()
    buttons = [
        b.text
        for kb in (prep_keyboard(1), prep_stage_keyboard(1, "tecnica"))
        for row in kb.inline_keyboard
        for b in row
    ]
    assert len(buttons) == 4
    for label in buttons:
        core = re.sub(r"^[^\w]+", "", label).strip()
        assert core in text, label
