"""Pytest root configuration: machine-independent test environment.

Entry-point modules (``bot.py`` in slice 8a, ``cli.py`` in 8b) live at the
repo root, outside the installed ``cva`` package; handler tests import them
directly (no PTB network I/O).

Test-portability contract (beacon-bridge fresh-clone evidence): the suite must
not depend on the developer's shell, ``.env``, or worktree ``config.toml``.
Two guarantees:

- ``_no_ambient_cva_env`` scrubs every ``CVA_*`` environment variable for each
  test (autouse), so no test can inherit the developer's ambient secrets.
- ``clean_settings`` builds ``Settings`` with dummy secrets via their env-name
  aliases (``validation_alias`` hides the field names), ``_env_file=None`` and
  a nonexistent TOML file, so neither layer can leak machine-specific values.
"""

import os
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from cva.config import Settings

sys.path.insert(0, str(Path(__file__).parent))

from tests import public_suite


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "owner: needs the owner's private persona data; skipped when it is absent "
        "or when CVA_PUBLIC_SUITE=1 hides it",
    )
    public_suite.install_if_requested()


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    public_suite.skip_owner_items(items)


@pytest.fixture(autouse=True)
def _no_ambient_cva_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Delete ambient ``CVA_*`` env vars so tests only see what they set."""
    for name in [key for key in os.environ if key.startswith("CVA_")]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def clean_settings(
    tmp_path_factory: pytest.TempPathFactory,
) -> Callable[..., Settings]:
    """Machine-independent ``Settings`` factory.

    Forces ``_env_file=None`` (no ``.env`` leak) and a nonexistent TOML file
    (no ``config.toml``/``config.local.toml`` leak — see
    ``Settings.settings_customise_sources``, which special-cases a
    ``toml_file`` init kwarg since the installed pydantic-settings 2.15
    ``BaseSettings.__init__`` has no such constructor override itself), and
    supplies dummy secrets through their ``validation_alias`` names — the
    field-name kwargs are ignored by pydantic-settings because the aliases
    do not set ``populate_by_name``. Keyword arguments override any default
    (including ``toml_file`` for the config-layering tests).
    """
    absent_toml = tmp_path_factory.mktemp("clean-settings") / "absent.toml"

    def _factory(**over: object) -> Settings:
        kwargs: dict[str, object] = {
            "CVA_TELEGRAM_TOKEN": "123456:TEST",
            "CVA_LLM_API_KEY": "sk-test",
            "_env_file": None,
            "toml_file": str(absent_toml),
        }
        kwargs.update(over)
        return Settings(**kwargs)

    return _factory
