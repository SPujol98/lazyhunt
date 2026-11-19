"""CLI entrypoint (design D12, tasks 8b.5–8b.6): argparse over JobService.

Thin plain-text shell with bot parity: the analysis breakdown reuses the
notifier's ``breakdown_message``, so bot and CLI render identical output.
Verbs (``check|ingest|analyze|cv|sent|export|import-history|keywords|beacon``) map 1:1 to
service use-cases — no business logic here. ``init`` is the exception: the
interactive first-run wizard (``cva/setup/wizard.py``, English text) runs
before any settings exist, so ``main`` dispatches it without building them.
User-facing strings follow ``Settings.ui_language`` (English or Spanish; the
catalog is ``cva/i18n/panel.py``); code and comments are English.
"""

import argparse
import asyncio
import sys
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import TextIO

from cva.config import Settings
from cva.families import BUILTIN_REGISTRY, FamilyRegistry
from cva.gmail.client import (
    DEFAULT_CREDENTIALS_PATH,
    DEFAULT_TOKEN_PATH,
    GMAIL_SCOPE,
    GmailAuthError,
    run_auth_flow,
)
from cva.i18n import DEFAULT_LANG, LEGACY_LANG, Lang, t
from cva.llm import describe_llm_setup
from cva.notifier import (
    beacon_action_message,
    beacon_paused_message,
    beacons_list_message,
    judge_breakdown_message,
    new_job_message,
    sent_message,
)
from cva.profile_paths import resolve_profile_paths
from cva.service import (
    JobService,
    OfferClosedError,
    ServiceError,
    display_judgment,
    display_result,
)
from cva.setup.wizard import run_init
from cva.tracker import RepositoryError

# --- rejudge argparse type functions (hardening: fail with a clear message
# and a non-zero exit instead of silently misbehaving on bad input) ---------


def _iso_date(lang: Lang) -> Callable[[str], str]:
    def parse(value: str) -> str:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                t("panel.cli.since", lang, value=repr(value))
            ) from exc
        return value

    return parse


def _positive_int(lang: Lang) -> Callable[[str], int]:
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                t("panel.cli.int", lang, value=repr(value))
            ) from exc
        if parsed < 1:
            raise argparse.ArgumentTypeError(
                t("panel.cli.ge1", lang, value=repr(value))
            )
        return parsed

    return parse


def _non_negative_float(lang: Lang) -> Callable[[str], float]:
    def parse(value: str) -> float:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                t("panel.cli.number", lang, value=repr(value))
            ) from exc
        if parsed < 0:
            raise argparse.ArgumentTypeError(
                t("panel.cli.ge0", lang, value=repr(value))
            )
        return parsed

    return parse


def _score_0_100(lang: Lang) -> Callable[[str], int]:
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                t("panel.cli.int", lang, value=repr(value))
            ) from exc
        if not 0 <= parsed <= 100:
            raise argparse.ArgumentTypeError(
                t("panel.cli.range", lang, value=repr(value))
            )
        return parsed

    return parse


def _cmd_check(service: JobService, lang: Lang) -> str:
    sweep = asyncio.run(service.run_due_beacons())
    if not sweep.notifications and not sweep.paused_alerts:
        return t("panel.cli.nothing_new", lang)
    lines: list[str] = []
    for notification in sweep.notifications:
        lines.append(
            f"🆕 [{notification.beacon_name}] "
            + new_job_message(
                title=notification.title,
                company=notification.company,
                age=notification.age or t("panel.cli.recent", lang),
                job_id=notification.job_id,
                threshold=service.threshold,
                lang=lang,
            )
        )
    lines.extend(
        beacon_paused_message(alert, lang=lang) for alert in sweep.paused_alerts
    )
    return "\n".join(lines)


def _cmd_ingest(service: JobService, args: argparse.Namespace, lang: Lang) -> str:
    offer_id = asyncio.run(service.ingest(args.url, args.paste))
    return t("panel.cli.ingested", lang, id=offer_id)


def _cmd_analyze(service: JobService, args: argparse.Namespace, lang: Lang) -> str:
    try:
        result = asyncio.run(service.analyze(args.offer_id))
    except OfferClosedError as closed:
        # Already discarded + noted by the service; nothing to analyze.
        return t("panel.cli.closed", lang, id=closed.offer_id, marker=closed.marker)
    judgment, fallback = service.latest_judgment(args.offer_id)
    return judge_breakdown_message(
        display_result(result, lang),
        display_judgment(judgment, lang),
        args.offer_id,
        judge_fallback=fallback,
        lang=lang,
    )


def _judge_eval_labels(args: argparse.Namespace, settings: Settings | None) -> Path:
    """``--labels`` wins; else the profile's eval bank; else the owner's ``eval/``."""
    if args.labels is not None:
        return Path(args.labels)
    profile_dir = settings.profile_dir if settings is not None else None
    return resolve_profile_paths(profile_dir).eval_labels


def _cmd_judge_eval(
    service: JobService,
    args: argparse.Namespace,
    settings: Settings | None,
    lang: Lang,
) -> str:
    # Printed directly (not via the return-string channel) so the banner and
    # spend warning appear BEFORE the LLM calls the report is about to make.
    if settings is not None:
        print(describe_llm_setup(settings))
    print(t("panel.cli.judge_eval_warning", lang))
    return asyncio.run(service.judge_eval(_judge_eval_labels(args, settings)))


def _cmd_rejudge(
    service: JobService,
    args: argparse.Namespace,
    settings: Settings | None,
    lang: Lang,
) -> str:
    # Same pattern as judge-eval: the banner and spend warning are printed
    # directly (not via the return-string channel) so they appear BEFORE the
    # LLM calls the run is about to make. The banner prints unconditionally
    # (also for --dry-run/--report-only, which make zero LLM calls) so the
    # effective provider is visible even on a pure dry-run.
    if settings is not None:
        print(describe_llm_setup(settings))
    if not args.dry_run and not args.report_only:
        print(t("panel.cli.rejudge_warning", lang))
    result = asyncio.run(
        service.rejudge_backlog(
            since=args.since,
            title_filter=args.title_filter,
            include_discarded=args.include_discarded,
            force=args.force,
            limit=args.limit,
            dry_run=args.dry_run,
            report_only=args.report_only,
            min_score=args.min_score,
            pace_seconds=args.pace,
            max_consecutive_failures=args.max_consecutive_failures,
            progress_fn=print,
        )
    )
    if result.dry_run:
        would_judge = (
            t(
                "panel.cli.of",
                lang,
                planned=result.planned_candidates,
                total=result.total_candidates,
            )
            if result.planned_candidates != result.total_candidates
            else f"{result.total_candidates}"
        )
        return t(
            "panel.cli.dry_run",
            lang,
            would_judge=would_judge,
            state=result.skipped_state,
            short=result.skipped_short_text,
            since=result.skipped_since,
            title=result.skipped_title_filter,
            judged=result.skipped_already_judged,
            tokens=result.estimated_tokens,
        )
    footer = (
        "\n\n" + t("panel.cli.report", lang, path=result.report_path)
        if result.report_path
        else ""
    )
    return result.report_text + footer


def _cmd_cv(service: JobService, args: argparse.Namespace, lang: Lang) -> str:
    try:
        pdf_path = asyncio.run(service.generate_cv(args.offer_id, args.role, args.lang))
    except ServiceError as exc:
        return f"⚠️ {exc.text(lang)}"
    return t("panel.cli.cv_done", lang, path=pdf_path)


def _cmd_sent(service: JobService, args: argparse.Namespace, lang: Lang) -> str:
    try:
        service.mark_sent(args.offer_id)
    except ServiceError as exc:
        return f"⚠️ {exc.text(lang)}"
    return sent_message(
        args.offer_id, service.offer(args.offer_id).match or 0, lang=lang
    )


def _cmd_export(service: JobService, args: argparse.Namespace, lang: Lang) -> str:
    path = service.export(Path(args.out) if args.out else None)
    return t("panel.cli.exported", lang, path=path)


def _cmd_import_history(
    service: JobService, args: argparse.Namespace, lang: Lang
) -> str:
    try:
        result = service.import_history(Path(args.path), dry_run=args.dry_run)
    except ServiceError as exc:
        return f"⚠️ {exc.text(lang)}"
    head = t(
        "panel.cli.import_dry" if result.dry_run else "panel.cli.import_head", lang
    )
    lines = [
        t(
            "panel.cli.import_counts",
            lang,
            head=head,
            inserted=result.inserted,
            merged=result.merged,
            skipped=result.skipped,
            invalid=len(result.invalid),
        )
    ]
    lines.extend(f"⚠️ {line}" for line in result.invalid)
    return "\n".join(lines)


def _cmd_keywords(service: JobService, args: argparse.Namespace, lang: Lang) -> str:
    rows = service.keywords(args.profile)
    if not rows:
        return t("panel.cli.no_keywords", lang)
    return "\n".join(
        f"{row.status or '·'} {row.keyword} ×{row.frequency}" for row in rows
    )


def _cmd_beacon(service: JobService, args: argparse.Namespace, lang: Lang) -> str:
    action, rest = args.action, args.rest
    if action == "list":
        return beacons_list_message(service.beacons(), lang=lang)
    if action == "add":
        if len(rest) != 2:
            return t("panel.cli.beacon_add_usage", lang)
        return t(
            "panel.cli.beacon_created",
            lang,
            id=service.create_beacon(rest[0], rest[1]),
        )
    if action in ("resume", "disable") and len(rest) == 1:
        # RepositoryError IS a ValueError: parse and dispatch need SEPARATE
        # try-blocks so the unknown-id handler is not swallowed by the parse one.
        try:
            beacon_id = int(rest[0])
        except ValueError:
            return t("panel.cli.beacon_id_usage", lang)
        try:
            if action == "resume":
                service.resume_beacon(beacon_id)
            else:
                service.disable_beacon(beacon_id)
        except RepositoryError:
            return t("panel.cli.no_beacon", lang, id=beacon_id)
        return beacon_action_message(action, beacon_id, lang=lang)
    return t("panel.cli.beacon_usage", lang)


def _cmd_gmail_auth(
    args: argparse.Namespace, settings: Settings | None, lang: Lang
) -> str:
    credentials = args.credentials or (
        settings.gmail_credentials_path if settings else DEFAULT_CREDENTIALS_PATH
    )
    token = args.token or (
        settings.gmail_token_path if settings else DEFAULT_TOKEN_PATH
    )
    credentials_path = Path(credentials).expanduser()
    token_path = Path(token).expanduser()
    try:
        run_auth_flow(credentials_path, token_path, lang=lang)
    except GmailAuthError as exc:
        return f"⚠️ {exc}"
    return t("panel.cli.gmail_ok", lang, scope=GMAIL_SCOPE, path=token_path)


def _families(service: object) -> FamilyRegistry:
    """The profile's role families (built-ins for a service double)."""
    families = getattr(getattr(service, "preferences", None), "families", None)
    return families if isinstance(families, FamilyRegistry) else BUILTIN_REGISTRY


def build_parser(
    families: FamilyRegistry = BUILTIN_REGISTRY,
    lang: Lang = LEGACY_LANG,
) -> argparse.ArgumentParser:
    """Argparse surface (help in ``lang``, same verbs as design D12); ``cv``'s
    role choices are the profile's role families (role-families T5)."""

    def h(key: str) -> str:
        return t(f"panel.cli.help.{key}", lang)

    parser = argparse.ArgumentParser(
        prog="cli.py",
        description=t("panel.cli.description", lang),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "init",
        help="interactive first-run setup: checks Claude Code, writes secrets to "
        ".env and settings to config.local.toml, creates a profile skeleton "
        "(safe to re-run)",
    )
    sub.add_parser("check", help=h("check"))

    p_ingest = sub.add_parser("ingest", help=h("ingest"))
    source = p_ingest.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help=h("url"))
    source.add_argument("--paste", help=h("paste"))

    p_analyze = sub.add_parser("analyze", help=h("analyze"))
    p_analyze.add_argument("offer_id", type=int)

    p_cv = sub.add_parser("cv", help=h("cv"))
    p_cv.add_argument("offer_id", type=int)
    p_cv.add_argument(
        "role",
        nargs="?",
        default=None,
        choices=[family.role for family in families.families],
    )
    p_cv.add_argument("lang", nargs="?", default=None, choices=["es", "en", "ca"])

    p_sent = sub.add_parser("sent", help=h("sent"))
    p_sent.add_argument("offer_id", type=int)

    p_export = sub.add_parser("export", help=h("export"))
    p_export.add_argument("--out", default=None, help=h("out"))

    p_import = sub.add_parser("import-history", help=h("import"))
    p_import.add_argument("path", help=h("import_path"))
    p_import.add_argument(
        "--dry-run",
        action="store_true",
        help=h("import_dry"),
    )

    p_keywords = sub.add_parser("keywords", help=h("keywords"))
    p_keywords.add_argument("profile", nargs="?", default=None)

    p_judge_eval = sub.add_parser("judge-eval", help=h("judge_eval"))
    p_judge_eval.add_argument("--labels", default=None, help=h("labels"))

    p_rejudge = sub.add_parser("rejudge", help=h("rejudge"))
    p_rejudge.add_argument(
        "--since", type=_iso_date(lang), default=None, help=h("since")
    )
    p_rejudge.add_argument(
        "--title-filter", action="store_true", help=h("title_filter")
    )
    p_rejudge.add_argument(
        "--include-discarded", action="store_true", help=h("include_discarded")
    )
    p_rejudge.add_argument("--force", action="store_true", help=h("force"))
    p_rejudge.add_argument("--limit", type=_positive_int(lang), default=None)
    p_rejudge.add_argument("--dry-run", action="store_true", help=h("rejudge_dry"))
    p_rejudge.add_argument("--report-only", action="store_true", help=h("report_only"))
    p_rejudge.add_argument(
        "--pace", type=_non_negative_float(lang), default=20.0, help=h("pace")
    )
    p_rejudge.add_argument("--min-score", type=_score_0_100(lang), default=50)
    p_rejudge.add_argument(
        "--max-consecutive-failures",
        type=_positive_int(lang),
        default=3,
        help=h("max_failures"),
    )

    p_gmail = sub.add_parser("gmail-auth", help=h("gmail_auth"))
    p_gmail.add_argument("--credentials", default=None, help=h("credentials"))
    p_gmail.add_argument("--token", default=None, help=h("token"))

    p_beacon = sub.add_parser("beacon", help=h("beacon"))
    p_beacon.add_argument(
        "action",
        nargs="?",
        default="list",
        choices=["list", "add", "resume", "disable"],
    )
    p_beacon.add_argument("rest", nargs="*")
    return parser


def run(
    argv: Sequence[str],
    service: JobService,
    out: TextIO | None = None,
    *,
    settings: Settings | None = None,
    lang: Lang | None = None,
) -> str:
    """Parse ``argv`` against the injected service; return the output text.

    The UI language is ``lang``, else ``settings.ui_language``, else the
    transitional Spanish default (callers that pass neither keep today's output).

    ``settings``, when given, prints the effective-LLM banner (shared-config
    T2) at the start of ``judge-eval``/``rejudge``; tests that build a
    ``service`` double directly and pass no ``settings`` keep their existing
    output unchanged.
    """
    if lang is None:
        lang = settings.ui_language if settings is not None else LEGACY_LANG
    args = build_parser(_families(service), lang).parse_args(argv)
    handlers = {
        "check": lambda: _cmd_check(service, lang),
        "ingest": lambda: _cmd_ingest(service, args, lang),
        "analyze": lambda: _cmd_analyze(service, args, lang),
        "cv": lambda: _cmd_cv(service, args, lang),
        "sent": lambda: _cmd_sent(service, args, lang),
        "export": lambda: _cmd_export(service, args, lang),
        "import-history": lambda: _cmd_import_history(service, args, lang),
        "keywords": lambda: _cmd_keywords(service, args, lang),
        "beacon": lambda: _cmd_beacon(service, args, lang),
        "judge-eval": lambda: _cmd_judge_eval(service, args, settings, lang),
        "rejudge": lambda: _cmd_rejudge(service, args, settings, lang),
        "gmail-auth": lambda: _cmd_gmail_auth(args, settings, lang),
        "init": _cmd_init,
    }
    return handlers[args.command]()


def _cmd_init() -> str:
    """Run the setup wizard in the current directory (it prints as it goes)."""
    code = run_init(Path.cwd())
    return "" if code == 0 else f"init ended with exit code {code}"


def main(argv: Sequence[str] | None = None) -> None:
    """Real entrypoint: wire JobService from Settings and print the result.

    ``argv`` stays injectable for tests; the actual process invocation below
    passes nothing, so this reads the real command-line arguments
    (``sys.argv[1:]``) instead of always analyzing an empty argument list.
    """
    arguments = list(argv if argv is not None else sys.argv[1:])
    if arguments[:1] == ["init"]:
        # A fresh clone has no token yet: never load Settings for the wizard.
        build_parser(lang=DEFAULT_LANG).parse_args(arguments)
        code = run_init(Path.cwd())
        if code:
            raise SystemExit(code)
        return
    settings = Settings()
    print(
        run(
            arguments,
            JobService.from_settings(settings),
            settings=settings,
            lang=settings.ui_language,
        )
    )


if __name__ == "__main__":
    main()
