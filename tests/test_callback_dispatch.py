"""Callback dispatch table (onboarding-v2 T4, S8 refactor).

``bot.callback_router`` routes every inline button through one prefix ->
handler table instead of an if/elif chain. The payloads are a contract (old
cards stay in the chat for weeks), so this pins that every existing prefix
still routes to the same handler, that a payload a handler does not accept
still gets the "unknown action" reply, and that the cross-cutting
``forget_card`` side effect still runs for every prefix but ``cv`` and ``cl``.
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest

import bot
from cva.calibration_ui import CAL
from cva.fetch import FetchResponse
from cva.i18n import t
from cva.notifier import (
    BEACON_DISABLE,
    BEACON_RESUME,
    CL,
    CLC,
    CLD,
    FU,
    GM,
    OE,
    OUT,
    PREP,
    SAL,
)
from cva.star import ST
from tests.test_bot import (
    OFFER_URL,
    FakeContext,
    FakeFetcher,
    FakeQuery,
    FakeUpdate,
    make_service,
)

# prefix -> the handler that owned its branch of the old if/elif chain.
EXPECTED: dict[str, str] = {
    "an": "_cb_analyze",
    "cv": "_cb_cv",
    "lang": "_cb_toggle",
    "variant": "_cb_toggle",
    "discard": "_cb_discard",
    "disc": "_cb_disc",
    "sent": "_cb_sent",
    OUT: "_cb_outcome_menu",
    OE: "_cb_outcome",
    GM: "_cb_gmail",
    CAL: "_cb_calibration",
    FU: "_cb_followup",
    SAL: "_cb_salary",
    CL: "_cb_cover_letter",
    CLD: "_cb_dismiss_letter",
    CLC: "_cb_choose_letter",
    PREP: "_cb_prep",
    ST: "_cb_star",
    BEACON_RESUME: "_cb_beacon",
    BEACON_DISABLE: "_cb_beacon",
}


@pytest.mark.parametrize("prefix", sorted(EXPECTED))
def test_every_existing_prefix_routes_to_its_handler(prefix: str) -> None:
    assert bot.CALLBACK_HANDLERS[prefix] is getattr(bot, EXPECTED[prefix])


def test_the_table_holds_only_the_known_prefixes_and_onboarding() -> None:
    assert set(bot.CALLBACK_HANDLERS) == set(EXPECTED) | {"ob"}


def _service(tmp_path: Path) -> Any:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    return service


@pytest.mark.parametrize(
    "data",
    [
        "zz:1",  # unknown prefix
        "an",  # no id
        "lang:1:AI",  # wrong arity
        "oe:1:nope",  # unknown estado
        "gm:x:abc",  # unknown gmail action
        "fu:1:maybe",  # unknown follow-up action
        "clc:1:x:a",  # generation not a number
        "prep:1:nope",  # unknown stage
        f"{BEACON_RESUME}:1:2",  # wrong arity
    ],
)
def test_payloads_a_handler_refuses_get_the_unknown_action_reply(
    tmp_path: Path, data: str
) -> None:
    query = FakeQuery(data)
    asyncio.run(
        bot.callback_router(FakeUpdate(query=query), FakeContext(_service(tmp_path)))
    )
    assert query.answered
    assert query.message.replies == [t("bot.unknown_action", "es")]


@pytest.mark.parametrize(
    ("data", "forgets"),
    [("zz:1", True), ("disc:x", True), ("cv:x", False), (f"{CL}:x", False)],
)
def test_forget_card_runs_for_every_prefix_but_cv_and_cl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data: str, forgets: bool
) -> None:
    service = _service(tmp_path)
    forgotten: list[tuple[int, int]] = []
    monkeypatch.setattr(
        service, "forget_card", lambda chat, msg: forgotten.append((chat, msg))
    )
    query = FakeQuery(data)
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    assert forgotten == ([(5, 11)] if forgets else [])
