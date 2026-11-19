"""``/help``, the Telegram command menu and the card-button docs (``commands.*``).

Per command (``cva.commands.Command.name``): ``usage``, ``menu`` (Telegram
menu, <= 256 chars), ``explanation`` and ``example``. ``/cv``'s usage and
example and ``/keywords``' example name the profile's families, so
``cva.commands`` builds them instead. Per card button (``cva.commands.BUTTONS``
ids): ``label`` and ``what``.
"""

from typing import Final

EN: Final[dict[str, str]] = {
    "commands.header": "Job offer tracking assistant.",
    "commands.example": "e.g. {example}",
    "commands.section.analyze": "🔎 Analyze",
    "commands.section.cv": "📄 CVs and letters",
    "commands.section.tracking": "📊 Tracking",
    "commands.section.beacons": "🔭 Beacons and searches",
    "commands.section.interview": "🎤 Interviews",
    "commands.section.other": "ℹ️ Other",
    "commands.section.buttons": "🔘 Card buttons",
    # --- commands -------------------------------------------------------------
    "commands.cmd.analyze.usage": "/analyze [url]",
    "commands.cmd.analyze.menu": "Analyze an offer (link or pasted text)",
    "commands.cmd.analyze.explanation": (
        "Scores an offer against your profile. Without a link, it asks for the text."
    ),
    "commands.cmd.analyze.example": "/analyze https://www.linkedin.com/jobs/view/123456",
    "commands.cmd.rescatar.usage": "/rescue <id>",
    "commands.cmd.rescatar.menu": "Recover a discarded offer (see /panel)",
    "commands.cmd.rescatar.explanation": (
        "Resends the full card of a discarded offer, without touching its score."
    ),
    "commands.cmd.rescatar.example": "/rescue 42",
    "commands.cmd.cv.menu": "Generate the CV tailored to an offer",
    "commands.cmd.cv.explanation": (
        "CV PDF for offer #id. Role and language are optional "
        "(picked automatically when missing)."
    ),
    "commands.cmd.cartabase.usage": "/basecover [es|en|ca] (or /base)",
    "commands.cmd.cartabase.menu": "Your generic cover letter for portals (no offer)",
    "commands.cmd.cartabase.explanation": (
        "A letter for no specific offer, ready to copy."
    ),
    "commands.cmd.cartabase.example": "/basecover en",
    "commands.cmd.status.usage": "/status [id]",
    "commands.cmd.status.menu": "Status of recent offers",
    "commands.cmd.status.explanation": (
        "Without an id, the last 10 offers; with an id, the status of that one."
    ),
    "commands.cmd.status.example": "/status 42",
    "commands.cmd.sent.usage": "/sent <id>",
    "commands.cmd.sent.menu": "Mark an offer as sent",
    "commands.cmd.sent.explanation": (
        "Records that you sent the application for offer #id."
    ),
    "commands.cmd.sent.example": "/sent 42",
    "commands.cmd.outcome.usage": "/outcome <id>",
    "commands.cmd.outcome.menu": "Record the outcome of an application",
    "commands.cmd.outcome.explanation": (
        "History of sent offer #id and buttons to log how it is going. "
        "After 10 days without news it suggests a follow-up (you send it)."
    ),
    "commands.cmd.outcome.example": "/outcome 42",
    "commands.cmd.calibracion.usage": "/calibration",
    "commands.cmd.calibracion.menu": "Examples that calibrate the bot's score",
    "commands.cmd.calibracion.explanation": (
        "Outcomes that calibrate the score; each one can be turned off."
    ),
    "commands.cmd.calibracion.example": "/calibration",
    "commands.cmd.skip.usage": "/skip",
    "commands.cmd.skip.menu": "Skip the pending feedback note",
    "commands.cmd.skip.explanation": (
        "After an outcome the bot asks for a note; /skip skips it."
    ),
    "commands.cmd.skip.example": "/skip",
    "commands.cmd.export.usage": "/export",
    "commands.cmd.export.menu": "Export the xlsx of sent offers",
    "commands.cmd.export.explanation": (
        "Excel with the sent offers only; tells you where it was saved."
    ),
    "commands.cmd.export.example": "/export",
    "commands.cmd.panel.usage": "/panel",
    "commands.cmd.panel.menu": "HTML panel with KPIs, charts and table",
    "commands.cmd.panel.explanation": (
        "Offline HTML: metrics, charts, alerts, filterable table and "
        "discarded offers (/rescue)."
    ),
    "commands.cmd.panel.example": "/panel",
    "commands.cmd.keywords.usage": "/keywords [profile]",
    "commands.cmd.keywords.menu": "Log of the most requested keywords",
    "commands.cmd.keywords.explanation": (
        "Shows the keywords seen in offers and how often (filter by profile)."
    ),
    "commands.cmd.beacons.usage": "/beacons",
    "commands.cmd.beacons.menu": "Status of the beacons (watched searches)",
    "commands.cmd.beacons.explanation": (
        "Each beacon with its status, consecutive walls and last check."
    ),
    "commands.cmd.beacons.example": "/beacons",
    "commands.cmd.beacon.usage": "/beacon resume|disable <id>",
    "commands.cmd.beacon.menu": "Resume or disable a beacon",
    "commands.cmd.beacon.explanation": (
        "resume reactivates a paused beacon; disable turns it off."
    ),
    "commands.cmd.beacon.example": "/beacon resume 3",
    "commands.cmd.star.usage": "/star",
    "commands.cmd.star.menu": "Your bank of STAR stories for interviews",
    "commands.cmd.star.explanation": (
        "Asks for the situation, task, action and result of your experiences; "
        "then lists them (✅ complete, ✏️ missing parts) to view, edit or "
        "delete. /skip leaves a field empty."
    ),
    "commands.cmd.star.example": "/star",
    "commands.cmd.prep.usage": "/prep <id>",
    "commands.cmd.prep.menu": "Prepare an interview (HTML with topics and gaps)",
    "commands.cmd.prep.explanation": (
        "Pick a stage (HR, Technical, Final): HTML with topics, gaps, STAR, "
        "salary and questions."
    ),
    "commands.cmd.prep.example": "/prep 42",
    "commands.cmd.pausa.usage": "/pause",
    "commands.cmd.pausa.menu": "Pause searches, automatic analysis and Gmail",
    "commands.cmd.pausa.explanation": (
        "Stops everything automatic; /analyze keeps working."
    ),
    "commands.cmd.pausa.example": "/pause",
    "commands.cmd.reanudar.usage": "/resume",
    "commands.cmd.reanudar.menu": "Resume the automatic work after /pause",
    "commands.cmd.reanudar.explanation": "Turns back on what was paused.",
    "commands.cmd.reanudar.example": "/resume",
    "commands.cmd.help.usage": "/help",
    "commands.cmd.help.menu": "Show this help",
    "commands.cmd.help.explanation": "Commands and buttons (also /start).",
    "commands.cmd.help.example": "/help",
    # --- card buttons ---------------------------------------------------------
    "commands.button.analyze.label": "🔎 Analyze",
    "commands.button.analyze.what": (
        "on a new-offer alert: analyzes it just like /analyze."
    ),
    "commands.button.generate_cv.label": "📄 Generate CV",
    "commands.button.generate_cv.what": (
        "creates the PDF with the role and language chosen on the card."
    ),
    "commands.button.language.label": "Language: {langs}",
    "commands.button.language.what": "changes the CV language{note}.",
    "commands.button.language.limit": "{label} only {langs}",
    "commands.button.variant.label": "Variant: {labels}",
    "commands.button.variant.what": "switches the CV profile between {families}.",
    "commands.button.salary.label": "💶 Research salary",
    "commands.button.salary.what": (
        "searches the web for a salary range with sources (may find nothing)."
    ),
    "commands.button.cover_letter.label": "✉️ Cover letter",
    "commands.button.cover_letter.what": (
        "built on your base letter, with the company researched (and its "
        'sources) ("(requested)" = the offer asks for a letter).'
    ),
    "commands.button.use_this.label": "✅ Use this one",
    "commands.button.use_this.what": "saves the letter on the offer.",
    "commands.button.dont_use.label": "🗑 Don't use",
    "commands.button.dont_use.what": "removes the letter from the chat.",
    "commands.button.discard.label": "🗑 Discard",
    "commands.button.discard.what": (
        "discards the offer; if it already has a CV, no submission is recorded."
    ),
    "commands.button.mark_sent.label": "✅ Mark as sent",
    "commands.button.mark_sent.what": "same as /sent: the offer moves to «sent».",
    "commands.button.redo_cv.label": "🔄 Redo CV",
    "commands.button.redo_cv.what": (
        "generates the same CV again (in case the base CVs changed)."
    ),
    "commands.button.outcome.label": "📝 Outcome",
    "commands.button.outcome.what": (
        "on a sent offer: opens /outcome to log how the application is going."
    ),
    "commands.button.outcome_states.label": "Outcome states",
    "commands.button.outcome_states.what": (
        "Screening, Technical interview, Final interview, Offer, Accepted, "
        "Rejected, No response, Withdrawn: save the state and ask for a note."
    ),
    "commands.button.calibrate.label": "Calibrate: Yes · No",
    "commands.button.calibrate.what": (
        "after an interview or a rejection: use that offer (or not) to "
        "calibrate the score. Rejection reason: Requirements, Stack, Language "
        "or Other / not sure (does not calibrate)."
    ),
    "commands.button.followup_sent.label": "✅ Sent",
    "commands.button.followup_sent.what": (
        "on a follow-up: records that you sent the message yourself (2 at most)."
    ),
    "commands.button.followup_skip.label": "⏭️ Skip",
    "commands.button.followup_skip.what": ("on a follow-up: postpones it 7 days."),
    "commands.button.followup_no_reply.label": "🔕 No response",
    "commands.button.followup_no_reply.what": (
        "on a follow-up: marks the offer as no response."
    ),
    "commands.button.star_add.label": "➕ Experience or project",
    "commands.button.star_add.what": (
        "in /star: choose what to tell a story about (also «Add another»)."
    ),
    "commands.button.star_manage.label": "👁 View · ✏️ Edit · 🗑 Delete",
    "commands.button.star_manage.what": (
        "in /star: view a story, redo a field (Situation, Task, Action, "
        "Result, Use) or delete it (Yes, delete / Cancel). Back."
    ),
    "commands.button.prep.label": "🎯 Prepare interview",
    "commands.button.prep.what": (
        "in /outcome and when moving to screening or interview: pick a stage "
        "and get the /prep HTML."
    ),
    "commands.button.beacon_resume.label": "▶️ Resume",
    "commands.button.beacon_resume.what": "on a paused-beacon alert: reactivates it.",
    "commands.button.beacon_disable.label": "⏹ Disable",
    "commands.button.beacon_disable.what": (
        "on a paused-beacon alert: stops checking it."
    ),
}

ES: Final[dict[str, str]] = {
    "commands.header": "Asistente de seguimiento de ofertas.",
    "commands.example": "Ej.: {example}",
    "commands.section.analyze": "🔎 Analizar",
    "commands.section.cv": "📄 CVs y cartas",
    "commands.section.tracking": "📊 Seguimiento",
    "commands.section.beacons": "🔭 Beacons y búsquedas",
    "commands.section.interview": "🎤 Entrevistas",
    "commands.section.other": "ℹ️ Otros",
    "commands.section.buttons": "🔘 Botones de las tarjetas",
    # --- commands -------------------------------------------------------------
    "commands.cmd.analyze.usage": "/analyze [url]",
    "commands.cmd.analyze.menu": "Analiza una oferta (enlace o texto pegado)",
    "commands.cmd.analyze.explanation": (
        "Puntúa una oferta contra tu perfil. Sin enlace, pide el texto."
    ),
    "commands.cmd.analyze.example": "/analyze https://www.linkedin.com/jobs/view/123456",
    "commands.cmd.rescatar.usage": "/rescatar <id>",
    "commands.cmd.rescatar.menu": "Recupera una oferta descartada (mira /panel)",
    "commands.cmd.rescatar.explanation": (
        "Reenvía la tarjeta completa de una oferta descartada, sin tocar su nota."
    ),
    "commands.cmd.rescatar.example": "/rescatar 42",
    "commands.cmd.cv.menu": "Genera el CV adaptado a una oferta",
    "commands.cmd.cv.explanation": (
        "PDF del CV para la oferta #id. Rol e idioma opcionales "
        "(si faltan, se eligen solos)."
    ),
    "commands.cmd.cartabase.usage": "/cartabase [es|en|ca] (o /base)",
    "commands.cmd.cartabase.menu": "Tu carta genérica para portales (sin oferta)",
    "commands.cmd.cartabase.explanation": (
        "Carta sin oferta concreta, lista para copiar."
    ),
    "commands.cmd.cartabase.example": "/cartabase en",
    "commands.cmd.status.usage": "/status [id]",
    "commands.cmd.status.menu": "Estado de las ofertas recientes",
    "commands.cmd.status.explanation": (
        "Sin id, las últimas 10 ofertas; con id, el estado de una."
    ),
    "commands.cmd.status.example": "/status 42",
    "commands.cmd.sent.usage": "/sent <id>",
    "commands.cmd.sent.menu": "Marca una oferta como enviada",
    "commands.cmd.sent.explanation": (
        "Registra que enviaste la candidatura de la oferta #id."
    ),
    "commands.cmd.sent.example": "/sent 42",
    "commands.cmd.outcome.usage": "/outcome <id>",
    "commands.cmd.outcome.menu": "Registra el resultado de una candidatura",
    "commands.cmd.outcome.explanation": (
        "Historial de la enviada #id y botones para anotar cómo va. "
        "Tras 10 días sin noticias propone un seguimiento (lo envías tú)."
    ),
    "commands.cmd.outcome.example": "/outcome 42",
    "commands.cmd.calibracion.usage": "/calibracion",
    "commands.cmd.calibracion.menu": "Ejemplos que calibran la nota del bot",
    "commands.cmd.calibracion.explanation": (
        "Resultados que calibran la nota; cada uno se puede desactivar."
    ),
    "commands.cmd.calibracion.example": "/calibracion",
    "commands.cmd.skip.usage": "/skip",
    "commands.cmd.skip.menu": "Omite la nota de feedback pendiente",
    "commands.cmd.skip.explanation": (
        "Tras un resultado el bot pide una nota; /skip la omite."
    ),
    "commands.cmd.skip.example": "/skip",
    "commands.cmd.export.usage": "/export",
    "commands.cmd.export.menu": "Exporta el xlsx de enviadas",
    "commands.cmd.export.explanation": (
        "Excel solo con las enviadas; te dice dónde quedó."
    ),
    "commands.cmd.export.example": "/export",
    "commands.cmd.panel.usage": "/panel",
    "commands.cmd.panel.menu": "Panel HTML con KPIs, gráficos y tabla",
    "commands.cmd.panel.explanation": (
        "HTML sin conexión: métricas, gráficos, alertas, tabla filtrable y "
        "descartadas (/rescatar)."
    ),
    "commands.cmd.panel.example": "/panel",
    "commands.cmd.keywords.usage": "/keywords [perfil]",
    "commands.cmd.keywords.menu": "Registro de palabras clave más demandadas",
    "commands.cmd.keywords.explanation": (
        "Muestra las keywords vistas en ofertas y su frecuencia (filtra por perfil)."
    ),
    "commands.cmd.beacons.usage": "/beacons",
    "commands.cmd.beacons.menu": "Estado de los beacons (búsquedas vigiladas)",
    "commands.cmd.beacons.explanation": (
        "Cada beacon con estado, muros seguidos y última comprobación."
    ),
    "commands.cmd.beacons.example": "/beacons",
    "commands.cmd.beacon.usage": "/beacon resume|disable <id>",
    "commands.cmd.beacon.menu": "Reactiva o desactiva un beacon",
    "commands.cmd.beacon.explanation": (
        "resume reactiva un beacon pausado; disable lo desactiva."
    ),
    "commands.cmd.beacon.example": "/beacon resume 3",
    "commands.cmd.star.usage": "/star",
    "commands.cmd.star.menu": "Tu banco de historias STAR para entrevistas",
    "commands.cmd.star.explanation": (
        "Pregunta situación, tarea, acción y resultado de tus experiencias; "
        "luego las lista (✅ completa, ✏️ faltan partes) para ver, editar o "
        "borrar. /skip deja un campo vacío."
    ),
    "commands.cmd.star.example": "/star",
    "commands.cmd.prep.usage": "/prep <id>",
    "commands.cmd.prep.menu": "Prepara una entrevista (HTML con temas y huecos)",
    "commands.cmd.prep.explanation": (
        "Elige fase (RR. HH., Técnica, Final): HTML con temas, huecos, STAR, "
        "salario y preguntas."
    ),
    "commands.cmd.prep.example": "/prep 42",
    "commands.cmd.pausa.usage": "/pausa",
    "commands.cmd.pausa.menu": "Pausa búsquedas, análisis automático y Gmail",
    "commands.cmd.pausa.explanation": "Detiene todo lo automático; /analyze sigue.",
    "commands.cmd.pausa.example": "/pausa",
    "commands.cmd.reanudar.usage": "/reanudar",
    "commands.cmd.reanudar.menu": "Reanuda lo automático tras /pausa",
    "commands.cmd.reanudar.explanation": "Vuelve a activar lo pausado.",
    "commands.cmd.reanudar.example": "/reanudar",
    "commands.cmd.help.usage": "/help",
    "commands.cmd.help.menu": "Muestra esta ayuda",
    "commands.cmd.help.explanation": "Comandos y botones (también /start).",
    "commands.cmd.help.example": "/help",
    # --- card buttons ---------------------------------------------------------
    "commands.button.analyze.label": "🔎 Analizar",
    "commands.button.analyze.what": (
        "en el aviso de una oferta nueva: la analiza igual que /analyze."
    ),
    "commands.button.generate_cv.label": "📄 Generar CV",
    "commands.button.generate_cv.what": (
        "crea el PDF con el rol e idioma elegidos en la tarjeta."
    ),
    "commands.button.language.label": "Idioma: {langs}",
    "commands.button.language.what": "cambia el idioma del CV{note}.",
    "commands.button.language.limit": "{label} solo {langs}",
    "commands.button.variant.label": "Variante: {labels}",
    "commands.button.variant.what": "alterna el perfil del CV entre {families}.",
    "commands.button.salary.label": "💶 Investigar salario",
    "commands.button.salary.what": (
        "busca en la web un rango salarial con fuentes (puede no encontrar nada)."
    ),
    "commands.button.cover_letter.label": "✉️ Carta de presentación",
    "commands.button.cover_letter.what": (
        "sobre tu carta base, con la empresa investigada (y sus fuentes) "
        '("(la piden)" = la oferta pide carta).'
    ),
    "commands.button.use_this.label": "✅ Usar esta",
    "commands.button.use_this.what": "guarda la carta en la oferta.",
    "commands.button.dont_use.label": "🗑 No usar",
    "commands.button.dont_use.what": "quita la carta del chat.",
    "commands.button.discard.label": "🗑 Descartar",
    "commands.button.discard.what": (
        "descarta la oferta; si ya hay CV, no se registra envío."
    ),
    "commands.button.mark_sent.label": "✅ Marcar enviada",
    "commands.button.mark_sent.what": "igual que /sent: la oferta pasa a «enviada».",
    "commands.button.redo_cv.label": "🔄 Rehacer CV",
    "commands.button.redo_cv.what": (
        "vuelve a generar el mismo CV (por si cambiaron los CV base)."
    ),
    "commands.button.outcome.label": "📝 Resultado",
    "commands.button.outcome.what": (
        "en una oferta enviada: abre /outcome para anotar cómo va la candidatura."
    ),
    "commands.button.outcome_states.label": "Estados del resultado",
    "commands.button.outcome_states.what": (
        "Screening, Entrevista técnica, Entrevista final, Oferta, Aceptada, "
        "Rechazada, Sin respuesta, Retirada: guardan el estado y piden nota."
    ),
    "commands.button.calibrate.label": "Calibrar: Sí · No",
    "commands.button.calibrate.what": (
        "tras una entrevista o rechazo: usa esa oferta (o no) para "
        "calibrar la nota. Motivo del rechazo: Requisitos, Stack, Idioma "
        "u Otro / no sé (no calibra)."
    ),
    "commands.button.followup_sent.label": "✅ Enviado",
    "commands.button.followup_sent.what": (
        "en un seguimiento: anota que enviaste tú el mensaje (máximo 2)."
    ),
    "commands.button.followup_skip.label": "⏭️ Saltar",
    "commands.button.followup_skip.what": ("en un seguimiento: lo pospone 7 días."),
    "commands.button.followup_no_reply.label": "🔕 Sin respuesta",
    "commands.button.followup_no_reply.what": (
        "en un seguimiento: marca la oferta como sin respuesta."
    ),
    "commands.button.star_add.label": "➕ Experiencia o proyecto",
    "commands.button.star_add.what": (
        "en /star: elige de qué contar una historia (también «Añadir otra»)."
    ),
    "commands.button.star_manage.label": "👁 Ver · ✏️ Editar · 🗑 Borrar",
    "commands.button.star_manage.what": (
        "en /star: ver una historia, rehacer un campo (Situación, Tarea, "
        "Acción, Resultado, Uso) o borrarla (Sí, borrar / Cancelar). Volver."
    ),
    "commands.button.prep.label": "🎯 Preparar entrevista",
    "commands.button.prep.what": (
        "en /outcome y al pasar a screening o entrevista: elige fase y "
        "recibe el HTML de /prep."
    ),
    "commands.button.beacon_resume.label": "▶️ Reactivar",
    "commands.button.beacon_resume.what": "en el aviso de beacon pausado: lo reactiva.",
    "commands.button.beacon_disable.label": "⏹ Desactivar",
    "commands.button.beacon_disable.what": (
        "en el aviso de beacon pausado: deja de comprobarlo."
    ),
}
