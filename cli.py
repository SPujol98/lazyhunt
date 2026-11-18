"""CLI entrypoint (design D12, tasks 8b.5–8b.6): argparse over JobService.

Thin plain-text shell with bot parity: the analysis breakdown reuses the
notifier's ``breakdown_message``, so bot and CLI render identical output.
Verbs (``check|ingest|analyze|cv|sent|export|import-history|keywords|beacon``) map 1:1 to
service use-cases — no business logic here. ``init`` is the exception: the
interactive first-run wizard (``cva/setup/wizard.py``, English text) runs
before any settings exist, so ``main`` dispatches it without building them.
User-facing strings are Spanish on purpose (same owner as the bot); code and
comments are English.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import TextIO

from cva.config import Settings
from cva.gmail.client import (
    DEFAULT_CREDENTIALS_PATH,
    DEFAULT_TOKEN_PATH,
    GMAIL_SCOPE,
    GmailAuthError,
    run_auth_flow,
)
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
from cva.service import JobService, OfferClosedError, ServiceError
from cva.setup.wizard import run_init
from cva.tracker import RepositoryError

# --- rejudge argparse type functions (hardening: fail with a clear message
# and a non-zero exit instead of silently misbehaving on bad input) ---------


def _iso_date(value: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--since debe ser una fecha ISO YYYY-MM-DD: {value!r}"
        ) from exc
    return value


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"debe ser un entero: {value!r}") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"debe ser >= 1: {value!r}")
    return parsed


def _non_negative_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"debe ser un número: {value!r}") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"debe ser >= 0: {value!r}")
    return parsed


def _score_0_100(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"debe ser un entero: {value!r}") from exc
    if not 0 <= parsed <= 100:
        raise argparse.ArgumentTypeError(f"debe estar entre 0 y 100: {value!r}")
    return parsed


def _cmd_check(service: JobService) -> str:
    sweep = asyncio.run(service.run_due_beacons())
    if not sweep.notifications and not sweep.paused_alerts:
        return "Sin novedades: 0 ofertas nuevas, 0 beacons pausados."
    lines: list[str] = []
    for notification in sweep.notifications:
        lines.append(
            f"🆕 [{notification.beacon_name}] "
            + new_job_message(
                title=notification.title,
                company=notification.company,
                age=notification.age or "reciente",
                job_id=notification.job_id,
                threshold=service.threshold,
            )
        )
    lines.extend(beacon_paused_message(alert) for alert in sweep.paused_alerts)
    return "\n".join(lines)


def _cmd_ingest(service: JobService, args: argparse.Namespace) -> str:
    offer_id = asyncio.run(service.ingest(args.url, args.paste))
    return f"✅ Oferta #{offer_id} registrada."


def _cmd_analyze(service: JobService, args: argparse.Namespace) -> str:
    try:
        result = asyncio.run(service.analyze(args.offer_id))
    except OfferClosedError as closed:
        # Already discarded + noted by the service; nothing to analyze.
        return f"🔒 Oferta #{closed.offer_id} cerrada ({closed.marker}): descartada, sin analizar."
    judgment, fallback = service.latest_judgment(args.offer_id)
    return judge_breakdown_message(
        result, judgment, args.offer_id, judge_fallback=fallback
    )


def _judge_eval_labels(args: argparse.Namespace, settings: Settings | None) -> Path:
    """``--labels`` wins; else the profile's eval bank; else the owner's ``eval/``."""
    if args.labels is not None:
        return Path(args.labels)
    profile_dir = settings.profile_dir if settings is not None else None
    return resolve_profile_paths(profile_dir).eval_labels


def _cmd_judge_eval(
    service: JobService, args: argparse.Namespace, settings: Settings | None
) -> str:
    # Printed directly (not via the return-string channel) so the banner and
    # spend warning appear BEFORE the LLM calls the report is about to make.
    if settings is not None:
        print(describe_llm_setup(settings))
    print("⚠️ judge-eval usa el LLM configurado y consumirá tu cuota…")
    return asyncio.run(service.judge_eval(_judge_eval_labels(args, settings)))


def _cmd_rejudge(
    service: JobService, args: argparse.Namespace, settings: Settings | None
) -> str:
    # Same pattern as judge-eval: the banner and spend warning are printed
    # directly (not via the return-string channel) so they appear BEFORE the
    # LLM calls the run is about to make. The banner prints unconditionally
    # (also for --dry-run/--report-only, which make zero LLM calls) so the
    # effective provider is visible even on a pure dry-run.
    if settings is not None:
        print(describe_llm_setup(settings))
    if not args.dry_run and not args.report_only:
        print("⚠️ rejudge usa el LLM configurado (solo juez) y consumirá tu cuota…")
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
            f"{result.planned_candidates} de {result.total_candidates}"
            if result.planned_candidates != result.total_candidates
            else f"{result.total_candidates}"
        )
        return (
            f"🔎 Dry-run: se juzgarían {would_judge} ofertas candidatas "
            f"(saltadas: {result.skipped_state} por estado, "
            f"{result.skipped_short_text} por texto corto/placeholder, "
            f"{result.skipped_since} por --since, "
            f"{result.skipped_title_filter} por --title-filter, "
            f"{result.skipped_already_judged} ya juzgadas).\n"
            f"Estimación: ~{result.estimated_tokens} tokens "
            "(~10.000 tokens/oferta, cifra aproximada)."
        )
    footer = f"\n\n📄 Informe: {result.report_path}" if result.report_path else ""
    return result.report_text + footer


def _cmd_cv(service: JobService, args: argparse.Namespace) -> str:
    pdf_path = asyncio.run(service.generate_cv(args.offer_id, args.role, args.lang))
    return f"📄 CV generado: {pdf_path}"


def _cmd_sent(service: JobService, args: argparse.Namespace) -> str:
    service.mark_sent(args.offer_id)
    return sent_message(args.offer_id, service.offer(args.offer_id).match or 0)


def _cmd_export(service: JobService, args: argparse.Namespace) -> str:
    path = service.export(Path(args.out) if args.out else None)
    return f"📊 Exportado a {path}"


def _cmd_import_history(service: JobService, args: argparse.Namespace) -> str:
    try:
        result = service.import_history(Path(args.path), dry_run=args.dry_run)
    except ServiceError as exc:
        return f"⚠️ {exc}"
    head = "🔎 Dry-run (no se escribió nada)" if result.dry_run else "📥 Importación"
    lines = [
        (
            f"{head}: insertadas: {result.inserted} · fusionadas: {result.merged} · "
            f"omitidas: {result.skipped} · inválidas: {len(result.invalid)}"
        )
    ]
    lines.extend(f"⚠️ {line}" for line in result.invalid)
    return "\n".join(lines)


def _cmd_keywords(service: JobService, args: argparse.Namespace) -> str:
    rows = service.keywords(args.profile)
    if not rows:
        return "Sin keywords registradas todavía."
    return "\n".join(
        f"{row.status or '·'} {row.keyword} ×{row.frequency}" for row in rows
    )


def _cmd_beacon(service: JobService, args: argparse.Namespace) -> str:
    action, rest = args.action, args.rest
    if action == "list":
        return beacons_list_message(service.beacons())
    if action == "add":
        if len(rest) != 2:
            return "Uso: cli.py beacon add <nombre> <url>"
        return f"✅ Beacon #{service.create_beacon(rest[0], rest[1])} creado."
    if action in ("resume", "disable") and len(rest) == 1:
        # RepositoryError IS a ValueError: parse and dispatch need SEPARATE
        # try-blocks so the unknown-id handler is not swallowed by the parse one.
        try:
            beacon_id = int(rest[0])
        except ValueError:
            return "Uso: cli.py beacon resume|disable <id>"
        try:
            if action == "resume":
                service.resume_beacon(beacon_id)
            else:
                service.disable_beacon(beacon_id)
        except RepositoryError:
            return f"⚠️ No existe el beacon #{beacon_id}."
        return beacon_action_message(action, beacon_id)
    return "Uso: cli.py beacon list|add|resume|disable ..."


def _cmd_gmail_auth(args: argparse.Namespace, settings: Settings | None) -> str:
    credentials = args.credentials or (
        settings.gmail_credentials_path if settings else DEFAULT_CREDENTIALS_PATH
    )
    token = args.token or (
        settings.gmail_token_path if settings else DEFAULT_TOKEN_PATH
    )
    credentials_path = Path(credentials).expanduser()
    token_path = Path(token).expanduser()
    try:
        run_auth_flow(credentials_path, token_path)
    except GmailAuthError as exc:
        return f"⚠️ {exc}"
    return (
        f"✅ Gmail autorizado (solo lectura: {GMAIL_SCOPE}).\n"
        f"Token guardado en {token_path} (permisos 0600). "
        "Cópialo con las credenciales al PC del bot (ver docs/gmail.md)."
    )


def build_parser() -> argparse.ArgumentParser:
    """Argparse surface (Spanish help, same verbs as design D12)."""
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="Asistente de seguimiento de ofertas (mismo JobService que el bot).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "init",
        help="interactive first-run setup: checks Claude Code, writes secrets to "
        ".env and settings to config.local.toml, creates a profile skeleton "
        "(safe to re-run)",
    )
    sub.add_parser("check", help="comprueba los beacons que tocan (sweep)")

    p_ingest = sub.add_parser("ingest", help="registra una oferta (url o texto)")
    source = p_ingest.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="URL de la oferta")
    source.add_argument("--paste", help="texto de la oferta pegado")

    p_analyze = sub.add_parser("analyze", help="analiza una oferta registrada")
    p_analyze.add_argument("offer_id", type=int)

    p_cv = sub.add_parser("cv", help="genera el CV adaptado (PDF)")
    p_cv.add_argument("offer_id", type=int)
    p_cv.add_argument(
        "role", nargs="?", default=None, choices=["AI", "BACKEND"]
    )
    p_cv.add_argument("lang", nargs="?", default=None, choices=["es", "en", "ca"])

    p_sent = sub.add_parser("sent", help="marca la oferta como enviada")
    p_sent.add_argument("offer_id", type=int)

    p_export = sub.add_parser("export", help="exporta el xlsx de enviadas")
    p_export.add_argument("--out", default=None, help="ruta de destino")

    p_import = sub.add_parser(
        "import-history", help="importa el historial manual de ofertas (JSON)"
    )
    p_import.add_argument("path", help="JSON con la lista de registros")
    p_import.add_argument(
        "--dry-run",
        action="store_true",
        help="muestra los recuentos sin escribir nada",
    )

    p_keywords = sub.add_parser("keywords", help="registro de demanda (§5)")
    p_keywords.add_argument("profile", nargs="?", default=None)

    p_judge_eval = sub.add_parser(
        "judge-eval", help="compara el juez LLM con el banco de ofertas etiquetadas"
    )
    p_judge_eval.add_argument(
        "--labels",
        default=None,
        help="labels.json (por defecto: eval/ del profile_dir, o eval/labels.json)",
    )

    p_rejudge = sub.add_parser(
        "rejudge", help="re-evalúa el backlog de ofertas con el juez LLM (solo juez)"
    )
    p_rejudge.add_argument(
        "--since", type=_iso_date, default=None,
        help="YYYY-MM-DD (aprox., ver fecha_update)",
    )
    p_rejudge.add_argument(
        "--title-filter",
        action="store_true",
        help="pre-filtro de título/empresa (mismas listas que la extensión)",
    )
    p_rejudge.add_argument(
        "--include-discarded",
        action="store_true",
        help="incluye también las ofertas descartadas",
    )
    p_rejudge.add_argument(
        "--force",
        action="store_true",
        help="re-juzga aunque ya tenga un judge_results guardado",
    )
    p_rejudge.add_argument("--limit", type=_positive_int, default=None)
    p_rejudge.add_argument(
        "--dry-run",
        action="store_true",
        help="solo estima cuántas ofertas se juzgarían (sin llamar al LLM)",
    )
    p_rejudge.add_argument(
        "--report-only",
        action="store_true",
        help="reconstruye y escribe el informe desde judge_results guardados, "
        "sin llamar al LLM",
    )
    p_rejudge.add_argument(
        "--pace",
        type=_non_negative_float,
        default=20.0,
        help="segundos entre llamadas al juez",
    )
    p_rejudge.add_argument("--min-score", type=_score_0_100, default=50)
    p_rejudge.add_argument(
        "--max-consecutive-failures",
        type=_positive_int,
        default=3,
        help="detiene la ejecución tras N fallos consecutivos del juez "
        "(p.ej. cuota agotada)",
    )

    p_gmail = sub.add_parser(
        "gmail-auth",
        help="autoriza el acceso de solo lectura a Gmail (abre el navegador)",
    )
    p_gmail.add_argument(
        "--credentials", default=None, help="JSON de cliente OAuth (Escritorio)"
    )
    p_gmail.add_argument("--token", default=None, help="dónde guardar el token")

    p_beacon = sub.add_parser("beacon", help="gestión de beacons")
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
) -> str:
    """Parse ``argv`` against the injected service; return the output text.

    ``settings``, when given, prints the effective-LLM banner (shared-config
    T2) at the start of ``judge-eval``/``rejudge``; tests that build a
    ``service`` double directly and pass no ``settings`` keep their existing
    output unchanged.
    """
    args = build_parser().parse_args(argv)
    handlers = {
        "check": lambda: _cmd_check(service),
        "ingest": lambda: _cmd_ingest(service, args),
        "analyze": lambda: _cmd_analyze(service, args),
        "cv": lambda: _cmd_cv(service, args),
        "sent": lambda: _cmd_sent(service, args),
        "export": lambda: _cmd_export(service, args),
        "import-history": lambda: _cmd_import_history(service, args),
        "keywords": lambda: _cmd_keywords(service, args),
        "beacon": lambda: _cmd_beacon(service, args),
        "judge-eval": lambda: _cmd_judge_eval(service, args, settings),
        "rejudge": lambda: _cmd_rejudge(service, args, settings),
        "gmail-auth": lambda: _cmd_gmail_auth(args, settings),
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
        build_parser().parse_args(arguments)
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
        )
    )


if __name__ == "__main__":
    main()
