"""Cross-portal "También en": labels, stored cards, repo queries, in-place edit."""

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from cva import bridge
from cva.ingest.sources import SOURCES, source_label
from cva.notifier import (
    SentMessage,
    keyboard_from_json,
    keyboard_to_json,
    offer_status_line,
    with_also_on,
)
from cva.tracker.repo import SCHEMA_VERSION, OfferInput, OfferRepository

LI = "https://www.linkedin.com/jobs/view/111"
IJ = (
    "https://www.infojobs.net/barcelona/ai-engineer/of-0f8d7e6c5b4a39281706f5e4d3c2b1a0"
)
IND = "https://es.indeed.com/viewjob?jk=0123456789abcdef"


def _repo(tmp_path: Path) -> OfferRepository:
    conn = sqlite3.connect(tmp_path / "a.db", check_same_thread=False)
    return OfferRepository(conn, clock=lambda: "2026-10-18")


def _add(repo: OfferRepository, url: str, company: str = "Acme") -> int:
    return repo.upsert_offer(
        OfferInput(
            title="AI Engineer", company=company, url=url, text="x", source="link"
        )
    )


def _dup_of(repo: OfferRepository, url: str, original: int) -> int:
    oid = _add(repo, url)
    repo.set_estado(oid, "descartada")
    repo.append_note(oid, f"duplicada de #{original}")
    return oid


# --- labels ------------------------------------------------------------------------


def test_every_registered_portal_has_a_human_label() -> None:
    assert {s: SOURCES[s].label for s in SOURCES} == {
        "linkedin": "LinkedIn",
        "infojobs": "InfoJobs",
        "indeed": "Indeed",
        "tecnoempleo": "Tecnoempleo",
        "jobfluent": "JobFluent",
        "manfred": "Manfred",
        "adzuna": "Adzuna",
        "foorilla": "foorilla",
    }


def test_source_label_falls_back_to_the_id() -> None:
    assert source_label("infojobs") == "InfoJobs"
    assert source_label("otro") == "otro"


# --- repo: duplicate portals -------------------------------------------------------


def test_duplicate_portal_labels_are_distinct_ordered_and_exclude_own(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    first = _add(repo, LI)
    _dup_of(repo, IJ, first)
    _dup_of(repo, IND, first)
    _dup_of(repo, "https://www.linkedin.com/jobs/view/222", first)  # own portal
    assert repo.duplicate_portal_labels(first) == ["InfoJobs", "Indeed"]
    assert repo.duplicate_portal_labels(9999) == []


def test_duplicate_portal_labels_do_not_confuse_id_prefixes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = _add(repo, LI)
    other = _add(repo, "https://www.linkedin.com/jobs/view/333", company="Other")
    _dup_of(repo, IJ, other)
    assert repo.duplicate_portal_labels(first) == []
    assert repo.duplicate_portal_labels(other) == ["InfoJobs"]


# --- repo: stored cards + migration v19 ---------------------------------------------


def test_card_roundtrip_replace_and_forget(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oid = _add(repo, LI)
    assert repo.get_card(oid) is None
    repo.save_card(oid, 5, 77, "card", None, '{"inline_keyboard": []}')
    card = repo.get_card(oid)
    assert card is not None
    assert (card.chat_id, card.message_id, card.text, card.parse_mode) == (
        5,
        77,
        "card",
        None,
    )
    assert card.keyboard == '{"inline_keyboard": []}'
    repo.save_card(oid, 5, 78, "newer", "HTML", None)  # latest card wins
    card = repo.get_card(oid)
    assert card is not None and card.message_id == 78 and card.parse_mode == "HTML"
    repo.update_card_text(oid, "edited")
    card = repo.get_card(oid)
    assert card is not None and card.text == "edited"
    repo.forget_card(5, 99)  # another message: untouched
    assert repo.get_card(oid) is not None
    repo.forget_card(5, 78)
    assert repo.get_card(oid) is None


def test_offer_cards_table_and_idempotent_migration(tmp_path: Path) -> None:
    assert SCHEMA_VERSION == 23
    repo = _repo(tmp_path)
    cols = [r[1] for r in repo._conn.execute("PRAGMA table_info(offer_cards)")]
    assert cols == [
        "offer_id",
        "chat_id",
        "message_id",
        "text",
        "parse_mode",
        "keyboard",
    ]
    oid = _add(repo, LI)
    repo.save_card(oid, 1, 2, "t", None, None)
    repo._conn.execute("PRAGMA user_version = 18")  # interrupted: table exists
    repo._conn.commit()
    repo._conn.close()
    again = _repo(tmp_path)
    assert again._conn.execute("PRAGMA user_version").fetchone()[0] == 23
    assert again.get_card(oid) is not None  # data kept
    again._conn.execute("DROP TABLE offer_cards")
    again._conn.execute("PRAGMA user_version = 18")
    again._conn.commit()
    again._conn.close()
    third = _repo(tmp_path)
    assert third._conn.execute("PRAGMA user_version").fetchone()[0] == 23
    assert third.get_card(oid) is None


# --- notifier: the line, keyboard round-trip, /status -------------------------------


def test_with_also_on_appends_one_final_line() -> None:
    assert with_also_on("card", ["InfoJobs", "Indeed"]) == (
        "card\n📍 También en: InfoJobs, Indeed"
    )


def test_with_also_on_replaces_an_existing_line_instead_of_stacking() -> None:
    once = with_also_on("card", ["InfoJobs"])
    twice = with_also_on(once, ["InfoJobs", "Indeed"])
    assert twice == "card\n📍 También en: InfoJobs, Indeed"
    assert with_also_on(twice, []) == "card"


def test_with_also_on_escapes_labels_in_html() -> None:
    assert with_also_on("c", ["A<b>&"], parse_mode="HTML").endswith("A&lt;b&gt;&amp;")


def test_keyboard_json_roundtrip() -> None:
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("CV", callback_data="cv:1")]])
    back = keyboard_from_json(keyboard_to_json(kb))
    assert back is not None
    assert back.inline_keyboard[0][0].callback_data == "cv:1"
    assert keyboard_to_json(None) is None and keyboard_from_json(None) is None
    assert keyboard_from_json("not json") is None


def test_offer_status_line_shows_also_on_when_present() -> None:
    row = SimpleNamespace(
        id=3, estado="analizada", match=70, posicion="AI", perfil="ai"
    )
    assert "También en" not in offer_status_line(row)
    line = offer_status_line(row, also_on=["InfoJobs", "Indeed"])
    assert line.endswith("· También en: InfoJobs, Indeed")


# --- bridge: in-place edit on cross-source duplicate ---------------------------------


class _Editor:
    def __init__(self, exc: Exception | None = None) -> None:
        self.calls: list[tuple[int, int, str, str | None, Any]] = []
        self.exc = exc

    def __call__(
        self, chat_id: int, message_id: int, text: str, parse_mode: str | None, kb: Any
    ) -> None:
        if self.exc is not None:
            raise self.exc
        self.calls.append((chat_id, message_id, text, parse_mode, kb))


def _ij_job() -> dict[str, Any]:
    return {"url": IJ, "title": "AI Engineer", "company": "Acme", "text": "t"}


def _process(repo: OfferRepository, editor: Any, jobs: list[dict[str, Any]]) -> None:
    bridge._process_payload(
        "b",
        jobs,
        repo=repo,
        sender=lambda *a: None,
        threshold=65,
        auto_jobs=[],
        duplicates=[],
        editor=editor,
    )


def test_duplicate_edits_the_stored_card_silently_with_the_line(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    first = _add(repo, LI)
    repo.save_card(first, 5, 77, "card", None, keyboard_to_json(None))
    editor = _Editor()
    _process(repo, editor, [_ij_job()])
    assert [c[:4] for c in editor.calls] == [
        (5, 77, "card\n📍 También en: InfoJobs", None)
    ]
    card = repo.get_card(first)
    assert card is not None and card.text.endswith("📍 También en: InfoJobs")


def test_second_portal_replaces_the_line(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = _add(repo, LI)
    repo.save_card(first, 5, 77, "card", None, None)
    editor = _Editor()
    _process(repo, editor, [_ij_job()])
    _process(repo, editor, [{**_ij_job(), "url": IND}])
    assert editor.calls[-1][2] == "card\n📍 También en: InfoJobs, Indeed"


def test_duplicate_edit_keeps_the_card_keyboard(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = _add(repo, LI)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("CV", callback_data="cv:1")]])
    repo.save_card(first, 5, 77, "card", None, keyboard_to_json(kb))
    editor = _Editor()
    _process(repo, editor, [_ij_job()])
    assert editor.calls[0][4].inline_keyboard[0][0].callback_data == "cv:1"


def test_duplicate_without_a_stored_card_sends_and_edits_nothing(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _add(repo, LI)
    editor = _Editor()
    _process(repo, editor, [_ij_job()])
    assert editor.calls == []


def test_edit_failure_is_logged_and_never_breaks_the_payload(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    repo = _repo(tmp_path)
    first = _add(repo, LI)
    repo.save_card(first, 5, 77, "card", None, None)
    _process(repo, _Editor(RuntimeError("message to edit not found")), [_ij_job()])
    assert "also-on edit failed" in caplog.text
    assert repo.find_offer_by_url(
        "https://www.infojobs.net/barcelona/ai-engineer/of-0f8d7e6c5b4a39281706f5e4d3c2b1a0"
    )


# --- bridge: transport (message id out, edit in) --------------------------------------


class _Bot:
    def __init__(self) -> None:
        self.sent: list[tuple[Any, ...]] = []
        self.edited: list[dict[str, Any]] = []

    async def send_message(self, *args: Any, **kwargs: Any) -> Any:
        self.sent.append(args)
        return SimpleNamespace(message_id=42, chat_id=7)

    async def edit_message_text(self, **kwargs: Any) -> None:
        self.edited.append(kwargs)


def _bridge_with_bot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[bridge._Bridge, _Bot, asyncio.AbstractEventLoop]:
    monkeypatch.setattr(bridge, "DEFAULT_DB_PATH", tmp_path / "b.db")
    br = bridge._Bridge(secret="s", port=0, owner_id=7, threshold=65)
    bot = _Bot()
    br._bot = bot
    return br, bot, asyncio.new_event_loop()


def test_notify_card_returns_the_sent_message_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    br, _bot, loop = _bridge_with_bot(monkeypatch, tmp_path)
    sent = loop.run_until_complete(br._notify_card("hi", None))
    assert sent == SentMessage(chat_id=7, message_id=42)


def test_notify_card_without_a_message_object_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    br, bot, loop = _bridge_with_bot(monkeypatch, tmp_path)

    async def send(*args: Any, **kwargs: Any) -> bool:
        return True

    bot.send_message = send  # type: ignore[method-assign]
    assert loop.run_until_complete(br._notify_card("hi", None)) is None


def test_edit_card_calls_edit_message_text_with_mode_and_keyboard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    br, bot, _loop = _bridge_with_bot(monkeypatch, tmp_path)
    loop = asyncio.new_event_loop()
    br._loop = loop
    import threading

    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    try:
        br._edit_card(7, 42, "new", "HTML", None)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
    assert bot.edited == [
        {
            "chat_id": 7,
            "message_id": 42,
            "text": "new",
            "parse_mode": "HTML",
            "reply_markup": None,
        }
    ]


def test_keyboard_json_is_plain_json() -> None:
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("CV", callback_data="cv:1")]])
    text = keyboard_to_json(kb)
    assert text is not None and json.loads(text)["inline_keyboard"]


def test_ubicacion_column_and_idempotent_migration_v20(tmp_path: Path) -> None:
    assert SCHEMA_VERSION == 23
    repo = _repo(tmp_path)
    cols = [r[1] for r in repo._conn.execute("PRAGMA table_info(offers)")]
    assert cols[-1] == "ubicacion"
    oid = _add(repo, LI)
    repo._conn.execute("UPDATE offers SET ubicacion = 'Girona' WHERE id = ?", (oid,))
    repo._conn.execute("PRAGMA user_version = 19")  # interrupted: column exists
    repo._conn.commit()
    repo._conn.close()
    again = _repo(tmp_path)
    assert again._conn.execute("PRAGMA user_version").fetchone()[0] == 23
    assert again.get_offer(oid).ubicacion == "Girona"  # data kept
