"""User-interface strings in English and Spanish (``Settings.ui_language``).

Only text the user reads in Telegram, the CLI and the HTML panel lives here;
document language (CVs, cover letters, follow-up drafts) follows the offer.

Layout: one catalog module per area, each exporting ``EN`` and ``ES``
(``dict[str, str]``) with keys prefixed by the module name:

- :mod:`cva.i18n.core`: shared words (list joiners, ...), ``core.*``;
- :mod:`cva.i18n.commands`: ``/help``, the command menu, card-button docs,
  ``commands.*``;
- :mod:`cva.i18n.notifier`, :mod:`cva.i18n.bot`, :mod:`cva.i18n.panel`,
  :mod:`cva.i18n.prompts`: the later areas, ``notifier.*``, ``bot.*``, ...;
- :mod:`cva.i18n.onboarding`: the guided ``/setup`` questionnaire,
  ``onboarding.*``.

To add strings, a unit edits ONLY its own module: add the key to both
``EN`` and ``ES`` with the same ``{placeholders}``. Values are
``str.format`` templates (write a literal brace as ``{{``/``}}``).
``tests/test_i18n.py`` fails when a key is missing in one language, the
placeholders differ, or two modules declare the same key.

``LEGACY_LANG`` is the default of the ``lang`` parameter of functions whose
callers have not been migrated yet: they keep today's Spanish output. The
bot passes ``Settings.ui_language`` explicitly; the default goes away once
every caller does (bot-i18n T2-T6).
"""

from string import Formatter
from typing import Final, Literal, get_args

from cva.i18n import bot, commands, core, notifier, onboarding, panel, prompts

Lang = Literal["en", "es"]
LANGS: Final[tuple[Lang, ...]] = get_args(Lang)
DEFAULT_LANG: Final[Lang] = "en"
LEGACY_LANG: Final[Lang] = "es"

_MODULES = (core, commands, notifier, bot, panel, prompts, onboarding)


def _merge(lang: Lang) -> dict[str, str]:
    merged: dict[str, str] = {}
    for module in _MODULES:
        catalog: dict[str, str] = module.EN if lang == "en" else module.ES
        clash = merged.keys() & catalog.keys()
        if clash:
            raise ValueError(f"duplicate i18n keys: {sorted(clash)}")
        merged.update(catalog)
    return merged


CATALOGS: Final[dict[Lang, dict[str, str]]] = {lang: _merge(lang) for lang in LANGS}


def placeholders(template: str) -> frozenset[str]:
    """The ``{fields}`` a catalog template formats."""
    return frozenset(
        field for _, field, _, _ in Formatter().parse(template) if field is not None
    )


def t(key: str, lang: Lang, /, **fmt: object) -> str:
    """The ``lang`` text for ``key``, formatted with ``fmt``.

    An unknown key or a missing placeholder value raises ``KeyError``
    (never a silent fallback to the other language or to the key).
    """
    try:
        template = CATALOGS[lang][key]
    except KeyError:
        raise KeyError(f"no i18n key {key!r} for language {lang!r}") from None
    return template.format(**fmt)


def join_list(items: list[str], lang: Lang) -> str:
    """Natural list: "a", "a and b", "a, b and c" ("y" in Spanish)."""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} {t('core.and', lang)} {items[-1]}"
