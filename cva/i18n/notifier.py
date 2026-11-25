"""Notifier cards and keyboards (``cva/notifier.py``): bot-i18n T2.

Keys are prefixed ``notifier.``. Button labels that ``/help`` documents are NOT
here: the keyboards read ``commands.button.<id>.label`` so help and keyboards
cannot drift. Placeholders named ``days`` are pre-rendered by ``cva.notifier``
(plural handled there); stored values (estados, statuses) are data.
"""

from typing import Final

EN: Final[dict[str, str]] = {
    "notifier.letter_quality": "🧪 Letter quality: {score}/100",
    "notifier.letter_quality_missing": " · not mentioned: {items}",
    "notifier.letter_company_mismatch": (
        "The letter names {other}, not {company} (the company on the card): "
        "fix it before sending."
    ),
    # --- shared words -----------------------------------------------------------
    "notifier.no_title": "(untitled)",
    "notifier.no_company": "(no company)",
    "notifier.no_position": "(no position)",
    # "Search elsewhere" URL buttons on analysis cards; {site} is a site name.
    "notifier.search_on": "🔎 On {site}",
    "notifier.more": "… and {n} more",
    "notifier.age_recent": "recent",
    "notifier.days.one": "1 day",
    "notifier.days.other": "{n} days",
    "notifier.days_plain.one": "{n} day",
    "notifier.days_plain.other": "{n} days",
    # --- estados (display of stored values) --------------------------------------
    "notifier.estado.pendiente_analisis": "pending analysis",
    "notifier.estado.analizada": "analyzed",
    "notifier.estado.preparada": "prepared",
    "notifier.estado.descartada": "discarded",
    "notifier.estado.enviada": "sent",
    "notifier.estado.screening": "screening",
    "notifier.estado.entrevista_tecnica": "technical interview",
    "notifier.estado.entrevista_final": "final interview",
    "notifier.estado.oferta": "offer",
    "notifier.estado.aceptada": "accepted",
    "notifier.estado.rechazada": "rejected",
    "notifier.estado.sin_respuesta": "no response",
    "notifier.estado.retirada": "withdrawn",
    # --- outcomes -----------------------------------------------------------------
    "notifier.outcome.screening": "📞 Screening",
    "notifier.outcome.entrevista_tecnica": "💻 Technical interview",
    "notifier.outcome.entrevista_final": "🏁 Final interview",
    "notifier.outcome.oferta": "🎉 Offer",
    "notifier.outcome.aceptada": "✅ Accepted",
    "notifier.outcome.rechazada": "❌ Rejected",
    "notifier.outcome.sin_respuesta": "🤐 No response",
    "notifier.outcome.retirada": "↩️ Withdrawn",
    "notifier.outcome_note_prompt": ("Want to add a note (feedback)? Type it or /skip"),
    "notifier.stage.rrhh": "HR",
    "notifier.stage.tecnica": "Technical",
    "notifier.stage.final": "Final",
    # --- cards ---------------------------------------------------------------------
    "notifier.unknown_role": (
        "⚠️ The {role} variant is no longer in your profile: using {default_label}."
    ),
    "notifier.new_job": (
        "🔔 {title} · {company} · {age}\n{url}\nTop-Choice threshold: {threshold}"
    ),
    "notifier.also_on": "📍 Also on: ",
    "notifier.also_on_status": " · Also on: ",
    "notifier.apply_warning_header": (
        "⚠️ Heads up: this offer asks for something more when applying"
    ),
    "notifier.deadline_today": "⏰ Closes today",
    "notifier.deadline_in": "⏰ Closes in {days} ({date})",
    "notifier.stale_posting": "🕰️ Posted {days} ago (may be closed)",
    "notifier.easy_apply": "⚡ Easy Apply",
    "notifier.deadline_alert_head": (
        "⏰ Offers about to close that you haven't sent yet:"
    ),
    "notifier.deadline_entry": "• {position} · {company} — closes {when}",
    "notifier.deadline_when_today": "today",
    "notifier.deadline_when_in": "in {days}",
    "notifier.breakdown_above": "above the threshold ✅",
    "notifier.breakdown_below": "below the threshold (Top-Choice) ❌",
    "notifier.breakdown_head": (
        "📊 Offer #{offer_id} — score {score}/100 (threshold {threshold}): {verdict}"
    ),
    "notifier.weight_must": "must-have",
    "notifier.weight_nice": "nice-to-have",
    "notifier.borderline": (
        "🟡 {title} · {company} — score {score}/100 (threshold {threshold})\n"
        "{url}\n"
        "Close to the Top-Choice threshold: review it and generate your CV if it fits."
    ),
    "notifier.round_head": "📊 Round '{beacon}': {analyzed} analyzed",
    "notifier.round_counts": (
        "🟢 {above} above the threshold · 🟡 {gray} in the gray zone · "
        "⚪ {below} discarded by threshold"
    ),
    "notifier.round_walled": (
        "🧱 {n} discarded by a wall (stack, eligibility or language)"
    ),
    "notifier.round_closed": "🔒 {n} closed offers (discarded without analysis)",
    "notifier.round_duplicates": "♻️ {n} duplicates (already seen on another source)",
    "notifier.round_pending": "♻️ {n} pending retry",
    "notifier.round_discarded_head": "Discarded (score — title — company):",
    "notifier.verdict.apply": "apply",
    "notifier.verdict.apply_with_caveats": "apply with caveats",
    "notifier.verdict.discard": "discard",
    "notifier.judge_fallback": (
        "⚠️ The LLM judgment failed; showing the keyword score."
    ),
    "notifier.wall": "🚫 Wall: {reason}",
    "notifier.judge_head": "{emoji} Offer #{offer_id} — score {score}/100 ({label})",
    "notifier.judge_role": "Core role: {role}",
    "notifier.judge_cv_base": "Suggested base CV: {cv_base}",
    "notifier.judge_fit": "Main fit:",
    "notifier.judge_walls": "Walls:",
    "notifier.judge_keyword_detail": "Keyword detail:",
    "notifier.cover_letter_asked": " (requested)",
    "notifier.cover_sources_none": (
        "ℹ️ I found no reliable company data: paragraph 2 relies only on the "
        "offer and stays more generic. Review it before using it."
    ),
    "notifier.cover_sources": ("🔎 Company sources (used in paragraph 2):\n{lines}"),
    "notifier.no_cv_line": "📨 {label}: applied through your portal profile (no CV)",
    "notifier.no_cv_reply": (
        "ℹ️ This offer is from {label}: you apply with your portal profile, "
        "no CV needed."
    ),
    "notifier.cv_ready": (
        "📄 CV ready: {path}{pages}. Mark it as sent once you send it.{link}"
    ),
    "notifier.cv_ready_pages": " ({pages} pp.)",
    "notifier.cv_ready_link": "\n🔗 Offer: {url}",
    # --- salary ----------------------------------------------------------------------
    "notifier.salary_unavailable": (
        "💶 Salary research is not available with this provider."
    ),
    "notifier.salary_cached": (
        "\n(saved result from today, {checked_at}; the search is not repeated)"
    ),
    "notifier.salary_no_data": (
        "💶 No reliable salary data for {subject} ({where}). "
        "Not enough published sources were found.{cached}"
    ),
    "notifier.salary_median": " (median {median} €)",
    "notifier.salary_head": "💶 Salary research — {subject} · {where}{cached}",
    "notifier.salary_range": (
        "Range: {min}–{max} € gross/year{median} · confidence {confidence}"
    ),
    "notifier.salary_sources": "Sources:",
    "notifier.salary_notes": "Notes: {notes}",
    "notifier.salary_disclaimer": (
        "⚠️ Estimate based on web sources; not company data."
    ),
    # --- outcome / follow-up ----------------------------------------------------------
    "notifier.outcome_head": "📝 Outcome of #{id} · {company} · {position}",
    "notifier.outcome_state": "Status: {estado}",
    "notifier.outcome_sent_on": " · sent {date}",
    "notifier.outcome_no_events": "No events yet.",
    "notifier.event_estado": "{at} · status → {estado}",
    "notifier.event_followup": "{at} · follow-up sent",
    "notifier.event_note": "{at} · note: {note}",
    "notifier.followup_head": "📬 Follow-up {number} of 2 · {label}",
    "notifier.followup_silent": (
        "You've had no news for {days} since your last activity."
    ),
    "notifier.followup_no_draft": (
        "I couldn't prepare a draft this time; write it yourself."
    ),
    "notifier.followup_draft": "Draft (send it yourself if it fits):",
    "notifier.followup_review": "⚠️ Review the draft:",
    "notifier.followup_to": "📧 Send to: {address} ({source})",
    "notifier.followup_to.contact": "contact saved on the offer",
    "notifier.followup_to.gmail": "latest email from the company",
    "notifier.followup_to.offer": "address in the offer",
    "notifier.followup_to_none": "📧 No address known: answer through the portal.",
    "notifier.followup_proposal": (
        "🔕 {label}\n"
        "{days} without a reply and you already sent 2 follow-ups. "
        "Mark it as no response?"
    ),
    # --- confirmations and errors ------------------------------------------------------
    "notifier.sent": "✅ Offer #{offer_id} marked as sent (match {match}).",
    "notifier.discarded": "🗑 Offer #{offer_id} discarded. No PDF will be generated.",
    "notifier.discarded_no_send": "🗑 Offer discarded — no submission recorded.",
    "notifier.not_found": (
        "🔒 That offer seems to have been removed or closed (404); "
        "there is nothing to analyze. If you think it is still active, paste "
        "the text with /analyze."
    ),
    "notifier.offer_closed": (
        "🔒 The offer is closed (the text itself says so): discarded, not analyzed."
    ),
    "notifier.paste_prompt": (
        "⚠️ I couldn't access the offer (wall/block). "
        "Paste the offer text as a message and I'll analyze it."
    ),
    "notifier.failure_unknown": "unknown error",
    "notifier.failure_http": "the LLM returned HTTP {code}",
    "notifier.failure_llm": "the LLM failed: {text}",
    "notifier.failure_hint": "{cause} — check CVA_LLM_* in .env",
    "notifier.analyze_error": (
        "❌ The analysis failed: {reason} — paste the text again once you fix it."
    ),
    "notifier.analyze_rate_limit": (
        "❌ The analysis failed: LLM rate limit (429) — "
        "wait a few minutes and paste the offer again."
    ),
    "notifier.auto_failure": (
        "⚠️ The automatic analysis failed ({cause}). Press Analyze to retry."
    ),
    "notifier.backstop": "⚠️ Processing your message failed: {reason}",
    # --- AI outage (llm-outage) -----------------------------------------------------
    "notifier.ai.paused_auth": (
        "⛔ The AI stopped answering: {provider} has no valid login or key. "
        "I paused the automatic analysis: new offers wait, no error cards."
    ),
    "notifier.ai.paused_quota": (
        "⏸️ The AI usage limit was reached ({provider}). "
        "I paused the automatic analysis: new offers wait, no error cards."
    ),
    "notifier.ai.fix_claude_code": (
        "Fix: run `claude auth login` on the bot's machine. For an unattended "
        "bot, run `claude setup-token` and put the token in .env as "
        "CLAUDE_CODE_OAUTH_TOKEN=..., then restart the bot."
    ),
    "notifier.ai.fix_codex_cli": "Fix: run `codex login` on the bot's machine.",
    "notifier.ai.fix_anthropic": "Fix: check ANTHROPIC_API_KEY in .env.",
    "notifier.ai.fix_openai_compatible": (
        "Fix: check the API key (CVA_LLM_API_KEY) and CVA_LLM_* in .env."
    ),
    "notifier.ai.resume_at": "Limit reached; I'll resume by myself at {time}.",
    "notifier.ai.resume_poll": (
        "I check again every 15 minutes and resume by myself; "
        "the pending offers are analysed then."
    ),
    "notifier.ai.detail": "Details: {detail}",
    "notifier.ai.still_down": "🔄 Checked: the AI still does not answer.",
    "notifier.ai.back": "✅ AI is back, analysing {count} pending offers.",
    "notifier.ai.ok": "✅ The AI is working; nothing is paused.",
    "notifier.ai.status": ("⛔ AI paused since {since} ({cause}); next check {next}."),
    "notifier.ai.cause.auth": "login/key",
    "notifier.ai.cause.quota": "usage limit",
    "notifier.ai.button.check": "🔄 Check now",
    "notifier.ai.button.resume": "▶️ Resume",
    "notifier.status_line": "#{id} · {estado} · match {match} · {position}{tag}",
    # --- beacons -----------------------------------------------------------------------
    "notifier.beacons_none": "🔭 No beacons configured.",
    "notifier.beacons_head": "🔭 Beacons:",
    "notifier.searches_none": (
        "🔭 No scans reported yet: the counts start filling as the extension scans."
    ),
    "notifier.searches_head": "🔭 Your searches, last {days} days (zero new first):",
    "notifier.searches_line": (
        "• {name} · rounds {rounds} · found {found} · new {new}"
    ),
    "notifier.searches_failure": " · last failure: {failure}",
    "notifier.searches_little_data": (
        "Little data so far: the new-offer counts accumulate from this version on."
    ),
    "notifier.searches_hint": (
        "Searches that found nothing new in {days} days are candidates to remove "
        "(Options › Searches)."
    ),
    "notifier.beacons_line": (
        "#{id} {name} · {status} · walls: {walls} · last check: {last}"
    ),
    "notifier.beacons_never": "never",
    "notifier.beacon_resumed": "✅ Beacon #{beacon_id} resumed (wall counter reset).",
    "notifier.beacon_disabled": (
        "⏹ Beacon #{beacon_id} disabled (it will not be checked again)."
    ),
    "notifier.beacon_usage": "Usage: /beacon resume|disable <id>",
    "notifier.beacon_missing": "⚠️ Beacon #{beacon_id} does not exist.",
    "notifier.beacon_paused": (
        "🚨 Beacon «{name}» paused after {walls} consecutive walls (last: {kind})."
    ),
    # --- gmail -------------------------------------------------------------------------
    "notifier.gmail_proposal": (
        "📧 {company} · «{subject}»: looks like {estado}.\n"
        "Mark offer #{offer_id} as {estado}?"
    ),
    "notifier.gmail_yes": "Yes",
    "notifier.gmail_other": "Other status",
    "notifier.gmail_ignore": "Ignore",
    # --- card warnings ([search] preferences, onboarding-v2 S4) ---------------------
    "notifier.warn.work_mode": "\u26a0\ufe0f {mode}; you accept: {accepted}",
    "notifier.warn.mode.remote": "Remote work",
    "notifier.warn.mode.hybrid": "Hybrid work",
    "notifier.warn.mode.onsite": "On-site work",
    "notifier.warn.mode_name.remote": "remote",
    "notifier.warn.mode_name.hybrid": "hybrid",
    "notifier.warn.mode_name.onsite": "on-site",
    "notifier.warn.salary": (
        "\u26a0\ufe0f Salary below your minimum ({minimum} €): up to {amount} €"
    ),
}

ES: Final[dict[str, str]] = {
    "notifier.letter_quality": "🧪 Calidad de la carta: {score}/100",
    "notifier.letter_quality_missing": " · no menciona: {items}",
    "notifier.letter_company_mismatch": (
        "La carta nombra a {other}, no a {company} (la empresa de la tarjeta): "
        "corrígela antes de enviarla."
    ),
    # --- shared words -----------------------------------------------------------
    "notifier.no_title": "(sin título)",
    "notifier.no_company": "(sin empresa)",
    "notifier.no_position": "(sin posición)",
    "notifier.search_on": "🔎 En {site}",
    "notifier.more": "… y {n} más",
    "notifier.age_recent": "reciente",
    "notifier.days.one": "1 día",
    "notifier.days.other": "{n} días",
    "notifier.days_plain.one": "{n} días",
    "notifier.days_plain.other": "{n} días",
    # --- estados (display of stored values) --------------------------------------
    "notifier.estado.pendiente_analisis": "pendiente_analisis",
    "notifier.estado.analizada": "analizada",
    "notifier.estado.preparada": "preparada",
    "notifier.estado.descartada": "descartada",
    "notifier.estado.enviada": "enviada",
    "notifier.estado.screening": "screening",
    "notifier.estado.entrevista_tecnica": "entrevista_tecnica",
    "notifier.estado.entrevista_final": "entrevista_final",
    "notifier.estado.oferta": "oferta",
    "notifier.estado.aceptada": "aceptada",
    "notifier.estado.rechazada": "rechazada",
    "notifier.estado.sin_respuesta": "sin_respuesta",
    "notifier.estado.retirada": "retirada",
    # --- outcomes -----------------------------------------------------------------
    "notifier.outcome.screening": "📞 Screening",
    "notifier.outcome.entrevista_tecnica": "💻 Entrevista técnica",
    "notifier.outcome.entrevista_final": "🏁 Entrevista final",
    "notifier.outcome.oferta": "🎉 Oferta",
    "notifier.outcome.aceptada": "✅ Aceptada",
    "notifier.outcome.rechazada": "❌ Rechazada",
    "notifier.outcome.sin_respuesta": "🤐 Sin respuesta",
    "notifier.outcome.retirada": "↩️ Retirada",
    "notifier.outcome_note_prompt": (
        "¿Quieres añadir una nota (feedback)? Escríbela o /skip"
    ),
    "notifier.stage.rrhh": "RR. HH.",
    "notifier.stage.tecnica": "Técnica",
    "notifier.stage.final": "Final",
    # --- cards ---------------------------------------------------------------------
    "notifier.unknown_role": (
        "⚠️ La variante {role} ya no está en tu perfil: uso {default_label}."
    ),
    "notifier.new_job": (
        "🔔 {title} · {company} · {age}\n{url}\nUmbral Top-Choice: {threshold}"
    ),
    "notifier.also_on": "📍 También en: ",
    "notifier.also_on_status": " · También en: ",
    "notifier.apply_warning_header": "⚠️ Ojo: esta oferta pide algo más al aplicar",
    "notifier.deadline_today": "⏰ Cierra hoy",
    "notifier.deadline_in": "⏰ Cierra en {days} ({date})",
    "notifier.stale_posting": "🕰️ Publicada hace {days} (puede estar cerrada)",
    "notifier.easy_apply": "⚡ Solicitud sencilla",
    "notifier.deadline_alert_head": "⏰ Ofertas por cerrar que aún no has enviado:",
    "notifier.deadline_entry": "• {position} · {company} — cierra {when}",
    "notifier.deadline_when_today": "hoy",
    "notifier.deadline_when_in": "en {days}",
    "notifier.breakdown_above": "supera el umbral ✅",
    "notifier.breakdown_below": "no supera el umbral (Top-Choice) ❌",
    "notifier.breakdown_head": (
        "📊 Oferta #{offer_id} — puntuación {score}/100 (umbral {threshold}): {verdict}"
    ),
    "notifier.weight_must": "imprescindible",
    "notifier.weight_nice": "deseable",
    "notifier.borderline": (
        "🟡 {title} · {company} — puntuación {score}/100 (umbral {threshold})\n"
        "{url}\n"
        "Cerca del umbral Top-Choice: revísala y genera tu CV si encaja."
    ),
    "notifier.round_head": "📊 Ronda '{beacon}': {analyzed} analizadas",
    "notifier.round_counts": (
        "🟢 {above} sobre el umbral · 🟡 {gray} en zona gris · "
        "⚪ {below} descartadas por umbral"
    ),
    "notifier.round_walled": (
        "🧱 {n} descartadas por muro (stack, elegibilidad o idioma)"
    ),
    "notifier.round_closed": "🔒 {n} ofertas cerradas (descartadas sin analizar)",
    "notifier.round_duplicates": "♻️ {n} duplicadas (ya vistas en otra fuente)",
    "notifier.round_pending": "♻️ {n} pendientes de reintento",
    "notifier.round_discarded_head": "Descartadas (score — título — empresa):",
    "notifier.verdict.apply": "aplicar",
    "notifier.verdict.apply_with_caveats": "aplicar con matices",
    "notifier.verdict.discard": "descartar",
    "notifier.judge_fallback": (
        "⚠️ El juicio LLM falló; se muestra el score por palabras clave."
    ),
    "notifier.wall": "🚫 Muro: {reason}",
    "notifier.judge_head": (
        "{emoji} Oferta #{offer_id} — puntuación {score}/100 ({label})"
    ),
    "notifier.judge_role": "Rol principal: {role}",
    "notifier.judge_cv_base": "CV base sugerido: {cv_base}",
    "notifier.judge_fit": "Fit principal:",
    "notifier.judge_walls": "Muros:",
    "notifier.judge_keyword_detail": "Detalle por palabra clave:",
    "notifier.cover_letter_asked": " (la piden)",
    "notifier.cover_sources_none": (
        "ℹ️ No encontré datos fiables de la empresa: el párrafo 2 se apoya "
        "solo en la oferta y queda más genérico. Revísalo antes de usarla."
    ),
    "notifier.cover_sources": (
        "🔎 Fuentes de la empresa (usadas en el párrafo 2):\n{lines}"
    ),
    "notifier.no_cv_line": "📨 {label}: se aplica con tu perfil del portal (sin CV)",
    "notifier.no_cv_reply": (
        "ℹ️ Esta oferta es de {label}: se aplica con tu perfil del portal, "
        "no hace falta CV."
    ),
    "notifier.cv_ready": (
        "📄 CV listo: {path}{pages}. Márcalo como enviada cuando lo envíes.{link}"
    ),
    "notifier.cv_ready_pages": " ({pages} pág.)",
    "notifier.cv_ready_link": "\n🔗 Oferta: {url}",
    # --- salary ----------------------------------------------------------------------
    "notifier.salary_unavailable": (
        "💶 Investigación salarial no disponible con este proveedor."
    ),
    "notifier.salary_cached": (
        "\n(resultado guardado de hoy, {checked_at}; no se repite la búsqueda)"
    ),
    "notifier.salary_no_data": (
        "💶 Sin datos fiables de salario para {subject} ({where}). "
        "No se encontraron fuentes publicadas suficientes.{cached}"
    ),
    "notifier.salary_median": " (mediana {median} €)",
    "notifier.salary_head": "💶 Salario investigado — {subject} · {where}{cached}",
    "notifier.salary_range": (
        "Rango: {min}–{max} € brutos/año{median} · confianza {confidence}"
    ),
    "notifier.salary_sources": "Fuentes:",
    "notifier.salary_notes": "Notas: {notes}",
    "notifier.salary_disclaimer": (
        "⚠️ Estimación a partir de fuentes web; no es un dato de la empresa."
    ),
    # --- outcome / follow-up ----------------------------------------------------------
    "notifier.outcome_head": "📝 Resultado de #{id} · {company} · {position}",
    "notifier.outcome_state": "Estado: {estado}",
    "notifier.outcome_sent_on": " · enviada {date}",
    "notifier.outcome_no_events": "Sin eventos todavía.",
    "notifier.event_estado": "{at} · estado → {estado}",
    "notifier.event_followup": "{at} · seguimiento enviado",
    "notifier.event_note": "{at} · nota: {note}",
    "notifier.followup_head": "📬 Seguimiento {number} de 2 · {label}",
    "notifier.followup_silent": (
        "Llevas {days} sin noticias desde tu última actividad."
    ),
    "notifier.followup_no_draft": (
        "No he podido preparar un borrador esta vez; escríbelo tú."
    ),
    "notifier.followup_draft": "Borrador (envíalo tú si te encaja):",
    "notifier.followup_review": "⚠️ Revisa el borrador:",
    "notifier.followup_to": "📧 Enviar a: {address} ({source})",
    "notifier.followup_to.contact": "contacto guardado en la oferta",
    "notifier.followup_to.gmail": "último correo de la empresa",
    "notifier.followup_to.offer": "dirección de la oferta",
    "notifier.followup_to_none": "📧 Sin dirección conocida: respóndele por el portal.",
    "notifier.followup_proposal": (
        "🔕 {label}\n"
        "{days} sin respuesta y ya hiciste 2 seguimientos. "
        "¿La marco como sin respuesta?"
    ),
    # --- confirmations and errors ------------------------------------------------------
    "notifier.sent": "✅ Oferta #{offer_id} marcada como enviada (match {match}).",
    "notifier.discarded": "🗑 Oferta #{offer_id} descartada. No se generará ningún PDF.",
    "notifier.discarded_no_send": "🗑 Oferta descartada — no se registra envío.",
    "notifier.not_found": (
        "🔒 Esa oferta parece haber sido retirada o cerrada (404); "
        "no hay nada que analizar. Si crees que sigue activa, pega el texto "
        "con /analyze."
    ),
    "notifier.offer_closed": (
        "🔒 La oferta está cerrada (lo dice el propio texto): descartada, sin analizar."
    ),
    "notifier.paste_prompt": (
        "⚠️ No pude acceder a la oferta (muro/bloqueo). "
        "Pega el texto de la oferta como mensaje y la analizo."
    ),
    "notifier.failure_unknown": "error desconocido",
    "notifier.failure_http": "el LLM devolvió HTTP {code}",
    "notifier.failure_llm": "el LLM falló: {text}",
    "notifier.failure_hint": "{cause} — revisa CVA_LLM_* en .env",
    "notifier.analyze_error": (
        "❌ El análisis falló: {reason} — pega el texto de nuevo cuando lo corrijas."
    ),
    "notifier.analyze_rate_limit": (
        "❌ El análisis falló: límite de peticiones del LLM (429) — "
        "espera unos minutos y vuelve a pegar la oferta."
    ),
    "notifier.auto_failure": (
        "⚠️ El análisis automático falló ({cause}). Pulsa Analizar para reintentar."
    ),
    "notifier.backstop": "⚠️ Ha fallado el procesamiento de tu mensaje: {reason}",
    # --- AI outage (llm-outage) -----------------------------------------------------
    "notifier.ai.paused_auth": (
        "⛔ La IA dejó de responder: {provider} no tiene una sesión o clave "
        "válida. He pausado el análisis automático: las ofertas nuevas esperan, "
        "sin tarjetas de error."
    ),
    "notifier.ai.paused_quota": (
        "⏸️ Se alcanzó el límite de uso de la IA ({provider}). He pausado el "
        "análisis automático: las ofertas nuevas esperan, sin tarjetas de error."
    ),
    "notifier.ai.fix_claude_code": (
        "Arreglo: ejecuta `claude auth login` en la máquina del bot. Para un bot "
        "desatendido, ejecuta `claude setup-token` y pon el token en .env como "
        "CLAUDE_CODE_OAUTH_TOKEN=..., y reinicia el bot."
    ),
    "notifier.ai.fix_codex_cli": "Arreglo: ejecuta `codex login` en la máquina del bot.",
    "notifier.ai.fix_anthropic": "Arreglo: revisa ANTHROPIC_API_KEY en .env.",
    "notifier.ai.fix_openai_compatible": (
        "Arreglo: revisa la clave (CVA_LLM_API_KEY) y CVA_LLM_* en .env."
    ),
    "notifier.ai.resume_at": "Límite alcanzado; reanudo solo a las {time}.",
    "notifier.ai.resume_poll": (
        "Lo compruebo cada 15 minutos y reanudo solo; "
        "entonces analizo las ofertas pendientes."
    ),
    "notifier.ai.detail": "Detalle: {detail}",
    "notifier.ai.still_down": "🔄 Comprobado: la IA sigue sin responder.",
    "notifier.ai.back": "✅ La IA vuelve a funcionar, analizando {count} ofertas pendientes.",
    "notifier.ai.ok": "✅ La IA funciona; no hay nada en pausa.",
    "notifier.ai.status": (
        "⛔ IA en pausa desde {since} ({cause}); próxima comprobación {next}."
    ),
    "notifier.ai.cause.auth": "sesión/clave",
    "notifier.ai.cause.quota": "límite de uso",
    "notifier.ai.button.check": "🔄 Comprobar ahora",
    "notifier.ai.button.resume": "▶️ Reanudar",
    "notifier.status_line": "#{id} · {estado} · match {match} · {position}{tag}",
    # --- beacons -----------------------------------------------------------------------
    "notifier.beacons_none": "🔭 No hay beacons configurados.",
    "notifier.beacons_head": "🔭 Beacons:",
    "notifier.searches_none": (
        "🔭 Aún no hay escaneos reportados: los contadores se llenan según escanee "
        "la extensión."
    ),
    "notifier.searches_head": (
        "🔭 Tus búsquedas, últimos {days} días (primero las que no dieron nada nuevo):"
    ),
    "notifier.searches_line": (
        "• {name} · rondas {rounds} · encontradas {found} · nuevas {new}"
    ),
    "notifier.searches_failure": " · último fallo: {failure}",
    "notifier.searches_little_data": (
        "Pocos datos por ahora: los contadores de ofertas nuevas se acumulan "
        "desde esta versión."
    ),
    "notifier.searches_hint": (
        "Las búsquedas que no encontraron nada nuevo en {days} días son candidatas "
        "a quitar (Opciones › Búsquedas)."
    ),
    "notifier.beacons_line": (
        "#{id} {name} · {status} · muros: {walls} · última comprobación: {last}"
    ),
    "notifier.beacons_never": "nunca",
    "notifier.beacon_resumed": (
        "✅ Beacon #{beacon_id} reactivado (contador de muros a cero)."
    ),
    "notifier.beacon_disabled": (
        "⏹ Beacon #{beacon_id} desactivado (no se volverá a comprobar)."
    ),
    "notifier.beacon_usage": "Uso: /beacon resume|disable <id>",
    "notifier.beacon_missing": "⚠️ No existe el beacon #{beacon_id}.",
    "notifier.beacon_paused": (
        "🚨 Beacon «{name}» pausado tras {walls} muros consecutivos (último: {kind})."
    ),
    # --- gmail -------------------------------------------------------------------------
    "notifier.gmail_proposal": (
        "📧 {company} · «{subject}»: parece {estado}.\n"
        "¿Marcar la oferta #{offer_id} como {estado}?"
    ),
    "notifier.gmail_yes": "Sí",
    "notifier.gmail_other": "Otro estado",
    "notifier.gmail_ignore": "Ignorar",
    # --- card warnings ([search] preferences, onboarding-v2 S4) ---------------------
    "notifier.warn.work_mode": "\u26a0\ufe0f {mode}; aceptas: {accepted}",
    "notifier.warn.mode.remote": "Modalidad remota",
    "notifier.warn.mode.hybrid": "Modalidad híbrida",
    "notifier.warn.mode.onsite": "Modalidad presencial",
    "notifier.warn.mode_name.remote": "remoto",
    "notifier.warn.mode_name.hybrid": "híbrido",
    "notifier.warn.mode_name.onsite": "presencial",
    "notifier.warn.salary": (
        "\u26a0\ufe0f Salario por debajo de tu mínimo ({minimum} €): hasta {amount} €"
    ),
}
