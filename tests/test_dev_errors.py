"""pending-maintenance P6: no English developer message reaches the chat or the CLI.

Every user-triggerable ``ServiceError`` is keyed: the bot renders it in the UI
language, ``str(exc)`` stays the Spanish text.
"""

from pathlib import Path

import pytest

import bot
import cli
from cva.service import ServiceError
from tests.test_bot import FakeContext, FakeMessage  # noqa: F401
from tests.test_i18n_bot import _command, _context, _press, _service


def _unanalyzed(tmp_path: Path) -> tuple[object, int]:
    service = _service(tmp_path)
    from cva.tracker import OfferInput

    offer_id = service.repository.upsert_offer(
        OfferInput(
            job_id="9",
            title="T",
            company="C",
            url="https://x/9",
            text="t",
            source="link",
        )
    )
    return service, offer_id


def test_sent_command_on_an_unanalyzed_offer_is_friendly_in_english(
    tmp_path: Path,
) -> None:
    service, offer_id = _unanalyzed(tmp_path)
    message = _command(bot.sent_command, _context(service, [str(offer_id)]))  # type: ignore[arg-type]
    (reply,) = message.replies
    assert reply == (
        f"⚠️ Offer #{offer_id} has not been analyzed yet. Analyze it first with "
        "the 🔍 button or /analyze <url>."
    )
    assert "call analyze first" not in reply


def test_sent_button_on_an_unanalyzed_offer_is_friendly(tmp_path: Path) -> None:
    service, offer_id = _unanalyzed(tmp_path)
    query = _press(_context(service), f"sent:{offer_id}")  # type: ignore[arg-type]
    assert "call analyze first" not in str(query.message.replies)
    assert "Analyze it first" in query.message.replies[0]


def test_sent_error_keeps_its_spanish_str(tmp_path: Path) -> None:
    service, offer_id = _unanalyzed(tmp_path)
    with pytest.raises(ServiceError) as caught:
        service.mark_sent(offer_id)  # type: ignore[attr-defined]
    assert str(caught.value) == (
        f"La oferta #{offer_id} todavía no está analizada. Analízala primero con "
        "el botón 🔍 o /analyze <url>."
    )
    assert caught.value.text("en").startswith(f"Offer #{offer_id} has not")


def test_cli_sent_prints_the_friendly_message_instead_of_a_traceback(
    tmp_path: Path,
) -> None:
    service, offer_id = _unanalyzed(tmp_path)
    out = cli.run(["sent", str(offer_id)], service, lang="en")  # type: ignore[arg-type]
    assert out == (
        f"⚠️ Offer #{offer_id} has not been analyzed yet. Analyze it first with "
        "the 🔍 button or /analyze <url>."
    )


def test_cli_cv_with_a_language_the_family_lacks_is_friendly(tmp_path: Path) -> None:
    service, offer_id = _unanalyzed(tmp_path)
    with pytest.raises(ServiceError) as caught:
        import asyncio

        asyncio.run(service.generate_cv(offer_id, "BACKEND", "ca"))  # type: ignore[attr-defined]
    assert "invalid role/language" not in caught.value.text("en")
    assert "BACKEND" in caught.value.text("en")
    assert "BACKEND" in caught.value.text("es")
    assert "invalid role/language" not in str(caught.value)


def test_empty_offer_after_cleaning_is_friendly() -> None:
    from cva.ingest.guest import paste_offer
    from cva.service import _guard_offer

    with pytest.raises(ServiceError) as caught:
        _guard_offer(paste_offer("\u200b\u200b\u200b"))
    assert "removing hidden" not in caught.value.text("en")
    assert "removing hidden" not in str(caught.value)
