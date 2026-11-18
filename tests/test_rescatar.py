"""/rescatar <id>: re-send the full analysis card of a discarded offer."""

import asyncio
from pathlib import Path
from typing import Any

import pytest

import bot
from cva.commands import menu_commands
from cva.fetch import FetchResponse
from cva.notifier import SentMessage
from cva.service import RESCUED_NOTE, JobService, ServiceError
from cva.tracker import OfferRepository
from tests.test_also_on import IJ, _add, _dup_of
from tests.test_auto_triage import (
    JSONLD_BODY,
    OFFER_URL,
    FakeFetcher,
    FakeLlmClient,
    _bridged,
    _extraction,
)
from tests.test_bot import FakeContext, FakeMessage, FakeUpdate


class _Notify:
    """``notify(text, keyboard)`` double that reports where the card landed."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def __call__(self, text: str, keyboard: Any) -> SentMessage:
        self.calls.append((text, keyboard))
        return SentMessage(chat_id=9, message_id=100 + len(self.calls))


@pytest.fixture()
def analysed(tmp_path: Path) -> tuple[JobService, OfferRepository, int]:
    service, repo, offer_id = _bridged(
        tmp_path,
        FakeLlmClient(_extraction()),
        FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
    )
    asyncio.run(service.analyze(offer_id))
    return service, repo, offer_id


def test_rescue_sends_the_exact_auto_mode_card(
    analysed: tuple[JobService, OfferRepository, int],
) -> None:
    service, _repo, offer_id = analysed
    auto = _Notify()
    asyncio.run(
        service.analyze_new_bridge_job(
            offer_id, url=OFFER_URL, title="ML Engineer", company="Acme",
            notify=auto, min_score=0,
        )
    )  # fmt: skip
    rescued = _Notify()
    asyncio.run(service.rescue_offer(offer_id, rescued))
    assert len(rescued.calls) == 1
    assert rescued.calls[0][0] == auto.calls[0][0]
    assert rescued.calls[0][1].to_dict() == auto.calls[0][1].to_dict()


def test_rescue_keeps_score_and_estado_and_stores_the_card(
    analysed: tuple[JobService, OfferRepository, int],
) -> None:
    service, repo, offer_id = analysed
    before = (repo.get_offer(offer_id).estado, repo.latest_match(offer_id))
    notify = _Notify()
    asyncio.run(service.rescue_offer(offer_id, notify))
    assert (repo.get_offer(offer_id).estado, repo.latest_match(offer_id)) == before
    card = repo.get_card(offer_id)
    assert card is not None
    assert (card.chat_id, card.message_id, card.text) == (9, 101, notify.calls[0][0])
    assert card.keyboard  # "También en" edits need the keyboard back


def test_rescue_card_carries_the_also_on_line(
    analysed: tuple[JobService, OfferRepository, int],
) -> None:
    service, repo, offer_id = analysed
    _dup_of(repo, IJ, offer_id)
    notify = _Notify()
    asyncio.run(service.rescue_offer(offer_id, notify))
    assert "También en" in notify.calls[0][0]


def test_rescue_rejects_unknown_unanalysed_and_duplicate_offers(
    analysed: tuple[JobService, OfferRepository, int],
) -> None:
    service, repo, offer_id = analysed
    notify = _Notify()
    fresh = _add(repo, "https://www.linkedin.com/jobs/view/222")
    dup = _dup_of(repo, IJ, offer_id)
    for bad, text in ((9999, "No existe"), (fresh, "no está analizada")):
        with pytest.raises(ServiceError, match=text):
            asyncio.run(service.rescue_offer(bad, notify))
    with pytest.raises(ServiceError, match=f"duplicada de #{offer_id}"):
        asyncio.run(service.rescue_offer(dup, notify))
    assert notify.calls == []


def _run(service: JobService, *args: str) -> FakeMessage:
    message = FakeMessage()
    asyncio.run(
        bot.rescatar_command(
            FakeUpdate(message=message), FakeContext(service, args=list(args))
        )
    )
    return message


def test_command_sends_card_with_keyboard(
    analysed: tuple[JobService, OfferRepository, int],
) -> None:
    service, repo, offer_id = analysed
    message = _run(service, str(offer_id))
    assert len(message.replies) == 1 and message.markups[0] is not None
    card = repo.get_card(offer_id)
    assert card is not None and card.text == message.replies[0]
    assert card.message_id == message.sent[0].message_id


def test_command_replies_briefly_on_bad_input(
    analysed: tuple[JobService, OfferRepository, int],
) -> None:
    service, _repo, _offer_id = analysed
    assert _run(service).replies == ["Uso: /rescatar <id>"]
    assert _run(service, "abc").replies == ["Uso: /rescatar <id>"]
    reply = _run(service, "9999").replies
    assert len(reply) == 1 and reply[0].startswith("⚠️") and "9999" in reply[0]


def test_command_is_in_the_telegram_menu() -> None:
    assert "rescatar" in [name for name, _ in menu_commands()]


def test_rescue_with_unreadable_breakdown_is_a_service_error(
    analysed: tuple[JobService, OfferRepository, int],
) -> None:
    service, repo, offer_id = analysed
    repo._conn.execute(
        "UPDATE match_results SET breakdown_json = '{\"bogus\": 1}' WHERE offer_id = ?",
        (offer_id,),
    )
    repo._conn.commit()
    with pytest.raises(ServiceError):
        asyncio.run(service.rescue_offer(offer_id, _Notify()))


def test_rescue_records_the_rescue_and_works_for_a_walled_offer(
    analysed: tuple[JobService, OfferRepository, int],
) -> None:
    from cva.match import MatchResult
    from cva.match.gates import GateResult

    service, repo, offer_id = analysed
    walled = MatchResult(
        score=30, threshold=65, breakdown=[],
        gates=[GateResult(gate="location", verdict="FAIL", reason="Presencial fuera de Cataluña (Madrid)")],
    )  # fmt: skip
    repo.save_match_result(
        offer_id, score=30, threshold=65, breakdown_json=walled.model_dump_json()
    )
    notify = _Notify()
    asyncio.run(service.rescue_offer(offer_id, notify))
    assert len(notify.calls) == 1
    assert any(e.note == RESCUED_NOTE for e in repo.list_events(offer_id))
