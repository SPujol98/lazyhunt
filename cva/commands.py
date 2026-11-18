"""Single source of truth for the bot's commands (``/help`` + Telegram menu).

``COMMANDS`` feeds both :func:`help_text` (grouped, with explanation and
example) and :func:`menu_commands` (``set_my_commands``); a test fails when a
``CommandHandler`` in ``bot.py`` is missing here. User-facing text is Spanish.
"""

from dataclasses import dataclass
from typing import Final

SECTION_ANALYZE: Final[str] = "🔎 Analizar"
SECTION_CV: Final[str] = "📄 CVs y cartas"
SECTION_TRACKING: Final[str] = "📊 Seguimiento"
SECTION_BEACONS: Final[str] = "🔭 Beacons y búsquedas"
SECTION_INTERVIEW: Final[str] = "🎤 Entrevistas"
SECTION_OTHER: Final[str] = "ℹ️ Otros"
SECTIONS: Final[tuple[str, ...]] = (
    SECTION_ANALYZE,
    SECTION_CV,
    SECTION_TRACKING,
    SECTION_BEACONS,
    SECTION_INTERVIEW,
    SECTION_OTHER,
)

BUTTONS_SECTION_TITLE: Final[str] = "🔘 Botones de las tarjetas"


@dataclass(frozen=True)
class Command:
    name: str  # without the slash; Telegram rules: [a-z0-9_]{1,32}
    section: str
    usage: str  # shown in /help, e.g. "/cv <id> [AI|BACKEND] [es|en|ca]"
    menu: str  # short description for the Telegram command menu (<= 256)
    explanation: str
    example: str
    aliases: tuple[str, ...] = ()


COMMANDS: Final[tuple[Command, ...]] = (
    Command(
        "analyze",
        SECTION_ANALYZE,
        "/analyze [url]",
        "Analiza una oferta (enlace o texto pegado)",
        "Puntúa una oferta contra tu perfil. Sin enlace, pide el texto.",
        "/analyze https://www.linkedin.com/jobs/view/123456",
    ),
    Command(
        "rescatar",
        SECTION_ANALYZE,
        "/rescatar <id>",
        "Recupera una oferta descartada (mira /panel)",
        "Reenvía la tarjeta completa de una oferta descartada, sin tocar su nota.",
        "/rescatar 42",
    ),
    Command(
        "cv",
        SECTION_CV,
        "/cv <id> [AI|BACKEND] [es|en|ca]",
        "Genera el CV adaptado a una oferta",
        "PDF del CV para la oferta #id. Rol e idioma opcionales "
        "(si faltan, se eligen solos).",
        "/cv 42 BACKEND en",
    ),
    Command(
        "cartabase",
        SECTION_CV,
        "/cartabase [es|en|ca] (o /base)",
        "Tu carta genérica para portales (sin oferta)",
        "Carta sin oferta concreta, lista para copiar.",
        "/cartabase en",
        aliases=("base",),
    ),
    Command(
        "status",
        SECTION_TRACKING,
        "/status [id]",
        "Estado de las ofertas recientes",
        "Sin id, las últimas 10 ofertas; con id, el estado de una.",
        "/status 42",
    ),
    Command(
        "sent",
        SECTION_TRACKING,
        "/sent <id>",
        "Marca una oferta como enviada",
        "Registra que enviaste la candidatura de la oferta #id.",
        "/sent 42",
    ),
    Command(
        "outcome",
        SECTION_TRACKING,
        "/outcome <id>",
        "Registra el resultado de una candidatura",
        "Historial de la enviada #id y botones para anotar cómo va. "
        "Tras 10 días sin noticias propone un seguimiento (lo envías tú).",
        "/outcome 42",
    ),
    Command(
        "calibracion",
        SECTION_TRACKING,
        "/calibracion",
        "Ejemplos que calibran la nota del bot",
        "Resultados que calibran la nota; cada uno se puede desactivar.",
        "/calibracion",
    ),
    Command(
        "skip",
        SECTION_TRACKING,
        "/skip",
        "Omite la nota de feedback pendiente",
        "Tras un resultado el bot pide una nota; /skip la omite.",
        "/skip",
    ),
    Command(
        "export",
        SECTION_TRACKING,
        "/export",
        "Exporta el xlsx de enviadas",
        "Excel solo con las enviadas; te dice dónde quedó.",
        "/export",
    ),
    Command(
        "panel",
        SECTION_TRACKING,
        "/panel",
        "Panel HTML con KPIs, gráficos y tabla",
        "HTML sin conexión: métricas, gráficos, alertas, tabla filtrable y "
        "descartadas (/rescatar).",
        "/panel",
    ),
    Command(
        "keywords",
        SECTION_BEACONS,
        "/keywords [perfil]",
        "Registro de palabras clave más demandadas",
        "Muestra las keywords vistas en ofertas y su frecuencia (filtra por perfil).",
        "/keywords backend",
    ),
    Command(
        "beacons",
        SECTION_BEACONS,
        "/beacons",
        "Estado de los beacons (búsquedas vigiladas)",
        "Cada beacon con estado, muros seguidos y última comprobación.",
        "/beacons",
    ),
    Command(
        "beacon",
        SECTION_BEACONS,
        "/beacon resume|disable <id>",
        "Reactiva o desactiva un beacon",
        "resume reactiva un beacon pausado; disable lo desactiva.",
        "/beacon resume 3",
    ),
    Command(
        "star",
        SECTION_INTERVIEW,
        "/star",
        "Tu banco de historias STAR para entrevistas",
        "Pregunta situación, tarea, acción y resultado de tus experiencias; "
        "luego las lista (✅ completa, ✏️ faltan partes) para ver, editar o "
        "borrar. /skip deja un campo vacío.",
        "/star",
    ),
    Command(
        "prep",
        SECTION_INTERVIEW,
        "/prep <id>",
        "Prepara una entrevista (HTML con temas y huecos)",
        "Elige fase (RR. HH., Técnica, Final): HTML con temas, huecos, STAR, "
        "salario y preguntas.",
        "/prep 42",
    ),
    Command(
        "pausa",
        SECTION_OTHER,
        "/pausa",
        "Pausa búsquedas, análisis automático y Gmail",
        "Detiene todo lo automático; /analyze sigue.",
        "/pausa",
    ),
    Command(
        "reanudar",
        SECTION_OTHER,
        "/reanudar",
        "Reanuda lo automático tras /pausa",
        "Vuelve a activar lo pausado.",
        "/reanudar",
    ),
    Command(
        "help",
        SECTION_OTHER,
        "/help",
        "Muestra esta ayuda",
        "Comandos y botones (también /start).",
        "/help",
        aliases=("start",),
    ),
)

# Every inline button the bot shows on its cards (notifier keyboards).
BUTTONS: Final[tuple[tuple[str, str], ...]] = (
    ("🔎 Analizar", "en el aviso de una oferta nueva: la analiza igual que /analyze."),
    ("📄 Generar CV", "crea el PDF con el rol e idioma elegidos en la tarjeta."),
    ("Idioma: ES/EN/CA", "cambia el idioma del CV (Backend solo ES/EN)."),
    ("Variante: AI/Backend", "alterna el perfil del CV entre AI y Backend."),
    (
        "💶 Investigar salario",
        "busca en la web un rango salarial con fuentes (puede no encontrar nada).",
    ),
    (
        "✉️ Carta de presentación",
        (
            "sobre tu carta base, con la empresa investigada (y sus fuentes) "
            '("(la piden)" = la oferta pide carta).'
        ),
    ),
    ("✅ Usar esta", "debajo de la carta: la guarda en la oferta."),
    ("🗑 Descartar", "descarta la oferta; si ya hay CV, no se registra envío."),
    ("✅ Marcar enviada", "igual que /sent: la oferta pasa a «enviada»."),
    ("🔄 Rehacer CV", "vuelve a generar el mismo CV (por si cambiaron los CV base)."),
    (
        "📝 Resultado",
        "en una oferta enviada: abre /outcome para anotar cómo va la candidatura.",
    ),
    (
        "Estados del resultado",
        (
            "Screening, Entrevista técnica, Entrevista final, Oferta, Aceptada, "
            "Rechazada, Sin respuesta, Retirada: guardan el estado y piden nota."
        ),
    ),
    (
        "Calibrar: Sí · No",
        (
            "tras una entrevista o rechazo: usa esa oferta (o no) para "
            "calibrar la nota. Motivo del rechazo: Requisitos, Stack, Idioma "
            "u Otro / no sé (no calibra)."
        ),
    ),
    (
        "✅ Enviado",
        (
            "en un recordatorio de seguimiento: anota que enviaste tú el "
            "mensaje (máximo 2)."
        ),
    ),
    (
        "⏭️ Saltar",
        "en un recordatorio de seguimiento: lo pospone 7 días.",
    ),
    (
        "🔕 Sin respuesta",
        ("en un recordatorio de seguimiento: marca la oferta como sin respuesta."),
    ),
    (
        "➕ Experiencia o proyecto",
        "en /star: elige de qué contar una historia (también «Añadir otra»).",
    ),
    (
        "👁 Ver · ✏️ Editar · 🗑 Borrar",
        (
            "en /star: ver una historia, rehacer un campo (Situación, Tarea, "
            "Acción, Resultado, Uso) o borrarla (Sí, borrar / Cancelar). Volver."
        ),
    ),
    (
        "🎯 Preparar entrevista",
        (
            "en /outcome y al pasar a screening o entrevista: elige fase y "
            "recibe el HTML de /prep."
        ),
    ),
    ("▶️ Reactivar", "en el aviso de beacon pausado: lo reactiva."),
    ("⏹ Desactivar", "en el aviso de beacon pausado: deja de comprobarlo."),
)


def menu_commands() -> tuple[tuple[str, str], ...]:
    """(name, description) pairs for Telegram's ``set_my_commands``."""
    return tuple((c.name, c.menu) for c in COMMANDS)


def help_text() -> str:
    """Grouped ``/help`` text: usage, explanation and example per command."""
    lines = ["Asistente de seguimiento de ofertas."]
    for section in SECTIONS:
        lines.extend(["", section])
        for command in COMMANDS:
            if command.section == section:
                lines.extend(
                    [
                        f"{command.usage}",
                        f"  {command.explanation}",
                        f"  Ej.: {command.example}",
                    ]
                )
    lines.extend(["", BUTTONS_SECTION_TITLE])
    lines.extend(f"{label} — {what}" for label, what in BUTTONS)
    return "\n".join(lines)
