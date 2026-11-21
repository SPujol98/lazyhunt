"""Provider prerequisite checks that only ask yes/no questions.

``check_codex`` runs the user's own ``codex`` binary twice: ``codex
--version`` (first line shown) and ``codex login status``, of which only the
exit code is read (0 = logged in). That command's output can name the
account or show a masked API key, so it is discarded. Codex credential files
are never opened and no network call is made here.
"""

from collections.abc import Callable, Sequence

Runner = Callable[[Sequence[str]], tuple[int, str]]
Which = Callable[[str], str | None]


def check_codex(runner: Runner, which: Which, *, path: str = "codex") -> list[str]:
    """Indented report lines about the Codex CLI, for the setup wizard."""
    codex = which(path)
    if codex is None:
        return [
            (
                "  `codex` was not found on PATH. To use the codex_cli provider, "
                "install the Codex CLI (https://developers.openai.com/codex/cli) "
                "and run `codex login`, or choose another provider."
            )
        ]
    code, out = runner([codex, "--version"])
    first_line = out.strip().splitlines()[0][:80] if code == 0 and out.strip() else ""
    lines = [
        f"  Codex CLI: {first_line}"
        if first_line
        else "  Codex CLI: found, but `codex --version` did not answer."
    ]
    status_code, _discarded = runner([codex, "login", "status"])
    if status_code == 0:
        lines.append("  Codex CLI: logged in.")
    elif status_code > 0:
        lines.append(
            "  Codex CLI is installed but not logged in: run `codex login` "
            "with your ChatGPT account, then re-run this wizard if you like."
        )
    else:
        lines.append(
            "  Could not tell whether Codex CLI is logged in; check it with "
            "`codex login status`."
        )
    return lines
