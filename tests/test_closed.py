"""Closed-offer detector (pure) plus its service, bridge and bot wiring."""

import asyncio
from pathlib import Path

import pytest

from cva.fetch import FetchResponse
from cva.ingest.closed import closed_marker
from cva.notifier import ClosedOffer, round_summary_message
from tests.test_service import (
    OFFER_URL,
    FakeFetcher,
    FakeLlmClient,
    _extraction,
    make_service,
)


@pytest.mark.parametrize(
    "text",
    [
        "No longer accepting applications",
        "Senior AI Engineer\nThis job is no longer available.",
        "Sorry, the position is no longer open",
        "Applications are closed for this role.",
        "The application period has closed",
        "This posting has expired",
        "Esta oferta ya no acepta solicitudes",
        "Ya no aceptamos candidaturas para este puesto",
        "Oferta cerrada",
        "OFERTA CADUCADA el 12/10",
        "Oferta finalizada",
        "Proceso cerrado",
        "Proceso de selección finalizado",
        "Inscripción cerrada",
        "Esta oferta ya no está disponible",
        "Aquesta oferta ja no accepta sol·licituds",
        "Oferta tancada",
        "Inscripció tancada",
    ],
)
def test_closed_markers_detected(text: str) -> None:
    assert closed_marker(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "We use a closed-loop control system.",
        "We closed a funding round of 20M.",
        "Cerrada la ronda de inversión, seguimos creciendo.",
        "We value close collaboration with product.",
        "Applications are reviewed weekly; apply now.",
        "Estamos cerrando el trimestre con buenos resultados.",
        "Offering a closed beta of our platform.",
        "Oferta de empleo para AI Engineer. Aplica ya.",
        "The role is no longer a pure research position and now ships product.",
        "Experience with applications closed under review is a plus.",
        "Cerramos acuerdos con clientes enterprise.",
        "Procesos cerrados de calidad ISO en la planta",
        "Tancada la ronda de finançament",
        "",
        "AI Engineer en Acme. Imprescindible Python.",
    ],
)
def test_closed_markers_have_no_false_positives(text: str) -> None:
    assert closed_marker(text) is None


def test_marker_returns_the_matched_phrase() -> None:
    assert closed_marker("Foo. Oferta cerrada. Bar") == "oferta cerrada"


CLOSED_TEXT = "AI Engineer en Acme. Imprescindible Python. Oferta cerrada."


def _svc(tmp_path: Path) -> tuple[object, FakeLlmClient, object]:
    llm = FakeLlmClient(_extraction())
    service, repo = make_service(
        tmp_path, llm, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    return service, llm, repo


def test_analyze_skips_closed_offer_without_llm_spend(tmp_path: Path) -> None:
    from cva.service import OfferClosedError

    service, llm, repo = _svc(tmp_path)
    offer_id = asyncio.run(service.ingest(None, CLOSED_TEXT))  # type: ignore[attr-defined]
    with pytest.raises(OfferClosedError):
        asyncio.run(service.analyze(offer_id))  # type: ignore[attr-defined]
    assert llm.calls == 0
    row = repo.get_offer(offer_id)  # type: ignore[attr-defined]
    assert row.estado == "descartada"
    assert "oferta cerrada" in (row.notas or "")


def test_closed_note_is_idempotent_and_keeps_existing_notes(tmp_path: Path) -> None:
    from cva.service import OfferClosedError

    service, _llm, repo = _svc(tmp_path)
    offer_id = asyncio.run(service.ingest(None, CLOSED_TEXT))  # type: ignore[attr-defined]
    repo.append_note(offer_id, "nota previa")  # type: ignore[attr-defined]
    for _ in range(2):
        with pytest.raises(OfferClosedError):
            asyncio.run(service.analyze(offer_id))  # type: ignore[attr-defined]
    notas = repo.get_offer(offer_id).notas or ""  # type: ignore[attr-defined]
    assert notas.count("oferta cerrada") == 1 and "nota previa" in notas


def test_sent_offer_is_never_discarded_as_closed(tmp_path: Path) -> None:
    service, llm, repo = _svc(tmp_path)
    offer_id = asyncio.run(service.ingest(None, CLOSED_TEXT))  # type: ignore[attr-defined]
    repo.set_estado(offer_id, "enviada")  # type: ignore[attr-defined]
    asyncio.run(service.analyze(offer_id))  # type: ignore[attr-defined]
    assert llm.calls == 1  # analyzed normally, no OfferClosedError
    assert "oferta cerrada" not in (repo.get_offer(offer_id).notas or "")  # type: ignore[attr-defined]


def test_open_offer_still_analyzed(tmp_path: Path) -> None:
    service, llm, _repo = _svc(tmp_path)
    offer_id = asyncio.run(service.ingest(None, "AI Engineer. Python."))  # type: ignore[attr-defined]
    asyncio.run(service.analyze(offer_id))  # type: ignore[attr-defined]
    assert llm.calls == 1


def test_bridge_inline_closed_offer_takes_closed_zone_with_no_card(
    tmp_path: Path,
) -> None:
    service, llm, repo = _svc(tmp_path)
    offer_id = repo.upsert_offer(  # type: ignore[attr-defined]
        __import__("cva.tracker", fromlist=["OfferInput"]).OfferInput(
            title="AI Engineer",
            company="Acme",
            url=OFFER_URL,
            text=CLOSED_TEXT,
            source="bridge-inline",
        )
    )
    cards: list[object] = []

    async def notify(text: str, keyboard: object) -> None:
        cards.append(text)

    outcome = asyncio.run(
        service.analyze_new_bridge_job(  # type: ignore[attr-defined]
            offer_id,
            url=OFFER_URL,
            title="AI Engineer",
            company="Acme",
            notify=notify,
            text=CLOSED_TEXT,
        )
    )
    assert outcome.zone == "closed"
    assert cards == [] and llm.calls == 0
    assert repo.get_offer(offer_id).estado == "descartada"  # type: ignore[attr-defined]
    assert repo.get_offer(offer_id).analyze_attempts in (0, None)  # type: ignore[attr-defined]


def test_tally_records_closed_offers() -> None:
    from cva.bridge import _RoundTally

    tally = _RoundTally("b", 1)
    tally.record("closed", None, "Dev", "Acme", "https://x.test/1")
    assert tally.closed == 1
    assert tally.closed_offers == [ClosedOffer("Dev", "Acme", "https://x.test/1")]


def test_round_summary_lists_closed_offers_compactly_and_capped() -> None:
    closed = [ClosedOffer(f"T{i}", "C<", None) for i in range(9)]
    text = round_summary_message(
        beacon="b",
        analyzed=0,
        above=0,
        gray=0,
        below=0,
        pending=0,
        discarded=[],
        closed=9,
        closed_offers=closed,
    )
    assert "9 ofertas cerradas" in text
    assert "T0" in text and "C&lt;" in text
    assert "… y 4 más" in text


def test_round_summary_without_closed_is_unchanged() -> None:
    text = round_summary_message(
        beacon="b",
        analyzed=1,
        above=1,
        gray=0,
        below=0,
        pending=0,
        discarded=[],
    )
    assert "cerradas" not in text


# --- bot wiring: 404 vs wall vs closed text -------------------------------------


def test_url_404_gets_a_distinct_removed_message_not_the_paste_prompt(
    tmp_path: Path,
) -> None:
    import bot
    from tests.test_bot import FakeContext, FakeMessage, FakeUpdate
    from tests.test_bot import make_service as bot_service

    service, _llm = bot_service(
        tmp_path, FakeFetcher(FetchResponse(404, "", OFFER_URL))
    )
    message = FakeMessage()
    context = FakeContext(service, args=[OFFER_URL])
    asyncio.run(bot.analyze_command(FakeUpdate(message=message), context))
    reply = message.replies[0].lower()
    assert "retirada o cerrada" in reply and "404" in reply
    assert "pega el texto de la oferta" not in reply
    assert not context.user_data.get(bot._PASTE_KEY)


def test_wall_keeps_the_paste_prompt_with_block_wording(tmp_path: Path) -> None:
    import bot
    from tests.test_bot import FakeContext, FakeMessage, FakeUpdate
    from tests.test_bot import make_service as bot_service

    service, _llm = bot_service(
        tmp_path, FakeFetcher(FetchResponse(429, "", OFFER_URL))
    )
    message = FakeMessage()
    context = FakeContext(service, args=[OFFER_URL])
    asyncio.run(bot.analyze_command(FakeUpdate(message=message), context))
    assert "muro/bloqueo" in message.replies[0]
    assert "pega el texto" in message.replies[0].lower()
    assert context.user_data[bot._PASTE_KEY] is True


def test_pasted_closed_offer_replies_closed_and_skips_the_llm(tmp_path: Path) -> None:
    import bot
    from tests.test_bot import FakeContext, FakeMessage, FakeUpdate
    from tests.test_bot import make_service as bot_service

    service, llm = bot_service(tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL)))
    context = FakeContext(service)
    context.user_data[bot._PASTE_KEY] = True
    message = FakeMessage(text=CLOSED_TEXT)
    asyncio.run(bot.paste_handler(FakeUpdate(message=message), context))
    assert llm.calls == 0
    assert "cerrada" in message.replies[0].lower()
    assert not context.user_data.get(bot._PASTE_KEY)


def test_closed_marker_returns_the_folded_phrase() -> None:
    # The evidence is the folded (lowercase, accent-stripped) phrase.
    assert closed_marker("Foo. OFERTA CADUCADA el 12") == "oferta caducada"


def test_append_note_matches_whole_entries_not_substrings(tmp_path: Path) -> None:
    service, _llm, repo = _svc(tmp_path)
    offer_id = asyncio.run(service.ingest(None, CLOSED_TEXT))  # type: ignore[attr-defined]
    repo.append_note(offer_id, "hablé: la oferta cerrada ayer según el recruiter")  # type: ignore[attr-defined]
    repo.append_note(offer_id, "oferta cerrada")  # type: ignore[attr-defined]
    repo.append_note(offer_id, "oferta cerrada")  # type: ignore[attr-defined]
    notas = repo.get_offer(offer_id).notas or ""  # type: ignore[attr-defined]
    assert notas.split(" · ")[-1] == "oferta cerrada"
    assert notas.count("oferta cerrada") == 2  # hand-written phrase + one marker
    repo.append_note(offer_id, "duplicada de #1")  # type: ignore[attr-defined]
    repo.append_note(offer_id, "duplicada de #12")  # type: ignore[attr-defined]
    notas = repo.get_offer(offer_id).notas or ""  # type: ignore[attr-defined]
    assert "duplicada de #1 · duplicada de #12" in notas


def test_cli_analyze_of_closed_offer_replies_closed_not_traceback() -> None:
    from cli import run
    from cva.service import OfferClosedError
    from tests.test_cli import FakeService

    class Closed(FakeService):
        async def analyze(self, offer_id: int):  # type: ignore[no-untyped-def]
            raise OfferClosedError(offer_id, "oferta cerrada")

    out = run(["analyze", "7"], Closed())
    assert "cerrada" in out


def test_retry_sweep_redrive_of_closed_offer_is_not_a_failure(tmp_path: Path) -> None:
    """A retry-pending offer whose refreshed text is closed: no failure
    counter bump, no fallback card, retry state cleared, never re-listed."""
    from datetime import UTC, datetime

    service, _llm, repo = _svc(tmp_path)
    offer_id = asyncio.run(service.ingest(None, CLOSED_TEXT))  # type: ignore[attr-defined]
    repo.record_analyze_failure(offer_id, next_analyze_at="2026-01-01T00:00:00+00:00")  # type: ignore[attr-defined]
    cards: list[object] = []

    async def notify(text: str, keyboard: object) -> None:
        cards.append(text)

    # exhausted budget: a generic failure here WOULD send the fallback card
    service._analyze_retry_max = 0  # type: ignore[attr-defined]
    outcome = asyncio.run(
        service.analyze_new_bridge_job(  # type: ignore[attr-defined]
            offer_id,
            url=OFFER_URL,
            title="AI Engineer",
            company="Acme",
            notify=notify,
            text=CLOSED_TEXT,
        )
    )
    assert outcome.zone == "closed" and cards == []
    row = repo.get_offer(offer_id)  # type: ignore[attr-defined]
    assert row.analyze_attempts == 0 and row.next_analyze_at is None
    assert row.estado == "descartada"
    now_iso = datetime.now(tz=UTC).isoformat()
    assert repo.list_retry_pending(now_iso, 5) == []  # type: ignore[attr-defined]
