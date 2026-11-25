"""Gmail proposals on Telegram (M3): message, buttons, callbacks, heartbeat job."""

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import bot
from cva.calibration import SentJudgment
from cva.gmail.sync import CARD_KEY_PREFIX, Proposal
from cva.notifier import (
    GM,
    gmail_proposal_keyboard,
    gmail_proposal_message,
)
from cva.scheduler import GMAIL_KEY, heartbeat
from cva.service import JobService
from cva.tracker import OfferInput, OfferRepository
from tests.helpers import run_coro
from tests.test_bot import (
    FakeBot,
    FakeContext,
    FakeFetcher,
    FakeQuery,
    FakeUpdate,
    FetchResponse,
    make_service,
)

MID = "18c0ffee12345678"


def _setup(
    tmp_path: Path, *, estado: str = "rechazada"
) -> tuple[JobService, OfferRepository, int]:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", "https://x/1"))
    )
    repo = service._repo
    offer_id = repo.upsert_offer(
        OfferInput(
            job_id="1",
            title="AI Engineer",
            company="Acme",
            url="https://x/1",
            text="t",
            source="link",
        )
    )
    repo.mark_sent(
        offer_id,
        cv_path="cv.pdf",
        cambios="c",
        match=60,
        judgment=SentJudgment(score=60),
    )
    repo.add_gmail_proposal(
        message_id=MID,
        offer_id=offer_id,
        sender="Ana <ana@acme.com>",
        subject="Tu candidatura",
        received_at=datetime(2026, 11, 3, tzinfo=UTC).isoformat(),
        proposed_estado=estado,
    )
    return service, repo, offer_id


def _press(context: FakeContext, action: str, mid: str = MID) -> FakeQuery:
    query = FakeQuery(f"{GM}:{action}:{mid}")
    run_coro(bot.callback_router(FakeUpdate(query=query), context))
    return query


def test_message_is_spanish_and_names_company_subject_offer_and_estado() -> None:
    text = gmail_proposal_message(
        Proposal(MID, 12, "Acme", "Entrevista técnica", "entrevista_tecnica")
    )
    assert "📧" in text and "Acme" in text and "«Entrevista técnica»" in text
    assert "#12" in text and "entrevista técnica" in text
    assert "¿Marcar la oferta #12" in text


def test_long_subjects_are_truncated() -> None:
    text = gmail_proposal_message(Proposal(MID, 1, "Acme", "x" * 500, "oferta"))
    assert len(text) < 400


def test_keyboard_has_yes_other_ignore_within_telegram_limits() -> None:
    markup = gmail_proposal_keyboard(MID)
    buttons = [b for row in markup.inline_keyboard for b in row]
    assert [b.callback_data for b in buttons] == [
        f"{GM}:y:{MID}",
        f"{GM}:o:{MID}",
        f"{GM}:i:{MID}",
    ]
    assert [b.text for b in buttons] == ["Sí", "Otro estado", "Ignorar"]
    assert all(len(b.callback_data.encode()) <= 64 for b in buttons)


def test_yes_records_the_outcome_and_the_calibration_question_follows(
    tmp_path: Path,
) -> None:
    service, repo, offer_id = _setup(tmp_path, estado="rechazada")
    context = FakeContext(service)
    query = _press(context, "y")

    assert service.offer(offer_id).estado == "rechazada"
    kinds = [e.kind for e in service.outcome_events(offer_id)]
    assert kinds.count("estado") == 1
    row = repo.get_gmail_proposal(MID)
    assert row is not None and row.decision == "accepted"
    replies = " | ".join(query.message.replies)
    assert f"#{offer_id}" in replies
    # same path as /outcome: the rejection-reason question is asked
    assert any(
        m is not None
        and any(
            b.callback_data.startswith("cal:") for r in m.inline_keyboard for b in r
        )  # type: ignore[attr-defined]
        for m in query.message.markups
    )
    assert query.edited_markups[-1] is None  # buttons removed from the proposal


def test_yes_for_a_positive_outcome_asks_the_calibration_question(
    tmp_path: Path,
) -> None:
    service, _repo, offer_id = _setup(tmp_path, estado="entrevista_tecnica")
    query = _press(FakeContext(service), "y")
    assert service.offer(offer_id).estado == "entrevista_tecnica"
    assert any("calibrar" in r for r in query.message.replies)


def test_second_press_is_polite_and_changes_nothing(tmp_path: Path) -> None:
    service, _repo, offer_id = _setup(tmp_path, estado="screening")
    context = FakeContext(service)
    _press(context, "y")
    again = _press(context, "y")
    assert [e.kind for e in service.outcome_events(offer_id)].count("estado") == 1
    assert "ya" in again.message.replies[-1].lower()
    ignore = _press(context, "i")
    assert "ya" in ignore.message.replies[-1].lower()


def test_unknown_message_is_polite(tmp_path: Path) -> None:
    service, _repo, _offer_id = _setup(tmp_path)
    query = _press(FakeContext(service), "y", mid="doesnotexist")
    assert "ya no" in query.message.replies[-1].lower() or "no encuentro" in (
        query.message.replies[-1].lower()
    )


def test_stale_proposal_when_the_offer_moved_on(tmp_path: Path) -> None:
    service, repo, offer_id = _setup(tmp_path, estado="screening")
    service.record_outcome(offer_id, "entrevista_final")  # owner got ahead of Gmail
    query = _press(FakeContext(service), "y")
    assert service.offer(offer_id).estado == "entrevista_final"
    assert "entrevista_final" in query.message.replies[-1]
    row = repo.get_gmail_proposal(MID)
    assert row is not None and row.decision == "stale"


def test_other_estado_opens_the_outcome_menu(tmp_path: Path) -> None:
    service, repo, offer_id = _setup(tmp_path)
    query = _press(FakeContext(service), "o")
    assert service.offer(offer_id).estado == "enviada"
    markups = [m for m in query.message.markups if m is not None]
    datas = [b.callback_data for m in markups for r in m.inline_keyboard for b in r]  # type: ignore[attr-defined]
    assert f"oe:{offer_id}:screening" in datas
    row = repo.get_gmail_proposal(MID)
    assert row is not None and row.decision == "other"


def test_ignore_marks_the_proposal_ignored(tmp_path: Path) -> None:
    service, repo, offer_id = _setup(tmp_path)
    query = _press(FakeContext(service), "i")
    assert service.offer(offer_id).estado == "enviada"
    row = repo.get_gmail_proposal(MID)
    assert row is not None and row.decision == "ignored"
    assert query.edited_markups[-1] is None


# --- sibling proposals (owner 2026-11-25: "cual clicko?") --------------------------


class _EditingBot(FakeBot):
    def __init__(self) -> None:
        super().__init__()
        self.edits: list[tuple[int, int, str, object]] = []

    async def edit_message_text(
        self,
        text: str,
        chat_id: int | None = None,
        message_id: int | None = None,
        reply_markup: object = None,
    ) -> None:
        assert chat_id is not None and message_id is not None
        self.edits.append((chat_id, message_id, text, reply_markup))


def _add_pending(
    repo: OfferRepository, offer_id: int, mid: str, estado: str, card: int | None
) -> None:
    repo.add_gmail_proposal(
        message_id=mid,
        offer_id=offer_id,
        sender="Ana <ana@acme.com>",
        subject="Video interview",
        received_at=datetime(2026, 11, 4, tzinfo=UTC).isoformat(),
        proposed_estado=estado,
    )
    if card is not None:
        repo.set_state(CARD_KEY_PREFIX + mid, json.dumps({"chat": 5, "msg": card}))


def _siblings(tmp_path: Path) -> tuple[JobService, OfferRepository, int, int]:
    service, repo, offer_id = _setup(tmp_path, estado="screening")
    other = repo.upsert_offer(
        OfferInput(
            job_id="2",
            title="AI Engineer",
            company="Globex",
            url="https://x/2",
            text="t",
            source="link",
        )
    )
    _add_pending(repo, offer_id, "dup-same", "screening", card=101)
    _add_pending(repo, offer_id, "dup-later", "entrevista_tecnica", card=102)
    _add_pending(repo, offer_id, "dup-nocard", "screening", card=None)
    _add_pending(repo, other, "other-offer", "screening", card=103)
    return service, repo, offer_id, other


def _decision(repo: OfferRepository, mid: str) -> str | None:
    row = repo.get_gmail_proposal(mid)
    return None if row is None else row.decision


def test_yes_resolves_every_other_pending_card_of_the_offer(tmp_path: Path) -> None:
    service, repo, offer_id, _other = _siblings(tmp_path)
    tg = _EditingBot()
    _press(FakeContext(service, tg_bot=tg), "y")

    assert service.offer(offer_id).estado == "screening"
    assert _decision(repo, MID) == "accepted"
    for mid in ("dup-same", "dup-later", "dup-nocard"):
        assert _decision(repo, mid) == "stale"
    assert _decision(repo, "other-offer") == "pending"  # another offer: untouched
    expected = f"✅ Ya resuelta (#{offer_id} → screening)"
    assert sorted(tg.edits, key=lambda e: e[1]) == [
        (5, 101, expected, None),
        (5, 102, expected, None),
    ]
    assert repo.get_state(CARD_KEY_PREFIX + "dup-same") in (None, "")
    assert repo.get_state(CARD_KEY_PREFIX + "other-offer")  # kept


def test_ignore_and_other_also_resolve_the_sibling_cards(tmp_path: Path) -> None:
    for action in ("i", "o"):
        base = tmp_path / action
        base.mkdir()
        service, repo, offer_id, _other = _siblings(base)
        tg = _EditingBot()
        _press(FakeContext(service, tg_bot=tg), action)
        assert _decision(repo, "dup-same") == "stale"
        assert _decision(repo, "dup-later") == "stale"
        assert _decision(repo, "other-offer") == "pending"
        assert {e[2] for e in tg.edits} == {f"✅ Ya resuelta (#{offer_id})"}
        assert all(e[3] is None for e in tg.edits)


def test_sibling_cards_are_edited_in_english(tmp_path: Path) -> None:
    service, _repo, offer_id, _other = _siblings(tmp_path)
    tg = _EditingBot()
    context = FakeContext(service, tg_bot=tg)
    context.bot_data[bot._LANG_KEY] = "en"
    _press(context, "y")
    assert {e[2] for e in tg.edits} == {f"✅ Already handled (#{offer_id} → screening)"}


def test_a_failing_sibling_edit_never_undoes_the_answer(tmp_path: Path) -> None:
    service, repo, offer_id, _other = _siblings(tmp_path)

    class _Broken(_EditingBot):
        async def edit_message_text(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("telegram: message to edit not found")

    _press(FakeContext(service, tg_bot=_Broken()), "y")
    assert service.offer(offer_id).estado == "screening"
    assert _decision(repo, MID) == "accepted"
    assert _decision(repo, "dup-same") == "stale"


def test_a_lone_proposal_answer_edits_nothing_else(tmp_path: Path) -> None:
    service, _repo, offer_id = _setup(tmp_path, estado="screening")
    tg = _EditingBot()
    query = _press(FakeContext(service, tg_bot=tg), "y")
    assert tg.edits == []
    assert service.offer(offer_id).estado == "screening"
    assert query.edited_markups[-1] is None


# --- review follow-ups: approval must survive Telegram/outcome failures -------------


def _press_failing_edit(context: FakeContext, action: str) -> FakeQuery:
    query = FakeQuery(f"{GM}:{action}:{MID}")

    async def boom(reply_markup: object = None) -> None:
        raise RuntimeError("telegram: message is not modified")

    query.edit_message_reply_markup = boom  # type: ignore[method-assign]
    run_coro(bot.callback_router(FakeUpdate(query=query), context))
    return query


def test_yes_survives_a_failing_markup_edit(tmp_path: Path) -> None:
    service, repo, offer_id = _setup(tmp_path, estado="rechazada")
    _press_failing_edit(FakeContext(service), "y")
    assert service.offer(offer_id).estado == "rechazada"
    row = repo.get_gmail_proposal(MID)
    assert row is not None and row.decision == "accepted"


def test_other_and_ignore_survive_a_failing_markup_edit(tmp_path: Path) -> None:
    service, repo, _offer_id = _setup(tmp_path)
    query = _press_failing_edit(FakeContext(service), "o")
    row = repo.get_gmail_proposal(MID)
    assert row is not None and row.decision == "other"
    assert any(m is not None for m in query.message.markups)  # menu still shown
    service.resolve_gmail_proposal(MID, "pending")
    _press_failing_edit(FakeContext(service), "i")
    row = repo.get_gmail_proposal(MID)
    assert row is not None and row.decision == "ignored"


def test_failed_record_outcome_reopens_and_keeps_the_buttons_for_a_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cva.service import ServiceError

    service, repo, offer_id = _setup(tmp_path, estado="screening")
    context = FakeContext(service)
    real = service.record_outcome

    def failing(*args: object, **kwargs: object) -> None:
        raise ServiceError("db busy")

    monkeypatch.setattr(service, "record_outcome", failing)
    query = _press(context, "y")
    row = repo.get_gmail_proposal(MID)
    assert row is not None and row.decision == "pending"
    assert service.offer(offer_id).estado == "enviada"
    assert None not in query.edited_markups  # the buttons were NOT removed
    assert "db busy" in " ".join(query.message.replies)

    monkeypatch.setattr(service, "record_outcome", real)
    _press(context, "y")  # the same still-live button now works
    assert service.offer(offer_id).estado == "screening"
    row = repo.get_gmail_proposal(MID)
    assert row is not None and row.decision == "accepted"


def test_unexpected_record_outcome_error_also_reopens_the_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repo, _offer_id = _setup(tmp_path, estado="screening")

    def failing(*args: object, **kwargs: object) -> None:
        raise RuntimeError("disk")

    monkeypatch.setattr(service, "record_outcome", failing)
    with pytest.raises(RuntimeError):
        _press(FakeContext(service), "y")
    row = repo.get_gmail_proposal(MID)
    assert row is not None and row.decision == "pending"


def test_estado_landed_before_an_error_stays_accepted_and_clears_the_buttons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repo, offer_id = _setup(tmp_path, estado="screening")
    real = service.record_outcome

    def lands_then_fails(*args: object, **kwargs: object) -> None:
        real(*args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError("telegram follow-up failed")

    monkeypatch.setattr(service, "record_outcome", lands_then_fails)
    context = FakeContext(service)
    query = FakeQuery(f"{GM}:y:{MID}")
    with pytest.raises(RuntimeError, match="follow-up"):
        run_coro(bot.callback_router(FakeUpdate(query=query), context))
    assert service.offer(offer_id).estado == "screening"
    row = repo.get_gmail_proposal(MID)
    assert row is not None and row.decision == "accepted"
    assert None in query.edited_markups  # buttons cleared best-effort


def test_non_repository_error_on_reopen_does_not_mask_the_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repo, _offer_id = _setup(tmp_path, estado="screening")
    real_offer = service.offer
    calls = {"n": 0}

    def flaky_offer(offer_id: int) -> object:
        calls["n"] += 1
        if calls["n"] > 1:  # the first call is the stale check
            raise ValueError("db closed")
        return real_offer(offer_id)

    def failing(*args: object, **kwargs: object) -> None:
        raise RuntimeError("disk")

    monkeypatch.setattr(service, "offer", flaky_offer)
    monkeypatch.setattr(service, "record_outcome", failing)
    with pytest.raises(RuntimeError, match="disk"):
        _press(FakeContext(service), "y")
    row = repo.get_gmail_proposal(MID)
    assert row is not None and row.decision == "pending"


# --- heartbeat job ------------------------------------------------------------------


class _Tg:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, object]] = []

    async def send_message(
        self, chat_id: int, text: str, reply_markup: object = None
    ) -> None:
        self.sent.append((chat_id, text, reply_markup))


class _FakeSync:
    def __init__(self, due: bool = True, error: Exception | None = None) -> None:
        self._due = due
        self.error = error
        self.runs = 0

    def due(self) -> bool:
        return self._due

    async def run(self, send_proposal, send_warning):  # type: ignore[no-untyped-def]
        self.runs += 1
        if self.error is not None:
            raise self.error
        await send_proposal(Proposal(MID, 7, "Acme", "Hola", "screening"))
        await send_warning("warn")


def _beat(service: JobService, sync: object, tg: _Tg) -> None:
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"service": service, "owner_id": 5, GMAIL_KEY: sync}
        ),
        bot=tg,
    )
    run_coro(heartbeat(context))


def test_heartbeat_delivers_proposals_and_warnings_to_the_owner(
    tmp_path: Path,
) -> None:
    service, _repo, _id = _setup(tmp_path)
    tg, sync = _Tg(), _FakeSync()
    _beat(service, sync, tg)
    assert sync.runs == 1
    proposal, warning = tg.sent[-2], tg.sent[-1]
    assert proposal[0] == 5 and "Acme" in proposal[1] and proposal[2] is not None
    assert warning[1] == "warn"


def test_heartbeat_skips_the_sync_when_not_due(tmp_path: Path) -> None:
    service, _repo, _id = _setup(tmp_path)
    sync = _FakeSync(due=False)
    _beat(service, sync, _Tg())
    assert sync.runs == 0


def test_heartbeat_survives_a_crashing_sync(tmp_path: Path) -> None:
    service, _repo, _id = _setup(tmp_path)
    _beat(service, _FakeSync(error=RuntimeError("boom")), _Tg())  # must not raise


def test_heartbeat_without_gmail_configured_is_unchanged(tmp_path: Path) -> None:
    service, _repo, _id = _setup(tmp_path)
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={"service": service, "owner_id": 5}),
        bot=_Tg(),
    )
    run_coro(heartbeat(context))


# --- wiring ---------------------------------------------------------------------------


def test_gmail_sync_is_not_built_unless_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cva.config import Settings

    service, _repo, _id = _setup(tmp_path)
    monkeypatch.chdir(tmp_path)  # no config.toml / config.local.toml in sight
    monkeypatch.setenv("CVA_TELEGRAM_TOKEN", "t")
    off = Settings(_env_file=None)
    assert bot.build_gmail_sync(off, service) is None


def test_gmail_sync_is_built_when_enabled_without_touching_the_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cva.config import Settings

    service, _repo, _id = _setup(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CVA_TELEGRAM_TOKEN", "t")
    monkeypatch.setenv("CVA_LLM_API_KEY", "fake-key")
    on = Settings(
        gmail_enabled=True,
        gmail_credentials_path=str(tmp_path / "missing-c.json"),
        gmail_token_path=str(tmp_path / "missing-t.json"),
        gmail_poll_minutes=45,
        _env_file=None,
    )
    sync = bot.build_gmail_sync(on, service)
    assert sync is not None and sync.due() is True
    assert not (tmp_path / "missing-t.json").exists()
