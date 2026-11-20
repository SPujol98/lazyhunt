"""Guided setup questionnaire (``/setup``, ``cva/onboarding``): ``onboarding.*``.

The portal terms warnings are NOT here: they are the extension's own texts
(``extension/_locales/<lang>/messages.json``), read by
:mod:`cva.onboarding.portals` so the bot and the options page say the same.
"""

from typing import Final

EN: Final[dict[str, str]] = {
    "onboarding.progress": "Setup {n}/{total}",
    # /setup <section> (T9): the names shown and accepted.
    "onboarding.section.contact": "contact",
    "onboarding.section.positions": "positions",
    "onboarding.section.level": "level",
    "onboarding.section.background": "background",
    "onboarding.section.location": "location",
    "onboarding.section.salary": "salary",
    "onboarding.section.offer_langs": "offer-languages",
    "onboarding.section.portals": "portals",
    "onboarding.section.cvs": "cvs",
    "onboarding.section.unknown": (
        "There is no setup section “{section}”. Use /setup alone for the whole "
        "questionnaire, or one of: {sections}."
    ),
    "onboarding.section.needs_setup": (
        "Your profile is not set up yet: run /setup first to answer the whole "
        "questionnaire; then /setup <section> changes one part of it."
    ),
    "onboarding.btn.back": "◀️ Back",
    "onboarding.btn.cancel": "✖️ Cancel",
    "onboarding.btn.skip": "Skip ⏭",
    "onboarding.btn.done": "Done ✔️",
    "onboarding.btn.confirm": "✅ Confirm",
    "onboarding.btn.edit": "✏️ Edit",
    "onboarding.btn.keep": "Keep {value}",
    "onboarding.btn.other": "✏️ Other titles…",
    "onboarding.btn.enable": "✅ Enable {portal}",
    "onboarding.btn.list": "↩️ Back to the list",
    "onboarding.q.lang": (
        "Let's set up your profile: one question per message, with Back and "
        "Cancel on every step. Nothing is saved until you confirm the summary.\n\n"
        "Which language should the bot use?"
    ),
    "onboarding.q.name": "Your full name (as it goes on your CV)?",
    "onboarding.q.email": "Your contact email?",
    "onboarding.q.phone": "Your phone number? (optional: Skip if you prefer)",
    "onboarding.q.city": "The city you live in?",
    "onboarding.q.linkedin": "Your LinkedIn profile URL?",
    "onboarding.q.families": (
        "Which positions are you looking for? Tick one or more, or add your "
        "own titles with “Other titles”, then Done."
    ),
    "onboarding.q.custom_titles": (
        "Type the job titles you are looking for, separated by commas "
        "(e.g. Data Analyst, BI Developer)."
    ),
    "onboarding.q.level": "Your level?",
    "onboarding.q.work_modes": "Which work modes do you accept? Tick them, then Done.",
    "onboarding.q.work_city": "Which city do you want to work in? Keep it or type another.",
    "onboarding.q.work_city_empty": "Which city do you want to work in?",
    "onboarding.q.country": "And which country? (e.g. Spain)",
    "onboarding.q.salary": (
        "Your minimum gross annual salary? Type a number (e.g. 30000) or Skip."
    ),
    "onboarding.q.currency": "In which currency? Tap one or type a 3-letter code.",
    "onboarding.q.offer_langs": (
        "In which languages may an offer be written? Offers that require any "
        "other language are discarded. Tick them, then Done."
    ),
    "onboarding.q.portals": (
        "Which job portals should feed the bot? Tap a portal to read its terms "
        "warning and enable it; tap an enabled one to turn it off. Then Done."
    ),
    "onboarding.q.portal_terms": "{warning}",
    "onboarding.q.cv_pdfs": (
        "Now your base CVs: the bot delivers these PDFs as they are. Send one "
        "PDF as a file (📎 → File) for each position and language below, at "
        "most {mb} MB and {pages} pages. Tap a line to choose which one the "
        "next PDF is for. Skipped ones get no CV until you add one."
    ),
    "onboarding.cv.send": "Send the PDF for: {slot}",
    "onboarding.cv.all_set": "All set. Tap a line to replace its PDF, or Done.",
    "onboarding.cv.received": "✅ PDF received for {slot} ({pages}).",
    "onboarding.cv.pages.one": "1 page",
    "onboarding.cv.pages.other": "{n} pages",
    "onboarding.cv.summary_missing": "no CV for {slots}",
    "onboarding.cv.summary_kept": "already saved: {slots}",
    "onboarding.cv.existing": "(already saved)",
    "onboarding.cv.saved": "Base CVs saved in cv_base_pdfs/: {count}.",
    "onboarding.cv.not_deliverable": (
        "No base CV for {slots}: the bot cannot deliver a CV there until you "
        "add one (/setup cvs, or cv_base_pdfs/manifest.json by hand)."
    ),
    "onboarding.btn.skip_one": "Skip this one ⏭",
    "onboarding.btn.skip_all": "Skip all ⏭⏭",
    "onboarding.edit.cv_pdfs": "Base CVs",
    "onboarding.err.cv_not_pdf": "⚠️ That is not a readable PDF. Send the CV as a PDF file.",
    "onboarding.err.cv_too_big": "⚠️ That file is too big: at most {mb} MB.",
    "onboarding.err.cv_pages": "⚠️ That PDF has too many pages: at most {pages}.",
    "onboarding.err.cv_download": "⚠️ Could not download that file from Telegram. Try again.",
    "onboarding.err.cv_pick_slot": (
        "⚠️ First tap the position and language this PDF is for."
    ),
    "onboarding.q.edit": "Which answer do you want to change?",
    "onboarding.q.summary": "Please check your answers:\n\n{summary}",
    "onboarding.err.name": "⚠️ Please type your name (1-100 characters).",
    "onboarding.err.email": "⚠️ That does not look like an email address (name@example.com).",
    "onboarding.err.phone": "⚠️ That does not look like a phone number (digits, +, spaces).",
    "onboarding.err.text": "⚠️ Please type an answer (1-100 characters).",
    "onboarding.err.linkedin": (
        "⚠️ That does not look like a web address (https://linkedin.com/in/…)."
    ),
    "onboarding.err.titles": "⚠️ Please type at least one job title.",
    "onboarding.err.pick_one": "⚠️ Pick at least one option.",
    "onboarding.err.salary": "⚠️ Please type a whole number, e.g. 30000, or Skip.",
    "onboarding.err.currency": "⚠️ Please type a 3-letter currency code, e.g. EUR.",
    "onboarding.level.junior": "Junior",
    "onboarding.level.mid": "Mid",
    "onboarding.level.senior": "Senior",
    "onboarding.mode.remote": "Remote",
    "onboarding.mode.hybrid": "Hybrid",
    "onboarding.mode.onsite": "On-site",
    "onboarding.olang.es": "Spanish",
    "onboarding.olang.en": "English",
    "onboarding.olang.ca": "Catalan",
    "onboarding.olang.fr": "French",
    "onboarding.olang.de": "German",
    "onboarding.olang.it": "Italian",
    "onboarding.olang.pt": "Portuguese",
    "onboarding.lang.en": "English",
    "onboarding.lang.es": "Spanish",
    "onboarding.custom": "Other: {titles}",
    "onboarding.edit.lang": "Language",
    "onboarding.edit.name": "Name",
    "onboarding.edit.email": "Email",
    "onboarding.edit.phone": "Phone",
    "onboarding.edit.city": "City",
    "onboarding.edit.linkedin": "LinkedIn",
    "onboarding.edit.families": "Positions",
    "onboarding.edit.level": "Level",
    "onboarding.edit.work_modes": "Work mode",
    "onboarding.edit.work_city": "Work city",
    "onboarding.edit.country": "Country",
    "onboarding.edit.salary": "Salary",
    "onboarding.edit.offer_langs": "Offer languages",
    "onboarding.edit.portals": "Portals",
    "onboarding.btn.other_lang": "✏️ Other languages…",
    "onboarding.q.spoken_langs": (
        "Which languages do you speak? Tick them, or add others with “Other "
        "languages”, then Done. Next you give each one its level."
    ),
    "onboarding.q.spoken_other": (
        "Type the other languages you speak, separated by commas (e.g. Basque, Dutch)."
    ),
    "onboarding.q.spoken_levels": "Your level of {language}?",
    "onboarding.q.skills": (
        "Your main skills or stack, separated by commas (e.g. Python, SQL, Docker)?"
    ),
    "onboarding.q.exp_years": "How many years of professional experience do you have?",
    "onboarding.q.last_role": (
        "Your last (or current) job title? (optional: Skip if you prefer)"
    ),
    "onboarding.q.last_company": "And the company? (optional: Skip if you prefer)",
    "onboarding.q.education": (
        "Your highest education, in one line (e.g. BSc Computer Science, "
        "University of Valencia)? (optional: Skip if you prefer)"
    ),
    "onboarding.err.skills": (
        "⚠️ Please type 1-20 skills separated by commas, each at most 50 characters."
    ),
    "onboarding.err.languages": (
        "⚠️ Please type 1-5 languages separated by commas, each at most 40 characters."
    ),
    "onboarding.cefr.native": "Native",
    "onboarding.cefr.C2": "C2",
    "onboarding.cefr.C1": "C1",
    "onboarding.cefr.B2": "B2",
    "onboarding.cefr.B1": "B1",
    "onboarding.cefr.A2": "A2",
    "onboarding.years.0": "0",
    "onboarding.years.lt1": "<1",
    "onboarding.years.1-2": "1-2",
    "onboarding.years.3-5": "3-5",
    "onboarding.years.6-10": "6-10",
    "onboarding.years.10plus": "10+",
    "onboarding.summary.years.0": "0 years",
    "onboarding.summary.years.lt1": "Less than 1 year",
    "onboarding.summary.years.1-2": "1-2 years",
    "onboarding.summary.years.3-5": "3-5 years",
    "onboarding.summary.years.6-10": "6-10 years",
    "onboarding.summary.years.10plus": "10+ years",
    "onboarding.edit.spoken_langs": "Spoken languages",
    "onboarding.edit.skills": "Skills",
    "onboarding.edit.exp_years": "Experience",
    "onboarding.edit.last_role": "Last role",
    "onboarding.edit.education": "Education",
    "onboarding.none": "—",
    "onboarding.summary.salary": "{amount} {currency} gross/year",
    "onboarding.portals.terms_missing": (
        "Enable {portal}? Check that its terms of use allow automated access "
        "first; you are responsible for complying with them."
    ),
    "onboarding.portals.line": (
        "To enable these portals, add this line to config.local.toml and "
        "restart the bot:\n{line}"
    ),
    "onboarding.cancelled": "Setup cancelled. Nothing was saved.",
    "onboarding.expired": "This setup expired. Send /setup to start again.",
    "onboarding.stale": "That button belongs to an earlier question; here is the current one.",
    "onboarding.no_profile_dir": (
        "The guided setup writes into a profile directory, and this bot has none "
        "configured (profile_dir in config.local.toml). Run `uv run cli.py init` "
        "first."
    ),
    "onboarding.saved": ("✅ Saved preferences.toml and profile.toml."),
    "onboarding.saved_language": "The bot language is now saved in config.local.toml.",
    "onboarding.missing": "Still missing before the bot can run normally: {missing}.",
    "onboarding.write_failed": "⚠️ Could not save your answers: {error}",
    "onboarding.write_undone": (
        "Nothing was changed; your uploads are kept, so you can fix it and "
        "press Confirm again."
    ),
    "onboarding.rollback_failed": (
        "⚠️ Some files could not be put back as they were: {error}. Check the "
        "profile folder before confirming again."
    ),
    "onboarding.reload_failed": (
        "⚠️ The files were saved but the bot could not reload them: {error}"
    ),
    "onboarding.portals.enabled": (
        "Portals now active: {portals} (saved in config.local.toml). In the "
        "extension, open Options › Portals and grant the browser permission "
        "for each one; it is asked once per portal."
    ),
    "onboarding.next_step": (
        "Next step: in the extension, open Options › Searches and press "
        '"Suggest from my profile" to add searches built from these answers.'
    ),
    "onboarding.identity.kept": (
        "ℹ️ Your identity claim ({claim} in claims.json) was edited by hand, so "
        "it was kept as it is: CVs and letters still use its contact details. "
        "To change them, edit that claim's text in claims.json."
    ),
    "onboarding.identity.kept_unrecorded": (
        "ℹ️ Your identity claim ({claim} in claims.json) was kept as it is "
        "because /setup did not create it: CVs and letters still use its "
        "contact details. To change them, edit that claim's text in claims.json."
    ),
}

ES: Final[dict[str, str]] = {
    "onboarding.progress": "Configuración {n}/{total}",
    # /configurar <sección> (T9): los nombres que se muestran y se aceptan.
    "onboarding.section.contact": "contacto",
    "onboarding.section.positions": "puestos",
    "onboarding.section.level": "nivel",
    "onboarding.section.background": "trayectoria",
    "onboarding.section.location": "ubicacion",
    "onboarding.section.salary": "salario",
    "onboarding.section.offer_langs": "idiomas-ofertas",
    "onboarding.section.portals": "portales",
    "onboarding.section.cvs": "cvs",
    "onboarding.section.unknown": (
        "No hay ninguna sección de configuración «{section}». Usa /configurar "
        "sin nada más para el cuestionario completo, o una de: {sections}."
    ),
    "onboarding.section.needs_setup": (
        "Tu perfil aún no está configurado: usa primero /configurar para "
        "responder el cuestionario completo; después /configurar <sección> "
        "cambia una parte."
    ),
    "onboarding.btn.back": "◀️ Atrás",
    "onboarding.btn.cancel": "✖️ Cancelar",
    "onboarding.btn.skip": "Omitir ⏭",
    "onboarding.btn.done": "Listo ✔️",
    "onboarding.btn.confirm": "✅ Confirmar",
    "onboarding.btn.edit": "✏️ Editar",
    "onboarding.btn.keep": "Mantener {value}",
    "onboarding.btn.other": "✏️ Otros puestos…",
    "onboarding.btn.enable": "✅ Activar {portal}",
    "onboarding.btn.list": "↩️ Volver a la lista",
    "onboarding.q.lang": (
        "Vamos a configurar tu perfil: una pregunta por mensaje, con Atrás y "
        "Cancelar en cada paso. No se guarda nada hasta que confirmes el resumen.\n\n"
        "¿En qué idioma quieres el bot?"
    ),
    "onboarding.q.name": "¿Tu nombre completo (tal como va en tu CV)?",
    "onboarding.q.email": "¿Tu email de contacto?",
    "onboarding.q.phone": "¿Tu teléfono? (opcional: pulsa Omitir si lo prefieres)",
    "onboarding.q.city": "¿En qué ciudad vives?",
    "onboarding.q.linkedin": "¿La URL de tu perfil de LinkedIn?",
    "onboarding.q.families": (
        "¿Qué puestos buscas? Marca uno o varios, o añade los tuyos con "
        "«Otros puestos», y pulsa Listo."
    ),
    "onboarding.q.custom_titles": (
        "Escribe los puestos que buscas separados por comas "
        "(p. ej. Data Analyst, BI Developer)."
    ),
    "onboarding.q.level": "¿Tu nivel?",
    "onboarding.q.work_modes": "¿Qué modalidades aceptas? Márcalas y pulsa Listo.",
    "onboarding.q.work_city": "¿En qué ciudad quieres trabajar? Mantenla o escribe otra.",
    "onboarding.q.work_city_empty": "¿En qué ciudad quieres trabajar?",
    "onboarding.q.country": "¿Y en qué país? (p. ej. España)",
    "onboarding.q.salary": (
        "¿Tu sueldo mínimo bruto anual? Escribe un número (p. ej. 30000) o pulsa "
        "Omitir."
    ),
    "onboarding.q.currency": "¿En qué moneda? Pulsa una o escribe su código de 3 letras.",
    "onboarding.q.offer_langs": (
        "¿En qué idiomas puede estar una oferta? Las que exijan otro idioma se "
        "descartan. Márcalos y pulsa Listo."
    ),
    "onboarding.q.portals": (
        "¿Qué portales de empleo alimentan el bot? Pulsa un portal para leer su "
        "aviso sobre las condiciones de uso y activarlo; pulsa uno activo para "
        "desactivarlo. Luego pulsa Listo."
    ),
    "onboarding.q.portal_terms": "{warning}",
    "onboarding.q.cv_pdfs": (
        "Ahora tus CV base: el bot entrega estos PDF tal cual. Envía un PDF "
        "como archivo (📎 → Archivo) por cada puesto e idioma de la lista, de "
        "{mb} MB y {pages} páginas como máximo. Pulsa una línea para elegir a "
        "cuál va el siguiente PDF. Los omitidos se quedan sin CV hasta que lo añadas."
    ),
    "onboarding.cv.send": "Envía el PDF de: {slot}",
    "onboarding.cv.all_set": "Todo listo. Pulsa una línea para cambiar su PDF, o Listo.",
    "onboarding.cv.received": "✅ PDF recibido para {slot} ({pages}).",
    "onboarding.cv.pages.one": "1 página",
    "onboarding.cv.pages.other": "{n} páginas",
    "onboarding.cv.summary_missing": "sin CV para {slots}",
    "onboarding.cv.summary_kept": "ya guardados: {slots}",
    "onboarding.cv.existing": "(ya guardado)",
    "onboarding.cv.saved": "CV base guardados en cv_base_pdfs/: {count}.",
    "onboarding.cv.not_deliverable": (
        "Sin CV base para {slots}: el bot no podrá entregar un CV para ellos "
        "hasta que añadas uno (/configurar cvs, o cv_base_pdfs/manifest.json a mano)."
    ),
    "onboarding.btn.skip_one": "Omitir este ⏭",
    "onboarding.btn.skip_all": "Omitir todos ⏭⏭",
    "onboarding.edit.cv_pdfs": "CV base",
    "onboarding.err.cv_not_pdf": "⚠️ Eso no es un PDF legible. Envía el CV como archivo PDF.",
    "onboarding.err.cv_too_big": "⚠️ Ese archivo es demasiado grande: {mb} MB como máximo.",
    "onboarding.err.cv_pages": "⚠️ Ese PDF tiene demasiadas páginas: {pages} como máximo.",
    "onboarding.err.cv_download": "⚠️ No he podido descargar ese archivo de Telegram. Inténtalo de nuevo.",
    "onboarding.err.cv_pick_slot": "⚠️ Primero pulsa el puesto e idioma de este PDF.",
    "onboarding.q.edit": "¿Qué respuesta quieres cambiar?",
    "onboarding.q.summary": "Revisa tus respuestas:\n\n{summary}",
    "onboarding.err.name": "⚠️ Escribe tu nombre (de 1 a 100 caracteres).",
    "onboarding.err.email": "⚠️ Eso no parece un email (nombre@ejemplo.com).",
    "onboarding.err.phone": "⚠️ Eso no parece un teléfono (dígitos, +, espacios).",
    "onboarding.err.text": "⚠️ Escribe una respuesta (de 1 a 100 caracteres).",
    "onboarding.err.linkedin": (
        "⚠️ Eso no parece una dirección web (https://linkedin.com/in/…)."
    ),
    "onboarding.err.titles": "⚠️ Escribe al menos un puesto.",
    "onboarding.err.pick_one": "⚠️ Elige al menos una opción.",
    "onboarding.err.salary": "⚠️ Escribe un número entero, p. ej. 30000, o pulsa Omitir.",
    "onboarding.err.currency": "⚠️ Escribe un código de moneda de 3 letras, p. ej. EUR.",
    "onboarding.level.junior": "Junior",
    "onboarding.level.mid": "Mid",
    "onboarding.level.senior": "Senior",
    "onboarding.mode.remote": "Remoto",
    "onboarding.mode.hybrid": "Híbrido",
    "onboarding.mode.onsite": "Presencial",
    "onboarding.olang.es": "Español",
    "onboarding.olang.en": "Inglés",
    "onboarding.olang.ca": "Catalán",
    "onboarding.olang.fr": "Francés",
    "onboarding.olang.de": "Alemán",
    "onboarding.olang.it": "Italiano",
    "onboarding.olang.pt": "Portugués",
    "onboarding.lang.en": "Inglés",
    "onboarding.lang.es": "Español",
    "onboarding.custom": "Otros: {titles}",
    "onboarding.edit.lang": "Idioma",
    "onboarding.edit.name": "Nombre",
    "onboarding.edit.email": "Email",
    "onboarding.edit.phone": "Teléfono",
    "onboarding.edit.city": "Ciudad",
    "onboarding.edit.linkedin": "LinkedIn",
    "onboarding.edit.families": "Puestos",
    "onboarding.edit.level": "Nivel",
    "onboarding.edit.work_modes": "Modalidad",
    "onboarding.edit.work_city": "Ciudad de trabajo",
    "onboarding.edit.country": "País",
    "onboarding.edit.salary": "Sueldo",
    "onboarding.edit.offer_langs": "Idiomas de las ofertas",
    "onboarding.edit.portals": "Portales",
    "onboarding.btn.other_lang": "✏️ Otros idiomas…",
    "onboarding.q.spoken_langs": (
        "¿Qué idiomas hablas? Márcalos, o añade otros con «Otros idiomas», y "
        "pulsa Listo. Después indicas el nivel de cada uno."
    ),
    "onboarding.q.spoken_other": (
        "Escribe los otros idiomas que hablas separados por comas (p. ej. euskera, "
        "neerlandés)."
    ),
    "onboarding.q.spoken_levels": "¿Tu nivel de {language}?",
    "onboarding.q.skills": (
        "¿Tus habilidades o stack principales, separados por comas (p. ej. Python, "
        "SQL, Docker)?"
    ),
    "onboarding.q.exp_years": "¿Cuántos años de experiencia profesional tienes?",
    "onboarding.q.last_role": (
        "¿Tu último puesto (o el actual)? (opcional: pulsa Omitir si lo prefieres)"
    ),
    "onboarding.q.last_company": "¿Y la empresa? (opcional: pulsa Omitir si lo prefieres)",
    "onboarding.q.education": (
        "¿Tu formación más alta, en una línea (p. ej. Grado en Ingeniería "
        "Informática, Universitat de València)? (opcional: pulsa Omitir si lo prefieres)"
    ),
    "onboarding.err.skills": (
        "⚠️ Escribe de 1 a 20 habilidades separadas por comas, de 50 caracteres "
        "como máximo cada una."
    ),
    "onboarding.err.languages": (
        "⚠️ Escribe de 1 a 5 idiomas separados por comas, de 40 caracteres como "
        "máximo cada uno."
    ),
    "onboarding.cefr.native": "Nativo",
    "onboarding.cefr.C2": "C2",
    "onboarding.cefr.C1": "C1",
    "onboarding.cefr.B2": "B2",
    "onboarding.cefr.B1": "B1",
    "onboarding.cefr.A2": "A2",
    "onboarding.years.0": "0",
    "onboarding.years.lt1": "<1",
    "onboarding.years.1-2": "1-2",
    "onboarding.years.3-5": "3-5",
    "onboarding.years.6-10": "6-10",
    "onboarding.years.10plus": "10+",
    "onboarding.summary.years.0": "0 años",
    "onboarding.summary.years.lt1": "Menos de 1 año",
    "onboarding.summary.years.1-2": "1-2 años",
    "onboarding.summary.years.3-5": "3-5 años",
    "onboarding.summary.years.6-10": "6-10 años",
    "onboarding.summary.years.10plus": "10+ años",
    "onboarding.edit.spoken_langs": "Idiomas que hablas",
    "onboarding.edit.skills": "Habilidades",
    "onboarding.edit.exp_years": "Experiencia",
    "onboarding.edit.last_role": "Último puesto",
    "onboarding.edit.education": "Formación",
    "onboarding.none": "—",
    "onboarding.summary.salary": "{amount} {currency} brutos/año",
    "onboarding.portals.terms_missing": (
        "¿Activar {portal}? Antes comprueba que sus condiciones de uso permiten el "
        "acceso automatizado; cumplirlas es responsabilidad tuya."
    ),
    "onboarding.portals.line": (
        "Para activar estos portales, añade esta línea a config.local.toml y "
        "reinicia el bot:\n{line}"
    ),
    "onboarding.cancelled": "Configuración cancelada. No se ha guardado nada.",
    "onboarding.expired": "Esta configuración ha caducado. Envía /configurar para empezar de nuevo.",
    "onboarding.stale": "Ese botón es de una pregunta anterior; aquí tienes la actual.",
    "onboarding.no_profile_dir": (
        "La configuración guiada escribe en un directorio de perfil y este bot no "
        "tiene ninguno (profile_dir en config.local.toml). Ejecuta antes "
        "`uv run cli.py init`."
    ),
    "onboarding.saved": ("✅ He guardado preferences.toml y profile.toml."),
    "onboarding.saved_language": "El idioma del bot queda guardado en config.local.toml.",
    "onboarding.missing": "Aún falta para que el bot funcione con normalidad: {missing}.",
    "onboarding.write_failed": "⚠️ No he podido guardar tus respuestas: {error}",
    "onboarding.write_undone": (
        "No se ha cambiado nada; tus archivos subidos se conservan, así que "
        "puedes corregirlo y pulsar Confirmar de nuevo."
    ),
    "onboarding.rollback_failed": (
        "⚠️ Algunos archivos no se han podido dejar como estaban: {error}. "
        "Revisa la carpeta del perfil antes de confirmar de nuevo."
    ),
    "onboarding.reload_failed": (
        "⚠️ Los archivos se han guardado pero el bot no ha podido recargarlos: {error}"
    ),
    "onboarding.portals.enabled": (
        "Portales activos ahora: {portals} (guardado en config.local.toml). En "
        "la extensión, abre Opciones › Portales y da el permiso del navegador "
        "a cada uno; se pide una vez por portal."
    ),
    "onboarding.next_step": (
        "Siguiente paso: en la extensión, abre Opciones › Búsquedas y pulsa "
        "«Sugerir desde mi perfil» para añadir búsquedas hechas con estas respuestas."
    ),
    "onboarding.identity.kept": (
        "ℹ️ Tu claim de identidad ({claim} en claims.json) se había editado a mano, "
        "así que lo he dejado tal cual: los CV y las cartas siguen usando sus "
        "datos de contacto. Para cambiarlos, edita el texto de ese claim en "
        "claims.json."
    ),
    "onboarding.identity.kept_unrecorded": (
        "ℹ️ Tu claim de identidad ({claim} en claims.json) se ha mantenido tal "
        "cual porque no lo creó /configurar: los CV y las cartas siguen usando "
        "sus datos de contacto. Para cambiarlos, edita el texto de ese claim en "
        "claims.json."
    ),
}
