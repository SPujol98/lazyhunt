"""Every test runs as the demo persona: the default profile is patched everywhere."""

import importlib
import pkgutil
import sys
from pathlib import Path

import pytest

import cva
import cva.profile_paths as profile_paths_module
from cva.config import Settings
from cva.profile_paths import resolve_profile_paths
from tests.demo import DEMO_DIR


def _import_all_cva_modules() -> None:
    for info in pkgutil.walk_packages(cva.__path__, prefix="cva."):
        importlib.import_module(info.name)
    for name in ("cli", "bot"):
        importlib.import_module(name)


def test_every_module_binding_the_resolver_sees_the_demo_default() -> None:
    _import_all_cva_modules()
    binders = {
        name: module
        for name, module in sys.modules.items()
        if hasattr(module, "resolve_profile_paths")
        and (name == "cli" or name == "bot" or name.startswith("cva."))
    }

    assert {"cva.service", "cva.suggest", "cva.cvgen.base_pdfs", "cva.bridge"} <= set(
        binders
    )
    for name, module in binders.items():
        assert module.resolve_profile_paths().claims == DEMO_DIR / "claims.json", name
    assert profile_paths_module.resolve_profile_paths().masters_dir == (
        DEMO_DIR / "masters"
    )


def test_an_explicit_argument_still_resolves_as_in_production() -> None:
    legacy = profile_paths_module.resolve_profile_paths(None)

    assert legacy.profile_toml is None
    assert legacy.claims.name == "claims.json" and "profiles" not in legacy.claims.parts
    assert resolve_profile_paths(DEMO_DIR / ".." / "demo").claims.name == "claims.json"


def test_settings_default_to_the_demo_profile_dir(tmp_path: Path) -> None:
    settings = Settings(
        CVA_TELEGRAM_TOKEN="123456:TEST",
        CVA_LLM_API_KEY="sk-test",
        _env_file=None,
        toml_file=str(tmp_path / "absent.toml"),
    )

    assert settings.profile_dir == DEMO_DIR


@pytest.mark.usefixtures("legacy_profile")
def test_the_legacy_opt_out_leaves_profile_dir_unset(tmp_path: Path) -> None:
    settings = Settings(
        CVA_TELEGRAM_TOKEN="123456:TEST",
        CVA_LLM_API_KEY="sk-test",
        _env_file=None,
        toml_file=str(tmp_path / "absent.toml"),
    )

    assert settings.profile_dir is None
