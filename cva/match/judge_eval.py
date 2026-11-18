"""Judge-eval harness: compare the LLM judge against the owner-reviewed
labeled eval bank (``eval/labels.json`` + ``eval/offers/*.txt``).

Runs the judge ONLY (no extraction / keyword score) — the harness exists to
calibrate the judge's prompt against the owner's manual analyses, and a
keyword-score column would need its own LLM round-trip per offer (extraction
is not "cheap": it spends the same quota again for a number this harness
does not need). ``cli.py judge-eval`` is the only real caller — it prints a
one-line spend warning before this runs and uses the configured LLM, so it
must never be invoked by tests against a real LLM.
"""

import json
from pathlib import Path
from typing import Final, NamedTuple

from cva.claimsdb import ClaimsDB
from cva.llm import LlmClient
from cva.match.judge import _DEFAULT_THRESHOLD, judge_offer

_DEFAULT_TOLERANCE: Final[int] = 10


class EvalRow(NamedTuple):
    """One labeled offer's judge-vs-label comparison."""

    file: str
    label_score: int
    judge_score: int
    abs_error: int
    label_verdict: str
    judge_verdict: str
    hard_walls: list[str]


class EvalFailure(NamedTuple):
    """One offer whose judge call raised instead of returning a judgment."""

    file: str
    error: str


async def run_judge_eval(
    labels_path: Path,
    claims: ClaimsDB,
    llm: LlmClient,
    *,
    model: str | None = None,
    threshold: int = _DEFAULT_THRESHOLD,
) -> str:
    """Judge every ``eval/labels.json`` offer; return the formatted report.

    Offer texts are read relative to ``labels_path``'s directory (matching
    each entry's ``"file"`` field, e.g. ``offers/acme-ai-engineer.txt``).
    ``threshold`` is forwarded to :func:`judge_offer` (:func:`derive_verdict`
    band) — it MUST be the same threshold ``analyze()`` uses in production
    (``Settings.threshold``/``JobService._threshold``), never a hardcoded
    default, or this harness silently grades verdict agreement against a
    different gate than the one actually in effect (robustness follow-up:
    duplicated threshold constant).

    One offer's judge call failing never aborts the run (robustness
    follow-up): it is recorded as a failure with its error message,
    excluded from MAE/verdict-agreement, and every other offer still gets
    judged and reported.
    """
    raw: dict[str, object] = json.loads(labels_path.read_text(encoding="utf-8"))
    tolerance_value = raw.get("tolerance", _DEFAULT_TOLERANCE)
    tolerance = tolerance_value if isinstance(tolerance_value, int) else _DEFAULT_TOLERANCE
    entries = raw.get("offers", [])
    rows: list[EvalRow] = []
    failures: list[EvalFailure] = []
    if isinstance(entries, list):
        for entry in entries:
            offer_path = labels_path.parent / entry["file"]
            text = offer_path.read_text(encoding="utf-8")
            try:
                judgment = await judge_offer(
                    text, claims, llm, model=model, threshold=threshold
                )
            except Exception as exc:  # noqa: BLE001 — the report row IS the surface
                failures.append(
                    EvalFailure(
                        file=entry["file"], error=f"{type(exc).__name__}: {exc}"
                    )
                )
                continue
            rows.append(
                EvalRow(
                    file=entry["file"],
                    label_score=entry["score"],
                    judge_score=judgment.score,
                    abs_error=abs(judgment.score - entry["score"]),
                    label_verdict=entry["verdict"],
                    judge_verdict=judgment.verdict,
                    hard_walls=[wall.text for wall in judgment.walls if wall.hard],
                )
            )
    return _format_report(rows, failures, tolerance)


def _format_report(
    rows: list[EvalRow], failures: list[EvalFailure], tolerance: int
) -> str:
    total = len(rows) + len(failures)
    if total == 0:
        return "No labeled offers found in the eval bank."
    lines: list[str] = []
    if rows:
        header = (
            f"{'file':<40} {'label':>6} {'judge':>6} {'|err|':>6} "
            f"{'label_verdict':<20} {'judge_verdict':<20} hard_walls"
        )
        lines.append(header)
        agree = 0
        within = 0
        total_error = 0
        for row in rows:
            if row.judge_verdict == row.label_verdict:
                agree += 1
            if row.abs_error <= tolerance:
                within += 1
            total_error += row.abs_error
            lines.append(
                f"{row.file:<40} {row.label_score:>6} {row.judge_score:>6} "
                f"{row.abs_error:>6} {row.label_verdict:<20} {row.judge_verdict:<20} "
                f"{'; '.join(row.hard_walls) or '-'}"
            )
        count = len(rows)
        mae = total_error / count
        lines.append("")
        lines.append(f"MAE: {mae:.1f} (tolerance {tolerance})")
        lines.append(f"Verdict agreement: {agree}/{count} ({agree / count:.0%})")
        lines.append(f"Within tolerance: {within}/{count} ({within / count:.0%})")
    else:
        lines.append("No offer was judged successfully.")
    if failures:
        lines.append("")
        lines.append(
            f"Failures: {len(failures)}/{total} (excluded from MAE/agreement):"
        )
        for failure in failures:
            lines.append(f"  {failure.file}: {failure.error}")
    return "\n".join(lines)
