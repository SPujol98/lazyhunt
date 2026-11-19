"""``cli.py gmail-auth`` (M1): wiring only; the OAuth flow itself is faked."""

from pathlib import Path

import pytest

import cli
from cva.gmail.client import GmailAuthError
from tests.test_cli import FakeService


def test_gmail_auth_uses_explicit_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        cli, "run_auth_flow", lambda c, t, *, lang: calls.append((c, t))
    )
    creds = tmp_path / "creds.json"
    token = tmp_path / "token.json"

    out = cli.run(
        ["gmail-auth", "--credentials", str(creds), "--token", str(token)],
        FakeService(),  # type: ignore[arg-type]
    )

    assert calls == [(creds, token)]
    assert str(token) in out
    assert "solo lectura" in out


def test_gmail_auth_defaults_come_from_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        cli, "run_auth_flow", lambda c, t, *, lang: calls.append((c, t))
    )
    monkeypatch.setenv("CVA_TELEGRAM_TOKEN", "tok")
    monkeypatch.setenv("CVA_GMAIL_CREDENTIALS_PATH", str(tmp_path / "c.json"))
    monkeypatch.setenv("CVA_GMAIL_TOKEN_PATH", str(tmp_path / "t.json"))
    from cva.config import Settings

    cli.run(["gmail-auth"], FakeService(), settings=Settings(_env_file=None))  # type: ignore[arg-type]

    assert calls == [(tmp_path / "c.json", tmp_path / "t.json")]


def test_gmail_auth_expands_the_home_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        cli, "run_auth_flow", lambda c, t, *, lang: calls.append((c, t))
    )
    cli.run(
        ["gmail-auth", "--credentials", "~/c.json", "--token", "~/t.json"],
        FakeService(),  # type: ignore[arg-type]
    )
    assert calls == [(Path("~/c.json").expanduser(), Path("~/t.json").expanduser())]


def test_gmail_auth_failure_is_a_clear_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(c: Path, t: Path, *, lang: str) -> None:
        raise GmailAuthError("no encuentro el fichero de credenciales")

    monkeypatch.setattr(cli, "run_auth_flow", boom)
    out = cli.run(["gmail-auth", "--token", str(tmp_path / "t")], FakeService())  # type: ignore[arg-type]
    assert out.startswith("⚠️")
    assert "credenciales" in out


def test_gmail_auth_flow_gets_the_ui_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    langs: list[str] = []

    def flow(c: Path, t: Path, *, lang: str) -> None:
        langs.append(lang)

    monkeypatch.setattr(cli, "run_auth_flow", flow)
    token = str(tmp_path / "t")
    cli.run(["gmail-auth", "--token", token], FakeService())  # type: ignore[arg-type]
    out = cli.run(["gmail-auth", "--token", token], FakeService(), lang="en")  # type: ignore[arg-type]
    assert langs == ["es", "en"]
    assert out.startswith("✅ Gmail authorized")
