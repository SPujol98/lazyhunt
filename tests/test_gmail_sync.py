"""Gmail sync run (M3): checkpoint, dedupe, gate, auth warning, error handling."""

import asyncio
import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel

from cva.calibration import SentJudgment
from cva.gmail.client import GmailApiError, GmailAuthError, GmailMessage
from cva.gmail.sync import (
    AUTH_WARNED_KEY,
    CHECKPOINT_KEY,
    FAILURES_KEY,
    WALK_KEY,
    GmailSync,
    Proposal,
    should_propose,
)
from cva.tracker import OfferInput, OfferRepository

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
BODY_SECRET = "TOP-SECRET-BODY-TEXT-12345"


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


class FakeClient:
    def __init__(self, messages: list[GmailMessage] | None = None) -> None:
        self.messages = {m.message_id: m for m in messages or []}
        self.list_after: list[datetime] = []
        self.fetched: list[str] = []
        self.list_error: Exception | None = None
        self.get_error: Exception | None = None
        self.get_errors: dict[str, Exception] = {}
        self.list_cap: int | None = None

    def list_message_ids(
        self, after: datetime, before: datetime | None = None
    ) -> tuple[list[str], bool]:
        self.list_after.append(after)
        if self.list_error is not None:
            raise self.list_error
        ids = [
            m.message_id
            for m in sorted(
                self.messages.values(), key=lambda m: m.received_at, reverse=True
            )
            if m.received_at >= after and (before is None or m.received_at < before)
        ]
        if self.list_cap is not None and len(ids) > self.list_cap:
            return ids[: self.list_cap], True
        return ids, False

    def get_message(self, message_id: str) -> GmailMessage:
        self.fetched.append(message_id)
        if self.get_error is not None:
            raise self.get_error
        if message_id in self.get_errors:
            raise self.get_errors[message_id]
        return self.messages[message_id]


class FakeLlm:
    model = "fake"

    def __init__(self, reply: str | Exception = "none") -> None:
        self.reply = reply
        self.calls = 0

    async def json_completion[ModelT: BaseModel](
        self, system: str, user: str, schema: type[ModelT], *, model: str | None = None
    ) -> ModelT:
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return schema.model_validate({"estado": self.reply})


def _msg(
    message_id: str,
    *,
    sender: str = "Ana <ana@acme.com>",
    subject: str = "Entrevista con Acme",
    text: str = "Nos gustaría invitarte a una entrevista.",
    at: datetime = NOW - timedelta(hours=2),
) -> GmailMessage:
    return GmailMessage(message_id, sender, subject, at, text)


@pytest.fixture()
def repo(tmp_path: Path) -> OfferRepository:
    return OfferRepository(sqlite3.connect(tmp_path / "t.db"))


def _sent_offer(repo: OfferRepository, company: str = "Acme", n: int = 1) -> int:
    offer_id = repo.upsert_offer(
        OfferInput(
            job_id=str(n),
            title="AI Engineer",
            company=company,
            url=f"https://x/{n}",
            text="t",
            source="link",
        )
    )
    repo.mark_sent(
        offer_id,
        cv_path="cv.pdf",
        cambios="c",
        match=70,
        judgment=SentJudgment(score=70),
    )
    return offer_id


class Sink:
    def __init__(self) -> None:
        self.proposals: list[Proposal] = []
        self.warnings: list[str] = []
        self.fail_proposals = False
        self.fail_warnings = False

    async def proposal(self, proposal: Proposal) -> None:
        if self.fail_proposals:
            raise RuntimeError("telegram down")
        self.proposals.append(proposal)

    async def warning(self, text: str) -> None:
        if self.fail_warnings:
            raise RuntimeError("telegram down")
        self.warnings.append(text)


class AnsweringSink(Sink):
    """The owner answers every card at once, so no proposal stays pending
    (a pending one would hold back later mail for the same offer/estado)."""

    def __init__(self, repo: OfferRepository) -> None:
        super().__init__()
        self._repo = repo

    async def proposal(self, proposal: Proposal) -> None:
        await super().proposal(proposal)
        self._repo.set_gmail_decision(proposal.message_id, "ignored")


def _sync(
    repo: OfferRepository,
    client: FakeClient,
    llm: FakeLlm | None = None,
    clock: Clock | None = None,
    poll: int = 60,
) -> GmailSync:
    return GmailSync(
        repo=repo,
        client=client,
        llm=llm or FakeLlm(),
        poll_minutes=poll,
        now_fn=clock or Clock(),
    )


def _run(sync: GmailSync, sink: Sink) -> object:
    return asyncio.run(sync.run(sink.proposal, sink.warning))


# --- should_propose --------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "proposed", "expected"),
    [
        ("enviada", "screening", True),
        ("sin_respuesta", "screening", True),
        ("screening", "screening", False),
        ("entrevista_tecnica", "screening", False),
        ("screening", "entrevista_tecnica", True),
        ("entrevista_final", "oferta", True),
        ("oferta", "entrevista_final", False),
        ("enviada", "rechazada", True),
        ("oferta", "rechazada", True),
        ("rechazada", "rechazada", False),
        ("rechazada", "screening", False),
        ("aceptada", "rechazada", False),
        ("retirada", "screening", False),
    ],
)
def test_should_propose(current: str, proposed: str, expected: bool) -> None:
    assert should_propose(current, proposed) is expected


# --- run -------------------------------------------------------------------------


def test_first_run_looks_back_seven_days(repo: OfferRepository) -> None:
    client = FakeClient()
    _run(_sync(repo, client), Sink())
    assert client.list_after == [NOW - timedelta(days=7)]


def test_matched_message_is_proposed_and_stored_without_body(
    repo: OfferRepository,
) -> None:
    offer_id = _sent_offer(repo)
    client = FakeClient([_msg("m1", text=f"Entrevista. {BODY_SECRET}")])
    sink = Sink()
    result = _run(_sync(repo, client), sink)

    assert len(sink.proposals) == 1
    proposal = sink.proposals[0]
    assert (proposal.message_id, proposal.offer_id, proposal.estado) == (
        "m1",
        offer_id,
        "screening",
    )
    assert proposal.company == "Acme"
    row = repo.get_gmail_proposal("m1")
    assert row is not None and row.decision == "pending"
    assert row.sender == "Ana <ana@acme.com>" and row.subject == "Entrevista con Acme"
    dump = "\n".join(repo._conn.iterdump())
    assert BODY_SECRET not in dump
    assert getattr(result, "proposed", None) == 1


def test_unmatched_messages_are_ignored_silently(repo: OfferRepository) -> None:
    _sent_offer(repo, "Acme")
    client = FakeClient(
        [
            _msg(
                "m1",
                sender="x@other.org",
                subject="Newsletter",
                text="entrevista tecnica",
            )
        ]
    )
    sink = Sink()
    _run(_sync(repo, client), sink)
    assert sink.proposals == [] and sink.warnings == []
    assert repo.get_gmail_proposal("m1") is None


def test_never_proposes_the_same_message_twice(repo: OfferRepository) -> None:
    _sent_offer(repo)
    client = FakeClient([_msg("m1")])
    sink = Sink()
    _run(_sync(repo, client), sink)
    repo.set_state(CHECKPOINT_KEY, "")  # lose the checkpoint: the table still dedupes
    repo._conn.commit()
    _run(_sync(repo, client), sink)
    assert [p.message_id for p in sink.proposals] == ["m1"]


def test_skips_when_the_offer_already_has_that_estado_or_a_later_one(
    repo: OfferRepository,
) -> None:
    offer_id = _sent_offer(repo)
    repo.record_estado_event(offer_id, "entrevista_tecnica")
    client = FakeClient([_msg("m1")])  # would propose "screening"
    sink = Sink()
    _run(_sync(repo, client), sink)
    assert sink.proposals == []


def test_rejection_is_proposed_even_after_progress(repo: OfferRepository) -> None:
    offer_id = _sent_offer(repo)
    repo.record_estado_event(offer_id, "entrevista_final")
    client = FakeClient(
        [_msg("m1", subject="Tu candidatura", text="Lamentablemente no continuamos.")]
    )
    sink = Sink()
    _run(_sync(repo, client), sink)
    assert [p.estado for p in sink.proposals] == ["rechazada"]


def test_unclear_matched_message_uses_the_llm(repo: OfferRepository) -> None:
    _sent_offer(repo)
    llm = FakeLlm("entrevista_final")
    client = FakeClient([_msg("m1", subject="Re: perfil", text="Hola, un saludo.")])
    sink = Sink()
    _run(_sync(repo, client, llm), sink)
    assert llm.calls == 1
    assert [p.estado for p in sink.proposals] == ["entrevista_final"]


def test_llm_none_proposes_nothing(repo: OfferRepository) -> None:
    _sent_offer(repo)
    client = FakeClient([_msg("m1", subject="Re: perfil", text="Hola, un saludo.")])
    sink = Sink()
    _run(_sync(repo, client, FakeLlm("none")), sink)
    assert sink.proposals == []


def test_checkpoint_advances_and_seen_ids_are_not_refetched(
    repo: OfferRepository,
) -> None:
    _sent_offer(repo)
    at = NOW - timedelta(hours=3)
    client = FakeClient([_msg("m1", at=at)])
    clock = Clock()
    sync = _sync(repo, client, clock=clock)
    _run(sync, Sink())
    saved = json.loads(repo.get_state(CHECKPOINT_KEY) or "{}")
    assert saved["after"] == int(at.timestamp())

    clock.now += timedelta(hours=1)
    _run(sync, Sink())
    assert client.list_after[-1] == datetime.fromtimestamp(int(at.timestamp()), tz=UTC)
    assert client.fetched == ["m1"]  # second run listed it again but skipped it


def test_empty_run_still_moves_the_checkpoint_forward(repo: OfferRepository) -> None:
    client = FakeClient()
    clock = Clock()
    _run(_sync(repo, client, clock=clock), Sink())
    saved = json.loads(repo.get_state(CHECKPOINT_KEY) or "{}")
    assert saved["after"] > int((NOW - timedelta(days=7)).timestamp())
    assert saved["after"] <= int(NOW.timestamp())


def test_list_api_error_keeps_the_checkpoint_and_does_not_raise(
    repo: OfferRepository,
) -> None:
    client = FakeClient()
    client.list_error = GmailApiError("503")
    result = _run(_sync(repo, client), Sink())
    assert repo.get_state(CHECKPOINT_KEY) is None
    assert getattr(result, "status", None) == "api_error"


def test_get_api_error_keeps_the_checkpoint_and_retries_next_run(
    repo: OfferRepository,
) -> None:
    _sent_offer(repo)
    client = FakeClient([_msg("m1")])
    client.get_error = GmailApiError("boom")
    clock = Clock()
    sync = _sync(repo, client, clock=clock)
    sink = Sink()
    _run(sync, sink)
    assert repo.get_state(CHECKPOINT_KEY) is None and sink.proposals == []

    client.get_error = None
    clock.now += timedelta(minutes=61)
    _run(sync, sink)
    assert [p.message_id for p in sink.proposals] == ["m1"]


def test_unexpected_exceptions_never_escape(repo: OfferRepository) -> None:
    client = FakeClient()
    client.list_error = ValueError("weird")
    result = _run(_sync(repo, client), Sink())
    assert getattr(result, "status", None) == "api_error"


def test_send_failure_drops_the_row_and_retries_next_run(
    repo: OfferRepository,
) -> None:
    _sent_offer(repo)
    client = FakeClient([_msg("m1")])
    clock = Clock()
    sync = _sync(repo, client, clock=clock)
    sink = Sink()
    sink.fail_proposals = True
    result = _run(sync, sink)
    assert getattr(result, "status", None) == "send_error"
    assert repo.get_gmail_proposal("m1") is None
    assert repo.get_state(CHECKPOINT_KEY) is None

    sink.fail_proposals = False
    clock.now += timedelta(minutes=61)
    _run(sync, sink)
    assert [p.message_id for p in sink.proposals] == ["m1"]


# --- gate ------------------------------------------------------------------------


def test_gate_is_open_at_startup_then_waits_poll_minutes(
    repo: OfferRepository,
) -> None:
    clock = Clock()
    sync = _sync(repo, FakeClient(), clock=clock, poll=60)
    assert sync.due() is True
    _run(sync, Sink())
    assert sync.due() is False
    clock.now += timedelta(minutes=59)
    assert sync.due() is False
    clock.now += timedelta(minutes=2)
    assert sync.due() is True


def test_gate_also_waits_after_a_failed_run(repo: OfferRepository) -> None:
    client = FakeClient()
    client.list_error = GmailApiError("x")
    clock = Clock()
    sync = _sync(repo, client, clock=clock)
    _run(sync, Sink())
    assert sync.due() is False


# --- auth failure ----------------------------------------------------------------


def test_auth_failure_warns_once_with_the_exact_command(repo: OfferRepository) -> None:
    client = FakeClient()
    client.list_error = GmailAuthError("invalid_grant")
    clock = Clock()
    sync = _sync(repo, client, clock=clock)
    sink = Sink()
    result = _run(sync, sink)
    assert getattr(result, "status", None) == "auth_error"
    assert len(sink.warnings) == 1
    assert "uv run cli.py gmail-auth" in sink.warnings[0]

    for _ in range(3):
        clock.now += timedelta(minutes=61)
        _run(sync, sink)
    assert len(sink.warnings) == 1  # not repeated every hour


def test_auth_warning_is_not_repeated_after_a_restart(repo: OfferRepository) -> None:
    client = FakeClient()
    client.list_error = GmailAuthError("revoked")
    sink = Sink()
    _run(_sync(repo, client), sink)
    _run(_sync(repo, client), sink)  # a fresh instance, same database
    assert len(sink.warnings) == 1


def test_warning_resets_after_a_successful_run(repo: OfferRepository) -> None:
    client = FakeClient()
    client.list_error = GmailAuthError("revoked")
    clock = Clock()
    sync = _sync(repo, client, clock=clock)
    sink = Sink()
    _run(sync, sink)
    client.list_error = None
    clock.now += timedelta(minutes=61)
    _run(sync, sink)
    assert repo.get_state(AUTH_WARNED_KEY) in (None, "")
    client.list_error = GmailAuthError("revoked again")
    clock.now += timedelta(minutes=61)
    _run(sync, sink)
    assert len(sink.warnings) == 2


def test_failed_warning_send_is_retried_next_run(repo: OfferRepository) -> None:
    client = FakeClient()
    client.list_error = GmailAuthError("revoked")
    clock = Clock()
    sync = _sync(repo, client, clock=clock)
    sink = Sink()
    sink.fail_warnings = True
    _run(sync, sink)
    sink.fail_warnings = False
    clock.now += timedelta(minutes=61)
    _run(sync, sink)
    assert len(sink.warnings) == 1


# --- privacy of logs --------------------------------------------------------------


def test_logs_never_contain_message_bodies(
    repo: OfferRepository, caplog: pytest.LogCaptureFixture
) -> None:
    _sent_offer(repo)

    class BoomLlm(FakeLlm):
        async def json_completion[ModelT: BaseModel](
            self,
            system: str,
            user: str,
            schema: type[ModelT],
            *,
            model: str | None = None,
        ) -> ModelT:
            raise RuntimeError(f"echo {user}")

    client = FakeClient([_msg("m1", subject="Re: x", text=f"Hola {BODY_SECRET}")])
    with caplog.at_level(logging.DEBUG):
        _run(_sync(repo, client, BoomLlm()), Sink())
    assert BODY_SECRET not in caplog.text


# --- review follow-ups: truncation, LLM outage, poison messages ------------------------


def _checkpoint(repo: OfferRepository) -> dict[str, object]:
    return json.loads(repo.get_state(CHECKPOINT_KEY) or "{}")  # type: ignore[no-any-return]


def _truncated_setup(
    repo: OfferRepository, count: int = 5, cap: int = 2
) -> tuple[FakeClient, list[GmailMessage]]:
    _sent_offer(repo)
    messages = [
        _msg(f"m{i}", at=NOW - timedelta(hours=10 - i), subject="Entrevista Acme")
        for i in range(count)
    ]
    client = FakeClient(messages)
    client.list_cap = cap
    return client, messages


def test_truncated_window_is_walked_back_across_runs_until_complete(
    repo: OfferRepository,
) -> None:
    client, messages = _truncated_setup(repo)
    clock = Clock()
    sync = _sync(repo, client, clock=clock)
    sink = AnsweringSink(repo)
    for expected_checkpoint in (False, False, False, True):  # 1-message overlap
        assert getattr(_run(sync, sink), "status", None) == "ok"
        assert (repo.get_state(CHECKPOINT_KEY) is not None) is expected_checkpoint
        clock.now += timedelta(minutes=61)
    assert {p.message_id for p in sink.proposals} == {m.message_id for m in messages}
    assert _checkpoint(repo)["after"] == int(messages[-1].received_at.timestamp())
    # the owner heard about the long window exactly once
    assert len(sink.warnings) == 1


def test_incomplete_window_with_a_stopped_run_never_moves_the_checkpoint(
    repo: OfferRepository,
) -> None:
    client, _messages = _truncated_setup(repo, count=4, cap=2)
    client.get_errors["m3"] = GmailApiError("boom")  # newest of the listed two
    sink = Sink()
    result = _run(_sync(repo, client), sink)
    assert getattr(result, "status", None) == "api_error"
    assert [p.message_id for p in sink.proposals] == ["m2"]  # progress was made
    assert repo.get_state(CHECKPOINT_KEY) is None  # older mail may be unseen


def test_failing_slice_boundary_is_counted_and_given_up_after_three_runs(
    repo: OfferRepository,
) -> None:
    client, _messages = _truncated_setup(repo, count=4, cap=2)
    client.get_errors["m2"] = GmailApiError("boom")  # oldest listed = boundary
    clock = Clock()
    sync = _sync(repo, client, clock=clock)
    sink = AnsweringSink(repo)
    statuses = []
    for _ in range(5):
        statuses.append(getattr(_run(sync, sink), "status", None))
        clock.now += timedelta(minutes=61)
    assert statuses[:2] == ["api_error", "api_error"]
    assert statuses[2:] == ["ok", "ok", "ok"]
    fetched = client.fetched.count("m2")
    _run(sync, sink)
    assert client.fetched.count("m2") == fetched  # given up: not fetched again
    assert {"m0", "m1", "m3"} <= {p.message_id for p in sink.proposals}


def _unclear(message_id: str, at: datetime) -> GmailMessage:
    return _msg(message_id, subject="Re: tu perfil", text="Un par de cosas.", at=at)


def test_llm_outage_holds_the_checkpoint_and_retries_the_message(
    repo: OfferRepository,
) -> None:
    _sent_offer(repo, "Acme")
    old = _msg("old", subject="Entrevista Acme", at=NOW - timedelta(hours=5))
    unclear = _msg(
        "unclear",
        subject="Acme: tu perfil",
        text="Un par de cosas.",
        at=NOW - timedelta(hours=2),
    )
    client = FakeClient([old, unclear])
    llm = FakeLlm(RuntimeError("llm down"))
    clock = Clock()
    sync = _sync(repo, client, llm, clock)
    sink = AnsweringSink(repo)
    result = _run(sync, sink)
    assert getattr(result, "status", None) == "api_error"
    assert [p.message_id for p in sink.proposals] == ["old"]
    # progress up to the processed older message only
    assert _checkpoint(repo)["after"] == int(old.received_at.timestamp())

    llm.reply = "screening"
    clock.now += timedelta(minutes=61)
    result = _run(sync, sink)
    assert getattr(result, "status", None) == "ok"
    assert [p.message_id for p in sink.proposals] == ["old", "unclear"]


def test_llm_outage_is_not_counted_against_the_message_and_notifies_once(
    repo: OfferRepository,
) -> None:
    _sent_offer(repo, "Acme")
    unclear = _msg(
        "unclear",
        subject="Acme: tu perfil",
        text="Un par de cosas.",
        at=NOW - timedelta(hours=2),
    )
    client = FakeClient([unclear])
    llm = FakeLlm(RuntimeError("llm down"))
    clock = Clock()
    sync = _sync(repo, client, llm, clock)
    sink = Sink()
    for run in range(1, 6):
        assert getattr(_run(sync, sink), "status", None) == "api_error"
        assert len(sink.warnings) == (1 if run >= 3 else 0)  # once, after 3 runs
        clock.now += timedelta(minutes=61)
    assert repo.get_state(FAILURES_KEY) in (None, "")  # never counted
    llm.reply = "screening"  # recovery: the message was never dropped
    assert getattr(_run(sync, sink), "status", None) == "ok"
    assert [p.message_id for p in sink.proposals] == ["unclear"]


def test_a_permanently_failing_message_does_not_block_newer_ones(
    repo: OfferRepository,
) -> None:
    _sent_offer(repo)
    bad = _msg("bad", at=NOW - timedelta(hours=5))
    good = _msg("good", at=NOW - timedelta(hours=1))
    client = FakeClient([bad, good])
    client.get_errors["bad"] = GmailApiError("unparseable")
    clock = Clock()
    sync = _sync(repo, client, clock=clock)
    sink = Sink()
    for expected in ("api_error", "api_error", "ok"):
        assert getattr(_run(sync, sink), "status", None) == expected
        clock.now += timedelta(minutes=61)
    assert [p.message_id for p in sink.proposals] == ["good"]
    # once skipped it is not fetched again
    fetched_before = client.fetched.count("bad")
    _run(sync, sink)
    assert client.fetched.count("bad") == fetched_before


def test_auth_error_on_a_message_is_not_counted_as_a_failure(
    repo: OfferRepository,
) -> None:
    _sent_offer(repo)
    client = FakeClient([_msg("m1")])
    client.get_errors["m1"] = GmailAuthError("revoked")
    clock = Clock()
    sync = _sync(repo, client, clock=clock)
    for _ in range(4):
        assert getattr(_run(sync, Sink()), "status", None) == "auth_error"
        clock.now += timedelta(minutes=61)


def test_walk_ending_with_no_new_progress_keeps_the_walk_newest_stamp(
    repo: OfferRepository,
) -> None:
    """A walk whose final complete listing only repeats handled ids must
    checkpoint at the walk's own newest stamp, never jump to now - margin."""
    client, messages = _truncated_setup(repo, count=3, cap=10)
    clock = Clock()
    sync = _sync(repo, client, clock=clock)
    sink = Sink()
    _run(sync, sink)  # complete listing: every message proposed
    walk_newest = int(messages[-1].received_at.timestamp())
    repo.set_state(
        WALK_KEY,
        json.dumps({"before": walk_newest + 1, "newest": walk_newest, "ids": []}),
    )
    clock.now += timedelta(days=2)
    assert getattr(_run(sync, sink), "status", None) == "ok"
    assert _checkpoint(repo)["after"] == walk_newest


# --- duplicate proposals (owner 2026-11-25) ----------------------------------------


def test_a_pending_proposal_blocks_another_for_the_same_offer_and_estado(
    repo: OfferRepository,
) -> None:
    _sent_offer(repo)
    client = FakeClient(
        [
            _msg("m1", subject="Schedule your interview", at=NOW - timedelta(hours=3)),
            _msg("m2", subject="Video interview", at=NOW - timedelta(hours=2)),
        ]
    )
    sink = Sink()
    result = _run(_sync(repo, client), sink)

    assert [p.message_id for p in sink.proposals] == ["m1"]
    assert getattr(result, "proposed", None) == 1
    first, extra = repo.get_gmail_proposal("m1"), repo.get_gmail_proposal("m2")
    assert first is not None and first.decision == "pending"
    # the extra email is kept as evidence (never proposed, never re-read)
    assert extra is not None and extra.decision == "stale"
    assert extra.subject == "Video interview"


def test_an_answered_proposal_no_longer_blocks_a_new_one(
    repo: OfferRepository,
) -> None:
    _sent_offer(repo)
    client = FakeClient([_msg("m1", at=NOW - timedelta(hours=3))])
    clock = Clock()
    sync = _sync(repo, client, clock=clock)
    sink = Sink()
    _run(sync, sink)
    repo.set_gmail_decision("m1", "ignored")
    client.messages["m2"] = _msg("m2", at=NOW - timedelta(minutes=5))
    clock.now += timedelta(minutes=61)
    _run(sync, sink)
    assert [p.message_id for p in sink.proposals] == ["m1", "m2"]


def test_a_different_estado_for_the_same_offer_is_still_proposed(
    repo: OfferRepository,
) -> None:
    _sent_offer(repo)
    client = FakeClient(
        [
            _msg("m1", at=NOW - timedelta(hours=3)),
            _msg(
                "m2",
                subject="Lamentablemente",
                text="Lamentablemente hemos decidido seguir con otros candidatos.",
                at=NOW - timedelta(hours=2),
            ),
        ]
    )
    sink = Sink()
    _run(_sync(repo, client, FakeLlm("rechazada")), sink)
    assert [(p.message_id, p.estado) for p in sink.proposals] == [
        ("m1", "screening"),
        ("m2", "rechazada"),
    ]


def test_a_timed_out_send_keeps_the_proposal_and_never_resends_it(
    repo: OfferRepository,
) -> None:
    from telegram.error import TimedOut

    _sent_offer(repo)
    client = FakeClient([_msg("m1")])
    clock = Clock()
    sync = _sync(repo, client, clock=clock)
    calls: list[str] = []

    async def timed_out(proposal: Proposal) -> None:
        calls.append(proposal.message_id)
        raise TimedOut()  # Telegram may well have delivered it

    async def warn(text: str) -> None:
        return None

    result = asyncio.run(sync.run(timed_out, warn))
    assert getattr(result, "status", None) == "ok"
    row = repo.get_gmail_proposal("m1")
    assert row is not None and row.decision == "pending"
    repo.set_state(CHECKPOINT_KEY, "")
    clock.now += timedelta(minutes=61)
    asyncio.run(sync.run(timed_out, warn))
    assert calls == ["m1"]


def test_the_delivered_card_is_remembered_for_later_edits(
    repo: OfferRepository,
) -> None:
    from cva.gmail.sync import card_ref

    _sent_offer(repo)
    client = FakeClient([_msg("m1")])

    async def send(proposal: Proposal) -> object:
        return type("Msg", (), {"chat_id": 5, "message_id": 77})()

    async def warn(text: str) -> None:
        return None

    asyncio.run(_sync(repo, client).run(send, warn))
    assert card_ref(repo, "m1") == (5, 77)
    assert card_ref(repo, "unknown") is None
