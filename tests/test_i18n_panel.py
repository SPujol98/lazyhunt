"""bot-i18n T4: panel, prep pack, calibration, health, gates, salary, Gmail, CLI.

English is the new language; Spanish (the default of every ``lang`` parameter
until the callers are migrated) must stay byte-identical to what shipped.
"""

import asyncio
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

import cli
from cva import calibration_ui as cu
from cva.beacons import health
from cva.calibration import CalibrationExample, CalibrationSummary
from cva.config import Settings
from cva.families import BUILTIN_REGISTRY
from cva.gmail import client as gmail_client
from cva.gmail import sync as gmail_sync
from cva.i18n import CATALOGS, DEFAULT_LANG
from cva.match.gates import (
    GatePrefs,
    gate_reason,
    language_gate,
    location_gate,
    run_gates,
)
from cva.match.salary import extract_published_salary
from cva.report.panel import DiscardedOffer, render_panel
from cva.report.prep_pack import prep_filename, prep_summary, render_prep_html
from cva.tracker import OfferRepository, OfferRow
from tests.test_interview_prep import _result

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
SPANISH_WORDS = re.compile(
    r"\b(?:fuera de|Exige|Menciona|revisa|Presencial|ubicación|Híbrido|Pide|"
    r"idioma|Remoto|Varias)\b"
)


def row(n: int, estado: str = "analizada", **kw: object) -> OfferRow:
    return OfferRow(id=n, estado=estado, source_data="x", **kw)  # type: ignore[arg-type]


ROWS = [
    row(2, "analizada", origen="linkedin", match=70, fecha_limite="2026-10-31"),
    row(3, "preparada", fecha_limite="2026-10-29"),
    row(4, "preparada", fecha_limite="2026-10-28"),
    row(5, "enviada", fecha_envio="2026-10-01", fecha_update="2026-10-10"),
    row(7, "entrevista_tecnica", fecha_envio="2026-10-21"),
]


def disc(**kw: object) -> DiscardedOffer:
    base: dict[str, object] = {
        "offer_id": 7,
        "title": "Dev",
        "company": "Acme",
        "portal": "LinkedIn",
        "url": None,
        "location": None,
        "kind": "below",
        "score": 55,
        "threshold": 65,
        "profile": "ai",
        "reason": "Motivo",
        "days_ago": 2,
    }
    base.update(kw)
    return DiscardedOffer(**base)  # type: ignore[arg-type]


# --- HTML panel ----------------------------------------------------------------


def test_panel_in_english() -> None:
    page = render_panel(ROWS, NOW, [disc()], lang="en")
    assert '<html lang="en">' in page
    assert "<title>Applications dashboard</title>" in page
    assert "due in 3 days" in page
    assert "due in 1 day<" in page
    assert "due today" in page
    assert "no news for 18 days" in page
    assert "Response rate" in page and "Placeholder" not in page
    assert 'placeholder="Search company, role…"' in page
    assert "2 days ago" in page and "<code>/rescue 7</code>" in page
    assert "Discarded by score" in page and "(last 7 days)" in page
    assert "technical interview" in page  # estado label in the by-status chart
    assert "Panel de candidaturas" not in page and "Embudo" not in page


def test_panel_spanish_is_the_default_and_unchanged() -> None:
    page = render_panel(ROWS, NOW, [disc()])
    assert page == render_panel(ROWS, NOW, [disc()], lang="es")
    assert '<html lang="es">' in page
    assert "<title>Panel de candidaturas</title>" in page
    assert "vence en 3 día(s)" in page and "vence en 0 día(s)" in page
    assert "sin novedades desde hace 18 días" in page
    assert "hace 2 días" in page and "<code>/rescatar 7</code>" in page
    assert "var EL=" not in page  # Spanish keeps the raw-value script


def test_panel_estado_script_follows_the_language() -> None:
    en = render_panel(ROWS, NOW, lang="en")
    assert '"entrevista_tecnica": "technical interview"' in en
    assert "(EL[r.estado]||r.estado.replace(/_/g,' '))" in en
    assert "r.posicion||'offer'" in en and "n+' of '+rows.length" in en


def test_panel_ai_family_tag() -> None:
    en = render_panel([], NOW, [disc()], BUILTIN_REGISTRY, lang="en")
    es = render_panel([], NOW, [disc()], BUILTIN_REGISTRY, lang="es")
    assert "55 / 65 (AI)" in en and "55 / 65 (IA)" in es


def test_panel_translates_stored_wall_reasons_for_display_only() -> None:
    stored = "Presencial fuera de Cataluña (Madrid)"
    item = disc(kind="walled", location="Madrid", reason=stored)
    assert "On-site outside Catalonia (Madrid)" in render_panel(
        [], NOW, [item], lang="en"
    )
    assert stored in render_panel([], NOW, [item], lang="es")
    assert item.reason == stored  # the DTO (and what is stored) is untouched


# --- prep pack -----------------------------------------------------------------


def test_prep_pack_in_english() -> None:
    result = _result(flags=True)
    page = render_prep_html(
        result,
        offer_id=3,
        company="Acme",
        position="Dev",
        created_at="2026-10-28",
        lang="en",
    )
    assert '<html lang="en">' in page
    assert "Likely topics for this stage" in page
    assert "These are likely topics, not certain questions." in page
    assert "Figures not backed by your data" in page
    assert "Questions to ask them" in page
    assert "Temas probables" not in page and "Preguntas para hacerles" not in page
    summary = prep_summary(
        result, offer_id=3, company="Acme", position="Dev", lang="en"
    )
    assert "Key topics (likely, not certain):" in summary
    assert "Full details in the attached HTML." in summary
    assert prep_filename(3, "tecnica", "en") == "preparation_3_tecnica.html"


def test_prep_pack_spanish_default() -> None:
    result = _result(flags=True)
    page = render_prep_html(
        result, offer_id=3, company="Acme", position="Dev", created_at="2026-10-28"
    )
    assert '<html lang="es">' in page
    assert "Temas probables para esta fase" in page
    assert "Cifras sin respaldo en tus datos" in page
    assert prep_filename(3, "tecnica") == "preparacion_3_tecnica.html"
    summary = prep_summary(result, offer_id=3, company="Acme", position="Dev")
    assert "Temas clave (probables, no seguros):" in summary


# --- calibration ---------------------------------------------------------------


def example(**kw: object) -> CalibrationExample:
    base: dict[str, object] = {
        "id": 1,
        "offer_id": 4,
        "outcome": "entrevista_tecnica",
        "polarity": "positive",
        "score_at_send": 70,
        "status": "active",
        "created_at": "2026-10-01",
        "core_role": "Backend",
    }
    base.update(kw)
    return CalibrationExample(**base)  # type: ignore[arg-type]


def test_calibration_in_english() -> None:
    ex = example()
    assert cu.question_message(ex, "en").startswith(
        "🎯 Use this offer to calibrate the score?\n"
        "Bot score: 70/100 · Result: technical interview\n"
    )
    assert "remove it later in /calibration" in cu.question_message(ex, "en")
    buttons = cu.question_keyboard(4, "en").inline_keyboard[0]
    assert [b.text for b in buttons] == ["✅ Yes", "No"]
    assert [b.callback_data for b in buttons] == ["cal:y:4", "cal:n:4"]  # ids stay
    reasons = cu.reason_keyboard(9, "en").inline_keyboard
    assert [b.text for row_ in reasons for b in row_] == [
        "Requirements",
        "Stack",
        "Language",
        "Other / not sure",
    ]
    assert [b.callback_data for b in reasons[1]] == ["cal:r:9:idioma", "cal:r:9:otro"]
    listing = cu.list_message(
        [example(outcome="rechazada", reason="stack", polarity="negative")],
        CalibrationSummary(total=1, positives_below=2, negatives_above=3),
        "en",
    )
    assert "Active calibration examples (1):" in listing
    assert "#4 · score 70 · rejected (stack) · Backend" in listing
    assert "• Positives (you reached an interview): 2 with a score below" in listing
    assert (
        cu.list_keyboard([ex], "en").inline_keyboard[0][0].text == "🗑 Deactivate (#4)"
    )
    assert "no active calibration examples" in cu.list_message(
        [], CalibrationSummary(), "en"
    )
    assert cu.reason_message("en").startswith("🎯 Why were you rejected?")


def test_calibration_spanish_default() -> None:
    ex = example()
    assert cu.question_message(ex) == (
        "🎯 ¿Usar esta oferta para calibrar la nota?\n"
        "Nota del bot: 70/100 · Resultado: entrevista técnica\n"
        "Solo se usan la nota, el rol y el resultado; nunca el texto de la "
        "oferta ni tus notas. Puedes quitarla luego en /calibracion."
    )
    assert [b.text for b in cu.question_keyboard(4).inline_keyboard[0]] == [
        "✅ Sí",
        "No",
    ]
    assert cu.REASON_LABELS["otro"] == "Otro / no sé"
    assert cu.outcome_text("sin_respuesta") == "sin respuesta"
    assert cu.outcome_text("🤷 Algo raro") == "algo raro"  # unknown: as before


# --- beacon health ---------------------------------------------------------------


def test_beacon_health_alerts_in_english_and_spanish() -> None:
    assert health._message(health.YIELD_DROP, "Bcn", "en") == (
        "⚠️ The beacon «Bcn» used to return offers and now returns 0 "
        "(2 scans in a row). The portal may have changed or the search may "
        "be broken."
    )
    assert "more than 50% of the offers arrive without a company" in health._message(
        health.PARSER_BROKEN, "Bcn", "en"
    )
    assert "3 unconfirmed empty scans in a row" in health._message(
        health.UNCONFIRMED_STREAK, "Bcn", "en"
    )
    assert health._message(health.YIELD_DROP, "Bcn") == (
        "⚠️ El beacon «Bcn» devolvía ofertas y ahora da 0 (2 escaneos seguidos). "
        "Puede que el portal haya cambiado o la búsqueda esté rota."
    )


def test_new_alerts_carry_the_language() -> None:
    from tests.test_beacon_alert_claims import DROP

    (alert,), _ = health.new_alerts("Bcn", DROP, set(), "en")
    assert alert.message.startswith("⚠️ The beacon «Bcn»")


# --- gate reasons ------------------------------------------------------------------

GATE_TEXTS = [
    ("Must be a US citizen. Spanish residents welcome in EU.", None),
    ("Requires US work authorization", None),
    ("We do not require US work authorization", None),
    ("Security clearance required", None),
    ("Native English C2 level required", None),
    ("Alemán imprescindible. Francés valorable", None),
    ("Alemán, francés e italiano obligatorio", None),
    ("German is a plus", None),
    ("Presencial en Madrid", "Madrid"),
    ("Híbrido en Madrid, 3 días en la oficina", "Madrid, España"),
    ("100% remoto, una vez al mes en la oficina de Madrid", None),
    ("Oficina en Barcelona y Madrid", None),
    ("Presencial. Mencionamos Madrid", None),
    ("Trabajo presencial, España", None),
    ("Mix remote and onsite in Berlin or Barcelona", "Berlin"),
    ("Hybrid role based in Valencia, Spain", "Valencia"),
]


def _reasons() -> list[str]:
    found = [
        g.reason for text, loc in GATE_TEXTS for g in run_gates(text, loc) if g.reason
    ]
    found.append(
        language_gate(
            "Se requiere catalán y alemán", GatePrefs(allowed_languages=("es", "en"))
        ).reason
    )
    return found


def test_stored_gate_reasons_stay_spanish() -> None:
    reasons = _reasons()
    assert len(reasons) >= 12
    assert all(SPANISH_WORDS.search(r) for r in reasons)
    assert location_gate("Presencial en Londres", "London").reason == (
        "Presencial fuera de Cataluña (London, Londres)"
    )


def test_gate_reasons_translate_to_english() -> None:
    reasons = _reasons()
    for reason in reasons:
        english = gate_reason(reason, "en")
        assert english != reason, reason
        assert not SPANISH_WORDS.search(english), (reason, english)
        assert gate_reason(reason, "es") == reason
    assert gate_reason("Pide habilitación de seguridad (security clearance)", "en") == (
        "Asks for a security clearance"
    )
    assert gate_reason(
        "Exige francés e italiano (idioma obligatorio fuera de ES/CA/EN)".replace(
            " e ", " y "
        ),
        "en",
    ) == ("Requires French and Italian (mandatory language outside ES/CA/EN)")
    assert gate_reason("Híbrido fuera de Cataluña (Madrid)", "en") == (
        "Hybrid outside Catalonia (Madrid)"
    )
    assert gate_reason(
        "Remoto con presencialidad sin ubicación concreta: revisa", "en"
    ) == ("Remote with on-site work without a specific location: check")


def test_gate_reason_leaves_other_texts_alone() -> None:
    for text in (
        "",
        "Stack wall: core is Java",
        "Motivo libre",
        "Algo fuera de Cataluña (X)",
    ):
        assert gate_reason(text, "en") == text


# --- salary --------------------------------------------------------------------------


def test_salary_note_language() -> None:
    text = "3.000 € al mes brutos"
    es = extract_published_salary(text)
    en = extract_published_salary(text, lang="en")
    assert es is not None and en is not None
    assert es.note == "mensual x12" and en.note == "monthly x12"
    assert (en.min_eur, en.max_eur, en.period) == (es.min_eur, es.max_eur, es.period)
    annual = extract_published_salary("28.000 - 34.000 € brutos/año", lang="en")
    assert annual is not None and annual.note == ""


# --- Gmail -----------------------------------------------------------------------------


def test_gmail_notices_in_english_and_spanish() -> None:
    assert gmail_sync.auth_warning("en").startswith(
        "⚠️ Gmail: the authorization has expired or been revoked"
    )
    assert gmail_sync.auth_warning("en").count(gmail_client.AUTH_COMMAND) == 1
    assert "3 syncs in a row" in gmail_sync.outage_warning("en")
    assert gmail_sync.window_warning("en").startswith("⚠️ Gmail: there is so much")
    assert gmail_sync.auth_warning().startswith(
        "⚠️ Gmail: la autorización ha caducado o se ha revocado, así que no puedo "
        "leer tu correo.\nEn tu PC ejecuta:\n"
    )
    assert "3 sincronizaciones seguidas" in gmail_sync.outage_warning()


def test_gmail_client_errors_follow_the_language(tmp_path: Path) -> None:
    missing = tmp_path / "token.json"
    with pytest.raises(gmail_client.GmailAuthError) as en:
        gmail_client._load_credentials(missing, "en")
    assert str(en.value) == (
        f"No Gmail token: run `{gmail_client.AUTH_COMMAND}` on your PC."
    )
    with pytest.raises(gmail_client.GmailAuthError) as es:
        gmail_client._load_credentials(missing)
    assert str(es.value) == (
        f"No hay token de Gmail: ejecuta `{gmail_client.AUTH_COMMAND}` en tu PC."
    )
    with pytest.raises(gmail_client.GmailAuthError, match="credentials file at"):
        gmail_client.run_auth_flow(tmp_path / "c.json", missing, lang="en")


def test_gmail_sync_sends_the_notice_in_its_language(tmp_path: Path) -> None:
    sent: list[str] = []

    async def send(text: str) -> None:
        sent.append(text)

    for lang, start in (
        ("en", "⚠️ Gmail: the authorization has expired"),
        ("es", "⚠️ Gmail: la autorización ha caducado"),
    ):
        sync = gmail_sync.GmailSync(
            repo=OfferRepository(sqlite3.connect(tmp_path / f"{lang}.db")),
            client=object(),  # type: ignore[arg-type]
            llm=object(),  # type: ignore[arg-type]
            lang=lang,  # type: ignore[arg-type]
        )
        asyncio.run(sync._warn_auth_once(send))
        assert sent[-1].startswith(start)


# --- CLI ----------------------------------------------------------------------------------


class _Service:
    preferences = None


def _help(argv: list[str], **kw: object) -> str:
    return argv_help(argv, **kw)


def argv_help(argv: list[str], **kw: object) -> str:
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), pytest.raises(SystemExit):
        cli.run(argv, _Service(), **kw)  # type: ignore[arg-type]
    return buf.getvalue()


def test_cli_help_in_english() -> None:
    text = _help(["--help"], lang="en")
    assert "Job application tracking assistant" in text
    assert "check the beacons that are due (sweep)" in text
    assert "register an offer (url or text)" in text
    rejudge = _help(["rejudge", "--help"], lang="en")
    assert "re-judge even if a judge_results is already stored" in rejudge
    assert "only estimate how many offers would be judged" in rejudge
    assert "Asistente" not in text and "juez" not in rejudge


def test_cli_help_defaults_to_spanish_and_follows_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert "Asistente de seguimiento de ofertas" in _help(["--help"])
    monkeypatch.setenv("CVA_TELEGRAM_TOKEN", "tok")
    monkeypatch.setenv("CVA_UI_LANGUAGE", "en")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.ui_language == "en"
    assert "Job application tracking assistant" in _help(["--help"], settings=settings)
    monkeypatch.setenv("CVA_UI_LANGUAGE", "es")
    es = Settings(_env_file=None)  # type: ignore[call-arg]
    assert "Asistente de seguimiento" in _help(["--help"], settings=es)


def test_cli_argument_errors_follow_the_language(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        cli.run(["rejudge", "--limit", "0"], _Service(), lang="en")  # type: ignore[arg-type]
    assert "must be >= 1: '0'" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.run(["rejudge", "--limit", "0"], _Service())  # type: ignore[arg-type]
    assert "debe ser >= 1: '0'" in capsys.readouterr().err


def test_cli_messages_in_english() -> None:
    class Svc:
        preferences = None

        def keywords(self, profile: str | None) -> list[object]:
            return []

        def create_beacon(self, name: str, url: str) -> int:
            return 4

    out = cli.run(["keywords"], Svc(), lang="en")  # type: ignore[arg-type]
    assert out == "No keywords recorded yet."
    assert cli.run(["beacon", "add", "n", "u"], Svc(), lang="en") == (  # type: ignore[arg-type]
        "✅ Beacon #4 created."
    )
    assert cli.run(["beacon", "add"], Svc(), lang="en") == (  # type: ignore[arg-type]
        "Usage: cli.py beacon add <name> <url>"
    )
    assert cli.run(["keywords"], Svc()) == "Sin keywords registradas todavía."  # type: ignore[arg-type]


# --- catalog ---------------------------------------------------------------------------------


def test_catalog_has_the_t4_keys_in_both_languages() -> None:
    panel_keys = {k for k in CATALOGS["es"] if k.startswith("panel.")}
    assert panel_keys == {k for k in CATALOGS["en"] if k.startswith("panel.")}
    assert len(panel_keys) > 150
    assert DEFAULT_LANG == "en"


def test_panel_shows_a_stored_stack_wall_reason_in_the_ui_language() -> None:
    """The stack-wall reason is stored in Spanish; the English panel shows it
    in English (T3 gap), the Spanish panel unchanged."""
    from cva.i18n import t

    stored = t("bot.stack_wall", "es", label="Java/Spring", core="Java")
    item = disc(kind="walled", reason=stored, score=None)
    english = t("bot.stack_wall", "en", label="Java/Spring", core="Java")
    page_en = render_panel(ROWS, NOW, [item], lang="en")
    assert english in page_en and stored not in page_en
    assert stored in render_panel(ROWS, NOW, [item], lang="es")
