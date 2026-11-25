"""bot-i18n T2: notifier builders speak English and Spanish.

``GOLDEN_ES`` is the HEAD output (before i18n) of the public builders for the
inputs in :func:`scenarios`, captured from a ``git archive`` copy: with
``lang="es"`` (and with no ``lang``, the legacy default) every card, status line
and keyboard (labels and callback data) must stay byte-identical (S5).
"""

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from telegram import InlineKeyboardMarkup

from cva import notifier
from cva.beacons import BeaconPausedAlert
from cva.commands import help_text
from cva.families import BUILTIN_AI, BUILTIN_BACKEND, FamilyRegistry
from cva.gmail.sync import Proposal as GmailProposal
from cva.i18n import LANGS, Lang
from cva.llm import LlmResponseError
from cva.match import MatchResult, Requirement, RequirementMatch
from cva.match.apply_instructions import ApplyInstruction
from cva.match.gates import GateResult
from cva.match.interview_prep import CompanyFact
from cva.match.judge import FitItem, OfferJudgment, Wall
from cva.match.salary_research import SalaryOutcome, SalaryResearch, SalarySource
from cva.tracker import BeaconRow, OfferEvent, OfferRow


def _payloads(markup: InlineKeyboardMarkup) -> list[tuple[str, str]]:
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]


def _requirement(name: str, must_have: bool) -> RequirementMatch:
    return RequirementMatch(
        requirement=Requirement(
            name=name, category="skill", must_have=must_have, evidence="evidence text"
        ),
        claim_ids=["c1"],
        status="✓" if must_have else "~",
    )


def _result(score: int = 72, *, gates: bool = False) -> MatchResult:
    return MatchResult(
        score=score,
        threshold=65,
        breakdown=[_requirement("Python", True), _requirement("SQL", False)],
        stack_wall="Java stack" if gates else None,
        gates=[
            GateResult(gate="language", verdict="FAIL", reason="needs German"),
            GateResult(gate="location", verdict="FLAG", reason="onsite Madrid"),
        ]
        if gates
        else [],
    )


def _judgment(verdict: str = "apply") -> OfferJudgment:
    return OfferJudgment(
        score=81,
        verdict=verdict,  # type: ignore[arg-type]
        core_role="AI Engineer",
        fit=[FitItem(requirement="RAG", claim_ids=["a", "b"])],
        walls=[Wall(text="soft wall", hard=False), Wall(text="hard wall", hard=True)],
        cv_base="AI_ES",
        rationale="Good fit.",
    )


def _offer(estado: str = "enviada", **extra: Any) -> OfferRow:
    return OfferRow(
        id=7,
        estado=estado,
        empresa="Acme",
        posicion="Dev",
        match=70,
        source_data="{}",
        fecha_envio="2026-11-01",
        **extra,
    )


_EMAIL = ApplyInstruction(
    kind="email", label="Email too", snippet="send CV", value="a@b.example"
)
_TODAY = date(2026, 11, 18)
_BACKEND = FamilyRegistry(
    (BUILTIN_AI, BUILTIN_BACKEND),
    "ai",
)


def scenarios(kw: dict[str, Any]) -> dict[str, Any]:
    """Output of the public builders; ``kw`` is the language keyword (or none)."""
    ui = {"ui_lang": kw["lang"]} if kw else {}
    sal = SalaryResearch(
        min=30000,
        max=40000,
        median=35000,
        sources=[
            SalarySource(publisher="Pub", url="https://p.example", date="2026", n=3)
        ],
        confidence="medium",
        notes="a note",
    )
    outcome = SalaryOutcome(
        status="ok",
        research=sal,
        cached=True,
        checked_at="2026-11-18",
        company="Acme",
        position="Dev",
        city="Madrid",
        level="junior",
    )
    rows = [
        OfferRow(
            id=3,
            estado="analizada",
            posicion=None,
            empresa=None,
            match=None,
            source_data="{}",
            perfil="backend",
        ),
        OfferRow(
            id=4, estado="entrevista_tecnica", posicion="X", match=55, source_data="{}"
        ),
    ]
    events = [
        OfferEvent(
            id=1, offer_id=7, at="2026-11-02", kind="estado", estado="screening"
        ),
        OfferEvent(id=2, offer_id=7, at="2026-11-03", kind="followup"),
        OfferEvent(id=3, offer_id=7, at="2026-11-04", kind="note", note="good"),
    ]
    due = [
        OfferRow(
            id=1,
            estado="analizada",
            posicion="P1",
            empresa="E1",
            fecha_limite="2026-11-18",
            url_oferta="https://u",
            source_data="{}",
        ),
        OfferRow(
            id=2,
            estado="analizada",
            posicion=None,
            empresa=None,
            fecha_limite="2026-11-19",
            source_data="{}",
        ),
        OfferRow(
            id=5,
            estado="analizada",
            posicion="P5",
            empresa="E5",
            fecha_limite="2026-11-23",
            source_data="{}",
        ),
    ]
    mail = GmailProposal(
        message_id="m1",
        offer_id=7,
        company="Acme",
        subject="Hello",
        estado="entrevista_tecnica",
    )
    http = LlmResponseError("HTTP 500 boom", status=500)
    return {
        "unknown_role": notifier.unknown_role_message("DS", "AI", **kw),
        "new_job": notifier.new_job_message(
            title=None, company="Acme", age="2h", job_id="9", threshold=65, **kw
        ),
        "new_job_url": notifier.new_job_message(
            title="T",
            company=None,
            age="2h",
            job_id="9",
            threshold=65,
            url="https://x.example/1",
            **kw,
        ),
        "new_job_kb": _payloads(notifier.new_job_keyboard("9", **kw)),
        "also_on": notifier.with_also_on("card", ["A", "B<"], parse_mode="HTML", **kw),
        "also_on_none": notifier.with_also_on("card", [], **kw),
        "apply_block": notifier.apply_warning_block([_EMAIL], **kw),
        "date_lines": [
            notifier.date_card_lines("2026-09-01", "2026-11-18", _TODAY, **kw),
            notifier.date_card_lines(None, "2026-11-19", _TODAY, **kw),
            notifier.date_card_lines(None, "2026-11-23", _TODAY, **kw),
        ],
        # pending-maintenance S3: only a LinkedIn offer with the badge.
        "easy_apply_line": [
            notifier.easy_apply_line("linkedin", True, **kw),
            notifier.easy_apply_line("linkedin", None, **kw),
            notifier.easy_apply_line("infojobs", True, **kw),
        ],
        "deadline_alert": notifier.deadline_alert_message(due, _TODAY, **kw),
        "deadline_batch": notifier.deadline_alert_batch(due * 6, _TODAY, **kw),
        "breakdown": notifier.breakdown_message(
            _result(),
            12,
            url="https://u",
            apply_findings=[_EMAIL],
            salary_line="💶 x",
            **kw,
        ),
        "breakdown_low": notifier.breakdown_message(_result(50), 12, **kw),
        "borderline": notifier.borderline_message(
            score=60,
            threshold=65,
            title=None,
            company="Acme",
            url="https://u",
            apply_findings=[_EMAIL],
            **kw,
        ),
        "round": notifier.round_summary_message(
            beacon="b<1>",
            analyzed=9,
            above=2,
            gray=1,
            below=2,
            pending=1,
            discarded=[
                notifier.DiscardedOffer(40, "T1", "C1", "https://u1"),
                notifier.DiscardedOffer(30, None, None, None),
            ],
            walled=7,
            walled_offers=[notifier.WalledOffer("reason", "W", None, "https://w")] * 7,
            closed=6,
            closed_offers=[notifier.ClosedOffer("Cl", "Co", None)] * 6,
            duplicates=8,
            duplicate_offers=[notifier.DuplicateOffer("D", "Dc", 3, "https://d")] * 6,
            **kw,
        ),
        "round_min": notifier.round_summary_message(
            beacon="b",
            analyzed=0,
            above=0,
            gray=0,
            below=0,
            pending=0,
            discarded=[],
            **kw,
        ),
        "judge_none": notifier.judge_breakdown_message(
            _result(gates=True), None, 12, judge_fallback=True, **kw
        ),
        "judge": notifier.judge_breakdown_message(
            _result(), _judgment(), 12, url="https://u", salary_line="💶 x", **kw
        ),
        "judge_caveats": notifier.judge_breakdown_message(
            _result(), _judgment("apply_with_caveats"), 12, **kw
        ),
        "judge_discard": notifier.judge_breakdown_message(
            _result(), _judgment("discard"), 12, **kw
        ),
        "cover_sources": notifier.cover_letter_sources_message(
            [CompanyFact(fact="f", source_url="https://s")], **kw
        ),
        "cover_sources_none": notifier.cover_letter_sources_message([], **kw),
        "cover_kb": _payloads(notifier.cover_letter_keyboard(5, "a", 2, **kw)),
        "no_cv": [
            notifier.no_cv_line("Portal", **kw),
            notifier.no_cv_reply("Portal", **kw),
            notifier.with_no_cv_line("card", "Portal", **kw),
        ],
        "cv_kb": _payloads(notifier.cv_keyboard(5, **ui)),
        "cv_kb_asked": _payloads(
            notifier.cv_keyboard(
                5,
                role="backend",
                lang="en",
                cover_letter_asked=True,
                families=_BACKEND,
                **ui,
            )
        ),
        "cv_kb_nocv": _payloads(notifier.cv_keyboard(5, cv_needed=False, **ui)),
        "sent_kb": _payloads(notifier.sent_keyboard(5, cv_base="BACKEND_EN", **kw)),
        "sent_kb_plain": _payloads(
            notifier.sent_keyboard(5, cover_letter_asked=True, **kw)
        ),
        "cv_ready": [
            notifier.cv_ready_message(
                Path("/x/cv.pdf"),
                pages=1,
                url="https://u",
                apply_findings=[_EMAIL],
                salary_line="💶 x",
                **kw,
            ),
            notifier.cv_ready_message(Path("/x/cv.pdf"), **kw),
        ],
        "salary": [
            notifier.salary_research_message(outcome, **kw),
            notifier.salary_research_message(
                SalaryOutcome(
                    status="ok",
                    research=sal.model_copy(update={"median": None, "notes": ""}),
                ),
                **kw,
            ),
            notifier.salary_research_message(
                SalaryOutcome(
                    status="no_data",
                    cached=True,
                    checked_at="d",
                    company="A",
                    position="P",
                    city="C",
                    level="L",
                ),
                **kw,
            ),
            notifier.salary_research_message(SalaryOutcome(status="unavailable"), **kw),
        ],
        "prep_kb": _payloads(notifier.prep_keyboard(5, **kw)),
        "prep_stage_kb": _payloads(notifier.prep_stage_keyboard(5, "tecnica", **kw)),
        "outcome_kb": _payloads(notifier.outcome_keyboard(5, **kw)),
        "outcome_kb_prep": _payloads(notifier.outcome_keyboard(5, "screening", **kw)),
        "sent_outcome_kb": _payloads(notifier.sent_outcome_keyboard(5, **kw)),
        "outcome_msg": notifier.outcome_message(_offer(), events, **kw),
        "outcome_msg_empty": notifier.outcome_message(
            OfferRow(id=8, estado="enviada", source_data="{}"), [], **kw
        ),
        "outcome_note": notifier.outcome_note_prompt(**kw),
        "followup_kb": _payloads(notifier.followup_keyboard(5, **kw)),
        "followup_prop_kb": _payloads(notifier.followup_proposal_keyboard(5, **kw)),
        "followup_msg": notifier.followup_message(
            _offer(), days_silent=9, number=1, draft="Hi!", warnings=["w1", "w2"], **kw
        ),
        "followup_msg_nodraft": notifier.followup_message(
            _offer(), days_silent=1, number=2, draft=None, warnings=[], **kw
        ),
        "followup_prop": notifier.followup_proposal_message(
            _offer(), days_silent=14, **kw
        ),
        "confirm": [
            notifier.sent_message(5, 70, **kw),
            notifier.discard_message(5, **kw),
            notifier.discard_no_send_message(**kw),
            notifier.not_found_message(**kw),
            notifier.offer_closed_message(**kw),
            notifier.paste_prompt(**kw),
        ],
        "errors": [
            notifier.failure_reason(None, **kw),
            notifier.failure_reason(http, **kw),
            notifier.failure_reason(LlmResponseError("bad payload"), **kw),
            notifier.failure_reason(ValueError("plain"), **kw),
            notifier.analyze_error_message("why", **kw),
            notifier.analyze_failure_message(LlmResponseError("x", status=429), **kw),
            notifier.analyze_failure_message(http, **kw),
            notifier.backstop_error_message("oops", **kw),
        ],
        "bridge_fail": notifier.bridge_auto_failure_message(
            http, title="T", company="C", job_id="9", threshold=65, **kw
        ),
        "help": notifier.help_message(**kw),
        "status_lines": [
            notifier.offer_status_line(rows[0], families=_BACKEND, **kw),
            notifier.offer_status_line(rows[1], also_on=["X", "Y"], **kw),
        ],
        "beacons": [
            notifier.beacons_list_message([], **kw),
            notifier.beacons_list_message(
                [
                    BeaconRow(
                        id=1, name="n", url="u", status="paused", consecutive_walls=3
                    ),
                    BeaconRow(id=2, name="m", url="v", last_checked_at="2026-11-01"),
                ],
                **kw,
            ),
        ],
        "beacon_actions": [
            notifier.beacon_action_message("resume", 3, **kw),
            notifier.beacon_action_message("disable", 3, **kw),
        ],
        "beacon_paused": notifier.beacon_paused_message(
            BeaconPausedAlert(
                beacon_id=3, beacon_name="n", wall_kind="js", consecutive_walls=3
            ),
            **kw,
        ),
        "beacon_kb": _payloads(notifier.beacon_alert_keyboard(3, **kw)),
        "gmail": notifier.gmail_proposal_message(mail, **kw),
        "gmail_kb": _payloads(notifier.gmail_proposal_keyboard("m1", **kw)),
    }


GOLDEN_ES: dict[str, Any] = {
    "unknown_role": "⚠️ La variante DS ya no está en tu perfil: uso AI.",
    "new_job": "🔔 (sin título) · Acme · 2h\nhttps://linkedin.com/jobs/view/9\nUmbral Top-Choice: 65",
    "new_job_url": "🔔 T · (sin empresa) · 2h\nhttps://x.example/1\nUmbral Top-Choice: 65",
    "new_job_kb": [
        [
            "🔎 Analizar",
            "an:9",
        ],
    ],
    "also_on": "card\n📍 También en: A, B&lt;",
    "also_on_none": "card",
    "apply_block": "⚠️ Ojo: esta oferta pide algo más al aplicar\n• 📧 Email too: a@b.example — «send CV»",
    "date_lines": [
        "⏰ Cierra hoy\n🕰️ Publicada hace 78 días (puede estar cerrada)",
        "⏰ Cierra en 1 día (19/11)",
        "⏰ Cierra en 5 días (23/11)",
    ],
    "easy_apply_line": ["⚡ Solicitud sencilla", "", ""],
    "deadline_alert": "⏰ Ofertas por cerrar que aún no has enviado:\n• P1 · E1 — cierra hoy\n  https://u\n• (sin título) · (sin empresa) — cierra en 1 día\n• P5 · E5 — cierra en 5 días",
    "deadline_batch": [
        "⏰ Ofertas por cerrar que aún no has enviado:\n• P1 · E1 — cierra hoy\n  https://u\n• (sin título) · (sin empresa) — cierra en 1 día\n• P5 · E5 — cierra en 5 días\n• P1 · E1 — cierra hoy\n  https://u\n• (sin título) · (sin empresa) — cierra en 1 día\n• P5 · E5 — cierra en 5 días\n• P1 · E1 — cierra hoy\n  https://u\n• (sin título) · (sin empresa) — cierra en 1 día\n• P5 · E5 — cierra en 5 días\n• P1 · E1 — cierra hoy\n  https://u\n• (sin título) · (sin empresa) — cierra en 1 día\n• P5 · E5 — cierra en 5 días\n• P1 · E1 — cierra hoy\n  https://u\n• (sin título) · (sin empresa) — cierra en 1 día\n• P5 · E5 — cierra en 5 días\n… y 3 más",
        15,
    ],
    "breakdown": "📊 Oferta #12 — puntuación 72/100 (umbral 65): supera el umbral ✅\nhttps://u\n\n✓ Python (imprescindible) — «evidence text»\n~ SQL (deseable) — «evidence text»\n\n💶 x\n\n⚠️ Ojo: esta oferta pide algo más al aplicar\n• 📧 Email too: a@b.example — «send CV»",
    "breakdown_low": "📊 Oferta #12 — puntuación 50/100 (umbral 65): no supera el umbral (Top-Choice) ❌\n\n✓ Python (imprescindible) — «evidence text»\n~ SQL (deseable) — «evidence text»",
    "borderline": "🟡 (sin título) · Acme — puntuación 60/100 (umbral 65)\nhttps://u\nCerca del umbral Top-Choice: revísala y genera tu CV si encaja.\n\n⚠️ Ojo: esta oferta pide algo más al aplicar\n• 📧 Email too: a@b.example — «send CV»",
    "round": '📊 Ronda \'b&lt;1&gt;\': 9 analizadas\n🟢 2 sobre el umbral · 🟡 1 en zona gris · ⚪ 2 descartadas por umbral\n🧱 7 descartadas por muro (stack, elegibilidad o idioma)\n· reason — <a href="https://w">W</a> @ (sin empresa)\n· reason — <a href="https://w">W</a> @ (sin empresa)\n· reason — <a href="https://w">W</a> @ (sin empresa)\n· reason — <a href="https://w">W</a> @ (sin empresa)\n· reason — <a href="https://w">W</a> @ (sin empresa)\n… y 2 más\n🔒 6 ofertas cerradas (descartadas sin analizar)\n· Cl @ Co\n· Cl @ Co\n· Cl @ Co\n· Cl @ Co\n· Cl @ Co\n… y 1 más\n♻️ 8 duplicadas (ya vistas en otra fuente)\n· <a href="https://d">D</a> @ Dc → #3\n· <a href="https://d">D</a> @ Dc → #3\n· <a href="https://d">D</a> @ Dc → #3\n· <a href="https://d">D</a> @ Dc → #3\n· <a href="https://d">D</a> @ Dc → #3\n… y 3 más\n♻️ 1 pendientes de reintento\nDescartadas (score — título — empresa):\n· 40/100 — <a href="https://u1">T1</a> @ C1\n· 30/100 — (sin título) @ (sin empresa)',
    "round_min": "📊 Ronda 'b': 0 analizadas\n🟢 0 sobre el umbral · 🟡 0 en zona gris · ⚪ 0 descartadas por umbral",
    "judge_none": "📊 Oferta #12 — puntuación 72/100 (umbral 65): supera el umbral ✅\n\n✓ Python (imprescindible) — «evidence text»\n~ SQL (deseable) — «evidence text»\n⚠️ El juicio LLM falló; se muestra el score por palabras clave.\n🚫 Muro: Java stack\n🚫 Muro: needs German\n⚠️ onsite Madrid",
    "judge": "✅ Oferta #12 — puntuación 81/100 (aplicar)\nhttps://u\nRol principal: AI Engineer\nCV base sugerido: AI_ES\nFit principal:\n· RAG (a, b)\nMuros:\n🚫 hard wall\n⚠️ soft wall\n\nGood fit.\n\nDetalle por palabra clave:\n📊 Oferta #12 — puntuación 72/100 (umbral 65): supera el umbral ✅\n\n✓ Python (imprescindible) — «evidence text»\n~ SQL (deseable) — «evidence text»\n\n💶 x",
    "judge_caveats": "🟡 Oferta #12 — puntuación 81/100 (aplicar con matices)\nRol principal: AI Engineer\nCV base sugerido: AI_ES\nFit principal:\n· RAG (a, b)\nMuros:\n🚫 hard wall\n⚠️ soft wall\n\nGood fit.\n\nDetalle por palabra clave:\n📊 Oferta #12 — puntuación 72/100 (umbral 65): supera el umbral ✅\n\n✓ Python (imprescindible) — «evidence text»\n~ SQL (deseable) — «evidence text»",
    "judge_discard": "⛔ Oferta #12 — puntuación 81/100 (descartar)\nRol principal: AI Engineer\nCV base sugerido: AI_ES\nFit principal:\n· RAG (a, b)\nMuros:\n🚫 hard wall\n⚠️ soft wall\n\nGood fit.\n\nDetalle por palabra clave:\n📊 Oferta #12 — puntuación 72/100 (umbral 65): supera el umbral ✅\n\n✓ Python (imprescindible) — «evidence text»\n~ SQL (deseable) — «evidence text»",
    "cover_sources": "🔎 Fuentes de la empresa (usadas en el párrafo 2):\n- f\n  https://s",
    "cover_sources_none": "ℹ️ No encontré datos fiables de la empresa: el párrafo 2 se apoya solo en la oferta y queda más genérico. Revísalo antes de usarla.",
    "cover_kb": [
        [
            "✅ Usar esta",
            "clc:5:2:a",
        ],
        [
            "🗑 No usar",
            "cld:5",
        ],
    ],
    "no_cv": [
        "📨 Portal: se aplica con tu perfil del portal (sin CV)",
        "ℹ️ Esta oferta es de Portal: se aplica con tu perfil del portal, no hace falta CV.",
        "card\n📨 Portal: se aplica con tu perfil del portal (sin CV)",
    ],
    "cv_kb": [
        [
            "📄 Generar CV",
            "cv:5:AI:es",
        ],
        [
            "Idioma: ES",
            "lang:5:AI:es",
        ],
        [
            "Variante: AI",
            "variant:5:AI:es",
        ],
        [
            "💶 Investigar salario",
            "sal:5",
        ],
        [
            "✉️ Carta de presentación",
            "cl:5",
        ],
        [
            "✅ Marcar enviada",
            "sent:5",
        ],
        [
            "🗑 Descartar",
            "discard:5",
        ],
    ],
    "cv_kb_asked": [
        [
            "📄 Generar CV",
            "cv:5:BACKEND:en",
        ],
        [
            "Idioma: EN",
            "lang:5:BACKEND:en",
        ],
        [
            "Variante: Backend",
            "variant:5:BACKEND:en",
        ],
        [
            "💶 Investigar salario",
            "sal:5",
        ],
        [
            "✉️ Carta de presentación (la piden)",
            "cl:5",
        ],
        [
            "✅ Marcar enviada",
            "sent:5",
        ],
        [
            "🗑 Descartar",
            "discard:5",
        ],
    ],
    "cv_kb_nocv": [
        [
            "💶 Investigar salario",
            "sal:5",
        ],
        [
            "✉️ Carta de presentación",
            "cl:5",
        ],
        [
            "✅ Marcar enviada",
            "sent:5",
        ],
        [
            "🗑 Descartar",
            "discard:5",
        ],
    ],
    "sent_kb": [
        [
            "✅ Marcar enviada",
            "sent:5",
        ],
        [
            "🗑 Descartar",
            "disc:5",
        ],
        [
            "🔄 Rehacer CV",
            "cv:5:BACKEND:en",
        ],
        [
            "💶 Investigar salario",
            "sal:5",
        ],
        [
            "✉️ Carta de presentación",
            "cl:5",
        ],
    ],
    "sent_kb_plain": [
        [
            "✅ Marcar enviada",
            "sent:5",
        ],
        [
            "🗑 Descartar",
            "disc:5",
        ],
        [
            "💶 Investigar salario",
            "sal:5",
        ],
        [
            "✉️ Carta de presentación (la piden)",
            "cl:5",
        ],
    ],
    "cv_ready": [
        "📄 CV listo: /x/cv.pdf (1 pág.). Márcalo como enviada cuando lo envíes.\n🔗 Oferta: https://u\n\n💶 x\n\n⚠️ Ojo: esta oferta pide algo más al aplicar\n• 📧 Email too: a@b.example — «send CV»",
        "📄 CV listo: /x/cv.pdf. Márcalo como enviada cuando lo envíes.",
    ],
    "salary": [
        "💶 Salario investigado — Acme · Dev · Madrid · junior\n(resultado guardado de hoy, 2026-11-18; no se repite la búsqueda)\nRango: 30.000–40.000 € brutos/año (mediana 35.000 €) · confianza medium\nFuentes:\n· Pub (2026, n=3) https://p.example\nNotas: a note\n⚠️ Estimación a partir de fuentes web; no es un dato de la empresa.",
        "💶 Salario investigado —  ·  ·  · \nRango: 30.000–40.000 € brutos/año · confianza medium\nFuentes:\n· Pub (2026, n=3) https://p.example\n⚠️ Estimación a partir de fuentes web; no es un dato de la empresa.",
        "💶 Sin datos fiables de salario para A · P (C · L). No se encontraron fuentes publicadas suficientes.\n(resultado guardado de hoy, d; no se repite la búsqueda)",
        "💶 Investigación salarial no disponible con este proveedor.",
    ],
    "prep_kb": [
        [
            "🎯 Preparar entrevista",
            "prep:5",
        ],
    ],
    "prep_stage_kb": [
        [
            "RR. HH.",
            "prep:5:rrhh",
        ],
        [
            "⭐ Técnica",
            "prep:5:tecnica",
        ],
        [
            "Final",
            "prep:5:final",
        ],
    ],
    "outcome_kb": [
        [
            "📞 Screening",
            "oe:5:screening",
        ],
        [
            "💻 Entrevista técnica",
            "oe:5:entrevista_tecnica",
        ],
        [
            "🏁 Entrevista final",
            "oe:5:entrevista_final",
        ],
        [
            "🎉 Oferta",
            "oe:5:oferta",
        ],
        [
            "✅ Aceptada",
            "oe:5:aceptada",
        ],
        [
            "❌ Rechazada",
            "oe:5:rechazada",
        ],
        [
            "🤐 Sin respuesta",
            "oe:5:sin_respuesta",
        ],
        [
            "↩️ Retirada",
            "oe:5:retirada",
        ],
    ],
    "outcome_kb_prep": [
        [
            "📞 Screening",
            "oe:5:screening",
        ],
        [
            "💻 Entrevista técnica",
            "oe:5:entrevista_tecnica",
        ],
        [
            "🏁 Entrevista final",
            "oe:5:entrevista_final",
        ],
        [
            "🎉 Oferta",
            "oe:5:oferta",
        ],
        [
            "✅ Aceptada",
            "oe:5:aceptada",
        ],
        [
            "❌ Rechazada",
            "oe:5:rechazada",
        ],
        [
            "🤐 Sin respuesta",
            "oe:5:sin_respuesta",
        ],
        [
            "↩️ Retirada",
            "oe:5:retirada",
        ],
        [
            "🎯 Preparar entrevista",
            "prep:5",
        ],
    ],
    "sent_outcome_kb": [
        [
            "📝 Resultado",
            "out:5",
        ],
    ],
    "outcome_msg": "📝 Resultado de #7 · Acme · Dev\nEstado: enviada · enviada 2026-11-01\n2026-11-02 · estado → screening\n2026-11-03 · seguimiento enviado\n2026-11-04 · nota: good",
    "outcome_msg_empty": "📝 Resultado de #8 · (sin empresa) · (sin posición)\nEstado: enviada\nSin eventos todavía.",
    "outcome_note": "¿Quieres añadir una nota (feedback)? Escríbela o /skip",
    "followup_kb": [
        [
            "✅ Enviado",
            "fu:5:ok",
        ],
        [
            "⏭️ Saltar",
            "fu:5:skip",
        ],
        [
            "🔕 Sin respuesta",
            "fu:5:none",
        ],
    ],
    "followup_prop_kb": [
        [
            "🔕 Sin respuesta",
            "fu:5:none",
        ],
    ],
    "followup_msg": "📬 Seguimiento 1 de 2 · #7 · Dev · Acme\nLlevas 9 días sin noticias desde tu última actividad.\n\nBorrador (envíalo tú si te encaja):\n\nHi!\n\n⚠️ Revisa el borrador:\n• w1\n• w2",
    "followup_msg_nodraft": "📬 Seguimiento 2 de 2 · #7 · Dev · Acme\nLlevas 1 días sin noticias desde tu última actividad.\n\nNo he podido preparar un borrador esta vez; escríbelo tú.",
    "followup_prop": "🔕 #7 · Dev · Acme\n14 días sin respuesta y ya hiciste 2 seguimientos. ¿La marco como sin respuesta?",
    "confirm": [
        "✅ Oferta #5 marcada como enviada (match 70).",
        "🗑 Oferta #5 descartada. No se generará ningún PDF.",
        "🗑 Oferta descartada — no se registra envío.",
        "🔒 Esa oferta parece haber sido retirada o cerrada (404); no hay nada que analizar. Si crees que sigue activa, pega el texto con /analyze.",
        "🔒 La oferta está cerrada (lo dice el propio texto): descartada, sin analizar.",
        "⚠️ No pude acceder a la oferta (muro/bloqueo). Pega el texto de la oferta como mensaje y la analizo.",
    ],
    "errors": [
        "error desconocido",
        "el LLM devolvió HTTP 500 — revisa CVA_LLM_* en .env",
        "el LLM falló: bad payload — revisa CVA_LLM_* en .env",
        "plain",
        "❌ El análisis falló: why — pega el texto de nuevo cuando lo corrijas.",
        "❌ El análisis falló: límite de peticiones del LLM (429) — espera unos minutos y vuelve a pegar la oferta.",
        "❌ El análisis falló: el LLM devolvió HTTP 500 — revisa CVA_LLM_* en .env — pega el texto de nuevo cuando lo corrijas.",
        "⚠️ Ha fallado el procesamiento de tu mensaje: oops",
    ],
    "bridge_fail": "🔔 T · C · reciente\nhttps://linkedin.com/jobs/view/9\nUmbral Top-Choice: 65\n⚠️ El análisis automático falló (el LLM devolvió HTTP 500 — revisa CVA_LLM_* en .env). Pulsa Analizar para reintentar.",
    "help": 'Asistente de seguimiento de ofertas.\n\n🔎 Analizar\n/analyze [url]\n  Puntúa una oferta contra tu perfil. Sin enlace, pide el texto.\n  Ej.: /analyze https://www.linkedin.com/jobs/view/123456\n/rescatar <id>\n  Reenvía la tarjeta completa de una oferta descartada, sin tocar su nota.\n  Ej.: /rescatar 42\n\n📄 CVs y cartas\n/cv <id> [AI|BACKEND] [es|en|ca]\n  PDF del CV para la oferta #id. Rol e idioma opcionales (si faltan, se eligen solos).\n  Ej.: /cv 42 BACKEND en\n/cartabase [es|en|ca] (o /base)\n  Carta sin oferta concreta, lista para copiar.\n  Ej.: /cartabase en\n\n📊 Seguimiento\n/estado [id]\n  Sin id, las últimas 10 ofertas; con id, el estado de una.\n  Ej.: /estado 42\n/sent <id>\n  Registra que enviaste la candidatura de la oferta #id.\n  Ej.: /sent 42\n/outcome <id>\n  Historial de la enviada #id y botones para anotar cómo va. Tras 10 días sin noticias propone un seguimiento (lo envías tú).\n  Ej.: /outcome 42\n/calibracion\n  Resultados que calibran la nota; cada uno se puede desactivar.\n/skip\n  Tras un resultado el bot pide una nota; /skip la omite.\n/export\n  Excel solo con las enviadas; te dice dónde quedó.\n/panel\n  HTML sin conexión: métricas, gráficos, alertas, tabla filtrable y descartadas (/rescatar).\n\n🔭 Beacons y búsquedas\n/keywords [perfil]\n  Muestra las keywords vistas en ofertas y su frecuencia (filtra por perfil).\n  Ej.: /keywords backend\n/beacons\n  Cada beacon con estado, muros seguidos y última comprobación.\n/beacon resume|disable <id>\n  resume reactiva un beacon pausado; disable lo desactiva.\n  Ej.: /beacon resume 3\n\n🎤 Entrevistas\n/star\n  Pregunta situación, tarea, acción y resultado de tus experiencias; luego las lista (✅ completa, ✏️ faltan partes) para ver, editar o borrar. /skip deja un campo vacío.\n/prep <id>\n  Elige fase (RR. HH., Técnica, Final): HTML con temas, huecos, STAR, salario y preguntas.\n  Ej.: /prep 42\n\nℹ️ Otros\n/pausa\n  Detiene todo lo automático; /analyze sigue.\n/reanudar\n  Vuelve a activar lo pausado.\n/configurar [sección]\n  Configuración guiada del perfil, o solo una sección (salario, cvs...).\n  Ej.: /configurar salario\n/help\n  Comandos y botones (también /start).\n\n🔘 Botones de las tarjetas\n🔎 Analizar — en el aviso de una oferta nueva: la analiza igual que /analyze.\n📄 Generar CV — crea el PDF con el rol e idioma elegidos en la tarjeta.\nIdioma: ES/EN/CA — cambia el idioma del CV (Backend solo ES/EN).\nVariante: AI/Backend — alterna el perfil del CV entre AI y Backend.\n💶 Investigar salario — busca en la web un rango salarial con fuentes (puede no encontrar nada).\n✉️ Carta de presentación — sobre tu carta base, con la empresa investigada (y sus fuentes) ("(la piden)" = la oferta pide carta).\n✅ Usar esta — guarda la carta en la oferta.\n🗑 No usar — quita la carta del chat.\n🗑 Descartar — descarta la oferta; si ya hay CV, no se registra envío.\n✅ Marcar enviada — igual que /sent: la oferta pasa a «enviada».\n🔄 Rehacer CV — vuelve a generar el mismo CV (por si cambiaron los CV base).\n📝 Resultado — en una oferta enviada: abre /outcome para anotar cómo va la candidatura.\nEstados del resultado — Screening, Entrevista técnica, Entrevista final, Oferta, Aceptada, Rechazada, Sin respuesta, Retirada: guardan el estado y piden nota.\nCalibrar: Sí · No — tras una entrevista o rechazo: usa esa oferta (o no) para calibrar la nota. Motivo del rechazo: Requisitos, Stack, Idioma u Otro / no sé (no calibra).\n✅ Enviado — en un seguimiento: anota que enviaste tú el mensaje (máximo 2).\n⏭️ Saltar — en un seguimiento: lo pospone 7 días.\n🔕 Sin respuesta — en un seguimiento: marca la oferta como sin respuesta.\n➕ Experiencia o proyecto — en /star: elige de qué contar una historia (también «Añadir otra»).\n👁 Ver · ✏️ Editar · 🗑 Borrar — en /star: ver una historia, rehacer un campo (Situación, Tarea, Acción, Resultado, Uso) o borrarla (Sí, borrar / Cancelar). Volver.\n🎯 Preparar entrevista — en /outcome y al pasar a screening o entrevista: elige fase y recibe el HTML de /prep.\n▶️ Reactivar — en el aviso de beacon pausado: lo reactiva.\n⏹ Desactivar — en el aviso de beacon pausado: deja de comprobarlo.',
    "status_lines": [
        "#3 · analizada · match — · (sin posición) [backend]",
        "#4 · entrevista_tecnica · match 55 · X · También en: X, Y",
    ],
    "beacons": [
        "🔭 No hay beacons configurados.",
        "🔭 Beacons:\n#1 n · paused · muros: 3 · última comprobación: nunca\n#2 m · active · muros: 0 · última comprobación: 2026-11-01",
    ],
    "beacon_actions": [
        "✅ Beacon #3 reactivado (contador de muros a cero).",
        "⏹ Beacon #3 desactivado (no se volverá a comprobar).",
    ],
    "beacon_paused": "🚨 Beacon «n» pausado tras 3 muros consecutivos (último: js).",
    "beacon_kb": [
        [
            "▶️ Reactivar",
            "beacon_resume:3",
        ],
        [
            "⏹ Desactivar",
            "beacon_disable:3",
        ],
    ],
    "gmail": "📧 Acme · «Hello»: parece entrevista técnica.\n¿Marcar la oferta #7 como entrevista técnica?",
    "gmail_kb": [
        [
            "Sí",
            "gm:y:m1",
        ],
        [
            "Otro estado",
            "gm:o:m1",
        ],
        [
            "Ignorar",
            "gm:i:m1",
        ],
    ],
}


def _norm(value: Any) -> Any:
    """JSON-style view (tuples as lists) so HEAD's JSON capture compares equal."""
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_norm(v) for v in value]
    return value


def _keyboards(out: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in out.items() if k.endswith("_kb") or "_kb_" in k}


def test_scenarios_cover_the_golden_keys() -> None:
    assert list(scenarios({})) == list(GOLDEN_ES)


@pytest.mark.parametrize("kw", [{"lang": "es"}, {}], ids=["explicit-es", "default"])
def test_spanish_is_byte_identical_to_head(kw: dict[str, Any]) -> None:
    got = _norm(scenarios(kw))
    for name, expected in GOLDEN_ES.items():
        assert got[name] == expected, name


def test_english_differs_from_spanish_everywhere() -> None:
    es, en = _norm(scenarios({"lang": "es"})), _norm(scenarios({"lang": "en"}))
    # ``also_on_none`` is the bare card in both languages (no line to translate).
    same = [n for n in es if es[n] == en[n] and n != "also_on_none"]
    assert not same, same


def test_english_cards_read_in_english() -> None:
    en = scenarios({"lang": "en"})
    assert en["new_job"].splitlines()[0] == "🔔 (untitled) · Acme · 2h"
    assert en["new_job"].endswith("Top-Choice threshold: 65")
    assert en["breakdown"].startswith(
        "📊 Offer #12 — score 72/100 (threshold 65): above the threshold ✅"
    )
    assert "(must-have)" in en["breakdown"] and "(nice-to-have)" in en["breakdown"]
    assert en["date_lines"] == [
        "⏰ Closes today\n🕰️ Posted 78 days ago (may be closed)",
        "⏰ Closes in 1 day (19/11)",
        "⏰ Closes in 5 days (23/11)",
    ]
    assert en["easy_apply_line"] == ["⚡ Easy Apply", "", ""]
    assert en["round"].splitlines()[0] == "📊 Round 'b&lt;1&gt;': 9 analyzed"
    assert "… and 2 more" in en["round"]
    assert en["judge"].splitlines()[0] == "✅ Offer #12 — score 81/100 (apply)"
    assert "Keyword detail:" in en["judge"]
    assert en["cv_ready"][0].startswith("📄 CV ready: /x/cv.pdf (1 pp.). Mark it")
    assert en["salary"][3] == "💶 Salary research is not available with this provider."
    assert en["status_lines"][1] == (
        "#4 · technical interview · match 55 · X · Also on: X, Y"
    )
    assert en["gmail"] == (
        "📧 Acme · «Hello»: looks like technical interview.\n"
        "Mark offer #7 as technical interview?"
    )
    assert en["errors"][1] == "the LLM returned HTTP 500 — check CVA_LLM_* in .env"
    assert en["outcome_note"] == "Want to add a note (feedback)? Type it or /skip"


def test_english_button_labels() -> None:
    en = scenarios({"lang": "en"})
    assert [text for text, _ in en["cv_kb"]] == [
        "📄 Generate CV",
        "Language: ES",
        "Variant: AI",
        "💶 Research salary",
        "✉️ Cover letter",
        "✅ Mark as sent",
        "🗑 Discard",
    ]
    assert en["cv_kb_asked"][4][0] == "✉️ Cover letter (requested)"
    assert [text for text, _ in en["outcome_kb"]][:3] == [
        "📞 Screening",
        "💻 Technical interview",
        "🏁 Final interview",
    ]
    assert [text for text, _ in en["prep_stage_kb"]] == ["HR", "⭐ Technical", "Final"]
    assert [text for text, _ in en["gmail_kb"]] == ["Yes", "Other status", "Ignore"]


def test_callback_data_never_depends_on_the_language() -> None:
    es, en = _norm(scenarios({"lang": "es"})), _norm(scenarios({"lang": "en"}))
    keyboards = _keyboards(es)
    assert len(keyboards) >= 15
    for name, es_kb in keyboards.items():
        assert [cb for _, cb in es_kb] == [cb for _, cb in en[name]], name


def test_also_on_line_is_replaced_whichever_language_wrote_it() -> None:
    spanish = notifier.with_also_on("card", ["A"], lang="es")
    english = notifier.with_also_on("card", ["A"], lang="en")
    assert english.endswith("📍 Also on: A")
    assert notifier.with_also_on(spanish, ["B"], lang="en") == "card\n📍 Also on: B"
    assert notifier.with_also_on(english, ["B"], lang="es") == "card\n📍 También en: B"
    assert notifier.with_also_on(english, [], lang="en") == "card"


def test_outcome_label_keeps_unknown_estados_as_data() -> None:
    assert notifier.outcome_label("mystery", "en") == "mystery"
    assert notifier.OUTCOME_LABELS["oferta"] == "🎉 Oferta"
    assert notifier.outcome_label("oferta", "en") == "🎉 Offer"
    assert notifier.ALSO_ON_PREFIX == "📍 También en: "


@pytest.mark.parametrize("lang", LANGS)
def test_notifier_keyboards_match_help(lang: Lang) -> None:
    """Every button label of the notifier keyboards is named in /help, in the
    language of both (the Spanish parity also lives in test_commands)."""
    help_ = help_text(lang=lang)
    out = scenarios({"lang": lang})
    gmail = {text for text, _ in out["gmail_kb"]}  # not a documented card button
    labels = {
        text
        for keyboard in _keyboards(out).values()
        for text, _ in keyboard
        if text not in gmail
    }
    assert len(labels) >= 20
    for text in sorted(labels):
        core = re.sub(r"^[^\w]+", "", text).split(":")[0].split(" (")[0]
        assert core in help_, (lang, text)
