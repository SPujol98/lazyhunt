"""Follow-up reminders: draft generation, selection, state, heartbeat job, buttons."""

import asyncio
import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

import bot
from cva.claimsdb import load_claims
from cva.fetch import FetchResponse
from cva.llm import LlmResponseError
from cva.match.cover_letter import validate_text
from cva.match.extraction import ExtractionCache
from cva.match.followup import (
    MAX_WORDS,
    MIN_WORDS,
    build_followup_prompt,
    generate_followup,
    validate_followup,
)
from cva.match.judge import build_claims_digest
from cva.match.scorer import load_synonyms
from cva.notifier import (
    FU,
    chunk_message,
    followup_keyboard,
    followup_message,
    followup_proposal_keyboard,
    followup_proposal_message,
)
from cva.scheduler import heartbeat
from cva.service import JobService, ServiceError
from cva.tracker import OfferInput, OfferRepository
from tests.demo import DEMO_CLAIMS, DEMO_MASTERS, DEMO_SYNONYMS
from tests.test_bot import FakeContext, FakeQuery, FakeUpdate

CLAIMS_PATH = DEMO_CLAIMS
SYNONYMS_PATH = DEMO_SYNONYMS
SENT_DAY = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
POSTING = (
    "Buscamos AI Engineer para nuestro equipo de agentes LLM en Valencia. "
    "Trabajarás con RAG y Python en producción."
)
LETTER = "Hola, me interesa el puesto de AI Engineer en Acme. Un saludo."
DRAFT = (
    "Hola, soy Alex Rivera. Os escribí hace unas semanas por el puesto de AI "
    "Engineer en Acme y quería saber si habéis podido revisar mi candidatura. "
    "Sigo muy interesado en el equipo y en el trabajo con agentes y RAG que "
    "describíais en la oferta, y estaré encantado de ampliar cualquier detalle "
    "de los proyectos que os mencioné en mi carta. Entiendo que estos procesos "
    "llevan su tiempo, así que os agradezco de antemano cualquier novedad sobre "
    "el estado del proceso. Un saludo, Alex Rivera"
)


def test_fixture_draft_is_inside_the_word_range() -> None:
    assert MIN_WORDS <= len(DRAFT.split()) <= MAX_WORDS


class Clock:
    def __init__(self, now: datetime = SENT_DAY) -> None:
        self.now = now

    def advance(self, days: float) -> None:
        self.now += timedelta(days=days)

    def day(self) -> str:
        return self.now.date().isoformat()


class Llm:
    model = "fake"

    def __init__(self, *replies: str | Exception) -> None:
        self.replies = list(replies) or [DRAFT]
        self.users: list[str] = []
        self.systems: list[str] = []

    async def json_completion(
        self, system: str, user: str, schema: type[BaseModel], *, model=None
    ):
        self.systems.append(system)
        self.users.append(user)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return schema.model_validate({"text": reply})


class NoFetch:
    async def get(self, url: str) -> FetchResponse:
        raise AssertionError("no fetch")


def make(tmp_path: Path, llm: object | None = None, clock: Clock | None = None):
    clock = clock or Clock()
    conn = sqlite3.connect(tmp_path / "app.db")
    repo = OfferRepository(conn, clock=clock.day)
    db = load_claims(CLAIMS_PATH)
    service = JobService(
        repo=repo,
        llm=llm if llm is not None else Llm(),  # type: ignore[arg-type]
        fetcher=NoFetch(),  # type: ignore[arg-type]
        db=db,
        syn=load_synonyms(SYNONYMS_PATH, db),
        cache=ExtractionCache(conn),
        masters_dir=DEMO_MASTERS,
        now_fn=lambda: clock.now,
    )
    return service, repo, clock


def sent_offer(repo: OfferRepository, n: int = 1) -> int:
    oid = repo.upsert_offer(
        OfferInput(
            job_id=str(n),
            title=f"AI Engineer {n}",
            company="Acme",
            url=f"https://x/{n}",
            text=POSTING,
            source="link",
            profile="ai",
        )
    )
    repo.set_cover_letter(oid, LETTER)
    repo.mark_sent(oid, cv_path="cv.pdf", cambios="c", match=70)
    return oid


# --- validator and generation -------------------------------------------------


def test_validate_text_takes_the_word_bounds() -> None:
    text = " ".join(["palabra"] * 40)
    assert (
        validate_text(text, "es", hard_min=30, hard_max=50, min_words=30, max_words=50)
        == []
    )
    problems = validate_text(
        text, "es", hard_min=50, hard_max=60, min_words=50, max_words=60
    )
    assert any("palabras" in p for p in problems)


def test_validate_followup_accepts_good_and_rejects_bad_drafts() -> None:
    assert validate_followup(DRAFT, "es") == []
    assert any("palabras" in p for p in validate_followup("Hola. Un saludo.", "es"))
    assert any(
        "apasiona" in p for p in validate_followup(DRAFT + " Me apasiona esto.", "es")
    )
    assert any(
        "guion largo" in p for p in validate_followup(DRAFT + " Y además — sí.", "es")
    )
    assert any("idioma" in p for p in validate_followup(DRAFT, "en"))


def _args(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "posting_text": POSTING,
        "sent_letter": LETTER,
        "title": "AI Engineer",
        "company": "Acme",
        "language": "es",
        "claims_digest": build_claims_digest(load_claims(CLAIMS_PATH)),
        "voice_sample": "VOZ-MUESTRA directa y corta.",
        "owner_name": "Alex Rivera",
        "days_silent": 12,
        "number": 1,
    }
    base.update(over)
    return base


def test_prompt_carries_facts_voice_and_rules() -> None:
    system, user = build_followup_prompt(**_args())  # type: ignore[arg-type]
    assert f"{MIN_WORDS}-{MAX_WORDS} words" in system
    assert "never invent" in system.lower()
    assert "<offer>" in user and POSTING in user
    assert LETTER in user
    assert "VOZ-MUESTRA" in user
    assert "Alex Rivera" in user
    assert "Spanish" in user
    assert "12" in user
    assert "identity-contact" in user  # claims digest


def test_generate_followup_clean_draft_is_one_call() -> None:
    llm = Llm(DRAFT)
    result = asyncio.run(generate_followup(llm, **_args()))  # type: ignore[arg-type]
    assert (result.text, result.warnings) == (DRAFT, [])
    assert len(llm.users) == 1


def test_generate_followup_retries_once_then_warns() -> None:
    bad = DRAFT + " Me apasiona todo."
    llm = Llm(bad, bad)
    result = asyncio.run(generate_followup(llm, **_args()))  # type: ignore[arg-type]
    assert len(llm.users) == 2
    assert "apasiona" in llm.users[1]
    assert result.text == bad and result.warnings


def test_generate_followup_retry_fixes_the_draft() -> None:
    llm = Llm(DRAFT + " Me apasiona todo.", DRAFT)
    result = asyncio.run(generate_followup(llm, **_args()))  # type: ignore[arg-type]
    assert (result.text, result.warnings) == (DRAFT, [])


def test_generate_followup_retry_failure_keeps_first_with_warnings() -> None:
    bad = DRAFT + " Me apasiona todo."
    llm = Llm(bad, LlmResponseError("boom"))
    result = asyncio.run(generate_followup(llm, **_args()))  # type: ignore[arg-type]
    assert result.text == bad and result.warnings


# --- selection ----------------------------------------------------------------


def _sub(tmp_path: Path) -> Path:
    path = tmp_path / "ref"
    path.mkdir()
    return path


def due_ids(service: JobService) -> list[int]:
    return [item.offer.id for item in service.followup_due()]


def test_reminder_due_exactly_after_ten_days_of_silence(tmp_path: Path) -> None:
    service, repo, clock = make(tmp_path)
    oid = sent_offer(repo)
    clock.advance(9)
    assert due_ids(service) == []
    clock.advance(1)
    (item,) = service.followup_due()
    assert (item.offer.id, item.days_silent, item.followups, item.kind) == (
        oid,
        10,
        0,
        "reminder",
    )


def test_recent_activity_resets_the_silence(tmp_path: Path) -> None:
    service, repo, clock = make(tmp_path)
    oid = sent_offer(repo)
    clock.advance(8)
    repo.add_event(oid, "note", note="me llamó Ana")
    clock.advance(5)
    assert due_ids(service) == []  # 5 days since the note
    clock.advance(5)
    assert due_ids(service) == [oid]


def test_only_enviada_offers_are_followed_up(tmp_path: Path) -> None:
    service, repo, clock = make(tmp_path)
    oid = sent_offer(repo)
    clock.advance(20)
    service.record_outcome(oid, "screening")
    assert due_ids(service) == []


def test_two_followups_stop_the_reminders(tmp_path: Path) -> None:
    service, repo, clock = make(tmp_path)
    oid = sent_offer(repo)
    clock.advance(10)
    assert service.record_followup_sent(oid) == 1
    clock.advance(10)
    (item,) = service.followup_due()
    assert (item.kind, item.followups) == ("reminder", 1)
    assert service.record_followup_sent(oid) == 2
    clock.advance(20)
    assert due_ids(service) == []


def test_record_followup_sent_logs_event_and_requires_a_sent_offer(
    tmp_path: Path,
) -> None:
    service, repo, _clock = make(tmp_path)
    oid = sent_offer(repo)
    service.record_followup_sent(oid)
    assert [e.kind for e in repo.list_events(oid)] == ["followup"]
    unsent = repo.upsert_offer(
        OfferInput(job_id="9", title="t", url="https://x/9", text="x", source="link")
    )
    with pytest.raises(ServiceError):
        service.record_followup_sent(unsent)


def test_reminder_is_sent_once_per_cycle(tmp_path: Path) -> None:
    service, repo, clock = make(tmp_path)
    oid = sent_offer(repo)
    clock.advance(10)
    (item,) = service.followup_due()
    service.mark_followup_notified(item)
    assert due_ids(service) == []
    clock.advance(30)
    assert due_ids(service) == []  # still the same cycle, no repeat
    service.record_followup_sent(oid)
    clock.advance(10)
    assert due_ids(service) == [oid]  # a new cycle after the follow-up


def test_skip_snoozes_seven_days_then_reminds_again(tmp_path: Path) -> None:
    service, repo, clock = make(tmp_path)
    oid = sent_offer(repo)
    clock.advance(10)
    (item,) = service.followup_due()
    service.mark_followup_notified(item)
    service.snooze_followup(oid)
    clock.advance(6)
    assert due_ids(service) == []
    clock.advance(1)
    assert due_ids(service) == [oid]


def test_proposal_after_sixty_days_and_two_followups_once(tmp_path: Path) -> None:
    service, repo, clock = make(tmp_path)
    oid = sent_offer(repo)
    clock.advance(10)
    service.record_followup_sent(oid)
    service.record_followup_sent(oid)
    clock.advance(59)
    assert due_ids(service) == []
    clock.advance(1)
    (item,) = service.followup_due()
    assert (item.kind, item.followups, item.days_silent) == ("proposal", 2, 60)
    service.mark_followup_notified(item)
    assert due_ids(service) == []


def test_sixty_days_with_fewer_than_two_followups_is_a_reminder(
    tmp_path: Path,
) -> None:
    service, repo, clock = make(tmp_path)
    sent_offer(repo)
    clock.advance(70)
    (item,) = service.followup_due()
    assert item.kind == "reminder"


def test_send_failure_backs_off_ten_minutes_then_an_hour(tmp_path: Path) -> None:
    service, repo, clock = make(tmp_path)
    oid = sent_offer(repo)
    clock.advance(10)
    (item,) = service.followup_due()
    service.record_followup_failure(item)
    assert due_ids(service) == []
    clock.advance(10 / 1440)
    assert due_ids(service) == [oid]
    service.record_followup_failure(item)
    clock.advance(30 / 1440)
    assert due_ids(service) == []  # second step is one hour
    clock.advance(30 / 1440)
    assert due_ids(service) == [oid]


def test_third_failed_attempt_gives_up_for_the_cycle_with_one_log_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    service, repo, clock = make(tmp_path)
    oid = sent_offer(repo)
    clock.advance(10)
    (item,) = service.followup_due()
    with caplog.at_level("WARNING"):
        for _ in range(3):
            service.record_followup_failure(item)
            clock.advance(0.3)
    assert due_ids(service) == []  # skipped for this cycle (snoozed)
    assert len([r for r in caplog.records if "giving up" in r.getMessage()]) == 1
    clock.advance(7)
    assert due_ids(service) == [oid]  # re-armed afterwards


def test_a_failing_item_does_not_back_off_the_others(tmp_path: Path) -> None:
    service, repo, clock = make(tmp_path)
    bad, good = sent_offer(repo, 1), sent_offer(repo, 2)
    clock.advance(10)
    bad_item = next(i for i in service.followup_due() if i.offer.id == bad)
    service.record_followup_failure(bad_item)
    assert due_ids(service) == [good]


def test_legacy_backlog_gets_no_reminders_after_the_upgrade(tmp_path: Path) -> None:
    clock = Clock()
    legacy = OfferRepository(sqlite3.connect(tmp_path / "app.db"), clock=clock.day)
    old = [sent_offer(legacy, n) for n in (1, 2, 3)]
    clock.advance(40)  # the upgrade happens 40 days after those sends
    service, repo, _ = make(tmp_path, clock=clock)
    assert due_ids(service) == []
    fresh = sent_offer(repo, 9)
    clock.advance(10)
    assert due_ids(service) == [fresh]
    assert all(o not in due_ids(service) for o in old)


def test_cutover_survives_a_restart(tmp_path: Path) -> None:
    clock = Clock()
    legacy = OfferRepository(sqlite3.connect(tmp_path / "app.db"), clock=clock.day)
    sent_offer(legacy, 1)
    clock.advance(40)
    make(tmp_path, clock=clock)
    clock.advance(30)
    service, _repo, _ = make(tmp_path, clock=clock)  # a later restart
    assert due_ids(service) == []


def test_pass_is_capped_and_leftovers_come_next_day(tmp_path: Path) -> None:
    service, repo, clock = make(tmp_path)
    ids = [sent_offer(repo, n) for n in range(1, 6)]
    clock.advance(10)
    tg = FakeBot()
    beat(service, tg)
    assert len(tg.sent) == 3
    beat(service, tg)
    assert len(tg.sent) == 3  # same day: gate closed
    clock.advance(1)
    beat(service, tg)
    assert len(tg.sent) == 5
    assert len({t for _c, t, _m in tg.sent}) == 5
    assert ids


def test_daily_gate_runs_once_per_day(tmp_path: Path) -> None:
    service, repo, clock = make(tmp_path)
    oid = sent_offer(repo)
    clock.advance(10)
    assert due_ids(service) == [oid]
    service.complete_followup_run()
    assert due_ids(service) == []
    clock.advance(1)
    assert due_ids(service) == [oid]


def test_state_survives_a_new_service_on_the_same_db(tmp_path: Path) -> None:
    service, repo, clock = make(tmp_path)
    oid = sent_offer(repo)
    clock.advance(10)
    (item,) = service.followup_due()
    service.mark_followup_notified(item)
    again, _repo2, _ = make(tmp_path, clock=clock)
    assert [i.offer.id for i in again.followup_due()] == []
    assert oid


# --- draft --------------------------------------------------------------------


def test_draft_uses_the_sent_snapshot_not_the_live_offer(tmp_path: Path) -> None:
    llm = Llm()
    service, repo, clock = make(tmp_path, llm)
    oid = sent_offer(repo)
    repo._conn.execute(
        "UPDATE offers SET source_data = 'EDITED LATER', carta_presentacion = 'NEW' "
        "WHERE id = ?",
        (oid,),
    )
    repo._conn.commit()
    clock.advance(10)
    draft = asyncio.run(service.followup_draft(oid, days_silent=10, number=1))
    assert draft.text == DRAFT and draft.warnings == []
    assert POSTING in llm.users[0] and LETTER in llm.users[0]
    assert "EDITED LATER" not in llm.users[0]
    assert "Spanish" in llm.users[0]


def test_draft_llm_failure_returns_no_text(tmp_path: Path) -> None:
    service, repo, _clock = make(tmp_path, Llm(LlmResponseError("boom")))
    oid = sent_offer(repo)
    draft = asyncio.run(service.followup_draft(oid, days_silent=10, number=1))
    assert draft.text is None


def test_draft_without_json_completion_returns_no_text(tmp_path: Path) -> None:
    class Bare:
        model = "x"

    service, repo, _clock = make(tmp_path, Bare())
    oid = sent_offer(repo)
    draft = asyncio.run(service.followup_draft(oid, days_silent=10, number=1))
    assert draft.text is None


# --- notifier -----------------------------------------------------------------


def test_followup_keyboards_payloads() -> None:
    data = [b.callback_data for r in followup_keyboard(7).inline_keyboard for b in r]
    assert data == [f"{FU}:7:ok", f"{FU}:7:skip", f"{FU}:7:none"]
    labels = [b.text for r in followup_keyboard(7).inline_keyboard for b in r]
    assert labels == ["✅ Enviado", "⏭️ Saltar", "🔕 Sin respuesta"]
    only = [
        b.callback_data
        for r in followup_proposal_keyboard(7).inline_keyboard
        for b in r
    ]
    assert only == [f"{FU}:7:none"]


def test_followup_message_carries_days_draft_and_warnings(tmp_path: Path) -> None:
    _service, repo, _clock = make(tmp_path)
    offer = repo.get_offer(sent_offer(repo))
    text = followup_message(
        offer, days_silent=12, number=2, draft=DRAFT, warnings=["x"]
    )
    assert "AI Engineer 1" in text and "Acme" in text
    assert "12 días" in text and "2" in text
    assert DRAFT in text and "x" in text
    no_draft = followup_message(
        offer, days_silent=12, number=1, draft=None, warnings=[]
    )
    assert "borrador" in no_draft.lower() and DRAFT not in no_draft
    proposal = followup_proposal_message(offer, days_silent=65)
    assert "65 días" in proposal and "sin respuesta" in proposal.lower()


def test_chunk_message_splits_on_lines_within_the_limit() -> None:
    text = "\n".join(f"línea {i} " + "x" * 50 for i in range(100))
    parts = chunk_message(text, limit=500)
    assert len(parts) > 1 and all(len(p) <= 500 for p in parts)
    assert "\n".join(parts) == text
    assert chunk_message("corto", limit=500) == ["corto"]
    huge = "y" * 1200
    assert all(len(p) <= 500 for p in chunk_message(huge, limit=500))
    assert "".join(chunk_message(huge, limit=500)) == huge


# --- heartbeat job ------------------------------------------------------------


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, object]] = []
        self.fail = False
        self.fail_when: Callable[[str, int], bool] | None = None
        self.calls = 0

    async def send_message(
        self, chat_id: int, text: str, reply_markup: object = None
    ) -> None:
        self.calls += 1
        if self.fail or (self.fail_when and self.fail_when(text, self.calls)):
            raise RuntimeError("telegram down")
        self.sent.append((chat_id, text, reply_markup))


def ctx(service: JobService, tg: FakeBot) -> SimpleNamespace:
    return SimpleNamespace(
        application=SimpleNamespace(bot_data={"service": service, "owner_id": 5}),
        bot=tg,
    )


def beat(service: JobService, tg: FakeBot) -> None:
    asyncio.run(heartbeat(ctx(service, tg)))


def buttons(markup: object) -> list[str]:
    return [b.callback_data for r in markup.inline_keyboard for b in r]  # type: ignore[attr-defined]


def test_heartbeat_sends_reminder_with_draft_and_buttons_once(tmp_path: Path) -> None:
    service, repo, clock = make(tmp_path)
    oid = sent_offer(repo)
    tg = FakeBot()
    beat(service, tg)
    assert tg.sent == []  # not due yet
    clock.advance(10)
    beat(service, tg)
    (chat, text, markup) = tg.sent[0]
    assert chat == 5 and DRAFT in text and "10 días" in text
    assert buttons(markup) == [f"{FU}:{oid}:ok", f"{FU}:{oid}:skip", f"{FU}:{oid}:none"]
    beat(service, tg)
    clock.advance(0.5)
    beat(service, tg)
    assert len(tg.sent) == 1  # once


def test_heartbeat_without_llm_draft_still_sends_the_reminder(tmp_path: Path) -> None:
    service, repo, clock = make(tmp_path, Llm(LlmResponseError("boom")))
    sent_offer(repo)
    clock.advance(10)
    tg = FakeBot()
    beat(service, tg)
    (_chat, text, markup) = tg.sent[0]
    assert "borrador" in text.lower() and markup is not None


def test_heartbeat_send_failure_is_not_marked_and_backs_off(tmp_path: Path) -> None:
    service, repo, clock = make(tmp_path)
    sent_offer(repo)
    clock.advance(10)
    tg = FakeBot()
    tg.fail = True
    beat(service, tg)  # must not raise
    tg.fail = False
    beat(service, tg)
    assert tg.sent == []  # backing off
    clock.advance(11 / 1440)
    beat(service, tg)
    assert len(tg.sent) == 1  # retried after the backoff, not lost


def test_heartbeat_sends_the_sixty_day_proposal_once(tmp_path: Path) -> None:
    service, repo, clock = make(tmp_path)
    oid = sent_offer(repo)
    service.record_followup_sent(oid)
    service.record_followup_sent(oid)
    clock.advance(60)
    tg = FakeBot()
    beat(service, tg)
    (_chat, text, markup) = tg.sent[0]
    assert "60 días" in text and buttons(markup) == [f"{FU}:{oid}:none"]
    clock.advance(1)
    beat(service, tg)
    assert len(tg.sent) == 1


def test_heartbeat_chunks_long_messages_keyboard_on_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cva import notifier

    monkeypatch.setattr(notifier, "_TELEGRAM_LIMIT", 200)
    service, repo, clock = make(tmp_path)
    sent_offer(repo)
    clock.advance(10)
    tg = FakeBot()
    beat(service, tg)
    assert len(tg.sent) > 1
    assert all(len(t) <= 200 for _c, t, _m in tg.sent)
    assert [m is None for _c, _t, m in tg.sent] == [True] * (len(tg.sent) - 1) + [False]


def test_poison_item_does_not_starve_the_batch(tmp_path: Path) -> None:
    service, repo, clock = make(tmp_path)
    bad, good = sent_offer(repo, 1), sent_offer(repo, 2)
    clock.advance(10)
    tg = FakeBot()
    tg.fail_when = lambda text, _n: f"#{bad}" in text
    beat(service, tg)
    assert len(tg.sent) == 1 and f"#{good}" in tg.sent[0][1]
    for _ in range(3):  # retries of the bad one; the good one is not resent
        clock.advance(0.3)
        beat(service, tg)
    assert len(tg.sent) == 1
    assert due_ids(service) == []  # gave up for the cycle


def test_failed_later_chunk_is_not_resent_with_the_earlier_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cva import notifier

    monkeypatch.setattr(notifier, "_TELEGRAM_LIMIT", 200)
    service, repo, clock = make(tmp_path)
    sent_offer(repo)
    clock.advance(10)
    tg = FakeBot()
    tg.fail_when = lambda _t, n: n == 2  # the 2nd chunk fails once
    beat(service, tg)
    first = [t for _c, t, _m in tg.sent]
    assert len(first) == 1
    clock.advance(11 / 1440)
    beat(service, tg)
    texts = [t for _c, t, _m in tg.sent]
    assert len(texts) == len(set(texts))  # no chunk delivered twice
    assert texts[0] == first[0]
    assert tg.sent[-1][2] is not None  # keyboard on the last chunk
    assert due_ids(service) == []


def test_failure_of_an_offer_no_longer_due_is_pruned_so_the_gate_closes(
    tmp_path: Path,
) -> None:
    service, repo, clock = make(tmp_path)
    oid = sent_offer(repo)
    clock.advance(10)
    tg = FakeBot()
    tg.fail = True
    beat(service, tg)  # fails: backing off, gate stays open
    repo.set_estado(oid, "screening")  # the offer left "enviada" meanwhile
    clock.advance(11 / 1440)
    tg.fail = False
    beat(service, tg)  # nothing due: the stale failure must not hold the gate
    assert tg.sent == []
    assert service._followup_state()["failures"] == {}
    assert service._followup_state()["last_run"] == clock.day()


def test_failure_of_a_snoozed_offer_is_pruned(tmp_path: Path) -> None:
    service, repo, clock = make(tmp_path)
    oid = sent_offer(repo)
    clock.advance(10)
    (item,) = service.followup_due()
    service.record_followup_failure(item)
    service.snooze_followup(oid)
    clock.advance(1)
    assert due_ids(service) == []
    assert service._followup_state()["failures"] == {}


def test_failure_whose_kind_changed_is_pruned(tmp_path: Path) -> None:
    service, repo, clock = make(tmp_path)
    oid = sent_offer(repo)
    clock.advance(10)
    (item,) = service.followup_due()
    service.record_followup_failure(item)  # "reminder:<id>"
    service.record_followup_sent(oid)
    service.record_followup_sent(oid)  # now only a 60-day proposal could apply
    clock.advance(1)
    assert due_ids(service) == []
    assert f"reminder:{oid}" not in service._followup_state()["failures"]


def test_failure_of_an_item_cut_by_the_cap_is_kept(tmp_path: Path) -> None:
    service, repo, clock = make(tmp_path)
    ids = [sent_offer(repo, n) for n in range(1, 6)]
    clock.advance(10)
    items = service.followup_due()
    assert len(items) == 3
    service.record_followup_failure(items[0])
    clock.advance(11 / 1440)
    service.followup_due()  # prune pass must not drop a still-due candidate
    assert f"reminder:{ids[0]}" in service._followup_state()["failures"]


def test_daily_cap_counts_deliveries_across_passes_of_the_same_day(
    tmp_path: Path,
) -> None:
    service, repo, clock = make(tmp_path)
    bad = sent_offer(repo, 1)
    for n in range(2, 6):
        sent_offer(repo, n)
    clock.advance(10)
    tg = FakeBot()
    first = {"on": True}
    tg.fail_when = lambda text, _n: first["on"] and f"#{bad}" in text
    beat(service, tg)  # bad fails, the next two are delivered
    assert len(tg.sent) == 2
    first["on"] = False
    clock.advance(11 / 1440)  # same calendar day, backoff elapsed
    beat(service, tg)
    assert len(tg.sent) == 3  # the retry fills the cap; nothing beyond it
    clock.advance(0.2)
    beat(service, tg)
    assert len(tg.sent) == 3
    clock.advance(1)
    beat(service, tg)
    assert len(tg.sent) == 5


def test_daily_delivery_count_survives_a_restart(tmp_path: Path) -> None:
    service, repo, clock = make(tmp_path)
    for n in range(1, 6):
        sent_offer(repo, n)
    clock.advance(10)
    for item in service.followup_due():
        service.mark_followup_notified(item)
    again, _repo, _ = make(tmp_path, clock=clock)
    assert again.followup_due() == []  # cap already used today
    clock.advance(1)
    assert len(again.followup_due()) == 2


def test_resume_uses_the_stored_chunks_even_if_the_chunk_size_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cva import notifier

    monkeypatch.setattr(notifier, "_TELEGRAM_LIMIT", 200)
    ref_service, ref_repo, ref_clock = make(_sub(tmp_path))
    sent_offer(ref_repo)
    ref_clock.advance(10)
    ref_tg = FakeBot()
    beat(ref_service, ref_tg)
    expected = [t for _c, t, _m in ref_tg.sent]  # the undisturbed delivery
    assert len(expected) > 2
    service, repo, clock = make(tmp_path)
    sent_offer(repo)
    clock.advance(10)
    tg = FakeBot()
    tg.fail_when = lambda _t, n: n == 2
    beat(service, tg)
    monkeypatch.setattr(notifier, "_TELEGRAM_LIMIT", 150)  # chunking drifted
    clock.advance(11 / 1440)
    beat(service, tg)
    assert [t for _c, t, _m in tg.sent] == expected
    assert tg.sent[-1][2] is not None


# --- callbacks ----------------------------------------------------------------


def press(service: JobService, data: str) -> FakeQuery:
    query = FakeQuery(data)
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    return query


def test_ok_button_records_followup_number(tmp_path: Path) -> None:
    service, repo, _clock = make(tmp_path)
    oid = sent_offer(repo)
    query = press(service, f"{FU}:{oid}:ok")
    assert [e.kind for e in repo.list_events(oid)] == ["followup"]
    assert query.edited_markups[-1] is None
    assert any("#1" in r or "1" in r for r in query.message.replies)
    press(service, f"{FU}:{oid}:ok")
    assert [e.kind for e in repo.list_events(oid)] == ["followup", "followup"]


def test_skip_button_snoozes(tmp_path: Path) -> None:
    service, repo, clock = make(tmp_path)
    oid = sent_offer(repo)
    clock.advance(10)
    (item,) = service.followup_due()
    service.mark_followup_notified(item)
    query = press(service, f"{FU}:{oid}:skip")
    assert query.edited_markups[-1] is None
    clock.advance(6)
    assert due_ids(service) == []
    clock.advance(1)
    assert due_ids(service) == [oid]


def test_none_button_records_sin_respuesta(tmp_path: Path) -> None:
    service, repo, _clock = make(tmp_path)
    oid = sent_offer(repo)
    press(service, f"{FU}:{oid}:none")
    assert repo.get_offer(oid).estado == "sin_respuesta"
    assert [e.estado for e in repo.list_events(oid)] == ["sin_respuesta"]


def test_bad_followup_callbacks_are_ignored_or_reported(tmp_path: Path) -> None:
    service, repo, _clock = make(tmp_path)
    oid = sent_offer(repo)
    press(service, f"{FU}:{oid}:bogus")
    press(service, f"{FU}:abc:ok")
    query = press(service, f"{FU}:999:ok")
    assert repo.list_events(oid) == []
    assert any("⚠️" in r for r in query.message.replies)


def test_state_is_valid_json_in_app_state(tmp_path: Path) -> None:
    service, repo, clock = make(tmp_path)
    sent_offer(repo)
    clock.advance(10)
    (item,) = service.followup_due()
    service.mark_followup_notified(item)
    raw = repo.get_state("followup_state")
    assert raw is not None and isinstance(json.loads(raw), dict)


def test_completing_an_empty_pass_does_not_write_to_the_store(tmp_path: Path) -> None:
    service, repo, _clock = make(tmp_path)
    service.complete_followup_run()
    assert repo.get_state("followup_state") is None


def test_stale_buttons_do_not_act_once_the_offer_left_enviada(tmp_path: Path) -> None:
    service, repo, _clock = make(tmp_path)
    oid = sent_offer(repo)
    service.record_outcome(oid, "screening")
    for action in ("ok", "skip", "none"):
        query = press(service, f"{FU}:{oid}:{action}")
        assert query.message.replies == [
            "Esta candidatura ya está en screening; no he cambiado nada."
        ]
    assert repo.get_offer(oid).estado == "screening"
    assert [e.kind for e in repo.list_events(oid)] == ["estado"]
