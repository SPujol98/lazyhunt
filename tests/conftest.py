"""Shared fixtures: keep tests from writing into the real repository."""

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

import cva.cvgen.engine as cvgen_engine
import cva.profile_paths as profile_paths_module
import cva.service as service_module
from cva.profile_paths import ProfilePaths, resolve_profile_paths
from tests.demo import DEMO_DIR

_REAL_RESOLVE = resolve_profile_paths


def _demo_default_paths(*args: Path | None) -> ProfilePaths:
    return _REAL_RESOLVE(*args) if args else _REAL_RESOLVE(DEMO_DIR)


@pytest.fixture(autouse=True)
def _isolate_default_output_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Redirect the relative default output locations under ``tmp_path``.

    Covers ``adaptadas/`` (generated CV PDFs), ``data/export.xlsx`` and the
    ``data/`` rejudge reports. No ``chdir``: some tests read repo-relative
    files (``config.toml``), so only the constants are patched.
    """
    monkeypatch.setattr(cvgen_engine, "OUTPUT_DIR", tmp_path / "adaptadas")
    monkeypatch.setattr(
        service_module, "_DEFAULT_EXPORT_PATH", tmp_path / "data" / "export.xlsx"
    )
    monkeypatch.setattr(service_module, "DEFAULT_REPORT_DIR", tmp_path / "data")
    # Every test runs as the fictional demo persona (Alex Rivera) unless it
    # opts out (``legacy_profile`` fixture or an explicit ``profile_dir``); a
    # developer's own profile_dir must never leak in either.
    monkeypatch.setenv("CVA_PROFILE_DIR", str(DEMO_DIR))
    # The suite pins today's Spanish UI (bot-i18n S5); tests of the English
    # default opt in with ``CVA_UI_LANGUAGE=en`` or by deleting the variable.
    monkeypatch.setenv("CVA_UI_LANGUAGE", "es")
    # Callers that resolve "the" profile without a directory get the demo
    # persona; an explicit argument, including None, still resolves exactly as in
    # production. Patched at the source and in EVERY module that bound the
    # function by name (``from cva.profile_paths import ...``), so no caller can
    # keep the real default (tests/test_demo_default_profile.py pins it).
    monkeypatch.setattr(
        profile_paths_module, "resolve_profile_paths", _demo_default_paths
    )
    # Test modules keep the real function (they exercise its contract).
    tests_dir = str(Path(__file__).parent)
    for module in list(sys.modules.values()):
        if str(getattr(module, "__file__", "")).startswith(tests_dir):
            continue
        if getattr(module, "resolve_profile_paths", None) is _REAL_RESOLVE:
            monkeypatch.setattr(module, "resolve_profile_paths", _demo_default_paths)
    yield


@pytest.fixture()
def legacy_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt out of the demo profile: the unset-``profile_dir`` (owner) layout."""
    monkeypatch.delenv("CVA_PROFILE_DIR", raising=False)
