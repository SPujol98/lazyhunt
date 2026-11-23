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
    "onboarding.q.country_other": "Type the country (e.g. Portugal).",
    "onboarding.btn.country_from": "{country} (from {city})",
    "onboarding.btn.country_other": "✏️ Other…",
    "onboarding.country.spain": "Spain",
    "onboarding.country.portugal": "Portugal",
    "onboarding.country.france": "France",
    "onboarding.country.germany": "Germany",
    "onboarding.country.italy": "Italy",
    "onboarding.country.uk": "United Kingdom",
    "onboarding.country.ireland": "Ireland",
    "onboarding.country.netherlands": "Netherlands",
    "onboarding.country.belgium": "Belgium",
    "onboarding.country.switzerland": "Switzerland",
    "onboarding.country.austria": "Austria",
    "onboarding.country.poland": "Poland",
    "onboarding.country.usa": "United States",
    "onboarding.country.canada": "Canada",
    "onboarding.country.mexico": "Mexico",
    "onboarding.country.argentina": "Argentina",
    "onboarding.country.colombia": "Colombia",
    "onboarding.country.chile": "Chile",
    "onboarding.q.salary": (
        "Your minimum gross annual salary? Type a number (e.g. 30000) or Skip."
    ),
    "onboarding.q.currency": (
        "In which currency? Tap one (the one for your country is ticked), or "
        "“Other” to type its 3-letter code. One currency: the one your "
        "minimum salary is in."
    ),
    "onboarding.q.currency_other": "Type the 3-letter ISO code of the currency (e.g. SEK).",
    "onboarding.btn.other_currency": "✏️ Other…",
    "onboarding.salary.reference": (
        "💡 Typical for {role} {level} in {city}: {low}–{high} EUR gross/year "
        "(source: {source}). Use {low}?"
    ),
    "onboarding.salary.no_reference": (
        "ℹ️ No salary reference for your profile, so no suggestion."
    ),
    "onboarding.btn.use_salary": "✅ Use {amount}",
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
        "Now your base CVs: the bot sends this CV, adapted, for each offer; "
        "one per position type × language (the list below). For each one, "
        "upload your own PDF (📎 → File, at most {mb} MB and {pages} pages), "
        "create one with lazyhunt from your answers, or leave it for later. "
        "Tap a line to work on that one."
    ),
    "onboarding.cv.upload_hint": "📎 Send the PDF now as a file (📎 → File).",
    "onboarding.btn.cv_upload": "📎 Upload my PDF",
    "onboarding.btn.cv_create": "✨ Create one with lazyhunt",
    "onboarding.btn.cv_later": "Not now ⏭",
    "onboarding.btn.cv_more": "➕ Create another version",
    "onboarding.btn.cv_create_now": "✨ Create one now",
    "onboarding.btn.cv_skip_anyway": "Skip anyway ⏭⏭",
    "onboarding.btn.letter": "✨ Create a base cover letter",
    "onboarding.btn.letter_on": "Base cover letter: create it on Confirm",
    "onboarding.cv.tpl.classic": "Classic",
    "onboarding.cv.tpl.modern": "Modern",
    "onboarding.cv.tpl.compact": "Compact",
    "onboarding.cv.tpl_about.classic": (
        "centred header and ruled sections; a safe choice anywhere."
    ),
    "onboarding.cv.tpl_about.modern": (
        "your name in blue, left-aligned, coloured headings; fresh but sober."
    ),
    "onboarding.cv.tpl_about.compact": (
        "smaller type and tight spacing; the most content on one page."
    ),
    "onboarding.cv.templates": (
        "Pick a template for {slot}. All three are one-column, ATS-friendly "
        "PDFs (selectable text, standard headings, no photo or tables) built "
        "only from your answers:\n{templates}\n\nIt is a starting point: "
        "add projects and achievements later (/setup cvs replaces it)."
    ),
    "onboarding.cv.created": (
        "✨ CV created for {slot} ({pages} page(s)). It is a starting point: "
        "add projects and achievements later."
    ),
    "onboarding.cv.created_mark": "✨ {template}",
    "onboarding.cv.created_saved": (
        "CVs created with lazyhunt: {slots}. They are a starting point: add "
        "projects and achievements later and replace them with /setup cvs."
    ),
    "onboarding.cv.skip_warning": (
        "⚠️ No CV yet for: {slots}. Without a CV the bot cannot send any — "
        "create one now?"
    ),
    "onboarding.cv.more": (
        "Another base CV, for another position type or language. Adding one "
        "also adds that position to your searches, or that language to the "
        "accepted offer languages, when they were not there yet."
    ),
    "onboarding.cv.added": "➕ Added {slot}: upload or create its CV.",
    "onboarding.err.cv_create": "⚠️ Could not create that CV. Try another template or upload a PDF.",
    "onboarding.letter.summary": "a base cover letter, from your answers",
    "onboarding.letter.saved": (
        "Base cover letter created (cover_letter_base.toml) from your answers: "
        "a short starting point you can edit."
    ),
    "onboarding.edit.cover_letter": "Cover letter",
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
        "Your main skills or stack, separated by commas (e.g. Python, SQL, "
        "Docker)? Up to 30; tip: the 10-15 strongest work best."
    ),
    "onboarding.q.exp_years": "How many years of professional experience do you have?",
    "onboarding.q.last_role": (
        "Your last (or current) job title? (optional: Skip if you prefer)"
    ),
    "onboarding.q.last_company": "And the company? (optional: Skip if you prefer)",
    "onboarding.q.education": (
        "Your education, up to 5 entries: one per line, or separated by commas "
        "(e.g. BSc Computer Science — University of Valencia, AWS Cloud "
        "Practitioner)? Use one line per entry when an entry has a comma. "
        "(optional: Skip if you prefer)"
    ),
    "onboarding.err.skills": (
        "⚠️ Please type 1-30 skills separated by commas, each at most 50 characters."
    ),
    "onboarding.err.education": (
        "⚠️ Please type 1-5 education entries (one per line or separated by "
        "commas), each at most 100 characters."
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
    "onboarding.q.country_other": "Escribe el país (p. ej. Portugal).",
    "onboarding.btn.country_from": "{country} (por {city})",
    "onboarding.btn.country_other": "✏️ Otro…",
    "onboarding.country.spain": "España",
    "onboarding.country.portugal": "Portugal",
    "onboarding.country.france": "Francia",
    "onboarding.country.germany": "Alemania",
    "onboarding.country.italy": "Italia",
    "onboarding.country.uk": "Reino Unido",
    "onboarding.country.ireland": "Irlanda",
    "onboarding.country.netherlands": "Países Bajos",
    "onboarding.country.belgium": "Bélgica",
    "onboarding.country.switzerland": "Suiza",
    "onboarding.country.austria": "Austria",
    "onboarding.country.poland": "Polonia",
    "onboarding.country.usa": "Estados Unidos",
    "onboarding.country.canada": "Canadá",
    "onboarding.country.mexico": "México",
    "onboarding.country.argentina": "Argentina",
    "onboarding.country.colombia": "Colombia",
    "onboarding.country.chile": "Chile",
    "onboarding.q.salary": (
        "¿Tu sueldo mínimo bruto anual? Escribe un número (p. ej. 30000) o pulsa "
        "Omitir."
    ),
    "onboarding.q.currency": (
        "¿En qué moneda? Pulsa una (va marcada la de tu país), u «Otra» para "
        "escribir su código de 3 letras. Una sola moneda: la de tu sueldo mínimo."
    ),
    "onboarding.q.currency_other": "Escribe el código ISO de 3 letras de la moneda (p. ej. SEK).",
    "onboarding.btn.other_currency": "✏️ Otra…",
    "onboarding.salary.reference": (
        "💡 Lo habitual para {role} {level} en {city}: {low}–{high} EUR brutos/año "
        "(fuente: {source}). ¿Usar {low}?"
    ),
    "onboarding.salary.no_reference": (
        "ℹ️ No hay referencia salarial para tu perfil, así que no sugiero ninguna cifra."
    ),
    "onboarding.btn.use_salary": "✅ Usar {amount}",
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
        "Ahora tus CV base: el bot envía este CV, adaptado, en cada oferta; "
        "uno por tipo de puesto × idioma (la lista de abajo). Para cada uno, "
        "sube tu PDF (📎 → Archivo, de {mb} MB y {pages} páginas como máximo), "
        "créalo con lazyhunt a partir de tus respuestas o déjalo para más tarde. "
        "Pulsa una línea para trabajar en ese."
    ),
    "onboarding.cv.upload_hint": "📎 Envía ahora el PDF como archivo (📎 → Archivo).",
    "onboarding.btn.cv_upload": "📎 Subir mi PDF",
    "onboarding.btn.cv_create": "✨ Crear uno con lazyhunt",
    "onboarding.btn.cv_later": "Ahora no ⏭",
    "onboarding.btn.cv_more": "➕ Crear otra versión",
    "onboarding.btn.cv_create_now": "✨ Crear uno ahora",
    "onboarding.btn.cv_skip_anyway": "Omitir igualmente ⏭⏭",
    "onboarding.btn.letter": "✨ Crear una carta de presentación base",
    "onboarding.btn.letter_on": "Carta base: se crea al Confirmar",
    "onboarding.cv.tpl.classic": "Clásica",
    "onboarding.cv.tpl.modern": "Moderna",
    "onboarding.cv.tpl.compact": "Compacta",
    "onboarding.cv.tpl_about.classic": (
        "cabecera centrada y secciones con línea; sirve en cualquier sitio."
    ),
    "onboarding.cv.tpl_about.modern": (
        "tu nombre en azul, alineado a la izquierda, títulos en color; fresca pero sobria."
    ),
    "onboarding.cv.tpl_about.compact": (
        "letra más pequeña y espaciado justo; el máximo contenido en una página."
    ),
    "onboarding.cv.templates": (
        "Elige una plantilla para {slot}. Las tres son PDF de una columna, "
        "aptos para ATS (texto seleccionable, títulos estándar, sin foto ni "
        "tablas) hechos solo con tus respuestas:\n{templates}\n\nEs un punto "
        "de partida: añade proyectos y logros más adelante (/configurar cvs lo sustituye)."
    ),
    "onboarding.cv.created": (
        "✨ CV creado para {slot} ({pages} página(s)). Es un punto de partida: "
        "añade proyectos y logros más adelante."
    ),
    "onboarding.cv.created_mark": "✨ {template}",
    "onboarding.cv.created_saved": (
        "CV creados con lazyhunt: {slots}. Son un punto de partida: añade "
        "proyectos y logros más adelante y sustitúyelos con /configurar cvs."
    ),
    "onboarding.cv.skip_warning": (
        "⚠️ Aún sin CV: {slots}. Sin un CV el bot no puede enviar ninguno — "
        "¿lo creamos ahora?"
    ),
    "onboarding.cv.more": (
        "Otro CV base, para otro tipo de puesto o idioma. Añadirlo también "
        "añade ese puesto a tus búsquedas, o ese idioma a los idiomas de "
        "oferta aceptados, si aún no estaban."
    ),
    "onboarding.cv.added": "➕ Añadido {slot}: sube o crea su CV.",
    "onboarding.err.cv_create": "⚠️ No he podido crear ese CV. Prueba otra plantilla o sube un PDF.",
    "onboarding.letter.summary": "una carta de presentación base, con tus respuestas",
    "onboarding.letter.saved": (
        "Carta de presentación base creada (cover_letter_base.toml) con tus "
        "respuestas: un punto de partida breve que puedes editar."
    ),
    "onboarding.edit.cover_letter": "Carta de presentación",
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
        "SQL, Docker)? Hasta 30; consejo: funcionan mejor las 10-15 más fuertes."
    ),
    "onboarding.q.exp_years": "¿Cuántos años de experiencia profesional tienes?",
    "onboarding.q.last_role": (
        "¿Tu último puesto (o el actual)? (opcional: pulsa Omitir si lo prefieres)"
    ),
    "onboarding.q.last_company": "¿Y la empresa? (opcional: pulsa Omitir si lo prefieres)",
    "onboarding.q.education": (
        "¿Tu formación, hasta 5 entradas: una por línea o separadas por comas "
        "(p. ej. Grado en Ingeniería Informática — Universitat de València, AWS "
        "Cloud Practitioner)? Usa una línea por entrada si alguna lleva coma. "
        "(opcional: pulsa Omitir si lo prefieres)"
    ),
    "onboarding.err.skills": (
        "⚠️ Escribe de 1 a 30 habilidades separadas por comas, de 50 caracteres "
        "como máximo cada una."
    ),
    "onboarding.err.education": (
        "⚠️ Escribe de 1 a 5 entradas de formación (una por línea o separadas "
        "por comas), de 100 caracteres como máximo cada una."
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
