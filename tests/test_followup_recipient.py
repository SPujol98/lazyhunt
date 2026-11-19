"""Follow-up proposals show whom to send them to (S1). Never invents."""

from pathlib import Path

from cva.match.followup import Recipient, resolve_recipient
from cva.notifier import followup_message
from cva.tracker import OfferRow
from tests.test_followups import DRAFT, FakeBot, beat, make, sent_offer

ATS = "Greenhouse <no-reply@greenhouse.io>"
ATS2 = "Talent <jobs@lever.co>"
HR = "RRHH Acme <rrhh@acme.com>"


def _r(**kw: object) -> Recipient:
    base: dict[str, object] = {"contact": None, "senders": [], "offer_text": None}
    return resolve_recipient(**{**base, **kw})  # type: ignore[arg-type]


def test_contact_column_wins() -> None:
    got = _r(contact="Ana <ana@acme.com>", senders=[HR], offer_text="a@b.com")
    assert got == Recipient("ana@acme.com", "contact")


def test_contact_that_is_not_an_email_falls_through() -> None:
    assert _r(contact="LinkedIn: Ana", senders=[HR]) == Recipient(
        "rrhh@acme.com", "gmail"
    )


def test_latest_non_ats_gmail_sender() -> None:
    assert _r(senders=[ATS, ATS2, HR, "x <old@acme.com>"]) == Recipient(
        "rrhh@acme.com", "gmail"
    )


def test_ats_only_senders_are_skipped() -> None:
    assert _r(senders=[ATS, ATS2]) == Recipient(None)


def test_offer_text_email_used_and_privacy_addresses_ignored() -> None:
    text = (
        "Protección de datos: data.protection@acme.com, privacy@acme.com, "
        "dpo@acme.com, noreply@acme.com. Envía tu CV a talento@acme.com"
    )
    assert _r(offer_text=text) == Recipient("talento@acme.com", "offer")
    only_privacy = "Contacta privacy@acme.com o data.protection@acme.com"
    assert _r(offer_text=only_privacy) == Recipient(None)


def test_nothing_known_invents_nothing() -> None:
    assert _r() == Recipient(None)


def test_message_line_es_and_en() -> None:
    offer = OfferRow(id=1, estado="enviada", source_data="{}", empresa="Acme")
    kw = {"days_silent": 10, "number": 1, "draft": DRAFT, "warnings": []}
    es = followup_message(
        offer,
        recipient=Recipient("rrhh@acme.com", "gmail"),
        **kw,  # type: ignore[arg-type]
    )
    assert "📧 Enviar a: rrhh@acme.com (último correo de la empresa)" in es
    assert DRAFT in es
    en = followup_message(
        offer,
        lang="en",
        recipient=Recipient("a@b.com", "contact"),
        **kw,  # type: ignore[arg-type]
    )
    assert "📧 Send to: a@b.com (contact saved on the offer)" in en
    none_es = followup_message(offer, recipient=Recipient(None), **kw)  # type: ignore[arg-type]
    assert "Sin dirección conocida" in none_es
    none_en = followup_message(offer, lang="en", recipient=Recipient(None), **kw)  # type: ignore[arg-type]
    assert "No address known: answer through the portal" in none_en
    # Unresolved (no recipient passed): the message is unchanged.
    assert "📧" not in followup_message(offer, **kw)  # type: ignore[arg-type]


def test_service_reads_gmail_senders_and_heartbeat_shows_the_line(
    tmp_path: Path,
) -> None:
    service, repo, clock = make(tmp_path)
    oid = sent_offer(repo)
    offer = repo.get_offer(oid)
    assert service.followup_recipient(offer) == Recipient(None)
    for n, (sender, at) in enumerate([(HR, "2026-01-02"), (ATS, "2026-01-03")]):
        repo.add_gmail_proposal(
            message_id=f"m{n}",
            offer_id=oid,
            sender=sender,
            subject="s",
            received_at=at,
            proposed_estado="entrevista",
        )
    assert service.followup_recipient(offer) == Recipient("rrhh@acme.com", "gmail")
    clock.advance(10)
    tg = FakeBot()
    beat(service, tg)
    assert "📧 Enviar a: rrhh@acme.com" in tg.sent[0][1]
