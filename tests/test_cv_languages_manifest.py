"""CV languages follow the base CVs the profile actually has.

A family offers and delivers only the languages that have a ``<ROLE>_<LANG>``
entry in the profile's ``cv_base_pdfs/manifest.json`` (its declared languages,
in declared order, intersected with the manifest). A family with no manifest
entry at all keeps its declared languages. Dropping ``AI_CA`` from the
manifest therefore stops Catalan for ``ai`` everywhere: inferred Catalan
falls back to Spanish, an explicit ``ca`` is a clear error, and the Telegram
language toggle cycles ES <-> EN.
"""

import asyncio
import json
import shutil
from pathlib import Path

import pytest

import bot
from cva.notifier import LANG, cv_keyboard
from cva.service import JobService, ServiceError
from tests.demo import DEMO_BASE_PDFS
from tests.test_bot import FakeContext, FakeQuery, FakeUpdate
from tests.test_family_cv_delivery import CA_TEXT, EN_TEXT, ES_TEXT, _offer, _service
from tests.test_family_telegram import _data


def _base_without(tmp_path: Path, *keys: str) -> Path:
    """The demo base PDFs with ``keys`` (and their PDFs) removed."""
    base = tmp_path / "base"
    shutil.copytree(DEMO_BASE_PDFS, base)
    manifest_path = base / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for key in keys:
        entry = manifest["variants"].pop(key)
        (base / entry["file"]).unlink()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return base


def _no_catalan(tmp_path: Path) -> JobService:
    svc, _repo = _service(tmp_path, _base_without(tmp_path, "AI_CA"), None)
    return svc


def test_catalan_offer_gets_the_spanish_ai_cv_without_a_catalan_base(
    tmp_path: Path,
) -> None:
    base = _base_without(tmp_path, "AI_CA")
    svc, repo = _service(tmp_path, base, None)
    oid = _offer(repo, CA_TEXT, "ai")
    pdf = asyncio.run(svc.generate_cv(oid))
    row = repo.get_offer(oid)
    assert row.cv_base == "AI_ES"
    assert row.estado == "preparada"
    assert pdf.read_bytes() == (base / "CV_AI_Engineer_Alex_Rivera.pdf").read_bytes()


def test_explicit_catalan_without_a_catalan_base_is_a_clear_error(
    tmp_path: Path,
) -> None:
    svc, repo = _service(tmp_path, _base_without(tmp_path, "AI_CA"), None)
    oid = _offer(repo, ES_TEXT, "ai")
    with pytest.raises(ServiceError, match=r"AI_ca.*ai.*es, en"):
        asyncio.run(svc.generate_cv(oid, "AI", "ca"))
    row = repo.get_offer(oid)
    assert row.cv_base is None and row.cv_adaptado is None
    assert not (tmp_path / "adaptadas").exists()


def test_effective_languages_drop_catalan_keeping_declared_order(
    tmp_path: Path,
) -> None:
    families = _no_catalan(tmp_path).preferences.families
    assert families.resolve("ai").languages == ("es", "en")
    assert families.resolve("backend").languages == ("es", "en")
    assert families.languages == ("es", "en")


def test_language_toggle_cycles_es_en_without_catalan(tmp_path: Path) -> None:
    svc, repo = _service(tmp_path, _base_without(tmp_path, "AI_CA"), None)
    oid = _offer(repo, ES_TEXT, "ai")
    seen = []
    lang = "es"
    for _ in range(3):
        query = FakeQuery(f"{LANG}:{oid}:AI:{lang}")
        asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(svc)))  # type: ignore[arg-type]
        data = _data(query.edited_markups[-1])
        lang = next(d for d in data if d.startswith(f"cv:{oid}:AI:")).rsplit(":", 1)[1]
        seen.append(lang)
    assert seen == ["en", "es", "en"]
    keyboard = _data(
        cv_keyboard(oid, role="AI", lang="en", families=svc.preferences.families)
    )
    assert f"{LANG}:{oid}:AI:en" in keyboard


def test_family_without_any_base_cv_keeps_its_declared_languages(
    tmp_path: Path,
) -> None:
    svc, repo = _service(
        tmp_path, _base_without(tmp_path, "BACKEND_ES", "BACKEND_EN"), None
    )
    assert svc.preferences.families.resolve("backend").languages == ("es", "en")
    oid = _offer(repo, ES_TEXT, "backend")
    with pytest.raises(Exception, match="BACKEND_ES"):
        asyncio.run(svc.generate_cv(oid, "BACKEND", "es"))


@pytest.mark.parametrize(
    ("text", "lang", "key"),
    [(CA_TEXT, None, "AI_CA"), (ES_TEXT, "ca", "AI_CA"), (EN_TEXT, None, "AI_EN")],
)
def test_manifest_with_catalan_is_unchanged(
    tmp_path: Path, text: str, lang: str | None, key: str
) -> None:
    svc, repo = _service(tmp_path, _base_without(tmp_path), None)
    assert svc.preferences.families.resolve("ai").languages == ("es", "en", "ca")
    oid = _offer(repo, text, "ai")
    asyncio.run(svc.generate_cv(oid, None, lang))  # type: ignore[arg-type]
    assert repo.get_offer(oid).cv_base == key
