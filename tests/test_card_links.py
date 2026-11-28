""" "🔎 Search elsewhere" row on analysis cards (URL buttons, no callbacks).

Every rule goes through the public builders the bot uses: ``cv_keyboard``
(with the offer row), the bot's analyze flow and toggles, and the service's
bridge-card notification.
"""

import asyncio
import dataclasses
from pathlib import Path
from typing import Any

import pytest
from telegram import InlineKeyboardMarkup

import bot
from cva.fetch import FetchResponse
from cva.ingest import sources
from cva.notifier import cv_keyboard
from cva.tracker import OfferInput, OfferRow
from tests.test_bot import FakeContext, FakeFetcher, FakeQuery, FakeUpdate, make_service

OFFER_ID = 7


def _offer(
    title: str | None = "ML Engineer",
    company: str | None = "Acme & Co",
    *,
    origen: str | None = "indeed",
    url: str | None = "https://es.indeed.com/viewjob?jk=0123456789abcdef",
) -> OfferRow:
    return OfferRow(
        id=OFFER_ID,
        posicion=title,
        empresa=company,
        origen=origen,
        url_oferta=url,
        estado="analizada",
        source_data="x",
    )


@pytest.fixture
def paid_portals(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flag portals ``search_elsewhere`` (the public build has no paid portal,
    so the tests flag registered ones through the public registry)."""
    for pid in ("linkedin", "infojobs", "indeed"):
        monkeypatch.setitem(
            sources.SOURCES,
            pid,
            dataclasses.replace(sources.SOURCES[pid], search_elsewhere=True),
        )


def _rows(markup: InlineKeyboardMarkup) -> list[list[dict[str, Any]]]:
    return [[button.to_dict() for button in row] for row in markup.inline_keyboard]


def _search_row(markup: InlineKeyboardMarkup) -> list[tuple[str, str | None]]:
    return [(b.text, b.url) for b in markup.inline_keyboard[-1]]


def test_existing_rows_are_unchanged_and_the_search_row_goes_last(
    paid_portals: None,
) -> None:
    plain = cv_keyboard(OFFER_ID)
    with_row = cv_keyboard(OFFER_ID, offer=_offer())
    assert _rows(with_row)[:-1] == _rows(plain)
    last = with_row.inline_keyboard[-1]
    assert all(b.url and b.callback_data is None for b in last)
    # Same with the no-CV variant of the card.
    plain_no_cv = cv_keyboard(OFFER_ID, cv_needed=False)
    assert _rows(cv_keyboard(OFFER_ID, cv_needed=False, offer=_offer()))[:-1] == _rows(
        plain_no_cv
    )


def test_unflagged_portals_and_pasted_offers_get_no_row() -> None:
    plain = cv_keyboard(OFFER_ID)
    offers = [
        _offer(),  # Indeed
        _offer(origen="linkedin", url="https://www.linkedin.com/jobs/view/1/"),
        _offer(origen="infojobs", url="https://www.infojobs.net/x/of-i123"),
        _offer(origen="paste", url=None),
        _offer(origen="link", url="https://unknown.example/job/1"),
        _offer(origen=None, url=None),
    ]
    for offer in offers:
        assert _rows(cv_keyboard(OFFER_ID, offer=offer)) == _rows(plain)
        no_cv = cv_keyboard(OFFER_ID, cv_needed=False, offer=offer)
        assert _rows(no_cv) == _rows(cv_keyboard(OFFER_ID, cv_needed=False))


def test_flagged_portal_offer_gets_the_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        sources.SOURCES,
        "indeed",
        dataclasses.replace(sources.SOURCES["indeed"], search_elsewhere=True),
    )
    plain = cv_keyboard(OFFER_ID, ui_lang="en")
    markup = cv_keyboard(OFFER_ID, offer=_offer(), ui_lang="en")
    assert _rows(markup)[:-1] == _rows(plain)
    assert [t for t, _ in _search_row(markup)] == [
        "🔎 On LinkedIn",
        "🔎 On Google",
        "🔎 On InfoJobs",
    ]
    # Resolved from the URL host when the stored origen is missing.
    by_url = _offer(origen=None)
    assert len(_search_row(cv_keyboard(OFFER_ID, offer=by_url))) == 3


def test_link_set_searches_title_and_company_url_encoded(paid_portals: None) -> None:
    row = _search_row(cv_keyboard(OFFER_ID, offer=_offer(), ui_lang="en"))
    assert row == [
        (
            "🔎 On LinkedIn",
            "https://www.linkedin.com/jobs/search/?keywords=ML+Engineer+Acme+%26+Co",
        ),
        (
            "🔎 On Google",
            (
                "https://www.google.com/search?q=%22ML+Engineer%22+%22Acme+%26+Co%22"
                "+%28careers+OR+jobs+OR+empleo%29"
            ),
        ),
        (
            "🔎 On InfoJobs",
            (
                "https://www.infojobs.net/jobsearch/search-results/list.xhtml"
                "?keyword=ML+Engineer+Acme+%26+Co"
            ),
        ),
    ]


def test_labels_follow_the_ui_language(paid_portals: None) -> None:
    labels = [
        t for t, _ in _search_row(cv_keyboard(OFFER_ID, offer=_offer(), ui_lang="es"))
    ]
    assert labels == ["🔎 En LinkedIn", "🔎 En Google", "🔎 En InfoJobs"]


def test_unknown_company_drops_the_company_term(paid_portals: None) -> None:
    for company in (None, "", "   "):
        row = _search_row(cv_keyboard(OFFER_ID, offer=_offer(company=company)))
        urls = [u for _, u in row]
        assert urls[0] == "https://www.linkedin.com/jobs/search/?keywords=ML+Engineer"
        assert urls[1] == (
            "https://www.google.com/search?q=%22ML+Engineer%22"
            "+%28careers+OR+jobs+OR+empleo%29"
        )
        assert urls[2].endswith("?keyword=ML+Engineer")


def test_company_only_offer_still_gets_the_row(paid_portals: None) -> None:
    urls = [u for _, u in _search_row(cv_keyboard(OFFER_ID, offer=_offer(title=None)))]
    assert urls[0] == "https://www.linkedin.com/jobs/search/?keywords=Acme+%26+Co"


def test_no_title_and_no_company_means_no_row() -> None:
    plain = cv_keyboard(OFFER_ID)
    for title, company in ((None, None), ("", " ")):
        markup = cv_keyboard(OFFER_ID, offer=_offer(title=title, company=company))
        assert _rows(markup) == _rows(plain)


def test_the_offers_own_portal_is_skipped(paid_portals: None) -> None:
    linkedin = _offer(origen="linkedin", url="https://www.linkedin.com/jobs/view/1/")
    labels = [
        t for t, _ in _search_row(cv_keyboard(OFFER_ID, offer=linkedin, ui_lang="en"))
    ]
    assert labels == ["🔎 On Google", "🔎 On InfoJobs"]
    # Detected from the URL when the stored origen is missing.
    by_url = _offer(origen=None, url="https://es.linkedin.com/jobs/view/1/")
    labels = [
        t for t, _ in _search_row(cv_keyboard(OFFER_ID, offer=by_url, ui_lang="en"))
    ]
    assert labels == ["🔎 On Google", "🔎 On InfoJobs"]
    # An InfoJobs offer gets the Spanish search on Indeed instead.
    infojobs = _offer(origen="infojobs", url="https://www.infojobs.net/x/of-i123")
    row = _search_row(cv_keyboard(OFFER_ID, offer=infojobs, ui_lang="en"))
    assert [t for t, _ in row] == ["🔎 On LinkedIn", "🔎 On Google", "🔎 On Indeed"]
    assert row[2][1] == "https://es.indeed.com/jobs?q=ML+Engineer+Acme+%26+Co"


def test_very_long_terms_are_capped(paid_portals: None) -> None:
    markup = cv_keyboard(OFFER_ID, offer=_offer(title="A" * 2000, company="B" * 2000))
    assert all(len(u or "") < 1024 for _, u in _search_row(markup))


# --- bot and service wiring ----------------------------------------------------


OFFER_URL = "https://www.linkedin.com/jobs/view/4459804598/"


def test_lang_toggle_keeps_the_search_row(tmp_path: Path, paid_portals: None) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    offer_id = service._repo.upsert_offer(
        OfferInput(
            job_id="4459804598",
            title="ML Engineer",
            company="Acme",
            url=OFFER_URL,
            text="bridge: Test",
            source="link",
        )
    )
    query = FakeQuery(f"lang:{offer_id}:AI:es")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    markup = query.edited_markups[-1]
    # origen "link" names no portal: the LinkedIn URL decides, so no LinkedIn.
    assert [b.text for b in markup.inline_keyboard[-1]] == [
        "🔎 En Google",
        "🔎 En InfoJobs",
    ]
    assert all(b.url for b in markup.inline_keyboard[-1])


def test_lang_toggle_for_a_missing_offer_still_edits_the_keyboard(
    tmp_path: Path,
) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    query = FakeQuery("lang:999:AI:es")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    markup = query.edited_markups[-1]
    assert all(b.callback_data for row in markup.inline_keyboard for b in row)


def test_analyze_card_carries_the_search_row(
    tmp_path: Path, paid_portals: None
) -> None:
    from tests.test_bot import JSONLD_BODY, FakeMessage

    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    )
    message = FakeMessage()
    asyncio.run(
        bot.analyze_command(
            FakeUpdate(message=message), FakeContext(service, args=[OFFER_URL])
        )
    )
    markup = message.markups[0]
    assert isinstance(markup, InlineKeyboardMarkup)
    last = markup.inline_keyboard[-1]
    # A LinkedIn offer: no LinkedIn search, Google + InfoJobs instead.
    assert [b.url.split("?")[0] for b in last if b.url] == [
        "https://www.google.com/search",
        "https://www.infojobs.net/jobsearch/search-results/list.xhtml",
    ]


def test_bridge_card_from_the_service_carries_the_search_row(
    tmp_path: Path, paid_portals: None
) -> None:
    from tests.test_auto_triage import (
        JSONLD_BODY,
        _bridged,
        _extraction,
        _RecordingNotify,
    )
    from tests.test_auto_triage import (
        FakeFetcher as TriageFetcher,
    )
    from tests.test_auto_triage import (
        FakeLlmClient as TriageLlm,
    )

    service, _repo, offer_id = _bridged(
        tmp_path,
        TriageLlm(_extraction()),
        TriageFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
    )
    notify = _RecordingNotify()
    asyncio.run(
        service.analyze_new_bridge_job(
            offer_id, url=OFFER_URL, title="ML Engineer", company="Acme", notify=notify
        )
    )
    _text, keyboard = notify.calls[0]
    assert keyboard.inline_keyboard[0][0].callback_data == f"cv:{offer_id}:AI:es"
    urls = [b.url or "" for b in keyboard.inline_keyboard[-1]]
    # A LinkedIn offer (title refreshed from the page): Google + InfoJobs.
    assert urls[0].startswith("https://www.google.com/search?q=%22")
    assert "%22Acme%22" in urls[0]
    assert urls[1].startswith("https://www.infojobs.net/jobsearch/")
