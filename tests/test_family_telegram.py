"""role-families T5: Telegram cards, bot commands, CLI and bridge routing
follow the profile's role families (odd/tasks/role-families.md S3, S5; the
built-in ai/backend behaviour, S4, is pinned here and by test_notifier.py,
test_bot.py, test_cli.py and test_bridge.py).
"""

import asyncio
import shutil
import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import bot
from cli import run
from cva import bridge
from cva.commands import help_text
from cva.config import Settings
from cva.families import (
    BUILTIN_AI,
    BUILTIN_REGISTRY,
    FAMILY_ID_MAX_LEN,
    Family,
    FamilyRegistry,
)
from cva.notifier import (
    LANG,
    VARIANT,
    cv_keyboard,
    next_lang,
    next_role,
    offer_status_line,
    sent_keyboard,
)
from cva.profile_prefs import ProfileError, load_preferences
from tests.demo import DEMO_CLAIMS
from tests.service_stub import service_stub
from tests.test_bot import FakeContext, FakeMessage, FakeQuery, FakeUpdate
from tests.test_bridge import (
    OWNER_ID,
    SECRET,
    THRESHOLD,
    _post,
    _RecordingBot,
    _rows,
    _secret_header,
)
from tests.test_cli import FakeService
from tests.test_family_cv_delivery import (
    _FRONTEND,
    ES_TEXT,
    FRONTEND_PREFS,
    _base,
    _offer,
)
from tests.test_family_cv_delivery import _service as _family_service

FRONTEND = Family(id="frontend", label="Frontend", languages=("en", "es"))
REGISTRY = FamilyRegistry((BUILTIN_AI, FRONTEND), "ai")


def _buttons(markup: Any) -> list[tuple[str, str]]:
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]


def _data(markup: Any) -> list[str]:
    return [data for _, data in _buttons(markup)]


# --- notifier: cycles, labels, legacy and unknown roles -----------------------


def test_role_cycle_follows_the_declared_families() -> None:
    assert next_role("AI", families=REGISTRY) == "FRONTEND"
    assert next_role("FRONTEND", families=REGISTRY) == "AI"


def test_language_cycle_follows_the_family_languages() -> None:
    assert next_lang("FRONTEND", "en", families=REGISTRY) == "es"
    assert next_lang("FRONTEND", "es", families=REGISTRY) == "en"
    assert next_lang("FRONTEND", "ca", families=REGISTRY) == "en"  # not declared
    assert next_lang("AI", "en", families=REGISTRY) == "ca"


def test_card_keyboard_of_a_custom_family() -> None:
    buttons = _buttons(cv_keyboard(7, role="FRONTEND", lang="ca", families=REGISTRY))
    assert ("📄 Generar CV", "cv:7:FRONTEND:en") in buttons
    assert ("Idioma: EN", "lang:7:FRONTEND:en") in buttons
    assert ("Variante: Frontend", "variant:7:FRONTEND:en") in buttons


def test_card_keyboard_starts_on_the_default_family() -> None:
    custom = FamilyRegistry((BUILTIN_AI, FRONTEND), "frontend")
    assert "cv:7:FRONTEND:es" in _data(cv_keyboard(7, families=custom))


def test_rehacer_row_of_a_custom_family() -> None:
    data = _data(sent_keyboard(7, cv_base="FRONTEND_EN", families=REGISTRY))
    assert "cv:7:FRONTEND:en" in data


@pytest.mark.parametrize(
    ("role", "expected"),
    [("DS", "cv:7:AI:es"), ("XYZ", "cv:7:AI:es"), ("ai", "cv:7:AI:es")],
)
def test_legacy_and_unknown_roles_degrade_to_the_default(
    role: str, expected: str
) -> None:
    assert expected in _data(cv_keyboard(7, role=role, families=REGISTRY))
    assert next_role(role, families=REGISTRY) in {"AI", "FRONTEND"}


def test_unknown_rehacer_role_degrades_to_the_default_family() -> None:
    only_frontend = FamilyRegistry((FRONTEND,))
    data = _data(sent_keyboard(7, cv_base="BACKEND_ES", families=only_frontend))
    assert "cv:7:FRONTEND:es" in data
    data = _data(sent_keyboard(7, cv_base="AI_CA", families=only_frontend))
    assert "cv:7:FRONTEND:en" in data  # Catalan is not a frontend language


def test_status_tag_names_non_default_families() -> None:
    row = {"id": 1, "estado": "nueva", "match": 70, "posicion": "Dev"}
    line = offer_status_line(
        SimpleNamespace(**row, perfil="frontend"), families=REGISTRY
    )
    assert line.endswith("Dev [frontend]")
    assert "[" not in offer_status_line(
        SimpleNamespace(**row, perfil="ai"), families=REGISTRY
    )


# Built-in cards and buttons stay byte-identical (S4).
@pytest.mark.parametrize(
    ("role", "lang", "expected"),
    [
        ("AI", "ca", ["cv:3:AI:ca", "lang:3:AI:ca", "variant:3:AI:ca"]),
        (
            "BACKEND",
            "ca",
            ["cv:3:BACKEND:es", "lang:3:BACKEND:es", "variant:3:BACKEND:es"],
        ),
        ("DS", "en", ["cv:3:AI:en", "lang:3:AI:en", "variant:3:AI:en"]),
    ],
)
def test_builtin_card_keyboards_unchanged(
    role: str, lang: str, expected: list[str]
) -> None:
    buttons = _buttons(cv_keyboard(3, role=role, lang=lang))
    assert [d for _, d in buttons[:3]] == expected
    assert buttons[2][0] == (
        "Variante: Backend" if role == "BACKEND" else "Variante: AI"
    )
    assert (
        _buttons(cv_keyboard(3, role=role, lang=lang, families=BUILTIN_REGISTRY))
        == buttons
    )


def test_builtin_cycles_unchanged() -> None:
    assert [next_role(r) for r in ("AI", "BACKEND", "DS", "XYZ")] == [
        "BACKEND", "AI", "BACKEND", "AI",
    ]  # fmt: skip
    assert [next_lang("AI", x) for x in ("es", "en", "ca", "zz")] == [
        "en",
        "ca",
        "es",
        "es",
    ]
    assert [next_lang("BACKEND", x) for x in ("es", "en", "ca")] == ["en", "es", "es"]


# --- callback data length (Telegram: 64 bytes) --------------------------------


def test_callback_data_fits_telegram_for_the_longest_family_id() -> None:
    longest = Family(id="f" * FAMILY_ID_MAX_LEN, label="L" * 60, languages=("es",))
    registry = FamilyRegistry((longest,))
    offer_id = 9_999_999_999
    markups = [
        cv_keyboard(offer_id, role=longest.role, families=registry),
        sent_keyboard(offer_id, cv_base=f"{longest.role}_ES", families=registry),
    ]
    for markup in markups:
        for data in _data(markup):
            assert len(data.encode()) <= 64, data


def test_family_ids_longer_than_the_limit_are_rejected(tmp_path: Path) -> None:
    too_long = "f" * (FAMILY_ID_MAX_LEN + 1)
    with pytest.raises(ValueError, match="too long"):
        FamilyRegistry((Family(id=too_long, label="x", languages=("es",)),))
    prefs = tmp_path / "preferences.toml"
    prefs.write_text(f"[families.{too_long}]\n", encoding="utf-8")
    with pytest.raises(ProfileError, match=f"at most {FAMILY_ID_MAX_LEN}"):
        load_preferences(prefs)


# --- /help -------------------------------------------------------------------


def test_help_lists_the_profile_families() -> None:
    text = help_text(REGISTRY)
    assert "/cv <id> [AI|FRONTEND] [es|en|ca]" in text
    assert "Ej.: /cv 42 FRONTEND es" in text
    assert "Ej.: /keywords frontend" in text
    assert (
        "Variante: AI/Frontend — alterna el perfil del CV entre AI y Frontend." in text
    )
    assert "Idioma: ES/EN/CA — cambia el idioma del CV (Frontend solo EN/ES)." in text


def test_builtin_help_unchanged() -> None:
    text = help_text(BUILTIN_REGISTRY)
    assert text == help_text()
    assert "/cv <id> [AI|BACKEND] [es|en|ca]" in text
    assert "Ej.: /cv 42 BACKEND en" in text
    assert "Variante: AI/Backend — alterna el perfil del CV entre AI y Backend." in text
    assert "Idioma: ES/EN/CA — cambia el idioma del CV (Backend solo ES/EN)." in text


# --- bot: /cv and callbacks with a declared frontend family -------------------


def _frontend_service(tmp_path: Path) -> tuple[Any, Any, int]:
    svc, repo = _family_service(tmp_path, _base(tmp_path, _FRONTEND), FRONTEND_PREFS)
    return svc, repo, _offer(repo, ES_TEXT, "frontend")


def test_cv_command_accepts_a_declared_role_case_insensitively(tmp_path: Path) -> None:
    svc, _repo, offer_id = _frontend_service(tmp_path)
    message = FakeMessage("/cv")
    context = FakeContext(svc, args=[str(offer_id), "frontend", "en"])
    asyncio.run(bot.cv_command(FakeUpdate(message=message), context))  # type: ignore[arg-type]
    assert svc.offer(offer_id).cv_base == "FRONTEND_EN"


def test_cv_command_usage_lists_the_profile_roles(tmp_path: Path) -> None:
    svc, _repo, offer_id = _frontend_service(tmp_path)
    message = FakeMessage("/cv")
    context = FakeContext(svc, args=[str(offer_id), "BACKEND"])
    asyncio.run(bot.cv_command(FakeUpdate(message=message), context))  # type: ignore[arg-type]
    assert message.replies == ["Uso: /cv <id> [AI|FRONTEND] [es|en|ca]"]


def test_toggles_cycle_the_profile_families(tmp_path: Path) -> None:
    svc, _repo, offer_id = _frontend_service(tmp_path)
    query = FakeQuery(f"{VARIANT}:{offer_id}:AI:es")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(svc)))  # type: ignore[arg-type]
    assert f"cv:{offer_id}:FRONTEND:es" in _data(query.edited_markups[-1])
    query = FakeQuery(f"{LANG}:{offer_id}:FRONTEND:es")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(svc)))  # type: ignore[arg-type]
    assert f"cv:{offer_id}:FRONTEND:en" in _data(query.edited_markups[-1])


def test_cv_callback_of_a_custom_family_delivers_its_cv(tmp_path: Path) -> None:
    svc, _repo, offer_id = _frontend_service(tmp_path)
    query = FakeQuery(f"cv:{offer_id}:FRONTEND:en")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(svc)))  # type: ignore[arg-type]
    assert svc.offer(offer_id).cv_base == "FRONTEND_EN"
    assert f"cv:{offer_id}:FRONTEND:en" in _data(query.edited_markups[-1])  # Rehacer


@pytest.mark.parametrize("legacy", ["DS", "AI"])
def test_legacy_cv_callbacks_still_deliver(tmp_path: Path, legacy: str) -> None:
    svc, _repo, offer_id = _frontend_service(tmp_path)
    query = FakeQuery(f"cv:{offer_id}:{legacy}:es")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(svc)))  # type: ignore[arg-type]
    assert svc.offer(offer_id).cv_base == "AI_ES"


def test_unknown_cv_callback_role_degrades_with_a_message(tmp_path: Path) -> None:
    svc, _repo, offer_id = _frontend_service(tmp_path)
    query = FakeQuery(f"cv:{offer_id}:BACKEND:es")  # backend is not declared here
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(svc)))  # type: ignore[arg-type]
    assert svc.offer(offer_id).cv_base == "AI_ES"
    assert any("BACKEND" in r and "AI" in r for r in query.message.replies)


# --- CLI -----------------------------------------------------------------------


class _FrontendCliService(FakeService):
    preferences = SimpleNamespace(families=REGISTRY)


def test_cli_cv_accepts_a_declared_role() -> None:
    service = _FrontendCliService()
    run(["cv", "12", "FRONTEND", "en"], service)  # type: ignore[arg-type]
    assert service.calls == [("generate_cv", (12, "FRONTEND", "en"))]


def test_cli_cv_rejects_an_undeclared_role(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        run(["cv", "12", "BACKEND", "en"], _FrontendCliService())  # type: ignore[arg-type]
    err = capsys.readouterr().err
    assert "invalid choice: 'BACKEND'" in err and "FRONTEND" in err


def test_cli_cv_builtin_roles_unchanged(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        run(["cv", "12", "DS", "en"], FakeService())  # type: ignore[arg-type]
    assert "invalid choice: 'DS'" in capsys.readouterr().err


# --- bridge routing --------------------------------------------------------------


def _job(title: str | None) -> dict[str, Any]:
    return {
        "title": title,
        "company": "Acme",
        "location": "BCN",
        "url": "https://www.linkedin.com/jobs/view/4200000001/",
    }


@pytest.mark.parametrize(
    ("beacon_profile", "title", "expected"),
    [
        ("frontend", "React Developer", "frontend"),
        ("frontend", "Software Engineer", "frontend"),
        ("frontend", "AI Engineer", "ai"),  # the AI title still wins
        ("nurse", "Software Engineer", "ai"),  # unknown -> default
        ("ai", "Frontend Developer", "frontend"),  # a matching family's terms win
    ],
)
def test_validated_routes_to_the_profile_families(
    beacon_profile: str, title: str, expected: str
) -> None:
    registry = load_preferences_text(FRONTEND_PREFS).families
    _beacon, jobs = bridge._validated(
        {"beacon": "b", "profile": beacon_profile, "jobs": [_job(title)]},
        families=registry,
    )
    assert jobs[0]["profile"] == expected


def load_preferences_text(text: str) -> Any:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "preferences.toml"
        path.write_text(text, encoding="utf-8")
        return load_preferences(path)


@pytest.mark.parametrize(
    ("beacon_profile", "title", "expected"),
    [
        ("backend", "AI Engineer Junior", "ai"),
        ("backend", "Backend Engineer", "backend"),
        ("backend", None, "backend"),
        ("ai", "Software Engineer", "ai"),
        ("java", "Software Engineer", "ai"),
        ("Backend", "Software Engineer", "ai"),
    ],
)
def test_validated_builtin_routing_unchanged(
    beacon_profile: str, title: str | None, expected: str
) -> None:
    for kwargs in ({}, {"families": BUILTIN_REGISTRY}):
        _beacon, jobs = bridge._validated(
            {"beacon": "b", "profile": beacon_profile, "jobs": [_job(title)]},
            **kwargs,
        )
        assert jobs[0]["profile"] == expected


def test_bridge_http_stores_the_declared_family(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
) -> None:
    for key, value in {
        "CVA_AUTO_ANALYZE": "false",
        "CVA_TELEGRAM_TOKEN": "tok",
        "CVA_LLM_API_KEY": "sk",
        "CVA_OWNER_TELEGRAM_ID": str(OWNER_ID),
        "CVA_BRIDGE_SECRET": SECRET,
        "CVA_BRIDGE_PORT": "0",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(tmp_path)
    profile = tmp_path / "profile"
    profile.mkdir()
    shutil.copyfile(DEMO_CLAIMS, profile / "claims.json")
    (profile / "preferences.toml").write_text(FRONTEND_PREFS, encoding="utf-8")
    app = SimpleNamespace(post_init=None, bot=_RecordingBot(), bot_data={})
    handle = bridge.register(
        app, clean_settings(profile_dir=profile), service_stub(THRESHOLD, profile)
    )
    assert handle is not None
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        asyncio.run_coroutine_threadsafe(app.post_init(app), loop).result(timeout=5)
        second = {
            **_job("Nurse"),
            "url": "https://www.linkedin.com/jobs/view/4200000002/",
        }
        payload = {
            "beacon": "b",
            "profile": "frontend",
            "jobs": [_job("Web Developer"), second],
        }
        resp = _post(handle.port, json=payload, headers=_secret_header())
    finally:
        asyncio.run_coroutine_threadsafe(handle.shutdown(), loop).result(timeout=5)
        handle.stop()
        loop.call_soon_threadsafe(loop.stop)
    assert resp.status_code == 200
    assert [row["perfil"] for row in _rows(tmp_path)] == ["frontend", "frontend"]
