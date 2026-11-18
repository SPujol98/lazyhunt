"""sent-without-cv: "✅ Marcar enviada" straight from the analysis card.

The owner sometimes applies without a generated CV (a base CV, or a portal that
needs none), so the analysis card offers the same ``sent:<id>`` button as the
post-generation keyboard, and marking an offer sent no longer requires a CV.
"""

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from openpyxl import load_workbook
from telegram import InlineKeyboardMarkup

import bot
from cva.claimsdb import load_claims
from cva.match.extraction import ExtractionCache
from cva.match.scorer import load_synonyms
from cva.notifier import (
    cv_keyboard,
    sent_keyboard,
    sent_message,
    sent_outcome_keyboard,
)
from cva.service import JobService
from cva.tracker import OfferInput, OfferRepository
from tests.demo import DEMO_CLAIMS, DEMO_MASTERS, DEMO_SYNONYMS
from tests.test_bot import (
    OFFER_URL,
    PASTE_TEXT,
    FakeContext,
    FakeFetcher,
    FakeLlmClient,
    FakeMessage,
    FakeQuery,
    FakeUpdate,
    FetchResponse,
)

TECNO_URL = "https://www.tecnoempleo.com/python-dev/rf-a1b2c3d4e5f6a1b2c3d4"
SENT_DAY = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
SENT_BUTTON = "✅ Marcar enviada"


class Clock:
    def __init__(self) -> None:
        self.now = SENT_DAY

    def day(self) -> str:
        return self.now.date().isoformat()


def _service(tmp_path: Path) -> tuple[JobService, OfferRepository, Clock]:
    """Real service over tmp SQLite with a mirror, a synced tracker and a clock."""
    clock = Clock()
    conn = sqlite3.connect(tmp_path / "app.db")
    repo = OfferRepository(conn, clock=clock.day)
    db = load_claims(DEMO_CLAIMS)
    service = JobService(
        repo=repo,
        llm=FakeLlmClient(),  # type: ignore[arg-type]
        fetcher=FakeFetcher(FetchResponse(200, "", OFFER_URL)),
        db=db,
        syn=load_synonyms(DEMO_SYNONYMS, db),
        cache=ExtractionCache(conn),
        threshold=65,
        masters_dir=DEMO_MASTERS,
        now_fn=lambda: clock.now,
        mirror_dir=tmp_path / "mirror",
        tracker_xlsx=tmp_path / "tracker.xlsx",
    )
    return service, repo, clock


def _pasted_offer(service: JobService) -> int:
    offer_id = asyncio.run(service.ingest(None, PASTE_TEXT))
    asyncio.run(service.analyze(offer_id))
    return offer_id


def _tecno_offer(service: JobService, repo: OfferRepository) -> int:
    offer_id = repo.upsert_offer(
        OfferInput(
            title="Dev",
            company="Acme",
            url=TECNO_URL,
            text=PASTE_TEXT,
            source="bridge-inline",
        )
    )
    asyncio.run(service.analyze(offer_id))
    return offer_id


def _buttons(markup: object) -> list[tuple[str, str]]:
    assert isinstance(markup, InlineKeyboardMarkup)
    return [
        (b.text, str(b.callback_data)) for row in markup.inline_keyboard for b in row
    ]


def _tracker_row(path: Path, offer_id: int) -> dict[str, object]:
    sheet = load_workbook(path).active
    assert sheet is not None
    rows = list(sheet.iter_rows(values_only=True))
    header = [str(h) for h in rows[0]]
    for row in rows[1:]:
        record = dict(zip(header, row, strict=True))
        if record["id"] == offer_id:
            return record
    raise AssertionError(f"offer {offer_id} not in the tracker")


def _mirror_files(tmp_path: Path) -> list[str]:
    root = tmp_path / "mirror"
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


# --- S1: the analysis card offers the button --------------------------------------


@pytest.mark.parametrize("cv_needed", [True, False])
def test_analysis_card_offers_mark_sent_next_to_discard(cv_needed: bool) -> None:
    rows = cv_keyboard(7, cv_needed=cv_needed).inline_keyboard
    last = [(b.text, b.callback_data) for b in rows[-1]]
    assert last == [(SENT_BUTTON, "sent:7"), ("🗑 Descartar", "discard:7")]
    # Same label and callback as the post-generation keyboard's button.
    assert (SENT_BUTTON, "sent:7") in _buttons(sent_keyboard(7))


def test_analyze_reply_card_carries_mark_sent(tmp_path: Path) -> None:
    service, repo, _clock = _service(tmp_path)
    for offer_id in (_pasted_offer(service), _tecno_offer(service, repo)):
        message = FakeMessage("/analyze")
        asyncio.run(
            bot._analyze_and_reply(service, message, FakeContext(service), offer_id)
        )
        assert (SENT_BUTTON, f"sent:{offer_id}") in _buttons(message.markups[-1])


# --- S2: pressing it on an offer without a CV -------------------------------------


@pytest.mark.parametrize("portal", ["pasted", "tecnoempleo"])
def test_sent_press_without_cv_marks_sent(tmp_path: Path, portal: str) -> None:
    service, repo, clock = _service(tmp_path)
    offer_id = (
        _pasted_offer(service) if portal == "pasted" else _tecno_offer(service, repo)
    )
    repo.set_cover_letter(offer_id, "Carta elegida")
    analysis = repo.latest_match(offer_id)
    assert analysis is not None
    query = FakeQuery(f"sent:{offer_id}")
    query.message.reply_markup = cv_keyboard(offer_id)

    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))

    assert query.message.replies == []  # no error reply
    assert query.edited == [sent_message(offer_id, analysis.score)]
    assert query.edited_markups == [sent_outcome_keyboard(offer_id)]
    offer = service.offer(offer_id)
    assert offer.estado == "enviada"
    assert offer.fecha_envio == SENT_DAY.date().isoformat()
    assert offer.match == analysis.score
    assert offer.cv_adaptado is None
    assert offer.cambios is None
    assert offer.cv_base is None
    assert offer.carta_presentacion == "Carta elegida"
    snapshot = repo.get_sent_snapshot(offer_id)
    assert snapshot is not None
    assert snapshot.cv_file is None
    assert snapshot.cover_letter == "Carta elegida"
    assert _mirror_files(tmp_path) == []  # no PDF to move
    row = _tracker_row(tmp_path / "tracker.xlsx", offer_id)
    assert row["estado"] == "enviada"
    assert row["cv_adaptado"] in (None, "")
    # The follow-up reminder is still scheduled from the send date.
    clock.now += timedelta(days=10)
    assert [item.offer.id for item in service.followup_due()] == [offer_id]


# --- S3: the flow with a generated CV is unchanged --------------------------------


def test_sent_press_with_cv_still_moves_the_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    service, repo, _clock = _service(tmp_path)
    offer_id = _pasted_offer(service)
    pdf_path = asyncio.run(service.generate_cv(offer_id, "AI"))
    rel = f"{pdf_path.parent.name}/{pdf_path.name}"
    assert _mirror_files(tmp_path) == [f"nuevos/{rel}"]
    query = FakeQuery(f"sent:{offer_id}")

    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))

    assert query.message.replies == []
    assert query.edited == [sent_message(offer_id, service.offer(offer_id).match or 0)]
    assert query.edited_markups == [sent_outcome_keyboard(offer_id)]
    assert _mirror_files(tmp_path) == [f"enviados/{rel}"]
    offer = service.offer(offer_id)
    assert offer.estado == "enviada"
    assert offer.cv_adaptado == str(pdf_path)
    snapshot = repo.get_sent_snapshot(offer_id)
    assert snapshot is not None and snapshot.cv_file == pdf_path.name
    row = _tracker_row(tmp_path / "tracker.xlsx", offer_id)
    assert row["cv_adaptado"] == str(pdf_path)


# --- S4: /sent <id> also works without a CV ---------------------------------------


def test_sent_command_without_cv(tmp_path: Path) -> None:
    service, repo, _clock = _service(tmp_path)
    offer_id = _pasted_offer(service)
    message = FakeMessage()

    asyncio.run(
        bot.sent_command(
            FakeUpdate(message=message), FakeContext(service, args=[str(offer_id)])
        )
    )

    assert message.replies == [
        sent_message(offer_id, service.offer(offer_id).match or 0)
    ]
    assert message.markups == [sent_outcome_keyboard(offer_id)]
    assert service.offer(offer_id).estado == "enviada"
    assert service.offer(offer_id).cv_adaptado is None
    assert repo.get_sent_snapshot(offer_id) is not None
