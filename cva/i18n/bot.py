"""Bot replies, usage lines and service errors (``bot.py``, ``cva/service.py``): bot-i18n T3.

Keys are prefixed ``bot.``:

- ``bot.err.*``: user-facing :class:`cva.service.ServiceError` messages
  (``ServiceError.keyed``; ``str(exc)`` is the Spanish text);
- ``bot.check.*``: cover-letter / follow-up validator warnings (the retry
  prompt keeps the Spanish text, the owner reads the UI language);
- ``bot.apply.*``: labels of the extra application steps found in an offer;
- ``bot.salary.*``: the card's published/reference salary line;
- ``bot.rejudge.*``: ``cli.py rejudge`` progress lines and report;
- the rest: chat replies of ``bot.py``.
"""

from typing import Final

EN: Final[dict[str, str]] = {
    # --- chat replies -------------------------------------------------------------
    "bot.usage": "Usage: {usage}",
    "bot.usage_rescue": "Usage: /rescue <id>",
    "bot.cartabase_usage": "Usage: /basecover [es|en|ca] (default es)",
    "bot.cartabase_note": (
        "ℹ️ Generic letter for job portals, with no specific offer. For an "
        "offer, the ✉️ button on its card adapts it to the company."
    ),
    "bot.letter_base_error": "⚠️ I could not read the base letter: {error}",
    "bot.empty_paste": "The text is empty; paste the offer again.",
    "bot.note_saved": "📝 Note saved on #{id}.",
    "bot.star_saved": "Saved exactly as you wrote it.",
    "bot.star_answer_hint": "(Write an answer or use /skip.)",
    "bot.star_exists": "You already have a story with this.",
    "bot.star_edit_which": "Which field of «{title}» do you want to redo?",
    "bot.star_delete_confirm": "Delete «{title}»? This cannot be undone.",
    "bot.star_deleted": "🗑 «{title}» deleted.",
    "bot.star_unchanged": "No changes to {field}.",
    "bot.unknown_action": "Unknown action.",
    "bot.skip_nothing": "There is nothing to skip.",
    "bot.skip_note": "OK, no note.",
    "bot.not_sent_yet": "Not sent yet: mark it with /sent {id} first.",
    "bot.already_in": "This application is already at {estado}; I changed nothing.",
    "bot.fu_ok": "✅ Follow-up {number} logged on #{id}.",
    "bot.fu_skip": "⏭️ #{id}: I will remind you in 7 days.",
    "bot.fu_none": "🔕 #{id} marked as no response.",
    "bot.cal_stale": "That question is already answered; I changed nothing.",
    "bot.cal_declined": "OK, it will not be used for calibration.",
    "bot.cal_deactivated": "🗑 Example deactivated: it no longer calibrates the score.",
    "bot.cal_approved": (
        "✅ Done: this offer will calibrate the score (you can remove it in "
        "/calibration)."
    ),
    "bot.no_offers": "No offers recorded yet.",
    "bot.exported": "📊 Exported to {path}",
    "bot.no_keywords": "No keywords recorded yet.",
    "bot.paused": (
        "⏸ Paused: no searches, automatic analysis or Gmail. "
        "/analyze still works. /resume to come back."
    ),
    "bot.resumed": "▶️ Resumed: everything works again.",
    "bot.writing_letter": "✍️ Writing the letter…",
    "bot.letter_failed": "⚠️ I could not write the letter: {reason}",
    "bot.letter_label": "✉️ Letter",
    "bot.letter_warnings": "⚠️ Careful, review it before using it:",
    "bot.letter_saved": "Letter saved on the offer",
    "bot.letter_pdf_failed": "⚠️ Could not create the letter PDF: {reason}",
    "bot.researching_salary": "🔎 Researching the salary on the web…",
    "bot.salary_failed": "⚠️ The salary research failed: {reason}",
    "bot.no_company": "(no company)",
    "bot.prep_choice": (
        "🎯 Prepare the interview for #{id} · {company}\n"
        "Which stage? Suggested: {stage} (from the status «{estado}»)."
    ),
    "bot.preparing": "⏳ Preparing…",
    "bot.prep_invalid": (
        "⚠️ I could not prepare the interview: the LLM returned an invalid "
        "format. Try again."
    ),
    "bot.prep_failed": "⚠️ I could not prepare the interview: {reason}",
    "bot.outcome_set": "✅ #{id} → {estado}.",
    "bot.gm_gone": "This proposal no longer exists.",
    "bot.gm_resolved": "This proposal is already resolved.",
    "bot.gm_ignored": "OK, I will ignore it.",
    "bot.wall": "wall",
    # --- service errors (ServiceError.keyed) -------------------------------------
    "bot.err.no_letter_llm": "this LLM provider cannot write cover letters",
    "bot.err.letter_stale": (
        "that letter belongs to an older generation; use the buttons of the latest one"
    ),
    "bot.err.no_letter": (
        "there is no letter generated for this offer; press «Cover letter» again"
    ),
    "bot.err.letter_version": "unknown letter version: {version}",
    "bot.err.stage": "Unknown stage: {stage}",
    "bot.err.no_prep_llm": "this LLM provider cannot prepare interviews",
    "bot.err.prep_busy": "I am already preparing that interview…",
    "bot.err.no_offer": "Offer #{id} does not exist.",
    "bot.err.duplicate": "Offer #{id} is a duplicate of #{original}: rescue that one.",
    "bot.err.not_analyzed": "Offer #{id} has not been analyzed yet.",
    "bot.err.unreadable": (
        "The stored analysis of #{id} cannot be read: analyze it again with /analyze."
    ),
    "bot.err.sent_not_analyzed": (
        "Offer #{id} has not been analyzed yet. Analyze it first with the 🔍 button "
        "or /analyze <url>."
    ),
    "bot.err.cv_language": (
        "There is no {role}_{lang} CV: the {family} family has CVs in {languages}."
    ),
    "bot.err.empty_offer": (
        "The offer has no readable text once hidden characters are removed; "
        "paste it again as plain text."
    ),
    "bot.err.not_sent": "Offer #{id} is not marked as sent yet.",
    "bot.err.estado": "Invalid status: {estado}",
    "bot.err.empty_note": "The note is empty.",
    "bot.err.reason": "Invalid reason: {reason}",
    "bot.err.already_sent": "It is already marked as sent.",
    "bot.err.star_gone": "That candidate no longer exists; use /star again.",
    "bot.err.field": "Unknown field: {field}",
    "bot.err.empty_answer": "The answer is empty.",
    # --- validator warnings (cover letter, follow-up) -----------------------------
    "bot.check.words": "has {words} words (must have between {low} and {high})",
    "bot.check.banned": "contains the banned phrase «{phrase}»",
    "bot.check.em_dash": "contains an em dash ({dash}), which must not be used",
    "bot.check.en_dash": "contains an en dash (–) used as punctuation",
    "bot.check.language": "does not seem to be written in the requested language ({language})",
    "bot.check.no_short": (
        "has no short sentence ({n} words or fewer); alternate short and long sentences"
    ),
    "bot.check.long": "has {count} sentences of more than {words} words (at most {max})",
    "bot.check.triads": (
        "uses more than one three-item list («X, Y and Z»); keep one at most"
    ),
    "bot.check.not_only": "uses the «not only… but also» structure (or «no solo… sino»)",
    "bot.check.opener": (
        "opens with a generic opener («{opener}»); the first sentence must name "
        "the company, the product or a problem of the role"
    ),
    "bot.check.salutation": (
        "the salutation is not the expected one (it must start with «{expected}» "
        "or address the person of the offer by name)"
    ),
    "bot.check.missing": "{part} is missing",
    "bot.check.part.linking": "the linking sentence",
    "bot.check.part.p2_why": "paragraph 2 (why the company)",
    "bot.check.part.p3_why": "paragraph 3 (why the role)",
    "bot.check.part.p2": "paragraph 2",
    "bot.check.part.p3": "paragraph 3",
    "bot.check.linking_one": "the linking sentence must be a single sentence",
    "bot.check.linking_words": "the linking sentence has {words} words (at most {max})",
    "bot.check.part_words": "{part} has {words} words (must have between {low} and {high})",
    "bot.check.filler": "contains generic filler «{filler}»",
    "bot.check.letter_words": (
        "the whole letter has {words} words (must have between {low} and "
        "{high}; the fixed text already adds {fixed})"
    ),
    "bot.check.formal": "formal register: use «ustedes», not «vosotros»",
    "bot.check.informal": "informal register: use «vosotros», not «ustedes»",
    # --- extra application steps found in the offer -------------------------------
    "bot.apply.external_apply": (
        "You have to apply outside LinkedIn (company website/portal)"
    ),
    "bot.apply.cover_letter": "They ask for a cover letter",
    "bot.apply.salary_expectations": "They ask for salary expectations",
    "bot.apply.start_date": "They ask for a start date / availability",
    "bot.apply.portfolio": "They ask for a portfolio / GitHub / code samples",
    "bot.apply.email_cv": "Also send the CV by email",
    "bot.apply.email_contact": "The offer gives a contact email",
    "bot.apply.phone": "The offer gives a contact phone / WhatsApp",
    "bot.apply.reference_code": "They ask you to quote the offer reference",
    # --- posting guard card line ---------------------------------------------------
    "bot.guard.line": "\u26a0\ufe0f Hidden text in the offer: {detail} ({suffix})",
    "bot.guard.chars.one": "{n} invisible character",
    "bot.guard.chars.other": "{n} invisible characters",
    "bot.guard.blocks.one": "{n} hidden block",
    "bot.guard.blocks.other": "{n} hidden blocks",
    "bot.guard.hidden": "hidden content",
    "bot.guard.removed.one": "removed",
    "bot.guard.removed.other": "removed",
    # --- stack wall (stored Spanish, shown in the UI language) ---------------------
    "bot.stack_wall": "core stack {label}, not {core}",
    # --- salary line on the cards -------------------------------------------------
    "bot.salary.published": "💶 Published salary: {amount} € gross/year{note}",
    "bot.salary.reference": (
        "💶 Reference: {low}–{high} (median {median}) · {branch} {level} · "
        "{location} · source: {source}, {as_of} · ask ~{ask}"
    ),
    "bot.salary.low_confidence": " · low confidence",
    "bot.salary.no_source": "no source",
    "bot.salary.no_location": " (location not detected)",
    "bot.salary.no_city_data": " (no data for {city})",
    "bot.salary.city.barcelona": "Barcelona",
    "bot.salary.city.madrid": "Madrid",
    "bot.salary.city.spain_remote": "Spain (remote)",
    # --- cli.py rejudge -------------------------------------------------------------
    "bot.rejudge.title": "# Backlog rejudge",
    "bot.rejudge.summary": (
        "Judged: {judged} · Shown (score ≥ {min_score}): {shown} · Failures: {failures}"
    ),
    "bot.rejudge.stopped": "⚠️ Run stopped before finishing the backlog: {reason}",
    "bot.rejudge.seen": " · seen {days} days ago",
    "bot.rejudge.stale": " (probably closed/advanced)",
    "bot.rejudge.earlier": " · (judged in an earlier run)",
    "bot.rejudge.row": (
        "- **{score}** [{verdict}] {title} — {company}{age} · walls: {walls} · "
        "{url}{run}"
    ),
    "bot.rejudge.no_title": "(untitled)",
    "bot.rejudge.no_company": "(no company)",
    "bot.rejudge.no_url": "(no url)",
    "bot.rejudge.failures": "## Failures ({n})",
    "bot.rejudge.failure": "- offer #{id}: {error}",
    "bot.rejudge.progress": "[{n}/{total}] offer #{id} — {title} ({company})",
    "bot.rejudge.interrupted": "interrupted by the user (KeyboardInterrupt)",
    "bot.rejudge.breaker": "{n} consecutive judge failures — last error: {error}",
}

ES: Final[dict[str, str]] = {
    # --- chat replies -------------------------------------------------------------
    "bot.usage": "Uso: {usage}",
    "bot.usage_rescue": "Uso: /rescatar <id>",
    "bot.cartabase_usage": "Uso: /cartabase [es|en|ca] (por defecto es)",
    "bot.cartabase_note": (
        "ℹ️ Carta genérica para portales, sin oferta concreta. Para una oferta, "
        "el botón ✉️ de su tarjeta la adapta a la empresa."
    ),
    "bot.letter_base_error": "⚠️ No pude leer la carta base: {error}",
    "bot.empty_paste": "El texto está vacío; pega la oferta de nuevo.",
    "bot.note_saved": "📝 Nota guardada en #{id}.",
    "bot.star_saved": "Guardada tal cual la escribiste.",
    "bot.star_answer_hint": "(Escribe una respuesta o usa /skip.)",
    "bot.star_exists": "Ya tienes una historia con esto.",
    "bot.star_edit_which": "¿Qué campo de «{title}» quieres rehacer?",
    "bot.star_delete_confirm": "¿Borrar «{title}»? No se puede deshacer.",
    "bot.star_deleted": "🗑 «{title}» borrada.",
    "bot.star_unchanged": "Sin cambios en {field}.",
    "bot.unknown_action": "Acción no reconocida.",
    "bot.skip_nothing": "No hay nada que saltar.",
    "bot.skip_note": "Vale, sin nota.",
    "bot.not_sent_yet": "Todavía no está enviada: márcala con /sent {id} primero.",
    "bot.already_in": "Esta candidatura ya está en {estado}; no he cambiado nada.",
    "bot.fu_ok": "✅ Seguimiento {number} anotado en #{id}.",
    "bot.fu_skip": "⏭️ #{id}: te lo recordaré dentro de 7 días.",
    "bot.fu_none": "🔕 #{id} marcada como sin respuesta.",
    "bot.cal_stale": "Esa pregunta ya está respondida; no he cambiado nada.",
    "bot.cal_declined": "Vale, no se usará para calibrar.",
    "bot.cal_deactivated": "🗑 Ejemplo desactivado: ya no calibra la nota.",
    "bot.cal_approved": (
        "✅ Hecho: esta oferta calibrará la nota (puedes quitarla en /calibracion)."
    ),
    "bot.no_offers": "Todavía no hay ofertas registradas.",
    "bot.exported": "📊 Exportado a {path}",
    "bot.no_keywords": "Sin keywords registradas todavía.",
    "bot.paused": (
        "⏸ En pausa: sin búsquedas, análisis automático ni Gmail. "
        "/analyze sigue. /reanudar para volver."
    ),
    "bot.resumed": "▶️ Reanudado: todo vuelve a funcionar.",
    "bot.writing_letter": "✍️ Escribiendo carta…",
    "bot.letter_failed": "⚠️ No pude escribir la carta: {reason}",
    "bot.letter_label": "✉️ Carta",
    "bot.letter_warnings": "⚠️ Ojo, revisa antes de usarla:",
    "bot.letter_saved": "Guardada la carta en la oferta",
    "bot.letter_pdf_failed": "⚠️ No se pudo crear el PDF de la carta: {reason}",
    "bot.researching_salary": "🔎 Investigando salario en la web…",
    "bot.salary_failed": "⚠️ La investigación salarial falló: {reason}",
    "bot.no_company": "(sin empresa)",
    "bot.prep_choice": (
        "🎯 Preparar entrevista de #{id} · {company}\n"
        "¿Qué fase? Sugerida: {stage} (según el estado «{estado}»)."
    ),
    "bot.preparing": "⏳ Preparando…",
    "bot.prep_invalid": (
        "⚠️ No pude preparar la entrevista: el LLM devolvió un formato "
        "inválido. Inténtalo de nuevo."
    ),
    "bot.prep_failed": "⚠️ No pude preparar la entrevista: {reason}",
    "bot.outcome_set": "✅ #{id} → {estado}.",
    "bot.gm_gone": "Esta propuesta ya no existe.",
    "bot.gm_resolved": "Esta propuesta ya está resuelta.",
    "bot.gm_ignored": "Vale, la ignoro.",
    "bot.wall": "muro",
    # --- service errors (ServiceError.keyed) -------------------------------------
    "bot.err.no_letter_llm": "este proveedor LLM no puede escribir cartas",
    "bot.err.letter_stale": (
        "esa carta es de una generación anterior; usa los botones de la última"
    ),
    "bot.err.no_letter": (
        "no hay una carta generada para esta oferta; pulsa de nuevo «Carta de presentación»"
    ),
    "bot.err.letter_version": "versión de carta desconocida: {version}",
    "bot.err.stage": "Fase desconocida: {stage}",
    "bot.err.no_prep_llm": "este proveedor LLM no puede preparar entrevistas",
    "bot.err.prep_busy": "Ya estoy preparando esa entrevista…",
    "bot.err.no_offer": "No existe la oferta #{id}.",
    "bot.err.duplicate": "La oferta #{id} es duplicada de #{original}: rescata esa.",
    "bot.err.not_analyzed": "La oferta #{id} no está analizada todavía.",
    "bot.err.unreadable": (
        "El análisis guardado de #{id} no se puede leer: vuelve a "
        "analizarla con /analyze."
    ),
    "bot.err.sent_not_analyzed": (
        "La oferta #{id} todavía no está analizada. Analízala primero con el botón "
        "🔍 o /analyze <url>."
    ),
    "bot.err.cv_language": (
        "No existe el CV {role}_{lang}: la familia {family} tiene CVs en {languages}."
    ),
    "bot.err.empty_offer": (
        "La oferta no tiene texto legible al quitar los caracteres ocultos; "
        "pégala de nuevo como texto plano."
    ),
    "bot.err.not_sent": "La oferta #{id} todavía no está marcada como enviada.",
    "bot.err.estado": "Estado no válido: {estado}",
    "bot.err.empty_note": "La nota está vacía.",
    "bot.err.reason": "Motivo no válido: {reason}",
    "bot.err.already_sent": "Ya está marcada como enviada.",
    "bot.err.star_gone": "Esa candidata ya no existe; usa /star de nuevo.",
    "bot.err.field": "Campo desconocido: {field}",
    "bot.err.empty_answer": "La respuesta está vacía.",
    # --- validator warnings (cover letter, follow-up) -----------------------------
    "bot.check.words": "tiene {words} palabras (debe tener entre {low} y {high})",
    "bot.check.banned": "contiene la frase prohibida «{phrase}»",
    "bot.check.em_dash": "contiene un guion largo ({dash}), no debe usarse",
    "bot.check.en_dash": "contiene un guion medio (–) como signo de puntuación",
    "bot.check.language": "no parece estar escrita en el idioma pedido ({language})",
    "bot.check.no_short": (
        "no tiene ninguna frase corta (de {n} palabras o menos); alterna frases "
        "cortas y largas"
    ),
    "bot.check.long": "tiene {count} frases de más de {words} palabras (máximo {max})",
    "bot.check.triads": (
        "usa más de una enumeración de tres elementos («X, Y y Z»); deja una como máximo"
    ),
    "bot.check.not_only": "usa la estructura «no solo… sino…» (o «not only… but also»)",
    "bot.check.opener": (
        "abre con una apertura genérica («{opener}»); la primera frase "
        "debe nombrar la empresa, el producto o un problema del puesto"
    ),
    "bot.check.salutation": (
        "el saludo no es el esperado (debe empezar con «{expected}» "
        "o dirigirse por su nombre a la persona de la oferta)"
    ),
    "bot.check.missing": "falta {part}",
    "bot.check.part.linking": "la frase de enlace",
    "bot.check.part.p2_why": "el párrafo 2 (por qué la empresa)",
    "bot.check.part.p3_why": "el párrafo 3 (por qué el puesto)",
    "bot.check.part.p2": "el párrafo 2",
    "bot.check.part.p3": "el párrafo 3",
    "bot.check.linking_one": "la frase de enlace debe ser una sola frase",
    "bot.check.linking_words": "la frase de enlace tiene {words} palabras (máximo {max})",
    "bot.check.part_words": "{part} tiene {words} palabras (debe tener entre {low} y {high})",
    "bot.check.filler": "contiene relleno genérico «{filler}»",
    "bot.check.letter_words": (
        "la carta completa tiene {words} palabras (debe tener entre "
        "{low} y {high}; el texto fijo ya aporta {fixed})"
    ),
    "bot.check.formal": "registro formal: usa «ustedes», no «vosotros»",
    "bot.check.informal": "registro cercano: usa «vosotros», no «ustedes»",
    # --- extra application steps found in the offer -------------------------------
    "bot.apply.external_apply": (
        "Hay que aplicar fuera de LinkedIn (web/portal de la empresa)"
    ),
    "bot.apply.cover_letter": "Piden carta de presentación",
    "bot.apply.salary_expectations": "Piden pretensiones salariales",
    "bot.apply.start_date": "Piden fecha de incorporación / disponibilidad",
    "bot.apply.portfolio": "Piden portfolio / GitHub / ejemplos de código",
    "bot.apply.email_cv": "Enviar el CV también por email",
    "bot.apply.email_contact": "La oferta da un email de contacto",
    "bot.apply.phone": "La oferta da un teléfono / WhatsApp de contacto",
    "bot.apply.reference_code": "Piden indicar la referencia de la oferta",
    # --- posting guard card line ---------------------------------------------------
    "bot.guard.line": "\u26a0\ufe0f Texto oculto en la oferta: {detail} ({suffix})",
    "bot.guard.chars.one": "{n} carácter invisible",
    "bot.guard.chars.other": "{n} caracteres invisibles",
    "bot.guard.blocks.one": "{n} bloque oculto",
    "bot.guard.blocks.other": "{n} bloques ocultos",
    "bot.guard.hidden": "contenido oculto",
    "bot.guard.removed.one": "eliminado",
    "bot.guard.removed.other": "eliminados",
    # --- stack wall (stored Spanish, shown in the UI language) ---------------------
    "bot.stack_wall": "stack principal {label}, no {core}",
    # --- salary line on the cards -------------------------------------------------
    "bot.salary.published": "💶 Salario publicado: {amount} € brutos/año{note}",
    "bot.salary.reference": (
        "💶 Referencia: {low}–{high} (mediana {median}) · {branch} {level} · "
        "{location} · fuente: {source}, {as_of} · pide ~{ask}"
    ),
    "bot.salary.low_confidence": " · confianza baja",
    "bot.salary.no_source": "sin fuente",
    "bot.salary.no_location": " (ubicación no detectada)",
    "bot.salary.no_city_data": " (sin dato de {city})",
    "bot.salary.city.barcelona": "Barcelona",
    "bot.salary.city.madrid": "Madrid",
    "bot.salary.city.spain_remote": "España remoto",
    # --- cli.py rejudge -------------------------------------------------------------
    "bot.rejudge.title": "# Rejudge del backlog",
    "bot.rejudge.summary": (
        "Juzgadas: {judged} · Mostradas (score ≥ {min_score}): {shown} "
        "· Fallos: {failures}"
    ),
    "bot.rejudge.stopped": (
        "⚠️ Ejecución detenida antes de completar el backlog: {reason}"
    ),
    "bot.rejudge.seen": " · visto hace {days} días",
    "bot.rejudge.stale": " (probablemente cerrada/avanzada)",
    "bot.rejudge.earlier": " · (juzgada en una ejecución anterior)",
    "bot.rejudge.row": (
        "- **{score}** [{verdict}] {title} — {company}{age} · walls: {walls} · "
        "{url}{run}"
    ),
    "bot.rejudge.no_title": "(sin título)",
    "bot.rejudge.no_company": "(sin empresa)",
    "bot.rejudge.no_url": "(sin url)",
    "bot.rejudge.failures": "## Fallos ({n})",
    "bot.rejudge.failure": "- oferta #{id}: {error}",
    "bot.rejudge.progress": "[{n}/{total}] oferta #{id} — {title} ({company})",
    "bot.rejudge.interrupted": "interrumpido por el usuario (KeyboardInterrupt)",
    "bot.rejudge.breaker": ("{n} fallos consecutivos del juez — último error: {error}"),
}
