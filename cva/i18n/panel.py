"""HTML panel, prep pack, calibration UI, health messages and CLI: bot-i18n T4.

Keys are prefixed ``panel.`` (sub-areas: ``panel.prep``, ``panel.cal``,
``panel.health``, ``panel.gate``, ``panel.salary``, ``panel.gmail``,
``panel.cli``). Entries are declared once as ``(key, en, es)`` and split into
the ``EN`` / ``ES`` catalogs the registry in :mod:`cva.i18n` expects.
"""

from typing import Final

_ENTRIES: Final[tuple[tuple[str, str, str], ...]] = (
    # --- HTML panel -------------------------------------------------------
    ("panel.title", "Applications dashboard", "Panel de candidaturas"),
    ("panel.generated", "Generated {stamp}", "Generado {stamp}"),
    ("panel.no_data", "No data.", "Sin datos."),
    ("panel.tile.analyzed", "Analyzed", "Analizadas"),
    ("panel.tile.sent", "Sent", "Enviadas"),
    ("panel.tile.response_rate", "Response rate", "Tasa de respuesta"),
    ("panel.tile.interviews", "Interviews", "Entrevistas"),
    ("panel.tile.offers", "Offers", "Ofertas"),
    ("panel.funnel.title", "Funnel", "Embudo"),
    ("panel.funnel.analyzed", "Analyzed", "Analizadas"),
    ("panel.funnel.sent", "Sent", "Enviadas"),
    ("panel.funnel.response", "Response", "Respuesta"),
    ("panel.funnel.interview", "Interview", "Entrevista"),
    ("panel.funnel.offer", "Offer", "Oferta"),
    ("panel.by_status", "By status", "Por estado"),
    ("panel.by_portal", "By portal", "Por portal"),
    ("panel.sent_per_week", "Sent per week", "Enviadas por semana"),
    ("panel.by_week", "By week", "Por semana"),
    ("panel.match", "Match", "Match"),
    ("panel.by_match", "By match", "Por match"),
    ("panel.alerts", "Alerts", "Alertas"),
    ("panel.no_alerts", "No alerts.", "Sin alertas."),
    ("panel.deadlines", "Upcoming deadlines", "Fecha límite próxima"),
    ("panel.silent", "Sent, no reply", "Enviadas sin respuesta"),
    ("panel.deadline_today", "due today", "vence en 0 día(s)"),
    ("panel.deadline_one", "due in {n} day", "vence en {n} día(s)"),
    ("panel.deadline_many", "due in {n} days", "vence en {n} día(s)"),
    ("panel.silent_since", "no news for {n} days", "sin novedades desde hace {n} días"),
    ("panel.offers", "Offers", "Ofertas"),
    ("panel.search", "Search company, role…", "Buscar empresa, puesto…"),
    ("panel.filter.status", "Status", "Estado"),
    ("panel.filter.profile", "Profile", "Perfil"),
    ("panel.filter.portal", "Portal", "Portal"),
    ("panel.col.company", "Company", "Empresa"),
    ("panel.col.role", "Role", "Puesto"),
    ("panel.col.profile", "Profile", "Perfil"),
    ("panel.col.portal", "Portal", "Portal"),
    ("panel.col.status", "Status", "Estado"),
    ("panel.col.match", "Match", "Match"),
    ("panel.col.sent", "Sent", "Envío"),
    ("panel.col.updated", "Updated", "Update"),
    ("panel.js.offer", "offer", "oferta"),
    ("panel.js.of", " of ", " de "),
    ("panel.ago_today", "today", "hoy"),
    ("panel.ago_one", "1 day ago", "hace 1 día"),
    ("panel.ago_many", "{days} days ago", "hace {days} días"),
    ("panel.rescue_cmd", "/rescue {id}", "/rescatar {id}"),
    ("panel.family_ai", "AI", "IA"),
    ("panel.discarded.score", "Discarded by score", "Descartadas por nota"),
    ("panel.discarded.last_days", "(last {days} days)", "(últimos {days} días)"),
    ("panel.discarded.wall", "Discarded by wall", "Descartadas por muro"),
    (
        "panel.discarded.summary",
        (
            "{count} discarded by score this week · {near} within {points} points "
            "of the threshold"
        ),
        (
            "{count} descartadas por nota esta semana · {near} a menos de {points} "
            "puntos del umbral"
        ),
    ),
    (
        "panel.discarded.none_score",
        "Nothing discarded by score this week.",
        "Nada descartado por nota esta semana.",
    ),
    (
        "panel.discarded.none_wall",
        "Nothing discarded by wall this week.",
        "Nada descartado por muro esta semana.",
    ),
    ("panel.col.score_threshold", "Score / threshold", "Nota / umbral"),
    ("panel.col.reason", "Reason", "Motivo"),
    ("panel.col.seen", "Seen", "Visto"),
    ("panel.col.rescue", "Rescue", "Rescatar"),
    ("panel.col.location", "Location", "Ubicación"),
    ("panel.col.wall", "Wall", "Muro"),
    # Estado names shown by the panel (DB values stay language-neutral).
    ("panel.estado.pendiente_analisis", "pending analysis", "pendiente analisis"),
    ("panel.estado.analizada", "analyzed", "analizada"),
    ("panel.estado.preparada", "prepared", "preparada"),
    ("panel.estado.enviada", "sent", "enviada"),
    ("panel.estado.screening", "screening", "screening"),
    ("panel.estado.entrevista_tecnica", "technical interview", "entrevista tecnica"),
    ("panel.estado.entrevista_final", "final interview", "entrevista final"),
    ("panel.estado.oferta", "offer", "oferta"),
    ("panel.estado.aceptada", "accepted", "aceptada"),
    ("panel.estado.sin_respuesta", "no response", "sin respuesta"),
    ("panel.estado.rechazada", "rejected", "rechazada"),
    ("panel.estado.retirada", "withdrawn", "retirada"),
    ("panel.estado.descartada", "discarded", "descartada"),
    # --- interview prep pack ------------------------------------------------
    ("panel.prep.stage.rrhh", "HR", "RR. HH."),
    ("panel.prep.stage.tecnica", "Technical", "Técnica"),
    ("panel.prep.stage.final", "Final", "Final"),
    (
        "panel.prep.s1",
        "What they will ask you to explain",
        "Lo que te van a pedir que expliques",
    ),
    ("panel.prep.s2", "Likely topics for this stage", "Temas probables para esta fase"),
    (
        "panel.prep.topics_note",
        "These are likely topics, not certain questions.",
        "Son temas probables, no preguntas seguras.",
    ),
    (
        "panel.prep.s3",
        "Your gaps and how to answer honestly",
        "Tus huecos y cómo responder con honestidad",
    ),
    ("panel.prep.acknowledge", "Acknowledge", "Reconoce"),
    ("panel.prep.adjacent", "Related experience", "Experiencia cercana"),
    ("panel.prep.learning", "How you would cover it", "Cómo lo cubrirías"),
    ("panel.prep.no_gaps", "No gaps flagged.", "Sin huecos señalados."),
    ("panel.prep.s4", "STAR stories that fit", "Historias STAR que encajan"),
    ("panel.prep.incomplete", "incomplete", "incompleta"),
    ("panel.prep.missing", ": missing {fields}", ": falta {fields}"),
    ("panel.prep.finish", "; complete it with /star", "; complétala con /star"),
    (
        "panel.prep.no_stories",
        "None of the stories in your bank fits yet.",
        "Ninguna historia de tu banco encaja todavía.",
    ),
    ("panel.prep.s5", "Salary and availability", "Salario y disponibilidad"),
    ("panel.prep.s6", "Questions to ask them", "Preguntas para hacerles"),
    ("panel.prep.s7", "Company facts", "Datos de la empresa"),
    ("panel.prep.no_facts", "no verified data", "sin datos verificados"),
    (
        "panel.prep.flags",
        "⚠️ Figures not backed by your data: {flags}",
        "⚠️ Cifras sin respaldo en tus datos: {flags}",
    ),
    (
        "panel.prep.heading",
        "🎯 {label} preparation · #{id}",
        "🎯 Preparación {label} · #{id}",
    ),
    (
        "panel.prep.doc_title",
        "Preparation #{id} · {label}",
        "Preparación #{id} · {label}",
    ),
    (
        "panel.prep.footer",
        (
            "Generated from your data; check every point before using it. There is "
            "no guarantee they will ask this."
        ),
        (
            "Generado con tus datos; revisa cada punto antes de usarlo. No hay "
            "garantía de que pregunten esto."
        ),
    ),
    (
        "panel.prep.file",
        "preparation_{id}_{stage}.html",
        "preparacion_{id}_{stage}.html",
    ),
    (
        "panel.prep.summary_head",
        "🎯 Preparation #{id} · {label}",
        "🎯 Preparación #{id} · {label}",
    ),
    (
        "panel.prep.summary_topics",
        "Key topics (likely, not certain):",
        "Temas clave (probables, no seguros):",
    ),
    (
        "panel.prep.summary_tail",
        "Full details in the attached HTML.",
        "Detalle completo en el HTML adjunto.",
    ),
    # --- calibration UI -------------------------------------------------------
    ("panel.cal.outcome.screening", "screening", "screening"),
    (
        "panel.cal.outcome.entrevista_tecnica",
        "technical interview",
        "entrevista técnica",
    ),
    ("panel.cal.outcome.entrevista_final", "final interview", "entrevista final"),
    ("panel.cal.outcome.oferta", "offer", "oferta"),
    ("panel.cal.outcome.aceptada", "accepted", "aceptada"),
    ("panel.cal.outcome.rechazada", "rejected", "rechazada"),
    ("panel.cal.outcome.sin_respuesta", "no response", "sin respuesta"),
    ("panel.cal.outcome.retirada", "withdrawn", "retirada"),
    ("panel.cal.reason.requisitos", "Requirements", "Requisitos"),
    ("panel.cal.reason.stack", "Stack", "Stack"),
    ("panel.cal.reason.idioma", "Language", "Idioma"),
    ("panel.cal.reason.otro", "Other / not sure", "Otro / no sé"),
    (
        "panel.cal.question",
        (
            "🎯 Use this offer to calibrate the score?\n"
            "Bot score: {score}/100 · Result: {result}\n"
            "Only the score, the role and the result are used; never the offer text "
            "or your notes. You can remove it later in /calibration."
        ),
        (
            "🎯 ¿Usar esta oferta para calibrar la nota?\n"
            "Nota del bot: {score}/100 · Resultado: {result}\n"
            "Solo se usan la nota, el rol y el resultado; nunca el texto de la "
            "oferta ni tus notas. Puedes quitarla luego en /calibracion."
        ),
    ),
    ("panel.cal.yes", "✅ Yes", "✅ Sí"),
    ("panel.cal.no", "No", "No"),
    (
        "panel.cal.reason_question",
        (
            "🎯 Why were you rejected? Only if it was a fit issue can it help "
            "calibrate the score."
        ),
        (
            "🎯 ¿Por qué te rechazaron? Solo si fue por encaje puede servir para "
            "calibrar la nota."
        ),
    ),
    (
        "panel.cal.empty",
        (
            "🎯 There are no active calibration examples.\n"
            "When you record a result with /outcome, the bot will ask whether you "
            "want to use it to calibrate the score."
        ),
        (
            "🎯 No hay ejemplos de calibración activos.\n"
            "Cuando anotes un resultado con /outcome, el bot te preguntará si "
            "quieres usarlo para calibrar la nota."
        ),
    ),
    (
        "panel.cal.list_head",
        "🎯 Active calibration examples ({total}):",
        "🎯 Ejemplos de calibración activos ({total}):",
    ),
    (
        "panel.cal.list_line",
        "#{id} · score {score} · {result}{role}",
        "#{id} · nota {score} · {result}{role}",
    ),
    (
        "panel.cal.positives",
        (
            "• Positives (you reached an interview): {count} with a score below the "
            "threshold."
        ),
        (
            "• Positivos (llegaste a entrevista): {count} con nota por debajo del "
            "umbral."
        ),
    ),
    (
        "panel.cal.negatives",
        "• Negatives (rejected on fit): {count} with a score above the threshold.",
        "• Negativos (rechazo por encaje): {count} con nota por encima del umbral.",
    ),
    ("panel.cal.deactivate", "🗑 Deactivate (#{id})", "🗑 Desactivar (#{id})"),
    # --- beacon health alerts ---------------------------------------------------
    (
        "panel.health.yield_drop",
        (
            "⚠️ The beacon «{name}» used to return offers and now returns 0 "
            "({runs} scans in a row). The portal may have changed or the search "
            "may be broken."
        ),
        (
            "⚠️ El beacon «{name}» devolvía ofertas y ahora da 0 "
            "({runs} escaneos seguidos). "
            "Puede que el portal haya cambiado o la búsqueda esté rota."
        ),
    ),
    (
        "panel.health.parser_broken",
        (
            "⚠️ The beacon «{name}»: more than {ratio} of the offers arrive without "
            "a company (did the portal's HTML change?)."
        ),
        (
            "⚠️ El beacon «{name}»: más del {ratio} de las "
            "ofertas llegan sin empresa (¿cambió el HTML del portal?)."
        ),
    ),
    (
        "panel.health.unconfirmed_streak",
        (
            "⚠️ The beacon «{name}» has had {runs} unconfirmed empty scans in a "
            "row. It may be a temporary {portal} block or the portal may have "
            "changed its HTML; open it by hand to check."
        ),
        (
            "⚠️ El beacon «{name}» lleva {runs} escaneos vacíos "
            "sin confirmar. Puede ser un bloqueo temporal de {portal} o que el "
            "portal haya cambiado su HTML; ábrelo a mano para comprobarlo."
        ),
    ),
    (
        "panel.health.unconfirmed_streak_generic",
        (
            "⚠️ The beacon «{name}» has had {runs} unconfirmed empty scans in a "
            "row. It may be a temporary block by the portal or it may have "
            "changed its HTML; open it by hand to check."
        ),
        (
            "⚠️ El beacon «{name}» lleva {runs} escaneos vacíos "
            "sin confirmar. Puede ser un bloqueo temporal del portal o que haya "
            "cambiado su HTML; ábrelo a mano para comprobarlo."
        ),
    ),
    # --- gate reasons (card text; the stored value stays Spanish) -----------------
    (
        "panel.gate.eligibility_eu_wording",
        "Mentions a non-EU eligibility requirement next to EU/Spain wording: check",
        "Menciona requisito de elegibilidad no UE junto a UE/España: revisa",
    ),
    (
        "panel.gate.eligibility_fail",
        "Requires citizenship/residence/work permit outside the EU",
        "Exige ciudadanía/residencia/permiso de trabajo fuera de la UE",
    ),
    (
        "panel.gate.eligibility_negated",
        "Mentions a non-EU eligibility requirement with a negation: check",
        "Menciona un requisito de elegibilidad no UE con una negación: revisa",
    ),
    (
        "panel.gate.clearance",
        "Asks for a security clearance",
        "Pide habilitación de seguridad (security clearance)",
    ),
    (
        "panel.gate.english_above_c1",
        "Asks for English above C1 (C2/native)",
        "Pide nivel de inglés por encima de C1 (C2/nativo)",
    ),
    (
        "panel.gate.language_fail",
        "Requires {labels} (mandatory language outside {allowed})",
        "Exige {labels} (idioma obligatorio fuera de {allowed})",
    ),
    (
        "panel.gate.language_flag",
        "Mentions {labels} (nice to have or unclear): check",
        "Menciona {labels} (valorable o sin aclarar): revisa",
    ),
    (
        "panel.gate.remote_visits",
        "Remote with office visits outside {region} ({places}): check",
        "Remoto con visitas a la oficina fuera de {region} ({places}): revisa",
    ),
    (
        "panel.gate.several_places",
        "Several locations, some outside {region} ({places}): check",
        "Varias ubicaciones, alguna fuera de {region} ({places}): revisa",
    ),
    (
        "panel.gate.mode_outside_fail",
        "{mode} outside {region} ({places})",
        "{mode} fuera de {region} ({places})",
    ),
    (
        "panel.gate.mode_outside_flag",
        "{mode} outside {region} ({places}): check",
        "{mode} fuera de {region} ({places}): revisa",
    ),
    (
        "panel.gate.mentions_outside",
        (
            "Mentions {places} (outside {region}) without saying whether it is the "
            "workplace: check"
        ),
        (
            "Menciona {places} (fuera de {region}) sin aclarar si es el lugar de "
            "trabajo: revisa"
        ),
    ),
    (
        "panel.gate.no_place",
        "{mode} without a specific location: check",
        "{mode} sin ubicación concreta: revisa",
    ),
    ("panel.gate.mode.onsite", "On-site", "Presencial"),
    ("panel.gate.mode.hybrid", "Hybrid", "Híbrido"),
    ("panel.gate.mode.mixed", "Remote with on-site work", "Remoto con presencialidad"),
    ("panel.gate.mode.unknown", "No remote work stated", "Sin teletrabajo indicado"),
    ("panel.gate.region.catalonia", "Catalonia", "Cataluña"),
    ("panel.gate.lang.es", "Spanish", "español"),
    ("panel.gate.lang.ca", "Catalan", "catalán"),
    ("panel.gate.lang.en", "English", "inglés"),
    ("panel.gate.lang.de", "German", "alemán"),
    ("panel.gate.lang.fr", "French", "francés"),
    ("panel.gate.lang.it", "Italian", "italiano"),
    ("panel.gate.lang.pt", "Portuguese", "portugués"),
    ("panel.gate.lang.nl", "Dutch", "neerlandés"),
    ("panel.gate.lang.pl", "Polish", "polaco"),
    ("panel.gate.lang.sv", "Swedish", "sueco"),
    ("panel.gate.lang.da", "Danish", "danés"),
    ("panel.gate.lang.no", "Norwegian", "noruego"),
    ("panel.gate.lang.fi", "Finnish", "finés"),
    ("panel.gate.lang.ar", "Arabic", "árabe"),
    ("panel.gate.lang.zh", "Chinese", "chino"),
    ("panel.gate.lang.ja", "Japanese", "japonés"),
    ("panel.gate.lang.ru", "Russian", "ruso"),
    ("panel.gate.lang.tr", "Turkish", "turco"),
    ("panel.gate.lang.he", "Hebrew", "hebreo"),
    ("panel.gate.lang.cs", "Czech", "checo"),
    ("panel.gate.lang.ro", "Romanian", "rumano"),
    ("panel.gate.lang.el", "Greek", "griego"),
    ("panel.gate.lang.hu", "Hungarian", "húngaro"),
    # --- salary ------------------------------------------------------------------
    ("panel.salary.monthly_note", "monthly x{factor}", "mensual x{factor}"),
    # --- Gmail -------------------------------------------------------------------
    (
        "panel.gmail.auth_warning",
        (
            "⚠️ Gmail: the authorization has expired or been revoked, so I can't "
            "read your email.\nOn your PC run:\n{command}\n"
            "and copy the token to the bot's PC (see docs/gmail.md). I won't warn "
            "you again until it works again."
        ),
        (
            "⚠️ Gmail: la autorización ha caducado o se ha revocado, así que no puedo "
            "leer tu correo.\nEn tu PC ejecuta:\n{command}\n"
            "y copia el token al PC del bot (ver docs/gmail.md). No volveré a avisar "
            "hasta que vuelva a funcionar."
        ),
    ),
    (
        "panel.gmail.window_warning",
        (
            "⚠️ Gmail: there is so much recent email that it does not fit in a "
            "single read. I'll go through it in chunks over the next syncs "
            "(nothing is lost). I won't warn you about this again."
        ),
        (
            "⚠️ Gmail: hay tanto correo reciente que no cabe en una sola lectura. Lo "
            "voy revisando por tramos en las próximas sincronizaciones (no se pierde "
            "nada). No volveré a avisar por esto."
        ),
    ),
    (
        "panel.gmail.outage_warning",
        (
            "⚠️ Gmail: I can't classify your emails because the LLM has not "
            "responded for {runs} syncs in a row. I keep retrying; no email is "
            "discarded. I won't warn you again until it works again."
        ),
        (
            "⚠️ Gmail: no puedo clasificar tus correos porque el LLM no responde "
            "desde hace {runs} sincronizaciones seguidas. Sigo "
            "reintentando; no se descarta ningún correo. No volveré a avisar hasta que "
            "vuelva a funcionar."
        ),
    ),
    (
        "panel.gmail.no_token",
        "No Gmail token: run `{command}` on your PC.",
        "No hay token de Gmail: ejecuta `{command}` en tu PC.",
    ),
    (
        "panel.gmail.bad_token",
        "The Gmail token is not valid: run `{command}`.",
        "El token de Gmail no es válido: ejecuta `{command}`.",
    ),
    (
        "panel.gmail.no_refresh",
        "No refresh token: run `{command}`.",
        "Sin refresh token: ejecuta `{command}`.",
    ),
    (
        "panel.gmail.refresh_rejected",
        (
            "Gmail rejected the renewal (authorization revoked or expired): "
            "run `{command}`."
        ),
        (
            "Gmail rechazó la renovación (autorización revocada o caducada): "
            "ejecuta `{command}`."
        ),
    ),
    (
        "panel.gmail.auth_rejected",
        "Gmail rejected the authorization ({action}): run `{command}`.",
        "Gmail rechazó la autorización ({action}): ejecuta `{command}`.",
    ),
    (
        "panel.gmail.no_credentials",
        "I can't find the Google credentials file at {path}.",
        "No encuentro el fichero de credenciales de Google en {path}.",
    ),
    (
        "panel.gmail.bad_credentials",
        (
            "The Google credentials file is not valid ({path}): download the "
            "desktop OAuth client again. ({error})"
        ),
        (
            "El fichero de credenciales de Google no es válido ({path}): "
            "descarga de nuevo el cliente OAuth de escritorio. ({error})"
        ),
    ),
    (
        "panel.gmail.flow_failed",
        (
            "Gmail authorization was not completed (consent denied or OAuth "
            "error: {error}). Run the command again."
        ),
        (
            "No se completó la autorización de Gmail (consentimiento denegado o "
            "error de OAuth: {error}). Vuelve a ejecutar el comando."
        ),
    ),
    (
        "panel.gmail.no_refresh_token",
        (
            "Google did not return a refresh token; I have not touched the "
            "existing token. Revoke the app's access at "
            "https://myaccount.google.com/permissions and repeat the command."
        ),
        (
            "Google no devolvió un refresh token; no he tocado el token existente. "
            "Revoca el acceso de la app en https://myaccount.google.com/permissions "
            "y repite el comando."
        ),
    ),
    (
        "panel.gmail.save_failed",
        "Could not save the token at {path}: {error}.",
        "No pude guardar el token en {path}: {error}.",
    ),
    # --- CLI ------------------------------------------------------------------------
    (
        "panel.cli.since",
        "--since must be an ISO date YYYY-MM-DD: {value}",
        "--since debe ser una fecha ISO YYYY-MM-DD: {value}",
    ),
    ("panel.cli.int", "must be an integer: {value}", "debe ser un entero: {value}"),
    ("panel.cli.ge1", "must be >= 1: {value}", "debe ser >= 1: {value}"),
    ("panel.cli.number", "must be a number: {value}", "debe ser un número: {value}"),
    ("panel.cli.ge0", "must be >= 0: {value}", "debe ser >= 0: {value}"),
    (
        "panel.cli.range",
        "must be between 0 and 100: {value}",
        "debe estar entre 0 y 100: {value}",
    ),
    (
        "panel.cli.nothing_new",
        "Nothing new: 0 new offers, 0 paused beacons.",
        "Sin novedades: 0 ofertas nuevas, 0 beacons pausados.",
    ),
    ("panel.cli.recent", "recent", "reciente"),
    ("panel.cli.ingested", "✅ Offer #{id} registered.", "✅ Oferta #{id} registrada."),
    (
        "panel.cli.closed",
        "🔒 Offer #{id} closed ({marker}): discarded, not analyzed.",
        "🔒 Oferta #{id} cerrada ({marker}): descartada, sin analizar.",
    ),
    (
        "panel.cli.judge_eval_warning",
        "⚠️ judge-eval uses the configured LLM and will consume your quota…",
        "⚠️ judge-eval usa el LLM configurado y consumirá tu cuota…",
    ),
    (
        "panel.cli.rejudge_warning",
        ("⚠️ rejudge uses the configured LLM (judge only) and will consume your quota…"),
        "⚠️ rejudge usa el LLM configurado (solo juez) y consumirá tu cuota…",
    ),
    (
        "panel.cli.dry_run",
        (
            "🔎 Dry-run: {would_judge} candidate offers would be judged "
            "(skipped: {state} by state, "
            "{short} for short/placeholder text, "
            "{since} by --since, "
            "{title} by --title-filter, "
            "{judged} already judged).\n"
            "Estimate: ~{tokens} tokens "
            "(~10,000 tokens/offer, approximate figure)."
        ),
        (
            "🔎 Dry-run: se juzgarían {would_judge} ofertas candidatas "
            "(saltadas: {state} por estado, "
            "{short} por texto corto/placeholder, "
            "{since} por --since, "
            "{title} por --title-filter, "
            "{judged} ya juzgadas).\n"
            "Estimación: ~{tokens} tokens "
            "(~10.000 tokens/oferta, cifra aproximada)."
        ),
    ),
    ("panel.cli.of", "{planned} of {total}", "{planned} de {total}"),
    ("panel.cli.report", "📄 Report: {path}", "📄 Informe: {path}"),
    ("panel.cli.cv_done", "📄 CV generated: {path}", "📄 CV generado: {path}"),
    ("panel.cli.exported", "📊 Exported to {path}", "📊 Exportado a {path}"),
    (
        "panel.cli.import_dry",
        "🔎 Dry-run (nothing was written)",
        "🔎 Dry-run (no se escribió nada)",
    ),
    ("panel.cli.import_head", "📥 Import", "📥 Importación"),
    (
        "panel.cli.import_counts",
        (
            "{head}: inserted: {inserted} · merged: {merged} · "
            "skipped: {skipped} · invalid: {invalid}"
        ),
        (
            "{head}: insertadas: {inserted} · fusionadas: {merged} · "
            "omitidas: {skipped} · inválidas: {invalid}"
        ),
    ),
    (
        "panel.cli.no_keywords",
        "No keywords recorded yet.",
        "Sin keywords registradas todavía.",
    ),
    (
        "panel.cli.beacon_add_usage",
        "Usage: cli.py beacon add <name> <url>",
        "Uso: cli.py beacon add <nombre> <url>",
    ),
    (
        "panel.cli.beacon_created",
        "✅ Beacon #{id} created.",
        "✅ Beacon #{id} creado.",
    ),
    (
        "panel.cli.beacon_id_usage",
        "Usage: cli.py beacon resume|disable <id>",
        "Uso: cli.py beacon resume|disable <id>",
    ),
    (
        "panel.cli.no_beacon",
        "⚠️ Beacon #{id} does not exist.",
        "⚠️ No existe el beacon #{id}.",
    ),
    (
        "panel.cli.beacon_usage",
        "Usage: cli.py beacon list|add|resume|disable ...",
        "Uso: cli.py beacon list|add|resume|disable ...",
    ),
    (
        "panel.cli.gmail_ok",
        (
            "✅ Gmail authorized (read-only: {scope}).\n"
            "Token saved at {path} (permissions 0600). "
            "Copy it with the credentials to the bot's PC (see docs/gmail.md)."
        ),
        (
            "✅ Gmail autorizado (solo lectura: {scope}).\n"
            "Token guardado en {path} (permisos 0600). "
            "Cópialo con las credenciales al PC del bot (ver docs/gmail.md)."
        ),
    ),
    (
        "panel.cli.description",
        "Job application tracking assistant (same JobService as the bot).",
        "Asistente de seguimiento de ofertas (mismo JobService que el bot).",
    ),
    (
        "panel.cli.help.check",
        "check the beacons that are due (sweep)",
        "comprueba los beacons que tocan (sweep)",
    ),
    (
        "panel.cli.help.ingest",
        "register an offer (url or text)",
        "registra una oferta (url o texto)",
    ),
    ("panel.cli.help.url", "offer URL", "URL de la oferta"),
    ("panel.cli.help.paste", "pasted offer text", "texto de la oferta pegado"),
    (
        "panel.cli.help.analyze",
        "analyze a registered offer",
        "analiza una oferta registrada",
    ),
    (
        "panel.cli.help.cv",
        "generate the tailored CV (PDF)",
        "genera el CV adaptado (PDF)",
    ),
    ("panel.cli.help.sent", "mark the offer as sent", "marca la oferta como enviada"),
    (
        "panel.cli.help.export",
        "export the sent-offers xlsx",
        "exporta el xlsx de enviadas",
    ),
    ("panel.cli.help.out", "destination path", "ruta de destino"),
    (
        "panel.cli.help.import",
        "import the manual offer history (JSON)",
        "importa el historial manual de ofertas (JSON)",
    ),
    (
        "panel.cli.help.import_path",
        "JSON with the list of records",
        "JSON con la lista de registros",
    ),
    (
        "panel.cli.help.import_dry",
        "show the counts without writing anything",
        "muestra los recuentos sin escribir nada",
    ),
    ("panel.cli.help.keywords", "demand log (§5)", "registro de demanda (§5)"),
    (
        "panel.cli.help.judge_eval",
        "compare the LLM judge with the labelled offer bank",
        "compara el juez LLM con el banco de ofertas etiquetadas",
    ),
    (
        "panel.cli.help.labels",
        "labels.json (default: eval/ of the profile_dir, or eval/labels.json)",
        "labels.json (por defecto: eval/ del profile_dir, o eval/labels.json)",
    ),
    (
        "panel.cli.help.details",
        "also print each offer's judgment (score, verdict, walls, rationale)",
        "imprime también el juicio de cada oferta (score, veredicto, muros, motivo)",
    ),
    (
        "panel.cli.help.rejudge",
        "re-evaluate the offer backlog with the LLM judge (judge only)",
        "re-evalúa el backlog de ofertas con el juez LLM (solo juez)",
    ),
    (
        "panel.cli.help.since",
        "YYYY-MM-DD (approx., see fecha_update)",
        "YYYY-MM-DD (aprox., ver fecha_update)",
    ),
    (
        "panel.cli.help.title_filter",
        "title/company pre-filter (same lists as the extension)",
        "pre-filtro de título/empresa (mismas listas que la extensión)",
    ),
    (
        "panel.cli.help.include_discarded",
        "also include discarded offers",
        "incluye también las ofertas descartadas",
    ),
    (
        "panel.cli.help.force",
        "re-judge even if a judge_results is already stored",
        "re-juzga aunque ya tenga un judge_results guardado",
    ),
    (
        "panel.cli.help.rejudge_dry",
        "only estimate how many offers would be judged (no LLM calls)",
        "solo estima cuántas ofertas se juzgarían (sin llamar al LLM)",
    ),
    (
        "panel.cli.help.report_only",
        (
            "rebuild and write the report from stored judge_results, without "
            "calling the LLM"
        ),
        (
            "reconstruye y escribe el informe desde judge_results guardados, "
            "sin llamar al LLM"
        ),
    ),
    (
        "panel.cli.help.pace",
        "seconds between judge calls",
        "segundos entre llamadas al juez",
    ),
    (
        "panel.cli.help.max_failures",
        ("stop the run after N consecutive judge failures (e.g. quota exhausted)"),
        (
            "detiene la ejecución tras N fallos consecutivos del juez "
            "(p.ej. cuota agotada)"
        ),
    ),
    (
        "panel.cli.help.gmail_auth",
        "authorize read-only access to Gmail (opens the browser)",
        "autoriza el acceso de solo lectura a Gmail (abre el navegador)",
    ),
    (
        "panel.cli.help.credentials",
        "OAuth client JSON (Desktop)",
        "JSON de cliente OAuth (Escritorio)",
    ),
    ("panel.cli.help.token", "where to store the token", "dónde guardar el token"),
    ("panel.cli.help.beacon", "manage beacons", "gestión de beacons"),
)

EN: Final[dict[str, str]] = {key: en for key, en, _ in _ENTRIES}
ES: Final[dict[str, str]] = {key: es for key, _, es in _ENTRIES}
