"""Provider prerequisite checks that only ask yes/no questions.

``check_codex`` runs the user's own ``codex`` binary twice: ``codex
--version`` (first line shown) and ``codex login status``, of which only the
exit code is read (0 = logged in). That command's output can name the
account or show a masked API key, so it is discarded. Codex credential files
are never opened and no network call is made here.
"""

from collections.abc import Callable, Sequence

from cva.i18n import Lang, t

Runner = Callable[[Sequence[str]], tuple[int, str]]
Which = Callable[[str], str | None]


def check_codex(
    runner: Runner, which: Which, *, path: str = "codex", lang: Lang = "en"
) -> list[str]:
    """Indented report lines about the Codex CLI, for the setup wizard."""
    codex = which(path)
    if codex is None:
        return [t("wizard.codex_missing", lang)]
    code, out = runner([codex, "--version"])
    first_line = out.strip().splitlines()[0][:80] if code == 0 and out.strip() else ""
    lines = [
        t("wizard.codex_version", lang, line=first_line)
        if first_line
        else t("wizard.codex_no_version", lang)
    ]
    status_code, _discarded = runner([codex, "login", "status"])
    if status_code == 0:
        lines.append(t("wizard.codex_logged_in", lang))
    elif status_code > 0:
        lines.append(t("wizard.codex_logged_out", lang))
    else:
        lines.append(t("wizard.codex_unknown", lang))
    return lines
