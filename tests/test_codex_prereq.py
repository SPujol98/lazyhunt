"""Codex CLI prerequisite check: installed / version / logged in, yes-no only.

The runner and ``which`` are injected, so no real `codex` is ever run. Only
the exit code of `codex login status` is read; its output (which can name
the account or a masked API key) is discarded.
"""

from collections.abc import Sequence

from cva.setup.prereq import check_codex


class _Runner:
    def __init__(self, replies: dict[str, tuple[int, str]]) -> None:
        self.replies = replies
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> tuple[int, str]:
        self.calls.append(list(argv))
        return self.replies.get(" ".join(argv[1:]), (-1, ""))


def _which(found: str | None) -> "_Which":
    return _Which(found)


class _Which:
    def __init__(self, found: str | None) -> None:
        self.found = found
        self.asked: list[str] = []

    def __call__(self, name: str) -> str | None:
        self.asked.append(name)
        return self.found


def test_missing_codex_says_how_to_install_and_runs_nothing() -> None:
    runner = _Runner({})

    lines = check_codex(runner, _which(None))

    assert runner.calls == []
    assert any("`codex` was not found" in line for line in lines)
    assert any("codex login" in line for line in lines)


def test_logged_in_reports_version_and_yes_without_echoing_status_output() -> None:
    runner = _Runner(
        {
            "--version": (0, "codex-cli 0.171.0\n"),
            "login status": (0, "Logged in using ChatGPT as someone@example.com\n"),
        }
    )

    lines = check_codex(runner, _which("/usr/bin/codex"))

    assert runner.calls == [
        ["/usr/bin/codex", "--version"],
        ["/usr/bin/codex", "login", "status"],
    ]
    assert lines == ["  Codex CLI: codex-cli 0.171.0", "  Codex CLI: logged in."]
    assert "someone@example.com" not in "\n".join(lines)


def test_not_logged_in_tells_the_user_to_run_codex_login() -> None:
    runner = _Runner({"--version": (0, "codex-cli 0.171.0"), "login status": (1, "")})

    lines = check_codex(runner, _which("/usr/bin/codex"))

    assert lines[-1].startswith("  Codex CLI is installed but not logged in")
    assert "`codex login`" in lines[-1]


def test_unknown_login_state_when_the_status_command_cannot_run() -> None:
    runner = _Runner({"--version": (0, "codex-cli 0.171.0")})

    lines = check_codex(runner, _which("/usr/bin/codex"))

    assert "Could not tell whether Codex CLI is logged in" in lines[-1]


def test_configured_path_is_looked_up() -> None:
    which = _which(None)

    check_codex(_Runner({}), which, path="/opt/codex/bin/codex")

    assert which.asked == ["/opt/codex/bin/codex"]
