"""/cartabase: the generic (no specific offer) cover letter for job portals.

Same fixed parts as the per-offer letters, no LLM call: an open salutation,
paragraph 1 without the linking sentence, one fixed generic paragraph, the
closing and the sign-off. Demo profile only; the owner's text is pinned in
``tests/owner/test_cover_letter_base_owner.py``.
"""

import asyncio
from pathlib import Path

import pytest

import bot
from cva.commands import COMMANDS
from cva.fetch import FetchResponse
from cva.match.cover_letter import assemble_open_letter
from cva.notifier import help_message
from cva.profile_persona import ProfileError, load_letter_bases
from tests.demo import DEMO_DIR
from tests.test_bot import (
    FakeContext,
    FakeFetcher,
    FakeMessage,
    FakeUpdate,
    make_service,
)

DEMO_BASE = DEMO_DIR / "cover_letter_base.toml"
BASES = load_letter_bases(DEMO_BASE)

OPEN_SALUTATIONS = {"es": "Hola,", "en": "Hello,", "ca": "Hola,"}
DEMO_OPEN_STARTS = {
    "es": "Busco un puesto como ingeniero de datos",
    "en": "I am looking for a data engineering",
    "ca": "Busco una feina com a enginyer de dades",
}


# --- assembly (pure) -----------------------------------------------------------


@pytest.mark.parametrize("language", ["es", "en", "ca"])
def test_open_letter_is_the_fixed_text_in_order(language: str) -> None:
    base = BASES[language]
    letter = assemble_open_letter(base)
    assert letter == "\n\n".join(
        (
            OPEN_SALUTATIONS[language],
            base.paragraph1,
            base.open_paragraph,
            base.closing,
            base.signoff,
        )
    )
    # Paragraph 1 starts right after the salutation: no linking sentence.
    assert letter.split("\n\n")[1] == base.paragraph1
    assert base.open_paragraph.startswith(DEMO_OPEN_STARTS[language])


@pytest.mark.parametrize("language", ["es", "en", "ca"])
def test_open_letter_has_no_company_or_placeholder(language: str) -> None:
    letter = assemble_open_letter(BASES[language])
    for marker in ("{", "}", "[", "]", "Empresa", "equipo de", "team,"):
        assert marker not in letter, marker


def test_open_letter_ignores_the_formal_variants() -> None:
    base = BASES["es"]
    letter = assemble_open_letter(base)
    assert base.closing in letter
    assert base.closing_formal not in letter


# --- loader ---------------------------------------------------------------------


@pytest.mark.parametrize("key", ["open_salutation", "open_paragraph"])
def test_base_without_the_open_keys_fails_loudly(tmp_path: Path, key: str) -> None:
    lines = DEMO_BASE.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if not line.startswith(f"{key} =")]
    path = tmp_path / "cover_letter_base.toml"
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    with pytest.raises(ProfileError, match=f"missing or empty {key}"):
        load_letter_bases(path)


# --- /cartabase command ---------------------------------------------------------


def _run(tmp_path: Path, args: list[str]) -> list[str]:
    service, llm = make_service(tmp_path, FakeFetcher(FetchResponse(200, "", "u")))
    message = FakeMessage()
    update = FakeUpdate(message=message)
    asyncio.run(bot.cartabase_command(update, FakeContext(service, args=args)))
    assert llm.calls == 0  # fixed text only: no LLM call
    return message.replies


@pytest.mark.parametrize("language", ["es", "en", "ca"])
def test_command_replies_with_the_open_letter(tmp_path: Path, language: str) -> None:
    replies = _run(tmp_path, [language])
    assert replies[0] == assemble_open_letter(BASES[language])
    assert len(replies) == 2
    assert "genérica" in replies[1] and "✉️" in replies[1]
    assert all(len(text) <= 4096 for text in replies)


def test_command_defaults_to_spanish(tmp_path: Path) -> None:
    assert _run(tmp_path, [])[0] == assemble_open_letter(BASES["es"])


def test_command_language_is_case_insensitive(tmp_path: Path) -> None:
    assert _run(tmp_path, ["EN"])[0] == assemble_open_letter(BASES["en"])


@pytest.mark.parametrize("args", [["fr"], ["es", "en"]])
def test_command_bad_argument_shows_usage(tmp_path: Path, args: list[str]) -> None:
    replies = _run(tmp_path, args)
    assert replies == ["Uso: /cartabase [es|en|ca] (por defecto es)"]


def test_command_reads_the_base_configured_in_bot_data(tmp_path: Path) -> None:
    text = DEMO_BASE.read_text(encoding="utf-8").replace(
        'open_salutation = "Hola,"', 'open_salutation = "Buenas,"', 1
    )
    custom = tmp_path / "cover_letter_base.toml"
    custom.write_text(text, encoding="utf-8")
    service, _ = make_service(tmp_path, FakeFetcher(FetchResponse(200, "", "u")))
    message = FakeMessage()
    context = FakeContext(service, args=[])
    context.bot_data[bot._LETTER_BASE_KEY] = custom
    asyncio.run(bot.cartabase_command(FakeUpdate(message=message), context))
    assert message.replies[0].startswith("Buenas,\n\n")


def test_command_reports_a_broken_base(tmp_path: Path) -> None:
    broken = tmp_path / "cover_letter_base.toml"
    broken.write_text("[es]\n", encoding="utf-8")
    service, _ = make_service(tmp_path, FakeFetcher(FetchResponse(200, "", "u")))
    message = FakeMessage()
    context = FakeContext(service, args=[])
    context.bot_data[bot._LETTER_BASE_KEY] = broken
    asyncio.run(bot.cartabase_command(FakeUpdate(message=message), context))
    assert message.replies[0].startswith("⚠️ No pude leer la carta base")


def test_base_alias_runs_the_same_command() -> None:
    (command,) = [c for c in COMMANDS if c.name == "cartabase"]
    assert command.aliases == ("base",)


def test_help_lists_the_command_and_fits_one_message() -> None:
    text = help_message()
    assert "/cartabase [es|en|ca]" in text and "/base" in text
    assert len(text) <= 4096


# --- demo profile -----------------------------------------------------------------


def test_demo_open_letter_is_alex_and_not_the_owner() -> None:
    owner_markers = ("AI Engineer", "Barcelona", "RAG")
    for language in ("es", "en", "ca"):
        letter = assemble_open_letter(BASES[language])
        assert letter.endswith("Alex Rivera")
        for marker in owner_markers:
            assert marker not in letter, (language, marker)


def test_catalan_closing_uses_the_correct_elision() -> None:
    """Owner OK 2026-11-18: «de immediat» is wrong Catalan; it is «d'immediat»."""
    from pathlib import Path

    for base in (
        Path(__file__).resolve().parents[1]
        / "profiles"
        / "demo"
        / "cover_letter_base.toml",
        Path(__file__).resolve().parents[1] / "data" / "cover_letter_base.toml",
    ):
        if base.is_file():
            text = base.read_text(encoding="utf-8")
            assert "de immediat" not in text
            assert "d'immediat" in text
