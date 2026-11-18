"""Gmail message -> sent offer matching (M2): pure, no I/O."""

from datetime import UTC, datetime

from cva.gmail.client import GmailMessage
from cva.gmail.matching import match_offer, normalize_company
from cva.tracker import OfferRow


def _offer(
    offer_id: int,
    company: str | None,
    *,
    sent: str | None = "2026-10-20",
    estado: str = "enviada",
) -> OfferRow:
    return OfferRow(
        id=offer_id,
        empresa=company,
        posicion="AI Engineer",
        fecha_envio=sent,
        estado=estado,
        source_data="x",
    )


def _msg(sender: str, subject: str = "Tu candidatura", text: str = "") -> GmailMessage:
    return GmailMessage(
        message_id="m1",
        sender=sender,
        subject=subject,
        received_at=datetime(2026, 11, 3, tzinfo=UTC),
        text=text,
    )


def test_normalize_company_strips_legal_suffixes_accents_and_punctuation() -> None:
    assert normalize_company("Acme, S.L.") == "acme"
    assert normalize_company("  Telefónica Tech S.A.U. ") == "telefonica tech"
    assert normalize_company("Globex Inc.") == "globex"
    assert normalize_company("Initech GmbH") == "initech"


def test_matches_on_sender_domain() -> None:
    offers = [_offer(1, "Acme S.L."), _offer(2, "Globex")]
    found = match_offer(_msg("Ana RH <ana@acme.com>"), offers)
    assert found is not None and found.id == 1


def test_matches_on_subdomain_and_country_tld() -> None:
    offers = [_offer(1, "Acme")]
    assert match_offer(_msg("rrhh@careers.acme.es"), offers) is not None
    assert match_offer(_msg("rrhh@mail.acme.co.uk"), offers) is not None


def test_matches_company_in_display_name() -> None:
    offers = [_offer(1, "Globex")]
    found = match_offer(_msg('"Globex Talent" <noreply@somemail.io>'), offers)
    assert found is not None and found.id == 1


def test_matches_company_in_subject() -> None:
    offers = [_offer(1, "Globex")]
    found = match_offer(
        _msg("jobs@hr-platform.io", subject="Globex - Entrevista para AI Engineer"),
        offers,
    )
    assert found is not None


def test_ats_sender_needs_the_company_name_in_the_message() -> None:
    offers = [_offer(1, "Initech")]
    ats = "Initech via Greenhouse <no-reply@greenhouse.io>"
    assert match_offer(_msg(ats), offers) is not None
    other = _msg("no-reply@greenhouse.io", subject="Thanks", text="Hello from Umbrella")
    assert match_offer(other, offers) is None


def test_ats_sender_with_company_in_body() -> None:
    offers = [_offer(1, "Initech")]
    message = _msg(
        "no-reply@hire.lever.co",
        subject="Update on your application",
        text="Thank you for applying to Initech. We will be in touch.",
    )
    found = match_offer(message, offers)
    assert found is not None and found.id == 1


def test_company_in_body_of_unknown_sender_still_matches() -> None:
    offers = [_offer(1, "Initech")]
    message = _msg("someone@gmail.com", text="Te escribo de parte de Initech")
    assert match_offer(message, offers) is not None


def test_word_boundaries_prevent_substring_matches() -> None:
    offers = [_offer(1, "Ing")]
    assert (
        match_offer(_msg("x@y.com", text="Hiring now in Madrid, engineering"), offers)
        is None
    )


def test_unmatched_message_returns_none() -> None:
    assert match_offer(_msg("news@random.org"), [_offer(1, "Acme")]) is None


def test_only_sent_and_open_offers_are_candidates() -> None:
    offers = [
        _offer(1, "Acme", sent=None, estado="analizada"),
        _offer(2, "Acme", estado="rechazada"),
        _offer(3, "Acme", estado="retirada"),
        _offer(4, "Acme", estado="aceptada"),
        _offer(5, "Acme", estado="descartada"),
    ]
    assert match_offer(_msg("ana@acme.com"), offers) is None


def test_open_post_send_estados_remain_candidates() -> None:
    for estado in ("enviada", "screening", "entrevista_tecnica", "sin_respuesta"):
        found = match_offer(_msg("ana@acme.com"), [_offer(1, "Acme", estado=estado)])
        assert found is not None, estado


def test_several_offers_of_the_company_pick_the_most_recently_sent() -> None:
    offers = [
        _offer(1, "Acme", sent="2026-10-01"),
        _offer(2, "Acme", sent="2026-10-25"),
        _offer(3, "Acme", sent="2026-10-10"),
    ]
    found = match_offer(_msg("ana@acme.com"), offers)
    assert found is not None and found.id == 2


def test_sent_date_tie_prefers_the_higher_id() -> None:
    offers = [
        _offer(1, "Acme", sent="2026-10-25"),
        _offer(2, "Acme", sent="2026-10-25"),
    ]
    found = match_offer(_msg("ana@acme.com"), offers)
    assert found is not None and found.id == 2


def test_stronger_signal_beats_a_more_recent_weak_one() -> None:
    offers = [
        _offer(1, "Acme", sent="2026-10-01"),  # sender domain
        _offer(2, "Globex", sent="2026-10-30"),  # only mentioned in the body
    ]
    message = _msg("ana@acme.com", text="Como comentamos con Globex...")
    found = match_offer(message, offers)
    assert found is not None and found.id == 1


def test_offers_without_a_usable_company_are_skipped() -> None:
    offers = [
        _offer(1, None),
        _offer(2, ""),
        _offer(3, "AI"),
        _offer(4, "Confidencial"),
    ]
    assert match_offer(_msg("x@ai.com", text="confidencial AI"), offers) is None


# --- review follow-ups: domain safety and business-word suffixes ---------------------


def test_meta_does_not_match_the_metabase_domain() -> None:
    assert match_offer(_msg("rrhh@metabase.example"), [_offer(1, "Meta")]) is None


def test_banco_santander_does_not_match_a_banco_domain() -> None:
    offers = [_offer(1, "Banco Santander")]
    assert match_offer(_msg("rrhh@banco.com"), offers) is None
    found = match_offer(_msg("rrhh@santander.example"), offers)
    assert found is not None and found.id == 1


def test_domain_matches_a_distinctive_company_token_or_the_exact_name() -> None:
    assert match_offer(_msg("x@telefonica.example"), [_offer(1, "Telefónica Tech")])
    assert match_offer(_msg("x@acme-robotics.com"), [_offer(1, "Acme Robotics")])
    assert match_offer(_msg("x@acmerobotics.com"), [_offer(1, "Acme Robotics")])


def test_domain_prefix_of_the_company_name_no_longer_matches() -> None:
    assert match_offer(_msg("x@acmecorp.com"), [_offer(1, "Acme")]) is None


def test_strong_header_mention_beats_a_false_domain_prefix() -> None:
    offers = [_offer(1, "Meta"), _offer(2, "Metabase Labs")]
    found = match_offer(
        _msg("x@metabase.example", subject="Metabase Labs: tu candidatura"), offers
    )
    assert found is not None and found.id == 2


def test_business_words_are_not_stripped_down_to_a_generic_word() -> None:
    assert normalize_company("Digital Group") == "digital group"
    assert normalize_company("Data Spain") == "data spain"
    assert normalize_company("Acme Group") == "acme"
    assert normalize_company("Globex Grupo España") == "globex"


def test_generic_remainder_does_not_match_unrelated_bodies() -> None:
    body = "Our digital transformation and data platform roadmap."
    offers = [_offer(1, "Digital Group"), _offer(2, "Data Spain")]
    assert match_offer(_msg("x@random.io", text=body), offers) is None


def test_generic_names_are_skipped() -> None:
    for name in ("Digital", "Tech", "Banco", "Global Solutions"):
        assert (
            match_offer(_msg("x@y.io", text=f"{name} news"), [_offer(1, name)]) is None
        )
