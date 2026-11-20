"""Notifier builders + owner whitelist gate (telegram-bot spec, tasks 8.3–8.4).

Pure, network-free: message strings and PTB ``InlineKeyboardMarkup`` objects
built in-process. NOTE ON LANGUAGE: the bot's end user is the Spanish-speaking
owner, so user-facing message STRINGS are Spanish by design; module code,
comments, and identifiers are English (artifact language contract).

The whitelist gate (``bot.is_owner``) is tested here per task 8.3 placement.
"""

from pathlib import Path

import pytest
from telegram import InlineKeyboardMarkup

import bot
from cva.beacons import BeaconPausedAlert
from cva.match import MatchResult, Requirement, RequirementMatch
from cva.match.apply_instructions import ApplyInstruction
from cva.notifier import (
    apply_warning_block,
    borderline_message,
    breakdown_message,
    cv_ready_message,
    judge_breakdown_message,
    with_apply_warnings,
)
from cva.tracker import BeaconRow, RepositoryError

OFFER_ID = 12
JOB_ID = "4459804598"


def _requirement(name: str, must_have: bool, evidence: str) -> Requirement:
    return Requirement(
        name=name, category="skill", must_have=must_have, evidence=evidence
    )


def _breakdown(score: int) -> MatchResult:
    return MatchResult(
        score=score,
        threshold=65,
        breakdown=[
            RequirementMatch(
                requirement=_requirement("Python", True, "experiencia con Python"),
                claim_ids=["skill-python"],
                status="✓",
            ),
            RequirementMatch(
                requirement=_requirement(
                    "PostgreSQL", False, "conocimientos de PostgreSQL"
                ),
                claim_ids=["skill-sql"],
                status="~",
            ),
            RequirementMatch(
                requirement=_requirement("Kubernetes", True, "clusters de Kubernetes"),
                claim_ids=[],
                status="✗",
            ),
        ],
    )


def _payloads(markup: InlineKeyboardMarkup) -> list[tuple[str, str]]:
    return [
        (button.text, button.callback_data)
        for row in markup.inline_keyboard
        for button in row
    ]


# --- New-job card -----------------------------------------------------------


def test_canonical_job_url() -> None:
    from cva.notifier import canonical_job_url

    assert canonical_job_url(JOB_ID) == f"https://linkedin.com/jobs/view/{JOB_ID}"


def test_new_job_message_contains_card_fields() -> None:
    from cva.notifier import canonical_job_url, new_job_message

    text = new_job_message(
        title="AI Engineer", company="Acme", age="2 días", job_id=JOB_ID, threshold=65
    )
    assert "AI Engineer" in text
    assert "Acme" in text
    assert "2 días" in text
    assert canonical_job_url(JOB_ID) in text
    assert "65" in text  # threshold context


def test_new_job_message_url_none_keeps_legacy_linkedin_shape() -> None:
    """url=None (default) keeps the legacy LinkedIn canonical — every caller
    that predates infojobs-source stays byte-identical."""
    from cva.notifier import canonical_job_url, new_job_message

    legacy = new_job_message(
        title="AI Engineer", company="Acme", age="2 días", job_id=JOB_ID, threshold=65
    )
    explicit = new_job_message(
        title="AI Engineer",
        company="Acme",
        age="2 días",
        job_id=JOB_ID,
        threshold=65,
        url=None,
    )
    assert legacy == explicit
    assert canonical_job_url(JOB_ID) in legacy


def test_new_job_message_url_overrides_with_per_source_canonical() -> None:
    """The bridge passes the registry canonical: an InfoJobs card must link
    the InfoJobs offer page, never a LinkedIn-shaped URL."""
    from cva.notifier import canonical_job_url, new_job_message

    ij_canonical = "https://www.infojobs.net/barcelona/ia/of-" + "a" * 32
    text = new_job_message(
        title="AI Engineer",
        company="Acme",
        age="2 días",
        job_id="a" * 32,
        threshold=65,
        url=ij_canonical,
    )
    assert ij_canonical in text
    assert canonical_job_url("a" * 32) not in text


def test_new_job_keyboard_carries_analyze_callback() -> None:
    from cva.notifier import new_job_keyboard

    payloads = _payloads(new_job_keyboard(JOB_ID))
    assert any("Analizar" in text for text, _ in payloads)
    assert f"an:{JOB_ID}" in [data for _, data in payloads]


# --- Breakdown message (table: mixed ✓/~/✗, both threshold branches) --------


def test_breakdown_message_renders_all_statuses_and_evidence() -> None:
    from cva.notifier import breakdown_message

    text = breakdown_message(_breakdown(50), OFFER_ID)
    assert "50/100" in text
    assert "65" in text
    assert "✓ Python" in text
    assert "~ PostgreSQL" in text
    assert "✗ Kubernetes" in text
    assert "experiencia con Python" in text
    assert "clusters de Kubernetes" in text


def test_breakdown_message_threshold_branches() -> None:
    from cva.notifier import breakdown_message

    below = breakdown_message(_breakdown(50), OFFER_ID)
    above = breakdown_message(_breakdown(72), OFFER_ID)
    assert "no supera" in below
    assert "supera" in above


# --- Card content (notification-pacing D6): canonical URL line ---------------


def test_breakdown_message_appends_canonical_url_when_given() -> None:
    from cva.notifier import breakdown_message, canonical_job_url

    text = breakdown_message(_breakdown(72), OFFER_ID, url=canonical_job_url(JOB_ID))
    assert canonical_job_url(JOB_ID) in text  # spec: card carries the offer URL
    assert "72/100" in text
    assert "supera el umbral" in text  # Spanish verdict kept verbatim
    assert "✓ Python" in text and "✗ Kubernetes" in text  # breakdown intact


def test_breakdown_message_without_url_omits_the_line() -> None:
    """url=None (default) keeps the legacy shape — no URL line inserted."""
    from cva.notifier import breakdown_message, canonical_job_url

    text = breakdown_message(_breakdown(72), OFFER_ID)
    assert canonical_job_url(JOB_ID) not in text
    assert "72/100" in text


# --- Judge card (llm-offer-judge design: hybrid match_mode) -----------------


def _judgment(
    *,
    score: int = 76,
    verdict: str = "apply",
    walls: list | None = None,
):
    from cva.match.judge import FitItem, OfferJudgment, Wall

    return OfferJudgment(
        score=score,
        verdict=verdict,  # type: ignore[arg-type]
        core_role="AI Engineer junior orientado a RAG/LLMs",
        fit=[
            FitItem(requirement="RAG", claim_ids=["skill-rag"]),
            FitItem(requirement="Python", claim_ids=["skill-python"]),
            FitItem(requirement="FastAPI", claim_ids=["skill-fastapi"]),
            FitItem(requirement="Docker", claim_ids=["skill-docker"]),
        ],
        walls=walls
        if walls is not None
        else [
            Wall(text="Java/Spring como stack principal", hard=True),
            Wall(text="2 años de experiencia", hard=False),
        ],
        gaps=["Sin experiencia con Power BI"],
        cv_base="AI_ES",
        tailoring_hints=["Destacar el proyecto RAG en la cabecera"],
        rationale="Buen encaje en el core RAG/LLM, pero el stack Java es un muro duro.",
    )


def test_judge_breakdown_message_falls_back_to_keyword_breakdown_when_no_judgment() -> (
    None
):
    """keyword-only mode (or hybrid with no fallback note): identical to
    breakdown_message — byte-for-byte, so the keyword-mode card never changes."""
    from cva.notifier import breakdown_message, judge_breakdown_message

    result = _breakdown(72)
    assert judge_breakdown_message(result, None, OFFER_ID) == breakdown_message(
        result, OFFER_ID
    )


def test_judge_breakdown_message_notes_fallback_when_judge_failed() -> None:
    from cva.notifier import judge_breakdown_message

    text = judge_breakdown_message(_breakdown(72), None, OFFER_ID, judge_fallback=True)
    assert "72/100" in text  # keyword score still shown
    assert "juicio" in text.lower() or "falló" in text.lower()


def test_judge_breakdown_message_renders_score_verdict_core_role_and_cv_base() -> None:
    from cva.notifier import judge_breakdown_message

    text = judge_breakdown_message(_breakdown(72), _judgment(), OFFER_ID)
    assert "76/100" in text  # judge score, not the keyword score
    assert "AI Engineer junior orientado a RAG/LLMs" in text
    assert "AI_ES" in text


def test_judge_breakdown_message_shows_top_three_fit_items_only() -> None:
    from cva.notifier import judge_breakdown_message

    text = judge_breakdown_message(_breakdown(72), _judgment(), OFFER_ID)
    assert "RAG" in text
    assert "Python" in text
    assert "FastAPI" in text
    assert "Docker" not in text  # 4th fit item, capped at 3


def test_judge_breakdown_message_shows_hard_walls_before_soft() -> None:
    from cva.notifier import judge_breakdown_message

    text = judge_breakdown_message(_breakdown(72), _judgment(), OFFER_ID)
    hard_index = text.index("Java/Spring")
    soft_index = text.index("2 años de experiencia")
    assert hard_index < soft_index


def test_judge_breakdown_message_includes_rationale_and_keyword_breakdown() -> None:
    from cva.notifier import judge_breakdown_message

    text = judge_breakdown_message(_breakdown(72), _judgment(), OFFER_ID)
    assert "Buen encaje en el core RAG/LLM" in text
    assert "✓ Python" in text  # keyword breakdown still shown below (transparency)
    assert "✗ Kubernetes" in text


def test_judge_breakdown_message_appends_url_line_when_given() -> None:
    from cva.notifier import canonical_job_url, judge_breakdown_message

    text = judge_breakdown_message(
        _breakdown(72), _judgment(), OFFER_ID, url=canonical_job_url(JOB_ID)
    )
    assert canonical_job_url(JOB_ID) in text


# --- Generate-CV keyboard (toggles + discard) --------------------------------


def test_cv_keyboard_default_payloads() -> None:
    from cva.notifier import cv_keyboard

    payloads = _payloads(cv_keyboard(OFFER_ID))
    data = [d for _, d in payloads]
    assert f"cv:{OFFER_ID}:AI:es" in data
    assert f"lang:{OFFER_ID}:AI:es" in data
    assert f"variant:{OFFER_ID}:AI:es" in data
    assert f"discard:{OFFER_ID}" in data
    texts = [t for t, _ in payloads]
    assert any("Descartar" in t for t in texts)
    assert any("Idioma" in t for t in texts)
    assert any("Variante" in t for t in texts)


def test_cv_keyboard_reflects_current_selection() -> None:
    from cva.notifier import cv_keyboard

    payloads = _payloads(cv_keyboard(OFFER_ID, role="BACKEND", lang="en"))
    data = [d for _, d in payloads]
    assert f"cv:{OFFER_ID}:BACKEND:en" in data
    assert f"lang:{OFFER_ID}:BACKEND:en" in data
    assert f"variant:{OFFER_ID}:BACKEND:en" in data


def test_cv_keyboard_legacy_ds_role_renders_as_ai() -> None:
    from cva.notifier import cv_keyboard

    data = [d for _, d in _payloads(cv_keyboard(OFFER_ID, role="DS", lang="ca"))]
    assert f"cv:{OFFER_ID}:AI:ca" in data
    assert not any(":DS:" in d for d in data)


# --- Language toggle cycle (v3 part B): AI offers ES→EN→CA, Backend has no CA ----


@pytest.mark.parametrize(
    ("role", "lang", "expected"),
    [
        pytest.param("AI", "es", "en", id="ai-es-to-en"),
        pytest.param("AI", "en", "ca", id="ai-en-to-ca"),
        pytest.param("AI", "ca", "es", id="ai-ca-wraps-to-es"),
        pytest.param("BACKEND", "es", "en", id="backend-es-to-en"),
        pytest.param("BACKEND", "en", "es", id="backend-en-wraps-to-es"),
        pytest.param("BACKEND", "ca", "es", id="backend-has-no-ca"),
        pytest.param("DS", "en", "ca", id="legacy-ds-follows-ai-cycle"),
        pytest.param("AI", "fr", "es", id="unknown-lang-resets-to-es"),
    ],
)
def test_next_lang_cycles_existing_combinations_only(
    role: str, lang: str, expected: str
) -> None:
    from cva.notifier import next_lang

    assert next_lang(role, lang) == expected


# --- Backend role (backend-profile T3): AI <-> BACKEND ------------


@pytest.mark.parametrize(
    ("role", "expected"),
    [("AI", "BACKEND"), ("BACKEND", "AI"), ("DS", "BACKEND"), ("???", "AI")],
)
def test_next_role_toggles_ai_and_backend_only(role: str, expected: str) -> None:
    from cva.notifier import next_role

    assert next_role(role) == expected


@pytest.mark.parametrize(
    ("lang", "expected"), [("es", "en"), ("en", "es"), ("ca", "es")]
)
def test_next_lang_backend_skips_catalan(lang: str, expected: str) -> None:
    from cva.notifier import next_lang

    assert next_lang("BACKEND", lang) == expected


def test_cv_keyboard_backend_label_and_catalan_fallback() -> None:
    from cva.notifier import cv_keyboard

    payloads = _payloads(cv_keyboard(OFFER_ID, role="BACKEND", lang="ca"))
    data = [d for _, d in payloads]
    assert f"cv:{OFFER_ID}:BACKEND:es" in data  # CA offers get the ES Backend CV
    assert any(t == "Variante: Backend" for t, _ in payloads)


def test_sent_keyboard_and_message() -> None:
    from cva.notifier import sent_keyboard, sent_message

    assert f"sent:{OFFER_ID}" in [d for _, d in _payloads(sent_keyboard(OFFER_ID))]
    text = sent_message(OFFER_ID, 72)
    assert str(OFFER_ID) in text
    assert "72" in text
    assert "enviada" in text


def test_sent_keyboard_offers_discard_next_to_sent() -> None:
    """Post-generation card: the owner can also discard the offer."""
    from cva.notifier import sent_keyboard

    payloads = [d for _, d in _payloads(sent_keyboard(OFFER_ID))]
    assert f"sent:{OFFER_ID}" in payloads
    assert f"disc:{OFFER_ID}" in payloads


def test_discard_no_send_message_exact_text() -> None:
    from cva.notifier import discard_no_send_message

    assert discard_no_send_message() == "🗑 Oferta descartada — no se registra envío."


def test_discard_message_names_the_offer() -> None:
    from cva.notifier import discard_message

    text = discard_message(OFFER_ID)
    assert str(OFFER_ID) in text
    assert "descartada" in text


def test_cv_ready_message_carries_path_and_pages() -> None:
    from cva.notifier import cv_ready_message

    pdf = Path("adaptadas/Alex_Rivera_AI_Engineer_Acme.pdf")
    text = cv_ready_message(pdf, pages=1)
    assert str(pdf) in text
    assert "1" in text


def test_cv_ready_message_shows_offer_link_when_known() -> None:
    from cva.notifier import cv_ready_message

    pdf = Path("adaptadas/Alex_Rivera_AI_Engineer.pdf")
    url = "https://www.linkedin.com/jobs/view/4471736418/"
    assert url in cv_ready_message(pdf, url=url)
    assert "http" not in cv_ready_message(pdf)


# --- Paste prompt + help ------------------------------------------------------


def test_paste_prompt_asks_for_pasted_text() -> None:
    from cva.notifier import paste_prompt

    text = paste_prompt()
    assert "pega" in text.lower()
    assert "oferta" in text.lower()


def test_help_message_lists_commands() -> None:
    from cva.notifier import help_message

    text = help_message()
    for command in ("/analyze", "/cv", "/status", "/sent", "/export", "/keywords"):
        assert command in text


def test_beacons_stub_message_removed_in_8b() -> None:
    """The 8a stub builder is gone; real beacon surface replaces it (8b)."""
    from cva import notifier

    assert not hasattr(notifier, "beacons_stub_message")


# --- Analyze failure cards (429 gets a tailored wait-and-repaste hint) --------


def test_analyze_failure_message_429_tailored_card() -> None:
    from cva.llm import LlmResponseError
    from cva.notifier import analyze_failure_message

    message = analyze_failure_message(
        LlmResponseError("LLM endpoint returned HTTP 429", status=429)
    )

    assert message == (
        "❌ El análisis falló: límite de peticiones del LLM (429) — "
        "espera unos minutos y vuelve a pegar la oferta."
    )


def test_analyze_failure_message_other_errors_keep_generic_card() -> None:
    from cva.llm import LlmResponseError
    from cva.notifier import analyze_failure_message

    message = analyze_failure_message(
        LlmResponseError("LLM endpoint returned HTTP 401", status=401)
    )

    assert "El análisis falló" in message
    assert "HTTP 401" in message
    assert "pega el texto de nuevo" in message


# --- Bridge auto-analysis fallback card (auto-triage D4; Spanish) -------------


def test_bridge_auto_failure_message_is_legacy_card_plus_cause() -> None:
    from cva.notifier import bridge_auto_failure_message

    message = bridge_auto_failure_message(
        ValueError("boom: no pude parsear"),
        title="ML Engineer",
        company="Acme",
        job_id="4200000000",
        threshold=65,
    )

    # Legacy new-job card content, so the owner can press [Analizar] with context.
    assert "🔔 ML Engineer · Acme" in message
    assert "https://linkedin.com/jobs/view/4200000000" in message
    assert "Umbral Top-Choice: 65" in message
    # Brief Spanish cause line naming the failure.
    assert "boom: no pude parsear" in message


def test_bridge_auto_failure_message_429_gets_failure_reason_hint() -> None:
    from cva.llm import LlmResponseError
    from cva.notifier import bridge_auto_failure_message

    message = bridge_auto_failure_message(
        LlmResponseError("LLM endpoint returned HTTP 429", status=429),
        title=None,
        company=None,
        job_id="4200000000",
        threshold=65,
    )

    assert "HTTP 429" in message
    assert "CVA_LLM_*" in message  # the .env hint from failure_reason
    assert "(sin título) · (sin empresa)" in message  # null-safe legacy card


# --- Beacon surface (job-discovery spec, tasks 8b.5; Spanish user-facing) ------


class FakeBeaconService:
    """Duck-typed service double: records resume/disable, fails on given ids."""

    def __init__(self, fail_ids: set[int] | None = None) -> None:
        self.fail_ids = fail_ids or set()
        self.calls: list[tuple[str, int]] = []

    def resume_beacon(self, beacon_id: int) -> None:
        if beacon_id in self.fail_ids:
            raise RepositoryError(f"beacon {beacon_id} not found")
        self.calls.append(("resume", beacon_id))

    def disable_beacon(self, beacon_id: int) -> None:
        if beacon_id in self.fail_ids:
            raise RepositoryError(f"beacon {beacon_id} not found")
        self.calls.append(("disable", beacon_id))


def test_beacons_list_message_shows_rows_with_status() -> None:
    from cva.notifier import beacons_list_message

    rows = [
        BeaconRow(
            id=1, name="ia-espana", url="u", status="degraded", consecutive_walls=2
        ),
        BeaconRow(
            id=2, name="ds-remoto", url="v", status="paused", consecutive_walls=3
        ),
    ]
    text = beacons_list_message(rows)
    assert "ia-espana" in text and "#1" in text and "degraded" in text
    assert "ds-remoto" in text and "paused" in text
    assert beacons_list_message([])  # empty state still answers something


def test_beacon_paused_message_and_alert_keyboard_actions() -> None:
    from cva.notifier import beacon_alert_keyboard, beacon_paused_message

    alert = BeaconPausedAlert(
        beacon_id=3,
        beacon_name="ia-espana",
        wall_kind="js_shell",
        consecutive_walls=3,
    )
    text = beacon_paused_message(alert)
    assert "ia-espana" in text and "3" in text and "js_shell" in text
    payloads = _payloads(beacon_alert_keyboard(3))
    assert ("▶️ Reactivar", "beacon_resume:3") in payloads
    assert ("⏹ Desactivar", "beacon_disable:3") in payloads


def test_beacon_command_reply_resume_and_disable() -> None:
    from cva.notifier import beacon_command_reply

    service = FakeBeaconService()
    assert "3" in beacon_command_reply(service, ["resume", "3"])
    assert "4" in beacon_command_reply(service, ["disable", "4"])
    assert service.calls == [("resume", 3), ("disable", 4)]


def test_beacon_command_reply_usage_and_unknown_id() -> None:
    from cva.notifier import beacon_command_reply

    service = FakeBeaconService()
    usage = beacon_command_reply(service, ["resume"])
    assert "Uso" in usage
    assert beacon_command_reply(service, ["pause", "3"]) == usage
    assert beacon_command_reply(service, ["resume", "x"]) == usage
    unknown = FakeBeaconService(fail_ids={9})
    assert "9" in beacon_command_reply(unknown, ["resume", "9"])  # unknown id
    assert unknown.calls == []  # nothing executed


# --- Single-owner whitelist (bot gate; task 8.3 placement) --------------------


def test_owner_whitelist_gate() -> None:
    assert bot.is_owner(5, 5) is True
    assert bot.is_owner(6, 5) is False
    assert bot.is_owner(None, 5) is False  # no effective user (channel post)


def test_owner_whitelist_unset_owner_refuses_everyone() -> None:
    # owner_telegram_id == 0 (unset) must never grant access to anyone.
    assert bot.is_owner(5, 0) is False
    assert bot.is_owner(0, 0) is False


# --- Gray-zone borderline card (notification-pacing amendment) ----------------


def test_borderline_message_compact_card_with_url_and_no_breakdown() -> None:
    """Gray-zone card: compact — score + title + company + canonical URL,
    Spanish, yellow banner, and NO ✓/~/✗ breakdown lines (the full evidence
    stays exclusive to the breakdown card)."""
    from cva.notifier import borderline_message

    text = borderline_message(
        score=64,
        threshold=65,
        title="ML Engineer",
        company="Acme",
        url="https://linkedin.com/jobs/view/4459804598",
    )

    assert text.startswith("🟡")
    assert "ML Engineer · Acme" in text
    assert "64/100" in text
    assert "umbral 65" in text
    assert "https://linkedin.com/jobs/view/4459804598" in text
    for forbidden in ("✓", "~", "✗", "«"):
        assert forbidden not in text  # no requirement/evidence breakdown


def test_borderline_message_missing_title_company_placeholders() -> None:
    """Triangulation: None title/company degrade to the new-job placeholders —
    never a literal 'None' in the owner's chat."""
    from cva.notifier import borderline_message

    text = borderline_message(
        score=50,
        threshold=65,
        title=None,
        company=None,
        url="https://linkedin.com/jobs/view/1",
    )

    assert "(sin título) · (sin empresa)" in text
    assert "None" not in text


# --- Round summary card (notification-pacing amendment D11; Spanish) ----------
# HTML-mode + clickable offer links (match-calibration D4): every dynamic
# value (beacon, title, company, url) is html.escape(..., quote=True)'d, and
# a discarded offer with a URL renders as a clickable anchor.


def test_round_summary_message_matches_owner_sample_shape() -> None:
    """The approved card: header + zone counts + discard list (score — title
    — company) rendered as clickable links, Spanish, per the owner's ask."""
    from cva.notifier import DiscardedOffer, round_summary_message

    text = round_summary_message(
        beacon="AI Barcelona (perfil AI/LLM)",
        analyzed=5,
        above=3,
        gray=2,
        below=2,
        pending=0,
        discarded=[
            DiscardedOffer(
                49, "Junior AI Engineer", "X", "https://linkedin.com/jobs/view/1"
            ),
            DiscardedOffer(44, "AI Developer", "Y", "https://linkedin.com/jobs/view/2"),
        ],
    )

    assert text == (
        "📊 Ronda 'AI Barcelona (perfil AI/LLM)': 5 analizadas\n"
        "🟢 3 sobre el umbral · 🟡 2 en zona gris · ⚪ 2 descartadas por umbral\n"
        "Descartadas (score — título — empresa):\n"
        '· 49/100 — <a href="https://linkedin.com/jobs/view/1">'
        "Junior AI Engineer</a> @ X\n"
        '· 44/100 — <a href="https://linkedin.com/jobs/view/2">'
        "AI Developer</a> @ Y"
    )


def test_round_summary_message_no_url_renders_escaped_title_without_anchor() -> None:
    """A discarded offer with no canonical URL keeps the plain escaped
    title/company text — no ``<a href="...">`` wrapper at all."""
    from cva.notifier import DiscardedOffer, round_summary_message

    text = round_summary_message(
        beacon="b",
        analyzed=1,
        above=0,
        gray=0,
        below=1,
        pending=0,
        discarded=[DiscardedOffer(30, "Junior Dev", "Acme", None)],
    )

    assert "· 30/100 — Junior Dev @ Acme" in text
    assert "<a " not in text


def test_round_summary_message_escapes_html_sensitive_characters() -> None:
    """``&``, ``<``, ``>``, ``"`` in beacon/title/company/url are
    ``html.escape(..., quote=True)``'d before landing in the HTML-mode
    message — no raw markup leaks into the sent text."""
    from cva.notifier import DiscardedOffer, round_summary_message

    text = round_summary_message(
        beacon='R&D "AI" <team>',
        analyzed=1,
        above=0,
        gray=0,
        below=1,
        pending=0,
        discarded=[
            DiscardedOffer(
                20,
                'Backend <Senior> "Dev"',
                'Acme & Co "Ltd"',
                'https://ex.com/?a=1&b="x"<y>',
            )
        ],
    )

    assert "R&amp;D &quot;AI&quot; &lt;team&gt;" in text
    assert "Backend &lt;Senior&gt; &quot;Dev&quot;" in text
    assert "Acme &amp; Co &quot;Ltd&quot;" in text
    assert 'href="https://ex.com/?a=1&amp;b=&quot;x&quot;&lt;y&gt;"' in text
    assert "<Senior>" not in text
    assert '"Ltd"' not in text
    assert "<team>" not in text


def test_round_summary_message_caps_discard_list_at_25_lines() -> None:
    """Spec: at most 25 discard lines, then '… y N más' (cap unaffected by
    the HTML/link change)."""
    from cva.notifier import DiscardedOffer, round_summary_message

    discards = [
        DiscardedOffer(40 + i, f"Oferta {i}", f"Empresa {i}", None) for i in range(30)
    ]
    text = round_summary_message(
        beacon="b",
        analyzed=30,
        above=0,
        gray=0,
        below=30,
        pending=0,
        discarded=discards,
    )

    lines = text.split("\n")
    discard_lines = [line for line in lines if line.startswith("· ")]
    assert len(discard_lines) == 25
    assert "· 40/100 — Oferta 0 @ Empresa 0" in lines
    assert "· 64/100 — Oferta 24 @ Empresa 24" in lines
    assert lines[-1] == "… y 5 más"
    assert "Oferta 25" not in text


def test_round_summary_message_pending_line_only_when_failures() -> None:
    """♻️ pendientes de reintento appears only when at least one job failed;
    a clean round has no ♻️ line and no discard section when nothing was
    below the gate."""
    from cva.notifier import round_summary_message

    with_pending = round_summary_message(
        beacon="b",
        analyzed=2,
        above=1,
        gray=0,
        below=0,
        pending=1,
        discarded=[],
    )
    assert "♻️ 1 pendientes de reintento" in with_pending
    assert "Descartadas" not in with_pending  # nothing below-gate to list

    clean = round_summary_message(
        beacon="b",
        analyzed=2,
        above=1,
        gray=1,
        below=0,
        pending=0,
        discarded=[],
    )
    assert "♻️" not in clean


def test_round_summary_message_null_safe_placeholders() -> None:
    """None title/company degrade to the card placeholders — never 'None' —
    and a ``None`` URL still renders with no anchor."""
    from cva.notifier import DiscardedOffer, round_summary_message

    text = round_summary_message(
        beacon="b",
        analyzed=1,
        above=0,
        gray=0,
        below=1,
        pending=0,
        discarded=[DiscardedOffer(30, None, None, None)],
    )

    assert "· 30/100 — (sin título) @ (sin empresa)" in text
    assert "None" not in text
    assert "<a " not in text


# --- apply-instruction warning block ---------------------------------------

_EMAIL = ApplyInstruction(
    kind="email",
    label="Enviar el CV también por email",
    snippet="…remite tu CV a talent@acme-data.example…",
    value="talent@acme-data.example",
)
_START = ApplyInstruction(
    kind="start_date",
    label="Indicar fecha de incorporación",
    snippet="…your earliest possible start date…",
    value=None,
)


def test_apply_warning_block_empty_is_blank() -> None:
    assert apply_warning_block([]) == ""


def test_apply_warning_block_format() -> None:
    assert apply_warning_block([_EMAIL, _START]) == (
        "⚠️ Ojo: esta oferta pide algo más al aplicar\n"
        "• 📧 Enviar el CV también por email: talent@acme-data.example — "
        "«…remite tu CV a talent@acme-data.example…»\n"
        "• 📅 Indicar fecha de incorporación — «…your earliest possible start date…»"
    )


def test_cards_unchanged_without_findings() -> None:
    result = _breakdown(80)
    assert breakdown_message(result, OFFER_ID, apply_findings=[]) == breakdown_message(
        result, OFFER_ID
    )
    assert cv_ready_message(Path("/x.pdf"), apply_findings=()) == cv_ready_message(
        Path("/x.pdf")
    )


def test_cards_append_block_after_blank_line() -> None:
    block = apply_warning_block([_EMAIL])
    result = _breakdown(80)
    assert breakdown_message(result, OFFER_ID, apply_findings=[_EMAIL]).endswith(
        "\n\n" + block
    )
    assert cv_ready_message(Path("/x.pdf"), apply_findings=[_EMAIL]).endswith(
        "\n\n" + block
    )
    assert borderline_message(
        score=60,
        threshold=65,
        title="t",
        company="c",
        url="u",
        apply_findings=[_EMAIL],
    ).endswith("\n\n" + block)
    assert judge_breakdown_message(
        result, None, OFFER_ID, apply_findings=[_EMAIL]
    ).endswith("\n\n" + block)


def test_block_never_pushes_card_over_telegram_limit() -> None:
    long = ApplyInstruction(kind="email", label="L" * 50, snippet="s" * 120, value="v")
    base = "a" * 4000
    out = with_apply_warnings(base, [long] * 6)
    assert len(out) <= 4096


def test_sent_keyboard_offers_regenerate_with_the_delivered_variant() -> None:
    """After a CV is delivered, [🔄 Rehacer CV] re-runs the same variant
    (e.g. after the base CVs changed); DS from old rows maps to AI."""
    from cva.notifier import sent_keyboard

    rows = sent_keyboard(OFFER_ID, cv_base="BACKEND_EN").inline_keyboard
    regen = [b for row in rows for b in row if "Rehacer" in b.text]
    assert [b.callback_data for b in regen] == [f"cv:{OFFER_ID}:BACKEND:en"]
    assert [
        b.callback_data
        for row in sent_keyboard(OFFER_ID, cv_base="AI_CA").inline_keyboard
        for b in row
        if "Rehacer" in b.text
    ] == [f"cv:{OFFER_ID}:AI:ca"]
    assert [
        b.callback_data
        for row in sent_keyboard(OFFER_ID, cv_base="DS_ES").inline_keyboard
        for b in row
        if "Rehacer" in b.text
    ] == [f"cv:{OFFER_ID}:AI:es"]
    assert not [
        b
        for row in sent_keyboard(OFFER_ID).inline_keyboard
        for b in row
        if "Rehacer" in b.text
    ]
