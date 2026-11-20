"""``cli.py init``: the interactive, re-runnable setup wizard (public-release S3).

Every test drives the wizard through its public entry (``run_init``) or the
CLI parser with scripted input, a fake process runner and ``tmp_path`` as the
installation directory: no network, no real ``claude`` call and nothing
written outside ``tmp_path``.
"""

import json
import os
import stat
import sys
import tomllib
from collections.abc import Iterable, Sequence
from pathlib import Path

import pytest

import cli
from cva.setup.wizard import WizardIO, run_init

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_PROFILE = REPO_ROOT / "profiles" / "demo"

TOKEN = "123456789:AAFakeTokenForTestsOnly_abcdefghijklmn"
OWNER_ID = "424242"


class ScriptedIO:
    """Answers prompts in order and records everything printed."""

    def __init__(
        self, answers: Iterable[str], secret_answers: Iterable[str] = ()
    ) -> None:
        self.answers = list(answers)
        self.secret_answers = list(secret_answers)
        self.out: list[str] = []
        self.prompts: list[str] = []

    def input(self, prompt: str) -> str:
        self.prompts.append(prompt)
        self.out.append(prompt)
        if not self.answers:
            raise AssertionError(f"unexpected prompt: {prompt!r}")
        return self.answers.pop(0)

    def getpass(self, prompt: str) -> str:
        self.prompts.append(prompt)
        self.out.append(prompt)
        if not self.secret_answers:
            raise AssertionError(f"unexpected secret prompt: {prompt!r}")
        return self.secret_answers.pop(0)

    def print(self, text: str = "") -> None:
        self.out.append(text)

    @property
    def io(self) -> WizardIO:
        return WizardIO(input=self.input, print=self.print, getpass=self.getpass)

    @property
    def transcript(self) -> str:
        return "\n".join(self.out)


def logged_in_runner(argv: Sequence[str]) -> tuple[int, str]:
    if list(argv[1:]) == ["--version"]:
        return 0, "9.9.9 (Claude Code)\n"
    if list(argv[1:3]) == ["auth", "status"]:
        return 0, json.dumps({"loggedIn": True, "email": "someone@example.org"})
    raise AssertionError(f"unexpected command: {argv!r}")


def claude_on_path(name: str) -> str | None:
    return "/usr/bin/claude" if name == "claude" else None


def no_claude(name: str) -> str | None:
    return None


def wizard(
    base: Path,
    scripted: ScriptedIO,
    *,
    which: object = claude_on_path,
) -> int:
    return run_init(
        base,
        scripted.io,
        runner=logged_in_runner,
        which=which,  # type: ignore[arg-type]
        demo_profile=DEMO_PROFILE,
    )


# Fresh-install answers, in prompt order: bot language (default), provider
# (default), owner id,
# profile dir (default), profile kind (skeleton), final confirmation, show the
# bridge secret (no). The bot token goes through getpass.
FRESH_ANSWERS = ["", "", OWNER_ID, "", "1", "y", "n"]


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def test_fresh_directory_writes_secrets_only_to_env(tmp_path: Path) -> None:
    scripted = ScriptedIO(FRESH_ANSWERS, [TOKEN])

    assert wizard(tmp_path, scripted) == 0

    env = read_env(tmp_path / ".env")
    assert env["CVA_TELEGRAM_TOKEN"] == TOKEN
    assert len(env["CVA_BRIDGE_SECRET"]) >= 32
    if os.name == "posix":
        assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600

    local_text = (tmp_path / "config.local.toml").read_text(encoding="utf-8")
    local = tomllib.loads(local_text)
    assert local["owner_telegram_id"] == int(OWNER_ID)
    assert local["profile_dir"] == "profiles/me"
    assert local["llm_provider"] == "claude_code"
    assert TOKEN not in local_text
    assert env["CVA_BRIDGE_SECRET"] not in local_text


def test_secrets_never_appear_in_printed_output(tmp_path: Path) -> None:
    scripted = ScriptedIO(FRESH_ANSWERS, [TOKEN])

    wizard(tmp_path, scripted)

    secret = read_env(tmp_path / ".env")["CVA_BRIDGE_SECRET"]
    assert TOKEN not in scripted.transcript
    assert secret not in scripted.transcript
    # The claude account details from `claude auth status` are never echoed.
    assert "someone@example.org" not in scripted.transcript


def test_bridge_secret_is_printed_once_only_on_request(tmp_path: Path) -> None:
    scripted = ScriptedIO(["", "", OWNER_ID, "", "1", "y", "y"], [TOKEN])

    wizard(tmp_path, scripted)

    secret = read_env(tmp_path / ".env")["CVA_BRIDGE_SECRET"]
    assert scripted.transcript.count(secret) == 1
    assert TOKEN not in scripted.transcript


def test_declining_the_final_confirmation_writes_nothing(tmp_path: Path) -> None:
    scripted = ScriptedIO(["", "", OWNER_ID, "", "1", "n"], [TOKEN])

    assert wizard(tmp_path, scripted) == 0

    assert sorted(p.name for p in tmp_path.iterdir()) == []
    assert "Nothing was written" in scripted.transcript


def test_existing_keys_are_not_overwritten_without_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "CVA_TELEGRAM_TOKEN=old-token\nCVA_BRIDGE_SECRET=old-secret-value\nOTHER=1\n",
        encoding="utf-8",
    )
    (tmp_path / "config.local.toml").write_text(
        "owner_telegram_id = 111\ncv_mirror_dir = 'x'\n", encoding="utf-8"
    )
    # provider, keep the existing token (n), owner id, keep the existing owner
    # id (n), keep the existing secret (n), profile dir, kind, confirm, no show.
    scripted = ScriptedIO(["", "", "n", OWNER_ID, "n", "n", "", "1", "y", "n"], [TOKEN])

    wizard(tmp_path, scripted)

    env = read_env(tmp_path / ".env")
    assert env == {
        "CVA_TELEGRAM_TOKEN": "old-token",
        "CVA_BRIDGE_SECRET": "old-secret-value",
        "OTHER": "1",
    }
    local = tomllib.loads((tmp_path / "config.local.toml").read_text(encoding="utf-8"))
    assert local["owner_telegram_id"] == 111
    assert local["cv_mirror_dir"] == "x"
    assert local["profile_dir"] == "profiles/me"
    assert "old-token" not in scripted.transcript
    assert "old-secret-value" not in scripted.transcript


def test_existing_keys_are_replaced_when_confirmed(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "CVA_TELEGRAM_TOKEN=old-token\nOTHER=1\n", encoding="utf-8"
    )
    (tmp_path / "config.local.toml").write_text(
        "owner_telegram_id = 111\n", encoding="utf-8"
    )
    # provider, replace the token (y), owner id, replace the owner id (y),
    # profile dir, kind, confirm, no show (no secret yet: one is generated).
    scripted = ScriptedIO(["", "", "y", OWNER_ID, "y", "", "1", "y", "n"], [TOKEN])

    wizard(tmp_path, scripted)

    env = read_env(tmp_path / ".env")
    assert env["CVA_TELEGRAM_TOKEN"] == TOKEN
    assert env["OTHER"] == "1"
    assert len(env["CVA_BRIDGE_SECRET"]) >= 32
    local = tomllib.loads((tmp_path / "config.local.toml").read_text(encoding="utf-8"))
    assert local["owner_telegram_id"] == int(OWNER_ID)


def test_rerun_with_the_same_answers_changes_nothing(tmp_path: Path) -> None:
    wizard(tmp_path, ScriptedIO(FRESH_ANSWERS, [TOKEN]))
    env_before = (tmp_path / ".env").read_text(encoding="utf-8")
    local_before = (tmp_path / "config.local.toml").read_text(encoding="utf-8")

    # provider, same owner id, keep the bridge secret (n), profile dir (exists:
    # no kind question), no secret show.
    second = ScriptedIO(["", "", OWNER_ID, "n", "", "n"], [TOKEN])
    assert wizard(tmp_path, second) == 0

    assert (tmp_path / ".env").read_text(encoding="utf-8") == env_before
    assert (tmp_path / "config.local.toml").read_text(encoding="utf-8") == local_before
    assert "Nothing to change" in second.transcript


def test_skeleton_profile_copies_the_demo_structure_with_placeholders(
    tmp_path: Path,
) -> None:
    wizard(tmp_path, ScriptedIO(FRESH_ANSWERS, [TOKEN]))

    profile = tmp_path / "profiles" / "me"
    demo_dirs = {
        p.relative_to(DEMO_PROFILE) for p in DEMO_PROFILE.rglob("*") if p.is_dir()
    }
    made_dirs = {p.relative_to(profile) for p in profile.rglob("*") if p.is_dir()}
    assert demo_dirs <= made_dirs
    for name in ("claims.json", "profile.toml", "preferences.toml", "synonyms.json"):
        assert (profile / name).is_file()
    assert "TODO" in (profile / "voice.md").read_text(encoding="utf-8")
    assert "Alex" not in (profile / "voice.md").read_text(encoding="utf-8")
    assert (profile / "profile.toml").read_text(encoding="utf-8").startswith("# TODO")
    assert not list(profile.rglob("*.pdf"))
    labels = json.loads((profile / "eval" / "labels.json").read_text(encoding="utf-8"))
    assert labels["offers"] == []
    assert (profile / "README.md").is_file()


def test_demo_profile_option_copies_the_demo_verbatim(tmp_path: Path) -> None:
    wizard(tmp_path, ScriptedIO(["", "", OWNER_ID, "", "2", "y", "n"], [TOKEN]))

    profile = tmp_path / "profiles" / "me"
    assert (profile / "voice.md").read_bytes() == (
        DEMO_PROFILE / "voice.md"
    ).read_bytes()
    assert len(list(profile.rglob("*.pdf"))) == len(list(DEMO_PROFILE.rglob("*.pdf")))


def test_existing_profile_is_never_overwritten(tmp_path: Path) -> None:
    profile = tmp_path / "profiles" / "me"
    profile.mkdir(parents=True)
    (profile / "voice.md").write_text("mine", encoding="utf-8")
    # No profile-kind question: the existing directory is reused as is.
    scripted = ScriptedIO(["", "", OWNER_ID, "", "y", "n"], [TOKEN])

    wizard(tmp_path, scripted)

    assert (profile / "voice.md").read_text(encoding="utf-8") == "mine"
    assert sorted(p.name for p in profile.iterdir()) == ["voice.md"]
    assert "already exists" in scripted.transcript


def test_missing_claude_prints_a_clear_message_and_continues(tmp_path: Path) -> None:
    scripted = ScriptedIO(FRESH_ANSWERS, [TOKEN])

    assert wizard(tmp_path, scripted, which=no_claude) == 0

    assert "`claude` was not found on PATH" in scripted.transcript
    assert (tmp_path / ".env").is_file()


def test_claude_logged_out_is_reported(tmp_path: Path) -> None:
    def logged_out(argv: Sequence[str]) -> tuple[int, str]:
        if list(argv[1:]) == ["--version"]:
            return 0, "9.9.9 (Claude Code)\n"
        return 1, json.dumps({"loggedIn": False})

    scripted = ScriptedIO(FRESH_ANSWERS, [TOKEN])
    run_init(
        tmp_path,
        scripted.io,
        runner=logged_out,
        which=claude_on_path,
        demo_profile=DEMO_PROFILE,
    )

    assert "9.9.9 (Claude Code)" in scripted.transcript
    assert "not logged in" in scripted.transcript
    assert "claude auth login" in scripted.transcript


def test_invalid_telegram_id_is_asked_again(tmp_path: Path) -> None:
    scripted = ScriptedIO(
        ["", "", "not-a-number", OWNER_ID, "", "1", "y", "n"], [TOKEN]
    )

    wizard(tmp_path, scripted)

    local = tomllib.loads((tmp_path / "config.local.toml").read_text(encoding="utf-8"))
    assert local["owner_telegram_id"] == int(OWNER_ID)


def test_cli_help_lists_init(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--help"])
    assert "init" in capsys.readouterr().out


def test_cli_main_runs_init_without_loading_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []

    def fake_run_init(base_dir: Path, *args: object, **kwargs: object) -> int:
        calls.append(base_dir)
        return 0

    def no_settings() -> None:
        raise AssertionError("init must not need a configured bot")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "run_init", fake_run_init)
    monkeypatch.setattr(cli, "Settings", no_settings)

    cli.main(["init"])

    assert calls == [tmp_path]


@pytest.mark.parametrize(
    "argv",
    [
        ["check"],
        ["ingest", "--url", "https://example.com/job"],
        ["analyze", "1"],
        ["cv", "1", "AI", "en"],
        ["sent", "1"],
        ["export", "--out", "x.xlsx"],
        ["import-history", "h.json", "--dry-run"],
        ["keywords"],
        ["judge-eval", "--labels", "l.json"],
        ["rejudge", "--dry-run", "--limit", "3"],
        ["gmail-auth", "--credentials", "c.json"],
        ["beacon", "add", "x"],
    ],
)
def test_existing_subcommands_still_parse(argv: list[str]) -> None:
    args = cli.build_parser().parse_args(argv)
    assert args.command == argv[0]


def test_wizard_writes_nothing_outside_the_base_dir(tmp_path: Path) -> None:
    base = tmp_path / "install"
    base.mkdir()
    wizard(base, ScriptedIO(FRESH_ANSWERS, [TOKEN]))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["install"]
    assert sys.version_info >= (3, 12)


@pytest.mark.parametrize(
    "answer", ["../x", "{abs}", "profiles/demo/sub", "profiles/demo"]
)
def test_profile_outside_the_base_or_inside_the_demo_is_asked_again(
    tmp_path: Path, answer: str
) -> None:
    """Verifier B1/B2: the wizard never creates a profile outside the base
    directory or inside the tracked demo; it explains and asks again."""
    base = tmp_path / "repo"
    base.mkdir()
    outside = tmp_path / "outside"
    answer = answer.replace("{abs}", str(outside))
    scripted = ScriptedIO(["", "", OWNER_ID, answer, "", "1", "y", "n"], [TOKEN])
    assert wizard(base, scripted) == 0
    assert not (tmp_path / "x").exists()
    assert not outside.exists()
    assert not (base / "profiles" / "demo").exists()
    assert (base / "profiles" / "me" / "README.md").is_file()
    assert "inside this directory" in scripted.transcript
    assert 'profile_dir = "profiles/me"' in (base / "config.local.toml").read_text(
        encoding="utf-8"
    )


def test_duplicate_env_keys_are_all_replaced(tmp_path: Path) -> None:
    """Verifier advisory 4: dotenv uses the last occurrence, so every
    occurrence of a confirmed key must get the new value."""
    (tmp_path / ".env").write_text(
        "CVA_TELEGRAM_TOKEN=old1\nOTHER=1\nCVA_TELEGRAM_TOKEN=old2\n", encoding="utf-8"
    )
    scripted = ScriptedIO(["", "", "y", OWNER_ID, "", "1", "y", "n"], [TOKEN])
    assert wizard(tmp_path, scripted) == 0
    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "old1" not in text and "old2" not in text
    assert text.count(f"CVA_TELEGRAM_TOKEN={TOKEN}") == 1
    assert "OTHER=1" in text


def test_an_unreadable_config_writes_nothing_at_all(tmp_path: Path) -> None:
    """Verifier advisory 2: both files are prepared and validated before any
    write, so a config the wizard cannot rewrite leaves .env untouched too."""
    (tmp_path / "config.local.toml").write_text(
        '"owner_telegram_id" = 3\n', encoding="utf-8"
    )
    scripted = ScriptedIO(["", "", OWNER_ID, "y", "", "1", "y"], [TOKEN])
    with pytest.raises(tomllib.TOMLDecodeError):
        wizard(tmp_path, scripted)
    assert not (tmp_path / ".env").exists()
    assert not (tmp_path / "profiles").exists()
    assert (tmp_path / "config.local.toml").read_text(
        encoding="utf-8"
    ) == '"owner_telegram_id" = 3\n'


def test_bot_language_defaults_to_auto_and_is_written(tmp_path: Path) -> None:
    scripted = ScriptedIO(FRESH_ANSWERS, [TOKEN])
    assert wizard(tmp_path, scripted) == 0
    local = tomllib.loads((tmp_path / "config.local.toml").read_text(encoding="utf-8"))
    assert local["ui_language"] == "auto"
    assert "auto, en, es" in scripted.transcript


def test_bot_language_english_is_written(tmp_path: Path) -> None:
    assert wizard(tmp_path, ScriptedIO(["en", *FRESH_ANSWERS[1:]], [TOKEN])) == 0
    local = tomllib.loads((tmp_path / "config.local.toml").read_text(encoding="utf-8"))
    assert local["ui_language"] == "en"


def test_bot_language_spanish_is_written_once(tmp_path: Path) -> None:
    scripted = ScriptedIO(["es", *FRESH_ANSWERS[1:]], [TOKEN])
    assert wizard(tmp_path, scripted) == 0
    text = (tmp_path / "config.local.toml").read_text(encoding="utf-8")
    assert tomllib.loads(text)["ui_language"] == "es"
    assert text.count("ui_language") == 1


def test_unknown_bot_language_is_asked_again(tmp_path: Path) -> None:
    scripted = ScriptedIO(["fr", "es", *FRESH_ANSWERS[1:]], [TOKEN])
    assert wizard(tmp_path, scripted) == 0
    assert "auto, en, es" in scripted.transcript
    local = tomllib.loads((tmp_path / "config.local.toml").read_text(encoding="utf-8"))
    assert local["ui_language"] == "es"


def test_rerun_keeps_the_previous_bot_language_as_default(tmp_path: Path) -> None:
    wizard(tmp_path, ScriptedIO(["es", *FRESH_ANSWERS[1:]], [TOKEN]))
    before = (tmp_path / "config.local.toml").read_text(encoding="utf-8")

    second = ScriptedIO(["", "", OWNER_ID, "n", "", "n"], [TOKEN])
    assert wizard(tmp_path, second) == 0

    assert (tmp_path / "config.local.toml").read_text(encoding="utf-8") == before
    assert "Nothing to change" in second.transcript


# --- onboarding-v2 T1: the empty profile, set up later in Telegram -------------


def test_empty_profile_is_the_recommended_default(tmp_path: Path) -> None:
    # Enter at the profile-kind prompt picks the empty profile.
    scripted = ScriptedIO(["", "", OWNER_ID, "", "", "y", "n"], [TOKEN])

    assert wizard(tmp_path, scripted) == 0

    profile = tmp_path / "profiles" / "me"
    assert profile.is_dir()
    assert list(profile.iterdir()) == []  # no demo copy, no placeholders
    local = tomllib.loads((tmp_path / "config.local.toml").read_text("utf-8"))
    assert local["profile_dir"] == "profiles/me"  # always explicit
    assert "(recommended)" in scripted.transcript
    assert "Start from [3]" in scripted.transcript
    assert "/setup" in scripted.transcript  # next steps point at Telegram


def test_skeleton_option_still_creates_the_skeleton(tmp_path: Path) -> None:
    wizard(tmp_path, ScriptedIO(["", "", OWNER_ID, "", "1", "y", "n"], [TOKEN]))
    assert (tmp_path / "profiles" / "me" / "claims.json").is_file()


def test_empty_profile_leads_the_bot_into_setup_mode(tmp_path: Path) -> None:
    from cva.setup_mode import missing_profile_files

    wizard(tmp_path, ScriptedIO(["", "", OWNER_ID, "", "3", "y", "n"], [TOKEN]))
    assert "claims.json" in missing_profile_files(tmp_path / "profiles" / "me")


# --- toml_set_key: one top-level key, every other byte kept (onboarding-v2) -------


def test_toml_set_key_replaces_only_that_key_and_keeps_every_other_byte() -> None:
    from cva.setup.wizard import toml_set_key

    base = (
        "# top\r\nowner_telegram_id = 1  # me\r\n"
        'enabled_portals = [\r\n  "indeed",  # [old]\r\n]\r\n'
        "\r\n[extra]\r\nenabled_portals = 2\r\n"
    )
    text = toml_set_key(base, "enabled_portals", ["linkedin", "infojobs"])
    assert text == base.replace(
        'enabled_portals = [\r\n  "indeed",  # [old]\r\n]\r\n',
        'enabled_portals = ["linkedin", "infojobs"]\r\n',
    )


def test_toml_set_key_adds_a_missing_key_before_the_first_table() -> None:
    from cva.setup.wizard import toml_set_key

    base = "a = 1\n# note\n[t]\nb = 2"
    assert toml_set_key(base, "enabled_portals", []) == (
        "a = 1\n# note\nenabled_portals = []\n[t]\nb = 2"
    )
    assert toml_set_key("a = 1", "k", ["x"]) == 'a = 1\nk = ["x"]\n'
    assert toml_set_key("", "k", ["x"]) == 'k = ["x"]\n'


def test_toml_set_key_refuses_a_result_that_is_not_toml() -> None:
    from cva.setup.wizard import toml_set_key

    with pytest.raises(ValueError):
        toml_set_key("a = = 1\n", "k", ["x"])


def test_an_invalid_config_stops_before_any_question_and_writes_nothing(
    tmp_path: Path,
) -> None:
    broken = b"owner_telegram_id = [unclosed\n"
    (tmp_path / "config.local.toml").write_bytes(broken)
    scripted = ScriptedIO([])  # any prompt would fail the test
    assert wizard(tmp_path, scripted) == 1
    assert "config.local.toml is not valid TOML" in scripted.transcript
    assert "run init again" in scripted.transcript
    assert scripted.prompts == []
    assert (tmp_path / "config.local.toml").read_bytes() == broken
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.local.toml"]
