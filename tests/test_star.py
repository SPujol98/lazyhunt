"""STAR story bank: table, repository, candidates, /star guided flow, help parity."""

import asyncio
import sqlite3
from pathlib import Path

import pytest

import bot
from cva.claimsdb import ClaimsDB, load_claims
from cva.commands import COMMANDS
from cva.fetch import FetchResponse
from cva.notifier import help_message
from cva.star import ST, STAR_FIELDS, star_candidates
from cva.tracker import STAR_PLACEHOLDER, OfferRepository, RepositoryError
from cva.tracker.repo import SCHEMA_VERSION
from tests.demo import DEMO_CLAIMS
from tests.test_bot import (
    FakeContext,
    FakeFetcher,
    FakeMessage,
    FakeQuery,
    FakeUpdate,
    make_service,
)

CLAIMS_PATH = DEMO_CLAIMS


def repo_at(tmp_path: Path, name: str = "a.db") -> OfferRepository:
    return OfferRepository(sqlite3.connect(tmp_path / name), clock=lambda: "2026-10-28")


# --- schema + repository ------------------------------------------------------


def test_schema_version_and_star_table(tmp_path: Path) -> None:
    repo = repo_at(tmp_path)
    assert SCHEMA_VERSION == 24
    cols = [r[1] for r in repo._conn.execute("PRAGMA table_info(star_stories)")]
    assert cols == [
        "id",
        "title",
        "source_claim_ids",
        "situation",
        "task",
        "action",
        "result",
        "metrics",
        "use_for",
        "complete",
        "updated_at",
    ]


def test_v16_database_migrates_to_v17_idempotently(tmp_path: Path) -> None:
    first = repo_at(tmp_path)
    story = first.add_star("Historia", ["exp-x"])
    first._conn.execute("PRAGMA user_version = 16")  # interrupted: table exists
    first._conn.commit()
    first._conn.close()
    again = repo_at(tmp_path)
    assert again._conn.execute("PRAGMA user_version").fetchone()[0] == 24
    assert again.get_star(story).title == "Historia"  # data kept
    again._conn.execute("DROP TABLE star_stories")
    again._conn.execute("PRAGMA user_version = 16")
    again._conn.commit()
    again._conn.close()
    third = repo_at(tmp_path)
    assert third._conn.execute("PRAGMA user_version").fetchone()[0] == 24
    assert third.list_stars() == []


def test_star_crud_roundtrip_and_completeness(tmp_path: Path) -> None:
    repo = repo_at(tmp_path)
    sid = repo.add_star("Migración Acme", ["exp-acme-etl"])
    story = repo.get_star(sid)
    assert story.source_claim_ids == ["exp-acme-etl"]
    assert story.situation == story.task == story.action == STAR_PLACEHOLDER
    assert story.complete is False and story.updated_at == "2026-10-28"
    for field in ("situation", "task", "action", "result"):
        repo.update_star(sid, **{field: f"  texto {field}  "})
    assert repo.get_star(sid).complete is False  # use_for still missing
    assert repo.get_star(sid).situation == "  texto situation  "  # stored as given
    repo.update_star(sid, use_for="problema técnico difícil")
    assert repo.get_star(sid).complete is True
    repo.update_star(sid, action=STAR_PLACEHOLDER)
    assert repo.get_star(sid).complete is False
    assert [s.id for s in repo.list_stars()] == [sid]
    repo.delete_star(sid)
    assert repo.list_stars() == []
    with pytest.raises(RepositoryError):
        repo.get_star(sid)


def test_update_star_rejects_unknown_fields(tmp_path: Path) -> None:
    repo = repo_at(tmp_path)
    sid = repo.add_star("T", [])
    with pytest.raises(RepositoryError):
        repo.update_star(sid, bogus="x")
    with pytest.raises(RepositoryError):
        repo.update_star(999, situation="x")


# --- candidates ---------------------------------------------------------------


def test_candidates_are_four_to_six_deterministic_and_grounded() -> None:
    db = load_claims(CLAIMS_PATH)
    first = star_candidates(db)
    assert first == star_candidates(db)
    assert 4 <= len(first) <= 6
    keys = [c.key for c in first]
    assert len(set(keys)) == len(keys)
    assert keys[:2] == ["exp-acme", "exp-northwind"]  # experience first
    for candidate in first:
        assert candidate.title.strip() and len(candidate.title) <= 120
        assert candidate.source_claim_ids
        for claim_id in candidate.source_claim_ids:
            assert claim_id in db.by_id
            assert not claim_id.endswith("_en")


def test_candidates_with_a_tiny_db_do_not_pad() -> None:
    db = load_claims(CLAIMS_PATH)
    claims = [c for c in db.claims if c.id.startswith("exp-acme")]
    tiny = ClaimsDB(claims=claims, by_id={c.id: c for c in claims})
    assert [c.key for c in star_candidates(tiny)] == ["exp-acme"]


# --- bot flow -----------------------------------------------------------------


def setup(tmp_path: Path):
    service, llm = make_service(tmp_path, FakeFetcher(FetchResponse(200, "", "u")))
    return service, llm, FakeContext(service)


def say(context: FakeContext, text: str) -> FakeMessage:
    message = FakeMessage(text)
    asyncio.run(bot.paste_handler(FakeUpdate(message=message), context))
    return message


def press(context: FakeContext, data: str) -> FakeQuery:
    query = FakeQuery(data)
    asyncio.run(bot.callback_router(FakeUpdate(query=query), context))
    return query


def run_star(context: FakeContext) -> FakeMessage:
    message = FakeMessage("/star")
    asyncio.run(bot.star_command(FakeUpdate(message=message), context))
    return message


def skip(context: FakeContext) -> FakeMessage:
    message = FakeMessage("/skip")
    asyncio.run(bot.skip_command(FakeUpdate(message=message), context))
    return message


def button_data(markup: object) -> list[str]:
    return [b.callback_data for r in markup.inline_keyboard for b in r]  # type: ignore[attr-defined]


def test_star_without_stories_offers_candidates_as_buttons(tmp_path: Path) -> None:
    _service, llm, context = setup(tmp_path)
    message = run_star(context)
    data = button_data(message.markups[-1])
    assert 4 <= len(data) <= 6
    assert all(d.startswith(f"{ST}:new:") for d in data)
    assert bot._STAR_KEY not in context.user_data
    assert llm.calls == 0


def test_choosing_a_candidate_starts_the_guided_flow_one_question_at_a_time(
    tmp_path: Path,
) -> None:
    service, llm, context = setup(tmp_path)
    query = press(context, f"{ST}:new:exp-acme")
    (story,) = service.star_stories()
    assert story.source_claim_ids and story.situation == STAR_PLACEHOLDER
    replies = "\n".join(query.message.replies)
    assert "Situación (contexto, 1-3 frases)" in replies
    assert "Tarea" not in replies  # one question at a time
    assert bot._STAR_KEY in context.user_data

    answers = [
        "  Había que migrar contratos de Excel.  ",
        "Yo era responsable del script de migración.",
        "Escribí la migración en Python con validaciones.",
        "Se migraron años de datos sin pérdidas.",
        "trabajo en equipo, problema técnico difícil",
    ]
    for index, answer in enumerate(answers[:-1]):
        message = say(context, answer)
        question = STAR_FIELDS[index + 1][1]
        assert question in message.replies[-1]
    final = say(context, answers[-1])
    stored = service.star_story(story.id)
    assert stored.situation == "Había que migrar contratos de Excel."  # strip only
    assert stored.task == answers[1] and stored.action == answers[2]
    assert stored.result == answers[3] and stored.use_for == answers[4]
    assert stored.complete is True
    assert "✅" in final.replies[-1]
    assert bot._STAR_KEY not in context.user_data
    assert llm.calls == 0  # nothing generated: answers are the owner's own words


def test_skip_leaves_placeholder_and_marks_incomplete(tmp_path: Path) -> None:
    service, _llm, context = setup(tmp_path)
    press(context, f"{ST}:new:exp-northwind")
    (story,) = service.star_stories()
    say(context, "Contexto")
    reply = skip(context)  # skips Tarea
    assert STAR_FIELDS[2][1] in "\n".join(reply.replies)  # asks Acción next
    say(context, "Hice esto")
    say(context, "Resultado")
    final = say(context, "equipo")
    stored = service.star_story(story.id)
    assert stored.task == STAR_PLACEHOLDER
    assert stored.complete is False
    assert "✏️" in final.replies[-1] and STAR_PLACEHOLDER in final.replies[-1]


def test_blank_answer_asks_the_same_question_again(tmp_path: Path) -> None:
    service, _llm, context = setup(tmp_path)
    press(context, f"{ST}:new:exp-acme")
    (story,) = service.star_stories()
    message = say(context, "   ")
    assert STAR_FIELDS[0][1] in message.replies[-1]
    assert service.star_story(story.id).situation == STAR_PLACEHOLDER
    assert bot._STAR_KEY in context.user_data


def test_pending_state_expires_after_thirty_minutes(tmp_path: Path) -> None:
    service, _llm, context = setup(tmp_path)
    press(context, f"{ST}:new:exp-acme")
    (story,) = service.star_stories()
    context.user_data[bot._STAR_AT_KEY] -= 31 * 60  # type: ignore[operator]
    message = say(context, "demasiado tarde")
    assert message.replies == []
    assert service.star_story(story.id).situation == STAR_PLACEHOLDER
    assert bot._STAR_KEY not in context.user_data


def test_each_answer_refreshes_the_expiry(tmp_path: Path) -> None:
    service, _llm, context = setup(tmp_path)
    press(context, f"{ST}:new:exp-acme")
    context.user_data[bot._STAR_AT_KEY] -= 29 * 60  # type: ignore[operator]
    say(context, "uno")
    context.user_data[bot._STAR_AT_KEY] -= 29 * 60  # type: ignore[operator]
    say(context, "dos")
    (story,) = service.star_stories()
    assert (story.situation, story.task) == ("uno", "dos")


def test_star_with_stories_lists_completeness_and_buttons(tmp_path: Path) -> None:
    service, _llm, context = setup(tmp_path)
    press(context, f"{ST}:new:exp-acme")
    for text in ("a", "b", "c", "d", "e"):
        say(context, text)
    press(context, f"{ST}:new:exp-northwind")
    message = run_star(context)
    text = message.replies[-1]
    assert "✅" in text and "✏️" in text
    data = button_data(message.markups[-1])
    for sid in (s.id for s in service.star_stories()):
        assert {f"{ST}:v:{sid}", f"{ST}:e:{sid}", f"{ST}:d:{sid}"} <= set(data)
    assert f"{ST}:add" in data
    assert bot._STAR_KEY not in context.user_data  # /star cancels a pending flow


def test_view_edit_one_field_and_delete_with_confirmation(tmp_path: Path) -> None:
    service, _llm, context = setup(tmp_path)
    press(context, f"{ST}:new:exp-acme")
    for text in ("s", "t", "a", "r", "u"):
        say(context, text)
    (story,) = service.star_stories()
    view = press(context, f"{ST}:v:{story.id}")
    assert "Situación" in view.message.replies[-1] and "s" in view.message.replies[-1]
    edit = press(context, f"{ST}:e:{story.id}")
    assert f"{ST}:f:{story.id}:3" in button_data(edit.message.markups[-1])
    ask = press(context, f"{ST}:f:{story.id}:3")  # Resultado
    assert STAR_FIELDS[3][1] in ask.message.replies[-1]
    say(context, "  Reduje el tiempo un 40%  ")
    assert service.star_story(story.id).result == "Reduje el tiempo un 40%"
    assert bot._STAR_KEY not in context.user_data  # single-field edit ends there
    confirm = press(context, f"{ST}:d:{story.id}")
    assert button_data(confirm.message.markups[-1]) == [
        f"{ST}:dy:{story.id}",
        f"{ST}:l",
    ]
    assert service.star_stories()  # not deleted yet
    press(context, f"{ST}:dy:{story.id}")
    assert service.star_stories() == []


def test_stale_and_bad_star_callbacks_are_harmless(tmp_path: Path) -> None:
    service, _llm, context = setup(tmp_path)
    for data in (
        f"{ST}:v:99",
        f"{ST}:dy:99",
        f"{ST}:f:99:1",
        f"{ST}:new:nope",
        f"{ST}:zzz",
    ):
        query = press(context, data)
        assert query.message.replies  # explained, never raised
    assert service.star_stories() == []
    assert bot._STAR_KEY not in context.user_data


def test_choosing_an_already_used_candidate_does_not_duplicate(tmp_path: Path) -> None:
    service, _llm, context = setup(tmp_path)
    press(context, f"{ST}:new:exp-acme")
    context.user_data.pop(bot._STAR_KEY)
    press(context, f"{ST}:new:exp-acme")
    assert len(service.star_stories()) == 1


def test_add_offers_only_unused_candidates(tmp_path: Path) -> None:
    _service, _llm, context = setup(tmp_path)
    press(context, f"{ST}:new:exp-acme")
    context.user_data.pop(bot._STAR_KEY)
    query = press(context, f"{ST}:add")
    data = button_data(query.message.markups[-1])
    assert f"{ST}:new:exp-acme" not in data and data


def test_outcome_button_clears_a_pending_star_flow(tmp_path: Path) -> None:
    service, _llm, context = setup(tmp_path)
    from cva.tracker import OfferInput

    oid = service._repo.upsert_offer(
        OfferInput(
            job_id="1",
            title="T",
            company="C",
            url="https://x/1",
            text="x",
            source="link",
            profile="ai",
        )
    )
    service._repo.mark_sent(oid, cv_path="cv.pdf", cambios="c", match=70)
    press(context, f"{ST}:new:exp-acme")
    press(context, f"oe:{oid}:screening")
    assert bot._STAR_KEY not in context.user_data


# --- help / menu parity -------------------------------------------------------


def test_star_is_in_the_command_table_and_help() -> None:
    assert "star" in {c.name for c in COMMANDS}
    assert "/star" in help_message()
    assert len(help_message()) < 4096


def test_star_keyboard_labels_are_documented_in_help(tmp_path: Path) -> None:
    import re

    from cva.star import (
        candidates_keyboard,
        delete_keyboard,
        edit_keyboard,
        list_keyboard,
        story_keyboard,
    )

    service, _llm, context = setup(tmp_path)
    press(context, f"{ST}:new:exp-acme")
    context.user_data.pop(bot._STAR_KEY)
    stories = service.star_stories()
    text = help_message()
    labels = [
        b.text
        for kb in (
            list_keyboard(stories),
            story_keyboard(1),
            edit_keyboard(1),
            delete_keyboard(1),
        )
        for row in kb.inline_keyboard
        for b in row
    ]
    assert labels
    for label in labels:
        core = re.sub(r"\d+", "", re.sub(r"^[^\w]+", "", label)).strip()
        assert core in text, label
    assert candidates_keyboard(service.star_candidates())  # built, titles dynamic


def test_skip_during_single_field_edit_keeps_the_answer(tmp_path: Path) -> None:
    service, _llm, context = setup(tmp_path)
    press(context, f"{ST}:new:exp-northwind")
    (story,) = service.star_stories()
    for text in ("Contexto", "Tarea X", "Hice esto", "Resultado", "equipo"):
        say(context, text)
    press(context, f"{ST}:f:{story.id}:1")  # edit Tarea only
    reply = skip(context)
    stored = service.star_story(story.id)
    assert stored.task == "Tarea X"
    assert stored.complete is True
    assert "Sin cambios en tarea" in "\n".join(reply.replies)
    assert bot._STAR_KEY not in context.user_data


def test_skip_in_the_initial_flow_still_uses_the_placeholder(tmp_path: Path) -> None:
    service, _llm, context = setup(tmp_path)
    press(context, f"{ST}:new:exp-northwind")
    (story,) = service.star_stories()
    skip(context)
    assert service.star_story(story.id).situation == STAR_PLACEHOLDER


def test_incomplete_story_names_missing_fields_in_spanish(tmp_path: Path) -> None:
    service, _llm, context = setup(tmp_path)
    press(context, f"{ST}:new:exp-northwind")
    (_story,) = service.star_stories()
    say(context, "Contexto")
    skip(context)
    say(context, "Hice esto")
    say(context, "Resultado")
    final = say(context, "equipo")
    text = final.replies[-1]
    assert "Falta: tarea" in text
    assert "situation" not in text and "use_for" not in text
