"""Single source of truth for the bot's commands (``/help`` + Telegram menu).

``COMMANDS`` feeds :func:`help_text` (grouped, with explanation and example),
:func:`menu_commands` (``set_my_commands``) and the ``CommandHandler``
registration in ``bot.py`` (every name and alias of every command). The text
lives in :mod:`cva.i18n.commands`, in English and Spanish; each command has a
Spanish and an English name, both always registered, and the menu and help
show the names of the UI language (``Settings.ui_language``).
"""

from dataclasses import dataclass
from typing import Final

from cva.families import BUILTIN_REGISTRY, FamilyRegistry
from cva.i18n import LEGACY_LANG, Lang, join_list, t

# Section ids, in /help order (titles: ``commands.section.<id>``).
SECTION_ANALYZE: Final[str] = "analyze"
SECTION_CV: Final[str] = "cv"
SECTION_TRACKING: Final[str] = "tracking"
SECTION_BEACONS: Final[str] = "beacons"
SECTION_INTERVIEW: Final[str] = "interview"
SECTION_OTHER: Final[str] = "other"
SECTIONS: Final[tuple[str, ...]] = (
    SECTION_ANALYZE,
    SECTION_CV,
    SECTION_TRACKING,
    SECTION_BEACONS,
    SECTION_INTERVIEW,
    SECTION_OTHER,
)


def section_title(section: str, lang: Lang = LEGACY_LANG) -> str:
    """A section's /help title; ``"buttons"`` is the card-buttons section."""
    return t(f"commands.section.{section}", lang)


@dataclass(frozen=True)
class Command:
    """One bot command. ``name`` is its stable id and Spanish name (Telegram
    rules: ``[a-z0-9_]{1,32}``); ``en_name`` its English name when different.
    Text: ``commands.cmd.<name>.{usage,menu,explanation,example}``.
    ``listed=False`` keeps a registered command out of the full /help and
    menu (a filtered menu, ``only=...``, still shows it)."""

    name: str
    section: str
    aliases: tuple[str, ...] = ()
    en_name: str | None = None
    listed: bool = True

    def name_for(self, lang: Lang) -> str:
        """The name the menu and /help show in ``lang``."""
        return self.en_name if lang == "en" and self.en_name else self.name

    @property
    def names(self) -> tuple[str, ...]:
        """Every language's name (registered as handlers, besides aliases)."""
        if not self.en_name or self.en_name == self.name:
            return (self.name,)
        return (self.name, self.en_name)

    def text(self, field: str, lang: Lang) -> str:
        """``usage``, ``menu``, ``explanation`` or ``example`` in ``lang``."""
        return t(f"commands.cmd.{self.name}.{field}", lang)


@dataclass(frozen=True)
class CommandDoc:
    """A command rendered for one language and profile (for /help)."""

    name: str
    section: str
    usage: str
    menu: str
    explanation: str
    example: str


COMMANDS: Final[tuple[Command, ...]] = (
    Command("analyze", SECTION_ANALYZE),
    Command("rescatar", SECTION_ANALYZE, en_name="rescue"),
    Command("cv", SECTION_CV),
    Command("cartabase", SECTION_CV, aliases=("base",), en_name="basecover"),
    # The base CV catalog (tailored-cv T1): unlisted like /connect (the full
    # /help is near Telegram's 4096 limit); the /setup Confirm reply and
    # docs/onboarding.md point to it.
    Command("cvs", SECTION_CV, listed=False),
    Command("status", SECTION_TRACKING),
    Command("sent", SECTION_TRACKING),
    Command("outcome", SECTION_TRACKING),
    Command("calibracion", SECTION_TRACKING, en_name="calibration"),
    Command("skip", SECTION_TRACKING),
    Command("export", SECTION_TRACKING),
    Command("panel", SECTION_TRACKING),
    Command("keywords", SECTION_BEACONS),
    Command("beacons", SECTION_BEACONS),
    Command("beacon", SECTION_BEACONS),
    Command("star", SECTION_INTERVIEW),
    Command("prep", SECTION_INTERVIEW),
    Command("pausa", SECTION_OTHER, en_name="pause"),
    Command("reanudar", SECTION_OTHER, en_name="resume"),
    # The guided setup (cva/onboarding): first run in setup mode, and a re-run
    # of the questionnaire in the full app; ``/setup <section>`` re-runs one
    # section (usage ``/setup [section]``, see docs/onboarding.md).
    Command("configurar", SECTION_OTHER, en_name="setup"),
    # Extension pairing (beta3-ux T1): a one-time code for the extension's
    # Options. Unlisted (the full /help is near Telegram's 4096 limit): the
    # setup-mode menu, its /help and the /setup Confirm reply offer it.
    Command("conectar", SECTION_OTHER, en_name="connect", listed=False),
    Command("help", SECTION_OTHER, aliases=("start",)),
)

# What setup mode answers (onboarding-v2 T1): the menu while the profile is
# not set up yet (``/start`` is the setup notice there, not an alias of help).
SETUP_COMMANDS: Final[tuple[str, ...]] = ("configurar", "conectar", "help")
# Not offered on a legacy install (no ``profile_dir``): /setup refuses there.
LEGACY_HIDDEN: Final[tuple[str, ...]] = ("configurar",)

# Every inline button the bot shows on its cards (notifier keyboards), by
# stable id; text: ``commands.button.<id>.{label,what}``. ``language`` and
# ``variant`` name the profile's families (see :func:`buttons_for`).
BUTTONS: Final[tuple[str, ...]] = (
    "analyze",
    "generate_cv",
    "language",
    "variant",
    "salary",
    "cover_letter",
    "use_this",
    "dont_use",
    "discard",
    "mark_sent",
    "redo_cv",
    "outcome",
    "outcome_states",
    "calibrate",
    "followup_sent",
    "followup_skip",
    "followup_no_reply",
    "star_add",
    "star_manage",
    "prep",
    "beacon_resume",
    "beacon_disable",
)


def menu_commands(
    lang: Lang = LEGACY_LANG,
    only: tuple[str, ...] | None = None,
    hidden: tuple[str, ...] = (),
) -> tuple[tuple[str, str], ...]:
    """(name, description) pairs for Telegram's ``set_my_commands``; ``only``
    keeps the commands with those ids (setup mode: ``SETUP_COMMANDS``);
    ``hidden`` drops those ids (a legacy install: :data:`LEGACY_HIDDEN`)."""
    return tuple(
        (c.name_for(lang), c.text("menu", lang))
        for c in COMMANDS
        if (c.listed if only is None else c.name in only) and c.name not in hidden
    )


def command_name(name: str, lang: Lang) -> str:
    """The ``/name`` of command id ``name`` in ``lang`` (e.g. ``/setup``)."""
    return f"/{_by_name(name).name_for(lang)}"


def cv_usage(
    families: FamilyRegistry = BUILTIN_REGISTRY, lang: Lang = LEGACY_LANG
) -> str:
    """``/cv`` usage naming the profile's roles and CV languages."""
    roles = "|".join(f.role for f in families.families)
    name = _by_name("cv").name_for(lang)
    return f"/{name} <id> [{roles}] [{'|'.join(families.languages)}]"


def _by_name(name: str) -> Command:
    (command,) = [c for c in COMMANDS if c.name == name]
    return command


def commands_for(
    families: FamilyRegistry = BUILTIN_REGISTRY,
    lang: Lang = LEGACY_LANG,
    hidden: tuple[str, ...] = (),
) -> tuple[CommandDoc, ...]:
    """``COMMANDS`` rendered in ``lang``; ``/cv`` and ``/keywords`` name the
    profile's role families (role-families T5)."""
    last = families.families[-1]
    computed = {
        ("cv", "usage"): cv_usage(families, lang),
        ("cv", "example"): f"/{_by_name('cv').name_for(lang)} 42 "
        f"{last.role} {last.languages[-1]}",
        ("keywords", "example"): f"/{_by_name('keywords').name_for(lang)} {last.id}",
    }

    def field(command: Command, name: str) -> str:
        return computed.get((command.name, name)) or command.text(name, lang)

    return tuple(
        CommandDoc(
            name=command.name_for(lang),
            section=command.section,
            usage=field(command, "usage"),
            menu=field(command, "menu"),
            explanation=field(command, "explanation"),
            example=field(command, "example"),
        )
        for command in COMMANDS
        if command.listed and command.name not in hidden
    )


def buttons_for(
    families: FamilyRegistry = BUILTIN_REGISTRY, lang: Lang = LEGACY_LANG
) -> tuple[tuple[str, str], ...]:
    """(label, what) per ``BUTTONS`` id in ``lang``; the language and variant
    toggles name the profile's families and their CV languages."""
    langs = families.languages
    limits = [
        t(
            "commands.button.language.limit",
            lang,
            label=f.label,
            langs="/".join(code.upper() for code in f.languages),
        )
        for f in families.families
        if f.languages != langs
    ]
    labels = [f.label for f in families.families]
    fmt: dict[str, dict[str, str]] = {
        "language": {
            "langs": "/".join(code.upper() for code in langs),
            "note": f" ({'; '.join(limits)})" if limits else "",
        },
        "variant": {"labels": "/".join(labels), "families": join_list(labels, lang)},
    }
    out: list[tuple[str, str]] = []
    for button in BUTTONS:
        values = fmt.get(button, {})
        label = t(f"commands.button.{button}.label", lang, **values)
        what = t(f"commands.button.{button}.what", lang, **values)
        out.append((label, what))
    return tuple(out)


def help_text(
    families: FamilyRegistry = BUILTIN_REGISTRY,
    lang: Lang = LEGACY_LANG,
    hidden: tuple[str, ...] = (),
) -> str:
    """Grouped ``/help`` text in ``lang``: usage, explanation and (when it
    differs from the usage) example per command; ``/cv``, ``/keywords`` and the card toggles name the profile's
    families. ``hidden`` drops those command ids (a legacy install)."""
    lines = [t("commands.header", lang)]
    commands = commands_for(families, lang, hidden)
    for section in SECTIONS:
        lines.extend(["", section_title(section, lang)])
        for command in commands:
            if command.section == section:
                lines.extend([command.usage, f"  {command.explanation}"])
                # An example identical to the usage line adds nothing (and
                # the help must fit one Telegram message, < 4096 chars).
                if command.example != command.usage:
                    lines.append(
                        f"  {t('commands.example', lang, example=command.example)}"
                    )
    lines.extend(["", section_title("buttons", lang)])
    lines.extend(f"{label} — {what}" for label, what in buttons_for(families, lang))
    return "\n".join(lines)


# Telegram refuses a text message longer than this many characters.
TELEGRAM_MESSAGE_LIMIT: Final[int] = 4096


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """``text`` cut into messages of at most ``limit`` characters, at line
    boundaries (a blank line is preferred, so a /help section stays whole);
    a single line longer than ``limit`` is cut inside. Joined back with
    ``"\\n"`` the pieces give ``text`` again (minus the boundary newlines).
    A text that fits is returned as is, in a one-item list."""
    if len(text) <= limit:
        return [text]
    pieces: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[: limit + 1]  # a newline right at ``limit`` still fits
        cut = window.rfind("\n\n")
        if cut <= 0:
            cut = window.rfind("\n")
        if cut <= 0:
            pieces.append(rest[:limit])
            rest = rest[limit:]
            continue
        pieces.append(rest[:cut].rstrip("\n"))
        rest = rest[cut:].lstrip("\n")
    if rest:
        pieces.append(rest)
    return [piece for piece in pieces if piece]
