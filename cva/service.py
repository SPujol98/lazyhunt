"""JobService: every use-case bot.py/cli.py know (design D12, tasks 8.1–8.2).

The single seam over ingest, match, cvgen and tracker — entrypoints stay thin
shells with no business logic. CV generation happens only on an explicit call
(slice-7 invariant); analysis is cached at the extraction layer, so re-analysis
makes zero LLM calls. Keyword auto-append writes the §5 demand log
(``keyword_registry`` with per-offer dedupe via ``offer_keywords``), never
``synonyms.json`` (design D6). ``generate_cv`` delivers a byte-identical copy
of the owner's fixed base PDF (``cv_base_pdfs/``, fixed-base-cvs); masters are
no longer rendered.
"""

import asyncio
import json
import logging
import re
import sqlite3
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal, NamedTuple

from pydantic import BaseModel, Field, ValidationError

from cva.beacons import SweepResult, check_beacon, checks_done_today
from cva.calibration import (
    FIT_REASONS,
    MAX_ANCHORS,
    REASON_OTHER,
    CalibrationExample,
    CalibrationSummary,
    SentJudgment,
    outcome_polarity,
    sanitize_core_role,
)
from cva.claimsdb import ClaimsDB, load_claims
from cva.config import DEFAULT_BACKEND_THRESHOLD, Settings
from cva.cvgen import CvVariant
from cva.cvgen import engine as _cvgen_engine
from cva.cvgen.base_pdfs import base_cambios, deliver_base_pdf
from cva.cvgen.engine import generic_output_path
from cva.fetch import Fetcher, HttpxFetcher
from cva.ingest import (
    FetchedOffer,
    canonical_url,
    fetch_offer,
    job_id_from_url,
    paste_offer,
    source_from_url,
)
from cva.ingest.closed import CLOSED_NOTE, closed_marker
from cva.ingest.dates import extract_dates
from cva.ingest.posting_guard import GuardReport, clean_text, warning_line
from cva.ingest.sources import needs_cv, no_cv_label, source_label
from cva.llm import LlmClient, LlmResponseError, build_llm_client, supports_web_search
from cva.match import MatchResult, OfferExtraction, infer_language, score
from cva.match.apply_instructions import detect_apply_instructions
from cva.match.cover_letter import (
    CoverLetterResult,
    generate_cover_letter,
    known_company,
    normalize_company,
    owner_name,
)
from cva.match.extraction import ExtractionCache, extract
from cva.match.followup import FollowupDraft, generate_followup
from cva.match.gates import run_gates
from cva.match.interview_prep import (
    STAGES,
    CompanyFact,
    CompanyFacts,
    PrepInputs,
    PrepPack,
    PrepResult,
    assemble,
    build_facts_prompt,
    build_prep_prompt,
    verified_facts,
)
from cva.match.judge import OfferJudgment, Wall, build_claims_digest, judge_offer
from cva.match.judge_eval import run_judge_eval
from cva.match.persona import JudgePersona
from cva.match.salary_reference import (
    CITY_LABEL,
    ReferenceRange,
    infer_city,
    infer_level,
    load_reference_table,
    salary_line,
)
from cva.match.salary_research import (
    SalaryOutcome,
    SalaryResearch,
    build_research_prompt,
    to_outcome,
)
from cva.match.scorer import SynonymMap, classify_keyword, load_synonyms
from cva.match.stack_wall import stack_wall_reason
from cva.mirror import mirror_root, on_discarded, on_generated, on_sent
from cva.notifier import (
    SentMessage,
    asks_cover_letter,
    borderline_message,
    bridge_auto_failure_message,
    cv_keyboard,
    date_card_lines,
    deadline_alert_batch,
    judge_breakdown_message,
    keyboard_to_json,
    new_job_keyboard,
    with_also_on,
    with_no_cv_line,
)
from cva.profile import Profile, infer_profile, normalize_profile, role_for_profile
from cva.profile_identity import load_identity, require_identity_claim
from cva.profile_paths import ProfilePaths, resolve_profile_paths
from cva.profile_persona import load_judge_persona, load_letter_bases
from cva.profile_prefs import ProfilePrefs, load_preferences
from cva.report.panel import (
    DISCARDED_DAYS,
    PANEL_FILENAME,
    DiscardedOffer,
    render_panel,
    write_panel_atomic,
)
from cva.report.prep_pack import prep_filename, prep_summary, render_prep_html
from cva.scheduler import HEARTBEAT_INTERVAL_SECONDS, default_jitter
from cva.star import StarCandidate, star_candidates
from cva.title_filter import passes_filter as _passes_title_filter
from cva.tracker import (
    OUTCOME_ESTADOS,
    STAR_PLACEHOLDER,
    STAR_TEXT_FIELDS,
    BeaconRow,
    GmailProposalRow,
    HistoryImportResult,
    KeywordRow,
    OfferEvent,
    OfferInput,
    OfferRepository,
    OfferRow,
    RepositoryError,
    StarStory,
    export_xlsx,
    parse_history,
    refresh_tracker_xlsx,
)
from cva.tracker.history import PRE_SEND_ESTADOS
from cva.tracker.repo import DUPLICATE_NOTE_PREFIX, _is_stored_duplicate
from cva.tracker.xlsx import write_tracker_xlsx

Role = Literal["AI", "BACKEND"]
Language = Literal["es", "en", "ca"]
MatchMode = Literal["keyword", "hybrid"]

_logger = logging.getLogger(__name__)


BridgeZone = Literal["above", "gray", "below", "walled", "closed", "pending"]


class BridgeJobOutcome(NamedTuple):
    """Per-job auto-analysis result reported back to the bridge.

    Round-summary seam: the service RETURNs what happened (zone + score)
    instead of forcing callback plumbing; the bridge folds it into the
    per-payload tally. ``pending`` covers every failure — retry-pending AND
    exhausted-with-final-card — so the owner sees one ♻️ count for jobs that
    did not complete their analysis. ``walled`` is a backend offer dropped by
    the non-Python stack wall or a gate FAIL: no card, counted apart from
    ``below`` and listed with its ``reason`` in the round summary. ``closed``
    is an offer whose text says it is closed: discarded without any LLM spend.
    """

    zone: BridgeZone
    score: int | None
    # Short Spanish wall reason, set only for ``walled`` outcomes.
    reason: str | None = None


DEFAULT_PROFILE: Final[str] = "default"
NEUTRAL_CAMBIOS: Final[str] = "plan neutro (sin selección LLM)"
# Prefix of the cambios text; the full text (variant + manifest sha) comes
# from base_cambios() so a later base-CV sync never mislabels new rows.
BASE_CAMBIOS: Final[str] = "CV base fijo"
# Flagged posting-guard verdicts kept in memory for the cards (oldest evicted).
_GUARD_REPORTS_MAX: Final[int] = 500
_OUTCOME_NOTE_MAX: Final[int] = 2000
_HTML_COMMENT_RE: Final[re.Pattern[str]] = re.compile(r"<!--.*?-->", re.DOTALL)


def _load_voice_sample(path: Path) -> str:
    """Owner's cover-letter style sample (comments stripped); ``""`` when absent."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        _logger.warning("cover letter voice sample not loaded: %s", exc)
        return ""
    return _HTML_COMMENT_RE.sub("", raw).strip()


def _parse_stored_research(raw: str | None) -> dict[str, Any] | None:
    """The stored research JSON, or None when absent/corrupt."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if isinstance(data, dict) and "checked_at" in data and "status" in data:
        return data
    return None


def _load_salary_table(path: Path) -> list[ReferenceRange]:
    """Reference table, or empty (published-salary only) when absent/invalid."""
    try:
        return load_reference_table(path)
    except (OSError, ValueError) as exc:  # TOMLDecodeError and pydantic are ValueErrors
        _logger.warning("salary reference table not loaded: %s", exc)
        return []


DEFAULT_DB_PATH: Final[Path] = Path("data/app.db")
_DEFAULT_EXPORT_PATH: Final[Path] = Path("data/export.xlsx")
# Default rejudge report directory; resolved at call time (tests redirect it).
DEFAULT_REPORT_DIR: Final[Path] = Path("data")
_ROLE_LABEL: Final[dict[str, str]] = {
    "AI": "AI Engineer",
    "BACKEND": "Backend Developer",
}

# rejudge-backlog (odd/tasks/rejudge-backlog.md): re-run the LLM judge over
# offers already stored from before the judge existed, without touching
# estado or re-scraping. "Real text" excludes both a too-short scrape AND
# the bridge's no-inline-text placeholder note (cva/bridge.py's
# f"bridge: {beacon}\n{canonical}", always well under the length floor in
# practice, but excluded explicitly rather than relying on that coincidence).
_REJUDGE_MIN_TEXT_LEN: Final[int] = 500
# Total write attempts of one heartbeat catch-up of the tracker xlsx
# (0.5s + 1s between them, run in a worker thread).
TRACKER_RETRY_ATTEMPTS: Final[int] = 3
_REJUDGE_BRIDGE_PLACEHOLDER_PREFIX: Final[str] = "bridge: "
# States the owner already decided on: never re-judged by default.
# "descartada" is the one exception the owner can opt back into
# (--include-discarded) — every post-application state stays excluded
# regardless, since re-judging an offer already in a hiring pipeline is
# pointless and touching judge_results for it could confuse later reads.
_REJUDGE_DEFAULT_SKIP_STATES: Final[frozenset[str]] = frozenset(
    {
        "descartada",
        "enviada",
        "screening",
        "entrevista_tecnica",
        "entrevista_final",
        "oferta",
        "aceptada",
        "rechazada",
        "retirada",
        "sin_respuesta",
    }
)
_REJUDGE_AGE_FLAG_DAYS: Final[int] = 21
# Rough estimate only (dry-run budget preview) — the judge's system prompt
# (rubric + calibration + claims digest) plus the offer text and JSON
# response; never metered against a real call in this codebase.
_REJUDGE_ESTIMATED_TOKENS_PER_OFFER: Final[int] = 10_000
# Hardening (fix/rejudge-hardening, odd/tasks/rejudge-backlog.md): stop a
# real run cleanly after this many CONSECUTIVE judge failures (e.g. quota
# exhausted mid-run would otherwise burn through the whole paced backlog
# failing every single call). A success resets the counter.
_REJUDGE_DEFAULT_MAX_CONSECUTIVE_FAILURES: Final[int] = 3


class RejudgeRow(BaseModel):
    """One re-judged offer, ranked-report line (feature rejudge-backlog).

    ``judged_this_run`` (hardening) distinguishes a judgment produced by THIS
    call from one read back from an earlier run's stored ``judge_results``
    row — the report always covers the WHOLE matching backlog, not only
    what this particular run judged, so a resumed run's report never loses
    earlier results.
    """

    offer_id: int
    score: int
    verdict: str
    title: str | None
    company: str | None
    url: str | None
    age_days: int | None
    age_flag: bool
    walls: list[str]
    judged_this_run: bool = True


class RejudgeFailure(BaseModel):
    """One offer whose judge call raised instead of returning a judgment
    (continue-on-failure, mirrors :func:`cva.match.judge_eval.run_judge_eval`)."""

    offer_id: int
    error: str


class RejudgeResult(BaseModel):
    """Structured outcome of :meth:`JobService.rejudge_backlog`.

    Hardening additions over the original shape: ``skipped_since`` and
    ``skipped_title_filter`` complete the exclusion-reason breakdown (every
    selection reason is now counted, not just state/short-text/already-
    judged); ``planned_candidates`` is ``total_candidates`` AFTER ``limit``
    is applied, so a caller can render "would judge N of M" instead of just
    the unlimited total; ``report_only`` marks a zero-LLM-call report-from-
    stored-results run; ``stopped_reason`` is set when the run ended early
    (circuit breaker or a caught ``KeyboardInterrupt``) instead of running
    every candidate.
    """

    dry_run: bool
    report_only: bool = False
    total_candidates: int
    planned_candidates: int = 0
    judged: int
    skipped_state: int
    skipped_already_judged: int
    skipped_short_text: int
    skipped_since: int = 0
    skipped_title_filter: int = 0
    estimated_tokens: int
    stopped_reason: str | None = None
    rows: list[RejudgeRow] = Field(default_factory=list)
    failures: list[RejudgeFailure] = Field(default_factory=list)
    report_text: str = ""
    report_path: Path | None = None


def _is_real_offer_text(text: str) -> bool:
    """Excludes both a too-short scrape and the bridge's placeholder note."""
    return len(text) > _REJUDGE_MIN_TEXT_LEN and not text.startswith(
        _REJUDGE_BRIDGE_PLACEHOLDER_PREFIX
    )


def _offer_age_days(
    fecha_update: str | None, today: date, fecha_alta: str | None = None
) -> int | None:
    """Age in days: ``fecha_alta`` (first seen) when stored and valid, else the
    best-effort ``offers.fecha_update`` approximation below.

    CAVEAT (owner decision pending, see the rejudge-backlog feature doc):
    the ``offers`` table has NO dedicated "first seen"/"created at" column
    — ``fecha_update`` is touched on every ingest/analyze/estado-change, so
    for a backlog row that was analyzed once and never revisited (the
    common case for the offers this feature targets) it approximates the
    ingest date; for a row touched again later (re-analyzed, discarded,
    etc.) it would UNDERSTATE the true age. Using it is a deliberate
    trade-off over inventing a column with no data behind it.
    """
    for candidate in (fecha_alta, fecha_update):
        if not candidate:
            continue
        try:
            return (today - date.fromisoformat(candidate[:10])).days
        except ValueError:
            continue
    return None


def _offer_date(fecha_update: str | None) -> date | None:
    """The ``date`` part of ``offers.fecha_update``, or ``None`` when absent
    or unparseable (same best-effort caveat as :func:`_offer_age_days`;
    kept separate since ``--since`` needs the ``date`` itself, not an age)."""
    if not fecha_update:
        return None
    try:
        return date.fromisoformat(fecha_update[:10])
    except ValueError:
        return None


def _format_rejudge_report(
    rows: list[RejudgeRow],
    failures: list[RejudgeFailure],
    *,
    min_score: int,
    total_judged: int,
    stopped_reason: str | None = None,
) -> str:
    """Ranked Markdown report (score desc, filtered to ``score >= min_score``).

    ``rows`` covers the WHOLE matching backlog (hardening: this run's fresh
    judgments plus any earlier run's stored ``judge_results``), so
    ``total_judged`` is the backlog-wide count, not just this run's. A row
    judged in an EARLIER run (``judged_this_run=False``) is annotated so the
    reader knows it was not re-scored just now. ``stopped_reason``
    (hardening: circuit breaker or a caught ``KeyboardInterrupt``) is
    surfaced as a header warning when the run ended early.
    """
    shown = [row for row in rows if row.score >= min_score]
    summary = (
        f"Juzgadas: {total_judged} · Mostradas (score ≥ {min_score}): {len(shown)} "
        f"· Fallos: {len(failures)}"
    )
    lines = ["# Rejudge del backlog", ""]
    if stopped_reason:
        lines.append(
            f"⚠️ Ejecución detenida antes de completar el backlog: {stopped_reason}"
        )
        lines.append("")
    lines.extend([summary, ""])
    for row in shown:
        age_note = ""
        if row.age_days is not None:
            age_note = f" · visto hace {row.age_days} días"
            if row.age_flag:
                age_note += " (probablemente cerrada/avanzada)"
        walls = "; ".join(row.walls) if row.walls else "-"
        run_note = (
            "" if row.judged_this_run else " · (juzgada en una ejecución anterior)"
        )
        lines.append(
            f"- **{row.score}** [{row.verdict}] {row.title or '(sin título)'} — "
            f"{row.company or '(sin empresa)'}{age_note} · walls: {walls} · "
            f"{row.url or '(sin url)'}{run_note}"
        )
    if failures:
        lines.append("")
        lines.append(f"## Fallos ({len(failures)})")
        for failure in failures:
            lines.append(f"- oferta #{failure.offer_id}: {failure.error}")
    return "\n".join(lines)


def _unique_report_path(report_dir: Path, stamp: str) -> Path:
    """``rejudge_<stamp>.md`` under ``report_dir``, or ``_2``/``_3``/... appended
    when that name is already taken (hardening: two runs whose ``stamp``
    collides — same second, or a resumed run right after an aborted one —
    must never silently overwrite an earlier report)."""
    path = report_dir / f"rejudge_{stamp}.md"
    suffix = 2
    while path.exists():
        path = report_dir / f"rejudge_{stamp}_{suffix}.md"
        suffix += 1
    return path


# offer_events note marking an offer rescued with /rescatar (leaves the panel).
RESCUED_NOTE: Final[str] = "rescatada con /rescatar"

_DUPLICATE_OF: Final = re.compile(re.escape(DUPLICATE_NOTE_PREFIX) + r"(\d+)")


def score_zone(
    score: int, gate: int, gray_zone: int
) -> Literal["above", "gray", "below"]:
    """The notification zone of a score: the ONE definition the bridge and the
    panel's "discarded" audit share (above: full card, gray: borderline card
    when ``gray_zone > 0``, below: nothing is sent)."""
    if score >= gate:
        return "above"
    if gray_zone > 0 and score >= gate - gray_zone:
        return "gray"
    return "below"


def _parse_stamp(stamp: str | None) -> datetime | None:
    """A stored ISO date/datetime as an aware UTC datetime (None if unusable)."""
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _first_sentence(text: str) -> str:
    """First sentence of ``text`` (split after ``.``/``!``/``?``), stripped."""
    return re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0].strip()


def _judgment_walls(judgment_json: str) -> list[str]:
    """Hard-wall texts from a stored ``judge_results.judgment_json`` row.

    Best-effort: a row that fails to decode (corrupted/older schema) yields
    no walls rather than blowing up the whole report.
    """
    try:
        judgment = OfferJudgment.model_validate_json(judgment_json)
    except ValidationError:
        return []
    return [wall.text for wall in judgment.walls if wall.hard]


def _rejudge_row(
    offer: OfferRow,
    *,
    score: int,
    verdict: str,
    walls: list[str],
    today: date,
    judged_this_run: bool,
) -> RejudgeRow:
    age_days = _offer_age_days(offer.fecha_update, today, offer.fecha_alta)
    return RejudgeRow(
        offer_id=offer.id,
        score=score,
        verdict=verdict,
        title=offer.posicion,
        company=offer.empresa,
        url=offer.url_oferta,
        age_days=age_days,
        age_flag=age_days is not None and age_days > _REJUDGE_AGE_FLAG_DAYS,
        walls=walls,
        judged_this_run=judged_this_run,
    )


_DEADLINE_ALERT_DAYS: Final[int] = 3
# app_state key: JSON list of offer ids already alerted (no migration needed).
_DEADLINE_ALERTED_KEY: Final[str] = "deadline_alerted_ids"
# Failed-send backoff for the alert (state: {"failures", "next"}); success resets it.
_DEADLINE_BACKOFF_KEY: Final[str] = "deadline_alert_backoff"
_DEADLINE_BACKOFF: Final[tuple[timedelta, ...]] = (
    timedelta(minutes=10),
    timedelta(hours=1),
    timedelta(hours=6),
)
# Follow-up reminders (owner-sent, never automatic): thresholds and state.
_FOLLOWUP_AFTER_DAYS: Final[int] = 10
_FOLLOWUP_MAX: Final[int] = 2
_FOLLOWUP_GIVE_UP_DAYS: Final[int] = 60
_FOLLOWUP_SNOOZE_DAYS: Final[int] = 7
_FOLLOWUP_PASS_CAP: Final[int] = 3  # reminders per daily pass; the rest next day
_FOLLOWUP_MAX_ATTEMPTS: Final[int] = 3  # failed sends per item before giving up
_FOLLOWUPS_SINCE_KEY: Final[str] = "followups_since"
# app_state keys: {"reminded": {id: followups}, "snooze": {id: date},
# "proposed": [id], "last_run": date} and the failed-send backoff.
_FOLLOWUP_STATE_KEY: Final[str] = "followup_state"
# Researched company facts for cover letters: one app_state entry per company.
_LETTER_FACTS_KEY: Final[str] = "letter_company_facts:"
_LETTER_FACTS_TTL: Final[timedelta] = timedelta(days=30)
_FOLLOWUP_BACKOFF_KEY: Final[str] = "followup_backoff"
_UNSET: Final = object()


@dataclass(frozen=True)
class FollowupItem:
    """One offer the follow-up job should message the owner about."""

    offer: OfferRow
    days_silent: int
    followups: int
    kind: Literal["reminder", "proposal"]


class OfferClosedError(Exception):
    """The offer text says it is closed; it was discarded without analysis."""

    def __init__(self, offer_id: int, marker: str) -> None:
        super().__init__(f"offer #{offer_id} is closed ({marker})")
        self.offer_id = offer_id
        self.marker = marker


@dataclass(frozen=True)
class PrepDelivery:
    """A stored interview prep pack, ready to send."""

    filename: str
    html: str
    summary: str
    pack_id: int
    result: PrepResult


class ServiceError(ValueError):
    """Guard failure in a use-case (missing analysis/CV, invalid request)."""


def _offer_input(fetched: FetchedOffer, *, today: date | None = None) -> OfferInput:
    """Adapt the ingest result to the tracker's input (slice-4 seam note).

    Posting date and deadline come from the JSON-LD fields when present, else
    from explicit text patterns (``cva.ingest.dates``); ``today`` is the
    receipt date used for relative posting ages.
    """
    dates = extract_dates(
        fetched.text,
        today or datetime.now(UTC).date(),
        posted=fetched.date_posted,
        valid_through=fetched.valid_through,
    )
    return OfferInput(
        job_id=fetched.job_id,
        title=fetched.title,
        company=fetched.company,
        url=fetched.url,
        text=fetched.text,
        source=fetched.source,
        fecha_publicacion=dates.publicacion,
        fecha_limite=dates.limite,
    )


def _guard_offer(fetched: FetchedOffer) -> tuple[FetchedOffer, GuardReport]:
    """Posting guard over every ingested offer (text, title, company).

    Returns the cleaned offer and one merged report; hidden HTML blocks the
    fetch already dropped are folded in. The text can never end up empty.
    """
    body = clean_text(fetched.text)
    title = clean_text(fetched.title) if fetched.title else None
    company = clean_text(fetched.company) if fetched.company else None
    if not body.text.strip():
        raise ServiceError("offer text is empty after removing hidden characters")
    parts = [r for r in (body, title, company) if r is not None]
    report = GuardReport(
        text=body.text,
        removed=sum(r.removed for r in parts),
        hidden_blocks=len(fetched.hidden_html),
        hidden_text=(
            *(t for r in parts for t in r.hidden_text),
            *fetched.hidden_html,
        ),
        tampering=any(r.tampering for r in parts),
    )
    cleaned = fetched.model_copy(
        update={
            "text": body.text,
            "title": title.text if title else fetched.title,
            "company": company.text if company else fetched.company,
        }
    )
    return cleaned, report


def _variant(role: str, lang: str) -> CvVariant:
    try:
        return CvVariant[f"{role}_{lang.upper()}"]
    except KeyError as exc:
        raise ServiceError(f"invalid role/language combination: {role}_{lang}") from exc


def _due(beacon: BeaconRow, now: datetime) -> bool:
    """Due rule (design D11) over the STORED due time (interval + jitter already
    baked into ``next_check_after`` at each completion; ``None`` = never checked
    → immediately due, which is also the startup catch-up). Paused/disabled
    beacons are never due — the pause alert path is the only way out (D3)."""
    if not beacon.enabled or beacon.status == "paused":
        return False
    return beacon.next_check_after is None or now >= datetime.fromisoformat(
        beacon.next_check_after
    )


class JobService:
    """All use-cases (design Interfaces); bot.py and cli.py call only these."""

    def __init__(
        self,
        *,
        repo: OfferRepository,
        llm: LlmClient,
        fetcher: Fetcher,
        db: ClaimsDB,
        syn: SynonymMap,
        cache: ExtractionCache,
        threshold: int = 65,
        masters_dir: Path | None = None,
        base_pdfs_dir: Path | None = None,
        profile_paths: ProfilePaths | None = None,
        output_dir: Path | None = None,
        interval_minutes: int = 480,
        checks_per_day: int = 3,
        global_daily_cap: int = 30,
        now_fn: Callable[[], datetime] | None = None,
        jitter_fn: Callable[[], timedelta] | None = None,
        mirror_dir: Path | str | None = None,
        tracker_xlsx: Path | str | None = None,
        analyze_retry_max: int = 5,
        gray_zone: int = 15,
        match_mode: MatchMode = "keyword",
        extraction_model: str | None = None,
        judge_model: str | None = None,
        backend_threshold: int = DEFAULT_BACKEND_THRESHOLD,
        backend_gray_zone: int | None = None,
        salary_table: list[ReferenceRange] | None = None,
        cover_letter_review: bool = False,
        paused_default: bool = False,
    ) -> None:
        self._repo = repo
        # Kill-switch default (Settings.paused); the persisted toggle wins.
        self._paused_default = paused_default
        # One extra LLM pass that reviews the generated cover letters.
        self._cover_letter_review = cover_letter_review
        # Salary reference table (salary-estimate T2); empty = published-only.
        self._salary_table: list[ReferenceRange] = salary_table or []
        self._llm = llm
        self._fetcher = fetcher
        self._db = db
        self._syn = syn
        self._cache = cache
        self._threshold = threshold
        # Owner files resolve through the profile (unset -> today's paths);
        # explicit directories (tests, callers) still win.
        self._paths = (
            profile_paths if profile_paths is not None else resolve_profile_paths()
        )
        # Name/file slug/identity claim of the profile (defaults = old constants).
        self._identity = load_identity(self._paths.profile_toml)
        if self._paths.profile_toml is not None:
            # A configured profile must name a real identity claim (a typo
            # would sign every letter with an empty name). The legacy layout
            # keeps its old behaviour (default id, no check).
            require_identity_claim(self._identity, self._db)
        # Stack wall, gates, search targets, title filters (defaults = old constants).
        self._prefs = load_preferences(self._paths.preferences)
        # Judge facts, named anchors, rubric persona (defaults = the owner's text).
        self._judge_persona = load_judge_persona(self._paths.judge_dir)
        # legacy: masters no longer drive delivery
        self._masters_dir = (
            masters_dir if masters_dir is not None else self._paths.masters_dir
        )
        self._base_pdfs_dir = (
            base_pdfs_dir if base_pdfs_dir is not None else self._paths.base_pdfs_dir
        )
        # Resolved at call time through the module so tests can redirect it.
        self._output_dir = (
            output_dir if output_dir is not None else _cvgen_engine.OUTPUT_DIR
        )
        self._interval_minutes = interval_minutes
        self._checks_per_day = checks_per_day
        self._global_daily_cap = global_daily_cap
        self._now = now_fn if now_fn is not None else (lambda: datetime.now(UTC))
        self._jitter_fn = jitter_fn
        self._analyze_retry_max = analyze_retry_max
        self._gray_zone = gray_zone
        # Backend profile (backend-profile): own gate + gray zone; the gray
        # zone follows the AI one unless configured.
        self._backend_threshold = backend_threshold
        self._backend_gray_zone = (
            backend_gray_zone if backend_gray_zone is not None else gray_zone
        )
        # CV mirror projection (auto-triage D5/D7): None/blank → disabled.
        self._mirror = mirror_root(mirror_dir)
        self._deadline_unmarked: set[int] = set()
        self._deadline_backoff_mem: object = _UNSET
        self._followup_mem: dict[str, Any] | None = None
        self._followups_since = self._init_followups_since()
        # Synced full-tracker xlsx (best-effort projection): None → disabled.
        self._tracker_xlsx = mirror_root(tracker_xlsx)
        # Set when a projection refresh ultimately failed (file locked by a
        # sync tool); the heartbeat catches up via retry_tracker_refresh().
        # ``_tracker_gen`` counts inline refreshes so a catch-up that raced a
        # mutation can tell its snapshot may be stale.
        self._tracker_dirty = False
        self._salary_locks: dict[int, asyncio.Lock] = {}
        self._letter_gen: dict[int, int] = {}
        # Cover letters: one generation per offer at a time, and the last
        # generated pair kept in memory only for the choose step.
        self._letter_locks: dict[int, asyncio.Lock] = {}
        self._facts_locks: dict[str, asyncio.Lock] = {}
        self._prep_claims: set[tuple[int, str]] = set()
        self._last_letters: dict[int, CoverLetterResult] = {}
        self._tracker_gen = 0
        # Posting guard verdicts per offer id (flagged ones only, in memory):
        # the cards read them through card_extras().
        self._guard_reports: OrderedDict[int, GuardReport] = OrderedDict()
        # LLM offer judge (llm-offer-judge design): "keyword" (default) is
        # today's UNCHANGED behavior — analyze() never touches the judge.
        self._match_mode = match_mode
        self._extraction_model = extraction_model
        self._judge_model = judge_model

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        db_path: Path = DEFAULT_DB_PATH,
        masters_dir: Path | None = None,
    ) -> "JobService":
        """Wire the real collaborators (SQLite, provider-selected LLM, httpx)."""
        paths = resolve_profile_paths(settings.profile_dir)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        db = load_claims(paths.claims)
        return cls(
            repo=OfferRepository(conn),
            llm=build_llm_client(settings),
            fetcher=HttpxFetcher(),
            db=db,
            syn=load_synonyms(paths.synonyms, db),
            cache=ExtractionCache(conn),
            threshold=settings.threshold,
            masters_dir=masters_dir,
            profile_paths=paths,
            interval_minutes=settings.beacon_interval_minutes,
            checks_per_day=settings.checks_per_day,
            global_daily_cap=settings.beacon_global_daily_cap,
            jitter_fn=default_jitter,
            mirror_dir=settings.cv_mirror_dir,
            tracker_xlsx=settings.tracker_xlsx_path,
            gray_zone=settings.notify_gray_zone,
            analyze_retry_max=settings.analyze_retry_max,
            match_mode=settings.match_mode,
            extraction_model=settings.extraction_model,
            judge_model=settings.judge_model,
            backend_threshold=settings.backend_threshold,
            backend_gray_zone=settings.backend_gray_zone,
            salary_table=_load_salary_table(paths.salary_table),
            cover_letter_review=settings.cover_letter_review,
            paused_default=settings.paused,
        )

    def is_paused(self) -> bool:
        """Global kill switch: the persisted toggle, else ``Settings.paused``."""
        return self._repo.is_paused(self._paused_default)

    def set_paused(self, paused: bool) -> None:
        """Persist the global pause (/pausa, /reanudar); survives restarts."""
        self._repo.set_paused(paused)

    def _voice_sample(self) -> str:
        """The profile's cover-letter voice sample (``""`` when absent)."""
        return _load_voice_sample(self._paths.voice)

    def salary_line(self, offer_id: int) -> str:
        """Card salary line (published salary, else reference); ``""`` if none."""
        offer = self._repo.get_offer(offer_id)
        return salary_line(
            title=offer.posicion,
            text=offer.source_data,
            perfil=offer.perfil,
            table=self._salary_table,
        )

    def _note_guard(self, offer_id: int, report: GuardReport) -> None:
        """Remember the guard verdict for the cards (in memory only)."""
        if report.flagged:
            self._guard_reports[offer_id] = report
            self._guard_reports.move_to_end(offer_id)
            while len(self._guard_reports) > _GUARD_REPORTS_MAX:
                self._guard_reports.popitem(last=False)
        else:
            self._guard_reports.pop(offer_id, None)

    def card_extras(self, offer_id: int) -> str:
        """Extra card lines: salary line plus deadline/age lines (blank-free)."""
        offer = self._repo.get_offer(offer_id)
        lines = [
            warning_line(self._guard_reports.get(offer_id)),
            self.salary_line(offer_id),
            date_card_lines(
                offer.fecha_publicacion, offer.fecha_limite, self._now().date()
            ),
        ]
        return "\n".join(line for line in lines if line)

    def deadline_alert(self) -> tuple[str, list[int]] | None:
        """Owner alert for un-alerted pre-send offers closing within 0-3 days.

        Returns ``(text, offer_ids)`` or ``None``. ``offer_ids`` are only the
        offers the (length-capped) message actually lists; the rest follow in
        the next message. Returns ``None`` while a previous failed send is
        backing off. Nothing is recorded here: the caller reports the outcome
        through :meth:`mark_deadline_alerted` / :meth:`record_deadline_alert_failure`.
        No network.
        """
        self._flush_deadline_marks()
        now = self._now()
        backoff = self._deadline_backoff()
        if backoff is not None and now < backoff[1]:
            return None
        today = now.date()
        rows = self._repo.list_unsent_with_deadline(
            today.isoformat(),
            (today + timedelta(days=_DEADLINE_ALERT_DAYS)).isoformat(),
        )
        done = self._deadline_alerted()
        fresh = [r for r in rows if r.id not in done]
        if not fresh:
            return None
        text, listed = deadline_alert_batch(fresh, today)
        return text, [r.id for r in fresh[:listed]]

    def mark_deadline_alerted(self, offer_ids: list[int]) -> None:
        """Record offers whose deadline alert was delivered (once-only).

        The ids are remembered in memory FIRST, so a failing store write after a
        successful send never causes a resend; the write is retried on later
        ticks (:meth:`_flush_deadline_marks`) and re-raised to the caller once.
        A restart before the write lands loses the memory (one possible resend).
        """
        self._deadline_unmarked |= set(offer_ids)
        self._set_deadline_backoff(0, None)
        self._persist_deadline_marks()

    def record_deadline_alert_failure(self) -> None:
        """A send failed: back off 10 min, then 1 h, then 6 h (cap)."""
        failures = (self._deadline_backoff() or (0, self._now()))[0] + 1
        delay = _DEADLINE_BACKOFF[min(failures, len(_DEADLINE_BACKOFF)) - 1]
        self._set_deadline_backoff(failures, self._now() + delay)

    def _persist_deadline_marks(self) -> None:
        merged = sorted(self._deadline_alerted())
        self._repo.set_state(_DEADLINE_ALERTED_KEY, json.dumps(merged))
        self._deadline_unmarked.clear()

    def _flush_deadline_marks(self) -> None:
        if self._deadline_unmarked:
            try:
                self._persist_deadline_marks()
            except Exception:
                _logger.warning("deadline alert mark still failing", exc_info=True)

    def _deadline_backoff(self) -> tuple[int, datetime] | None:
        if self._deadline_backoff_mem is not _UNSET:
            return self._deadline_backoff_mem  # type: ignore[return-value]
        raw = self._repo.get_state(_DEADLINE_BACKOFF_KEY)
        if raw:
            with suppress(ValueError, TypeError, KeyError):
                data = json.loads(raw)
                return int(data["failures"]), datetime.fromisoformat(data["next"])
        return None

    def _set_deadline_backoff(self, failures: int, next_at: datetime | None) -> None:
        value = (failures, next_at) if next_at is not None else None
        self._deadline_backoff_mem = value
        payload = (
            json.dumps({"failures": failures, "next": next_at.isoformat()})
            if next_at is not None
            else ""
        )
        with suppress(Exception):  # best effort; memory is authoritative
            self._repo.set_state(_DEADLINE_BACKOFF_KEY, payload)

    def _deadline_alerted(self) -> set[int]:
        raw = self._repo.get_state(_DEADLINE_ALERTED_KEY)
        stored: set[int] = set()
        if raw:
            with suppress(ValueError, TypeError):
                stored = {int(i) for i in json.loads(raw)}
        return stored | self._deadline_unmarked

    # --- Follow-up reminders -------------------------------------------------

    def followup_due(self) -> list[FollowupItem]:
        """Sent offers that need a follow-up reminder or a give-up proposal.

        Once per day (:meth:`complete_followup_run` closes the day) and never
        while a failed send is backing off. A reminder needs >= 10 days
        without activity (latest event / update / send), fewer than 2
        follow-ups, no active snooze and no reminder already sent for the
        current follow-up count. After 2 follow-ups and >= 60 days of silence
        a one-time proposal to mark ``sin_respuesta`` is returned instead.
        Nothing is recorded here and nothing is sent anywhere.
        """
        now = self._now()
        today = now.date()
        state = self._followup_state()
        if state.get("last_run") == today.isoformat():
            return []
        remaining = _FOLLOWUP_PASS_CAP - self._delivered_today(state)
        items: list[FollowupItem] = []
        for offer in self._repo.sent_offers():
            if offer.estado != "enviada" or not self._after_cutover(offer):
                continue
            events = self._repo.list_events(offer.id)
            followups = sum(1 for e in events if e.kind == "followup")
            silence = (today - self._last_activity(offer, events)).days
            key = str(offer.id)
            until = state["snooze"].get(key)
            if until is not None and today.isoformat() < until:
                continue
            if followups >= _FOLLOWUP_MAX:
                if (
                    silence >= _FOLLOWUP_GIVE_UP_DAYS
                    and offer.id not in state["proposed"]
                ):
                    items.append(FollowupItem(offer, silence, followups, "proposal"))
            elif (
                silence >= _FOLLOWUP_AFTER_DAYS
                and state["reminded"].get(key) != followups
            ):
                items.append(FollowupItem(offer, silence, followups, "reminder"))
        self._prune_failures(state, {self._followup_key(i) for i in items})
        items = [i for i in items if not self._item_backing_off(i, now)]
        return items[: max(remaining, 0)]

    def _delivered_today(self, state: dict[str, Any]) -> int:
        """Reminders delivered on the current calendar day (cap bookkeeping)."""
        entry = state.get("delivered") or {}
        if entry.get("day") != self._now().date().isoformat():
            return 0
        return int(entry.get("count") or 0)

    def _prune_failures(self, state: dict[str, Any], live: set[str]) -> None:
        """Drop failure entries of items that are no longer candidates.

        An offer that left ``enviada``, got snoozed or changed kind would
        otherwise keep the daily gate open forever.
        """
        stale = [k for k in state["failures"] if k not in live]
        if not stale:
            return
        for key in stale:
            del state["failures"][key]
        self._save_followup_state(state)

    def _init_followups_since(self) -> date:
        """First-run date of the reminders: earlier sends are never auto-reminded."""
        today = self._now().date()
        with suppress(Exception):
            raw = self._repo.get_state(_FOLLOWUPS_SINCE_KEY)
            if raw:
                return date.fromisoformat(raw)
            self._repo.set_state(_FOLLOWUPS_SINCE_KEY, today.isoformat())
        return today

    def _after_cutover(self, offer: OfferRow) -> bool:
        """Legacy backlog guard: only offers sent since the first run are eligible."""
        if not offer.fecha_envio:
            return False
        try:
            return date.fromisoformat(offer.fecha_envio[:10]) >= self._followups_since
        except ValueError:
            return False

    @staticmethod
    def _followup_key(item: FollowupItem) -> str:
        return f"{item.kind}:{item.offer.id}"

    def _item_backing_off(self, item: FollowupItem, now: datetime) -> bool:
        entry = self._followup_state()["failures"].get(self._followup_key(item))
        if not entry:
            return False
        with suppress(ValueError, KeyError, TypeError):
            return now < datetime.fromisoformat(entry["next"])
        return False

    def followup_resume(self, item: FollowupItem) -> tuple[str, int] | None:
        """(text, chunks already delivered) of a half-sent message, if any."""
        entry = self._followup_state()["failures"].get(self._followup_key(item))
        if entry and entry.get("text") and int(entry.get("sent") or 0) > 0:
            return str(entry["text"]), int(entry["sent"])
        return None

    def followup_chunks(self, item: FollowupItem) -> list[str] | None:
        """The exact chunks of a half-sent message, if they were stored."""
        entry = self._followup_state()["failures"].get(self._followup_key(item))
        chunks = entry.get("chunks") if entry else None
        if isinstance(chunks, list) and all(isinstance(c, str) for c in chunks):
            return list(chunks) or None
        return None

    def _last_activity(self, offer: OfferRow, events: list[OfferEvent]) -> date:
        """Latest of the send, the last update and the latest timeline event."""
        stamps = [offer.fecha_envio, offer.fecha_update, *(e.at for e in events)]
        snapshot = self._repo.get_sent_snapshot(offer.id)
        if snapshot is not None:
            stamps.append(snapshot.sent_at)
        days: list[date] = []
        for stamp in stamps:
            if stamp:
                with suppress(ValueError):
                    days.append(date.fromisoformat(stamp[:10]))
        return max(days) if days else self._now().date()

    async def followup_draft(
        self, offer_id: int, *, days_silent: int, number: int
    ) -> FollowupDraft:
        """Draft the status-check message from the SENT snapshot (never invents).

        Returns ``FollowupDraft(None)`` when the provider cannot write or
        fails: the reminder is still sent, without a draft.
        """
        if not callable(getattr(self._llm, "json_completion", None)):
            return FollowupDraft(None)
        offer = self._repo.get_offer(offer_id)
        snapshot = self._repo.get_sent_snapshot(offer_id)
        posting = snapshot.posting_text if snapshot else (offer.source_data or "")
        letter = snapshot.cover_letter if snapshot else offer.carta_presentacion
        try:
            return await generate_followup(
                self._llm,
                posting_text=posting,
                sent_letter=letter,
                title=offer.posicion or "(unknown position)",
                company=offer.empresa or "(unknown company)",
                language=infer_language(posting),
                claims_digest=build_claims_digest(self._db),
                voice_sample=self._voice_sample(),
                owner_name=owner_name(self._db, self._identity),
                days_silent=days_silent,
                number=number,
            )
        except Exception:  # a failing draft must not block the reminder
            _logger.warning("follow-up draft failed for #%s", offer_id, exc_info=True)
            return FollowupDraft(None)

    def mark_followup_notified(self, item: FollowupItem) -> None:
        """Record a DELIVERED reminder/proposal (call only after a successful send)."""
        state = self._followup_state()
        if item.kind == "proposal":
            state["proposed"].append(item.offer.id)
        else:
            state["reminded"][str(item.offer.id)] = item.followups
        state["failures"].pop(self._followup_key(item), None)
        today = self._now().date().isoformat()
        state["delivered"] = {
            "day": today,
            "count": self._delivered_today(state) + 1,
        }
        self._save_followup_state(state)

    def record_followup_failure(
        self,
        item: FollowupItem,
        *,
        text: str | None = None,
        delivered: int = 0,
        chunks: list[str] | None = None,
    ) -> None:
        """One item's send failed: its own backoff (10 min, 1 h, 6 h), then give up.

        ``text``/``delivered`` remember a partly delivered chunked message so a
        retry sends only the missing chunks. After the third failed attempt the
        item is skipped for this cycle (snoozed) with a single log line; the
        rest of the batch is unaffected.
        """
        state = self._followup_state()
        key = self._followup_key(item)
        previous = state["failures"].get(key) or {}
        attempts = int(previous.get("attempts") or 0) + 1
        if attempts >= _FOLLOWUP_MAX_ATTEMPTS:
            state["failures"].pop(key, None)
            until = self._now().date() + timedelta(days=_FOLLOWUP_SNOOZE_DAYS)
            state["snooze"][str(item.offer.id)] = until.isoformat()
            _logger.warning(
                "follow-up %s for #%s: giving up after %d failed sends",
                item.kind,
                item.offer.id,
                attempts,
            )
        else:
            delay = _DEADLINE_BACKOFF[min(attempts, len(_DEADLINE_BACKOFF)) - 1]
            state["failures"][key] = {
                "attempts": attempts,
                "next": (self._now() + delay).isoformat(),
                "sent": max(delivered, int(previous.get("sent") or 0)),
                "text": text if text is not None else previous.get("text"),
                "chunks": chunks if chunks is not None else previous.get("chunks"),
            }
        self._save_followup_state(state)

    def complete_followup_run(self) -> None:
        """Close today's pass: the job does not look again until tomorrow.

        A no-op while an item is still retrying (the pass is not finished).
        """
        state = self._followup_state()
        if state["failures"]:
            return
        # Memory only: the heartbeat runs on the event loop while bridge threads
        # share the connection, so an empty pass must not write (a restart just
        # repeats one idempotent pass; delivered marks are persisted).
        state["last_run"] = self._now().date().isoformat()

    def record_followup_sent(self, offer_id: int) -> int:
        """The owner sent follow-up #n by hand: log it and return n."""
        self._require_sent(offer_id)
        number = 1 + sum(
            1 for e in self._repo.list_events(offer_id) if e.kind == "followup"
        )
        self._repo.add_event(offer_id, "followup", note=f"#{number}")
        self._refresh_tracker()
        return number

    def snooze_followup(self, offer_id: int) -> None:
        """Skip: no reminder for 7 days; the cycle is re-armed after that."""
        self._require_sent(offer_id)
        state = self._followup_state()
        until = self._now().date() + timedelta(days=_FOLLOWUP_SNOOZE_DAYS)
        state["snooze"][str(offer_id)] = until.isoformat()
        state["reminded"].pop(str(offer_id), None)
        self._save_followup_state(state)

    def _followup_state(self) -> dict[str, Any]:
        if self._followup_mem is None:
            loaded: dict[str, Any] = {}
            raw = self._repo.get_state(_FOLLOWUP_STATE_KEY)
            if raw:
                with suppress(ValueError, TypeError):
                    data = json.loads(raw)
                    if isinstance(data, dict):
                        loaded = data
            self._followup_mem = {
                "reminded": dict(loaded.get("reminded") or {}),
                "snooze": dict(loaded.get("snooze") or {}),
                "proposed": [int(i) for i in loaded.get("proposed") or []],
                "failures": dict(loaded.get("failures") or {}),
                "delivered": dict(loaded.get("delivered") or {}),
                "last_run": loaded.get("last_run"),
            }
        return self._followup_mem

    def _save_followup_state(self, state: dict[str, Any]) -> None:
        # Memory is authoritative (a failing store write never causes a resend);
        # the write is retried on the next mutation.
        self._followup_mem = state
        try:
            self._repo.set_state(_FOLLOWUP_STATE_KEY, json.dumps(state))
        except Exception:
            _logger.warning("follow-up state not persisted", exc_info=True)

    async def research_salary(self, offer_id: int) -> SalaryOutcome:
        """On-demand web salary research for one offer (the "Investigar salario" button).

        One research per offer per day: a result (ok or no_data) stored today
        is returned as-is with ``cached=True`` and costs no LLM call. A
        provider without web search yields ``unavailable`` (nothing stored,
        nothing guessed); an LLM failure propagates to the caller.
        """
        offer = self._repo.get_offer(offer_id)
        city, _detected = infer_city(offer.posicion, offer.source_data)
        level = infer_level(offer.posicion, offer.source_data)
        base = SalaryOutcome(
            status="no_data",
            company=offer.empresa or "(unknown company)",
            position=offer.posicion or "(unknown position)",
            city=CITY_LABEL[city],
            level=level or "senior or above",
        )
        # One paid web research per offer at a time: a second press waits for
        # the first and then reuses its stored result (same-day cache).
        lock = self._salary_locks.setdefault(offer_id, asyncio.Lock())
        async with lock:
            return await self._research_salary_locked(offer_id, base)

    async def _research_salary_locked(
        self, offer_id: int, base: SalaryOutcome
    ) -> SalaryOutcome:
        offer = self._repo.get_offer(offer_id)
        today = self._now().date().isoformat()
        stored = _parse_stored_research(offer.salario_investigado)
        if stored is not None and stored["checked_at"] == today:
            raw = stored.get("research")
            return replace(
                base,
                status="ok" if stored["status"] == "ok" and raw else "no_data",
                research=SalaryResearch.model_validate(raw) if raw else None,
                cached=True,
                checked_at=today,
            )
        client = self._llm
        if not supports_web_search(client):
            return replace(base, status="unavailable")
        system, user = build_research_prompt(
            company=base.company,
            position=base.position,
            city=base.city,
            level=base.level,
        )
        parsed = await client.web_json_completion(  # type: ignore[attr-defined]
            system, user, SalaryResearch
        )
        outcome = to_outcome(parsed)
        payload = {
            "status": outcome.status,
            "checked_at": today,
            "research": outcome.research.model_dump() if outcome.research else None,
        }
        self._repo.set_salary_research(
            offer_id, json.dumps(payload, ensure_ascii=False)
        )
        self._refresh_tracker()
        return replace(
            base, status=outcome.status, research=outcome.research, checked_at=today
        )

    async def cover_letters(self, offer_id: int) -> CoverLetterResult:
        """Generate the cover letter (one version) for one offer.

        The profile's base letter is assembled around the generated parts; the
        company is researched once per 30 days (:meth:`_letter_facts`). Every
        press regenerates (nothing is stored until the owner picks it with
        :meth:`choose_cover_letter`); the last letter is kept in memory for
        that step. Raises :class:`ServiceError` when the provider cannot do
        structured completions; LLM failures propagate to the caller, research
        failures never do.
        """
        if not callable(getattr(self._llm, "json_completion", None)):
            raise ServiceError("este proveedor LLM no puede escribir cartas")
        lock = self._letter_locks.setdefault(offer_id, asyncio.Lock())
        async with lock:
            offer = self._repo.get_offer(offer_id)
            text = offer.source_data or ""
            language = infer_language(text)
            bases = load_letter_bases(self._paths.letter_base)
            facts = await self._letter_facts(
                offer.empresa or "", offer.posicion or "(unknown position)"
            )
            result = await generate_cover_letter(
                self._llm,
                offer_text=text,
                title=offer.posicion or "(unknown position)",
                company=offer.empresa or "(unknown company)",
                language=language,
                branch=role_for_profile(offer.perfil),
                claims_digest=build_claims_digest(self._db),
                apply_findings=detect_apply_instructions(text),
                voice_sample=self._voice_sample(),
                base=bases.get(language, bases["es"]),
                facts=facts,
                review=self._cover_letter_review,
                persona=self._prefs.cover_letter,
            )
            result = replace(result, generation=self._letter_gen.get(offer_id, 0) + 1)
            self._letter_gen[offer_id] = result.generation
            self._last_letters[offer_id] = result
            return result

    async def _letter_facts(self, company: str, position: str) -> list[CompanyFact]:
        """Verified (URL-backed) facts about ``company``, cached for 30 days.

        One web research per company per 30 days (key = normalized name). A
        successful search with nothing found is cached too; a failed or
        unsupported one is not, and an expired entry still serves when the
        refresh fails. Never raises.
        """
        if not known_company(company):
            return []
        key = _LETTER_FACTS_KEY + normalize_company(company)
        lock = self._facts_locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._read_letter_facts(key)
            now = self._now()
            if cached is not None and now - cached[0] < _LETTER_FACTS_TTL:
                return cached[1]
            searched = await self._web_company_facts(company, position)
            if searched is None:
                return cached[1] if cached is not None else []
            items = verified_facts(searched)
            payload = {
                "fetched_at": now.isoformat(),
                "items": [item.model_dump() for item in items],
            }
            try:
                self._repo.set_state(key, json.dumps(payload, ensure_ascii=False))
            except Exception:
                _logger.warning("company facts cache not persisted", exc_info=True)
            return items

    def _read_letter_facts(self, key: str) -> tuple[datetime, list[CompanyFact]] | None:
        raw = self._repo.get_state(key)
        if not raw:
            return None
        try:
            data = json.loads(raw)
            fetched = datetime.fromisoformat(data["fetched_at"])
            items = [CompanyFact.model_validate(item) for item in data["items"]]
        except (ValueError, TypeError, KeyError):
            return None
        return fetched, verified_facts(CompanyFacts(items=items))

    def choose_cover_letter(
        self, offer_id: int, version: str = "a", *, generation: int | None = None
    ) -> str:
        """Persist the generated letter (button version ``"a"``); return its text.

        ``generation`` (from the button) must match the latest letter, so a
        button on an older message never saves the newer letter's text.
        """
        letter = self._last_letters.get(offer_id)
        if (
            letter is not None
            and generation is not None
            and generation != letter.generation
        ):
            raise ServiceError(
                "esa carta es de una generación anterior; usa los botones de la última"
            )
        if letter is None:
            raise ServiceError(
                "no hay una carta generada para esta oferta; pulsa de nuevo «Carta de presentación»"
            )
        if version != "a":
            raise ServiceError(f"versión de carta desconocida: {version}")
        self._repo.set_cover_letter(offer_id, letter.text)
        self._refresh_tracker()
        return letter.text

    def _prep_salary_text(self, offer_id: int) -> str:
        """Existing salary knowledge only (research or reference); never new."""
        offer = self._repo.get_offer(offer_id)
        lines: list[str] = []
        stored = _parse_stored_research(offer.salario_investigado)
        raw = stored.get("research") if stored else None
        if stored and stored["status"] == "ok" and raw:
            research = SalaryResearch.model_validate(raw)
            line = (
                f"Rango investigado (web): {research.min}-{research.max} EUR brutos/año"
            )
            if research.median is not None:
                line += f", mediana {research.median}"
            lines.append(line)
        reference = self.salary_line(offer_id)
        if reference:
            lines.append(reference)
        return "\n".join(lines)

    def _prep_inputs(self, offer_id: int, stage: str) -> PrepInputs:
        offer = self._repo.get_offer(offer_id)
        snapshot = self._repo.get_sent_snapshot(offer_id)
        row = self._repo.latest_judge_result(offer_id)
        judge: OfferJudgment | None = None
        if row is not None:
            with suppress(ValidationError):
                judge = OfferJudgment.model_validate_json(row.judgment_json)
        notes: list[str] = []
        for event in self._repo.list_events(offer_id):
            if event.kind == "estado":
                notes.append(f"{event.at} · estado → {event.estado}")
            elif event.kind == "followup":
                notes.append(f"{event.at} · seguimiento enviado")
            elif event.note and not event.note.startswith("preparación "):
                notes.append(f"{event.at} · nota: {event.note}")
        return PrepInputs(
            stage=stage,
            title=offer.posicion or "(unknown position)",
            company=offer.empresa or "(unknown company)",
            posting_text=snapshot.posting_text
            if snapshot
            else (offer.source_data or ""),
            cover_letter=snapshot.cover_letter
            if snapshot
            else offer.carta_presentacion,
            cv_variant=snapshot.cv_variant if snapshot else offer.cv_base,
            judge=judge,
            claims_digest=build_claims_digest(self._db),
            stories=self._repo.list_stars(),
            notes=notes,
            salary_text=self._prep_salary_text(offer_id),
        )

    async def _company_facts(self, inputs: PrepInputs) -> CompanyFacts:
        """Web-searched company facts; any failure means "no verified data"."""
        found = await self._web_company_facts(inputs.company, inputs.title)
        return found if found is not None else CompanyFacts()

    async def _web_company_facts(
        self, company: str, position: str
    ) -> CompanyFacts | None:
        """One web search for company facts; ``None`` when unsupported or failed."""
        if not supports_web_search(self._llm):
            return None
        system, user = build_facts_prompt(company=company, position=position)
        try:
            facts: CompanyFacts = await self._llm.web_json_completion(  # type: ignore[attr-defined]
                system, user, CompanyFacts
            )
            return facts
        except Exception:
            _logger.warning("company facts search failed", exc_info=True)
            return None

    def prep_in_progress(self, offer_id: int, stage: str) -> bool:
        """True while a pack for this offer+stage is being generated."""
        return (offer_id, stage) in self._prep_claims

    def claim_prep(self, offer_id: int, stage: str) -> bool:
        """Atomically claim an offer+stage (synchronous: no await can interleave).

        False when a pack is already being prepared. Pair with ``release_prep``.
        """
        key = (offer_id, stage)
        if key in self._prep_claims:
            return False
        self._prep_claims.add(key)
        return True

    def release_prep(self, offer_id: int, stage: str) -> None:
        self._prep_claims.discard((offer_id, stage))

    async def prepare_interview(
        self, offer_id: int, stage: str, *, claimed: bool = False
    ) -> PrepDelivery:
        """Build, audit, render and store an interview prep pack.

        Grounded only on the owner's own data (see :mod:`cva.match.interview_prep`);
        no salary research is started. An LLM failure (or an invalid pack)
        propagates and nothing is stored; company facts degrade to
        "sin datos verificados".
        """
        if stage not in STAGES:
            raise ServiceError(f"Fase desconocida: {stage}")
        if not callable(getattr(self._llm, "json_completion", None)):
            raise ServiceError("este proveedor LLM no puede preparar entrevistas")
        if not claimed and not self.claim_prep(offer_id, stage):
            raise ServiceError("Ya estoy preparando esa entrevista…")
        try:
            inputs = self._prep_inputs(offer_id, stage)
            system, user = build_prep_prompt(inputs)
            pack = await self._llm.json_completion(system, user, PrepPack)
            facts = await self._company_facts(inputs)
            created = self._now().date().isoformat()

            def _build() -> tuple[PrepResult, str, str]:
                result = assemble(inputs, pack, facts)
                page = render_prep_html(
                    result,
                    offer_id=offer_id,
                    company=inputs.company,
                    position=inputs.title,
                    created_at=created,
                )
                summary = prep_summary(
                    result,
                    offer_id=offer_id,
                    company=inputs.company,
                    position=inputs.title,
                )
                return result, page, summary

            result, page, summary = await asyncio.to_thread(_build)
            pack_id = self._repo.add_prep_pack(
                offer_id, stage, html=page, json_text=result.model_dump_json()
            )
            return PrepDelivery(
                prep_filename(offer_id, stage), page, summary, pack_id, result
            )
        finally:
            if not claimed:
                self.release_prep(offer_id, stage)

    def _profile_of(self, offer_id: int) -> Profile:
        """The offer's profile; NULL (legacy) is 'ai'."""
        return normalize_profile(self._repo.get_offer(offer_id).perfil)

    def _gate_for(self, profile: Profile) -> tuple[int, int]:
        """(notify threshold, gray zone) of a profile."""
        if profile == "backend":
            return self._backend_threshold, self._backend_gray_zone
        return self._threshold, self._gray_zone

    def _ensure_profile(self, offer_id: int) -> None:
        """Stamp the profile inferred from the title when none is stored yet."""
        row = self._repo.get_offer(offer_id)
        if row.perfil is None:
            self._repo.set_profile_if_unset(offer_id, infer_profile(row.posicion))

    def _refresh_tracker(self) -> None:
        """Rewrite the synced full-tracker xlsx AFTER a committed state change
        (best-effort: warn-only, never fails the action; unset → no-op)."""
        if self._tracker_xlsx is None:
            return
        self._tracker_gen += 1
        # One attempt, no sleep: mutators run on the event loop (bot handlers,
        # bridge helper loop) and must never block; a locked file just marks
        # the tracker dirty for the heartbeat to retry off-loop.
        if refresh_tracker_xlsx(self._repo, self._tracker_xlsx):
            self._mark_tracker_clean()
        else:
            self._tracker_dirty = True

    def _mark_tracker_clean(self) -> None:
        if self._tracker_dirty:
            _logger.info("tracker xlsx refresh recovered after an earlier failure")
        self._tracker_dirty = False

    @property
    def tracker_dirty(self) -> bool:
        """True while the synced tracker xlsx is known to be out of date."""
        return self._tracker_dirty

    async def retry_tracker_refresh(self) -> bool:
        """Heartbeat catch-up: re-run a failed refresh; True when it succeeded.

        No-op (no filesystem activity) when the tracker is disabled or clean.
        Rows are read here on the loop thread; the write, with its locked-file
        retries and backoff sleeps, runs in a worker thread so the loop never
        blocks. If a mutation refreshed while the snapshot was being written,
        the snapshot may have replaced a newer file, so stay dirty and let the
        next tick rewrite it.
        """
        if self._tracker_xlsx is None or not self._tracker_dirty:
            return False
        generation = self._tracker_gen
        try:
            offers = self._repo.tracker_offers()
        except Exception as exc:  # noqa: BLE001 — best-effort projection
            _logger.warning(
                "tracker xlsx retry skipped (%s: %s)", type(exc).__name__, exc
            )
            return False
        ok = await asyncio.to_thread(
            write_tracker_xlsx,
            offers,
            self._tracker_xlsx,
            attempts=TRACKER_RETRY_ATTEMPTS,
        )
        if not ok:
            return False
        if self._tracker_gen != generation:
            self._tracker_dirty = True  # raced a mutation: rewrite next tick
            return False
        self._mark_tracker_clean()
        return True

    async def ingest(self, url: str | None, paste: str | None) -> int:
        """Ingest by link (wall-classified) or pasted text; return the offer id."""
        if paste is not None:
            fetched = paste_offer(paste, url)
        elif url is not None:
            fetched = await fetch_offer(url, fetcher=self._fetcher)
        else:
            raise ServiceError("ingest needs an offer url or pasted offer text")
        fetched, report = _guard_offer(fetched)
        offer_id = self._repo.upsert_offer(
            _offer_input(fetched, today=self._now().date())
        )
        self._note_guard(offer_id, report)
        self._ensure_profile(offer_id)
        self._refresh_tracker()
        return offer_id

    async def analyze_new_bridge_job(
        self,
        offer_id: int,
        *,
        url: str,
        title: str | None,
        company: str | None,
        notify: Callable[[str, Any], Awaitable[SentMessage | None]],
        min_score: int | None = None,
        text: str | None = None,
    ) -> BridgeJobOutcome:
        """Full ``an:`` flow for one bridged job (auto-triage D1 + pacing D5).

        Ingest first (the upsert hits the same row): with inline ``text``
        (infojobs-source D5 — the server fetch is dead for InfoJobs) a
        ``FetchedOffer(source="bridge-inline")`` is built from the payload
        text and upserted with ZERO fetch calls; without it the legacy URL
        re-ingest fetch runs exactly as before. Then the cached analyze
        use-case runs. Failure tree (notification-pacing D3/D4/D5): ANY
        error marks the row retry-pending (attempts+1, next = now + heartbeat
        interval) and sends NOTHING; when the attempt budget is exhausted it
        clears the retry state FIRST (idempotency latch: a crash loses one
        card, never duplicates one) and then sends the single final fallback
        card with the brief Spanish cause. Success (notification-pacing D5
        gate): the breakdown card goes out only when ``score >= min_score``
        — with ``min_score=None`` the live ``threshold`` is read at call time
        (D8) — and the retry state is cleared (by :meth:`analyze`), so a
        manual success also defuses the sweep.

        Returns the per-job outcome for the bridge's round summary
        (round-summary amendment): ``above``/``gray``/``below`` carry the
        score, any failure reports ``("pending", None)``.
        """
        job_id = job_id_from_url(url) or url.rstrip("/").rsplit("/", 1)[-1]
        try:
            if text is not None:
                fetched = FetchedOffer(
                    job_id=job_id_from_url(url),
                    title=title,
                    company=company,
                    url=url,
                    text=text,
                    source="bridge-inline",
                )
                fetched, report = _guard_offer(fetched)
                self._repo.upsert_offer(_offer_input(fetched, today=self._now().date()))
                self._note_guard(offer_id, report)
                self._refresh_tracker()
            else:
                await self.ingest(url, None)
            result = await self.analyze(offer_id)
        except OfferClosedError as closed:
            _logger.info("bridge offer #%s closed: %s", offer_id, closed.marker)
            return BridgeJobOutcome(zone="closed", score=None, reason=CLOSED_NOTE)
        except Exception as exc:  # noqa: BLE001 — the card tree below IS the surface
            attempts = self._repo.record_analyze_failure(
                offer_id,
                next_analyze_at=(
                    self._now() + timedelta(seconds=HEARTBEAT_INTERVAL_SECONDS)
                ).isoformat(),
            )
            exhausted = attempts > self._analyze_retry_max
            if exhausted:
                # Clearer wording for the LAST attempt: "attempt n/max" would
                # read as a fraction above one here (e.g. "1/0") — the retry
                # budget is exhausted, not merely one attempt out of many
                # (T4 cleanup).
                _logger.warning(
                    "Bridge auto-analysis failed for offer #%s (attempt %d, "
                    "retry budget of %d exhausted — sending the final "
                    "fallback card): %s: %s",
                    offer_id,
                    attempts,
                    self._analyze_retry_max,
                    type(exc).__name__,
                    exc,
                )
            else:
                _logger.warning(
                    "Bridge auto-analysis failed for offer #%s (attempt %d/%d): %s: %s",
                    offer_id,
                    attempts,
                    self._analyze_retry_max,
                    type(exc).__name__,
                    exc,
                )
            if exhausted:
                self._repo.clear_analyze_retry(offer_id)  # latch before the card
                # Card-content requirement: the final card carries the
                # canonical URL AS PRODUCED BY THE REGISTRY — normalize even
                # a caller-supplied non-canonical url shape (idempotent for
                # the bridge, which already passes the canonical).
                source = source_from_url(url)
                card_url = (
                    canonical_url(source, job_id, original=url)
                    if source is not None
                    else url
                )
                await notify(
                    bridge_auto_failure_message(
                        exc,
                        title=title,
                        company=company,
                        job_id=job_id,
                        threshold=self._threshold,
                        url=card_url,
                    ),
                    new_job_keyboard(job_id),
                )
            return BridgeJobOutcome(zone="pending", score=None)
        profile = self._profile_of(offer_id)
        profile_gate, gray_zone = self._gate_for(profile)
        card_role = role_for_profile(profile)
        gate = min_score if min_score is not None else profile_gate
        # [Generar CV] carries an explicit language, so the default "es" would
        # override inference: arm the keyboard with the offer's own language.
        source_data = self.offer(offer_id).source_data or ""
        lang = infer_language(source_data)
        apply_findings = detect_apply_instructions(source_data)
        card_salary = self.card_extras(offer_id)
        if wall_reason := result.walled_reason:
            # Hard wall (non-Python backend stack, or a gate FAIL: non-EU
            # eligibility / mandatory foreign language): no card, the offer
            # stays 'analizada' for manual review via /status.
            _logger.info(
                "bridge offer #%s walled: %s (score %s)",
                offer_id,
                wall_reason,
                result.score,
            )
            return BridgeJobOutcome(
                zone="walled", score=result.score, reason=wall_reason
            )
        zone = score_zone(result.score, gate, gray_zone)
        if zone == "above":
            await self._send_breakdown_card(notify, offer_id, result, url=url)
            return BridgeJobOutcome(zone="above", score=result.score)
        elif zone == "gray":
            # Owner amendment: gray zone [gate - zone, gate) → compact
            # borderline card for manual review; estado stays 'analizada'
            # (analyze() already persisted) and retry state is unaffected.
            await self._notify_analysis_card(
                notify,
                offer_id,
                borderline_message(
                    score=result.score,
                    threshold=gate,
                    title=title,
                    company=company,
                    url=url,
                    apply_findings=apply_findings,
                    salary_line=card_salary,
                ),
                cv_keyboard(
                    offer_id,
                    role=card_role,
                    lang=lang,
                    cover_letter_asked=asks_cover_letter(apply_findings),
                    cv_needed=self.cv_needed(offer_id),
                ),
            )
            return BridgeJobOutcome(zone="gray", score=result.score)
        return BridgeJobOutcome(zone="below", score=result.score)

    async def rescue_offer(
        self,
        offer_id: int,
        notify: Callable[[str, Any], Awaitable[SentMessage | None]],
    ) -> None:
        """``/rescatar``: send the full analysis card of an already analysed offer.

        The very same path as the above-threshold auto card
        (:meth:`_send_breakdown_card`: judge breakdown, CV/letter/salary
        keyboard, also-on line, card stored for silent edits), built from the
        STORED analysis: the score and estado are never touched. Raises
        :class:`ServiceError` (Spanish message) for an unknown, not yet
        analysed or duplicate offer.
        """
        try:
            offer = self._repo.get_offer(offer_id)
        except RepositoryError:
            raise ServiceError(f"No existe la oferta #{offer_id}.") from None
        if (dup := _DUPLICATE_OF.search(offer.notas or "")) is not None:
            raise ServiceError(
                f"La oferta #{offer_id} es duplicada de #{dup.group(1)}: rescata esa."
            )
        match = self._repo.latest_match(offer_id)
        if match is None:
            raise ServiceError(f"La oferta #{offer_id} no está analizada todavía.")
        try:
            result = MatchResult.model_validate_json(match.breakdown_json).model_copy(
                update={"score": match.score}
            )
        except ValidationError:
            raise ServiceError(
                f"El análisis guardado de #{offer_id} no se puede leer: vuelve a "
                f"analizarla con /analyze."
            ) from None
        await self._send_breakdown_card(notify, offer_id, result, url=offer.url_oferta)
        # Rescued offers leave the panel's discarded list (score/estado untouched).
        self._repo.add_event(offer_id, "note", note=RESCUED_NOTE)

    async def _send_breakdown_card(
        self,
        notify: Callable[[str, Any], Awaitable[SentMessage | None]],
        offer_id: int,
        result: MatchResult,
        *,
        url: str | None,
    ) -> None:
        """The full analysis card (judge breakdown + CV/letter/salary keyboard,
        also-on line, stored for silent edits): the above-threshold auto card,
        also used by ``/rescatar`` so both go through the very same path."""
        profile = self._profile_of(offer_id)
        source_data = self.offer(offer_id).source_data or ""
        apply_findings = detect_apply_instructions(source_data)
        judgment, fallback = self.latest_judgment(offer_id)
        await self._notify_analysis_card(
            notify,
            offer_id,
            judge_breakdown_message(
                result,
                judgment,
                offer_id,
                url=url,
                judge_fallback=fallback,
                apply_findings=apply_findings,
                salary_line=self.card_extras(offer_id),
            ),
            cv_keyboard(
                offer_id,
                role=role_for_profile(profile),
                lang=infer_language(source_data),
                cover_letter_asked=asks_cover_letter(apply_findings),
                cv_needed=self.cv_needed(offer_id),
            ),
        )

    async def _notify_analysis_card(
        self,
        notify: Callable[[str, Any], Awaitable[SentMessage | None]],
        offer_id: int,
        text: str,
        keyboard: Any,
    ) -> None:
        """Send an analysis card (with "También en" when duplicates are already
        known) and remember where it landed so it can be edited in place."""
        if (label := self.no_cv_label(offer_id)) is not None:
            text = with_no_cv_line(text, label)
        try:
            text = with_also_on(text, self.also_on(offer_id))
        except Exception as exc:  # noqa: BLE001 — the card goes out regardless
            _logger.warning("also-on lookup failed for offer #%s: %s", offer_id, exc)
        sent = await notify(text, keyboard)
        if sent is None:
            return
        try:
            self._repo.save_card(
                offer_id,
                sent.chat_id,
                sent.message_id,
                text,
                None,
                keyboard_to_json(keyboard),
            )
        except Exception as exc:  # noqa: BLE001 — bookkeeping never loses a card
            _logger.warning("could not store card of offer #%s: %s", offer_id, exc)

    def cv_needed(self, offer_id: int) -> bool:
        """False when the offer's portal is applied to without a CV."""
        offer = self.offer(offer_id)
        return needs_cv(offer.origen, offer.url_oferta)

    def no_cv_label(self, offer_id: int) -> str | None:
        """Portal label when the offer is applied to without a CV, else None."""
        offer = self.offer(offer_id)
        return no_cv_label(offer.origen, offer.url_oferta)

    def also_on(self, offer_id: int) -> list[str]:
        """Portals where the offer was also seen (cross-source duplicates)."""
        return self._repo.duplicate_portal_labels(offer_id)

    def move_card(
        self,
        offer_id: int,
        old_chat_id: int,
        old_message_id: int,
        chat_id: int,
        message_id: int,
    ) -> None:
        """Follow a card the owner's CV press re-sent as a new message (only
        when the pressed message is the tracked card).

        Bookkeeping only: a DB error is logged, never allowed to abort the press.
        """
        try:
            self._repo.move_card(
                offer_id, old_chat_id, old_message_id, chat_id, message_id
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning("could not move card of offer #%s: %s", offer_id, exc)

    def forget_card(self, chat_id: int, message_id: int) -> None:
        """Stop tracking a card the owner interacted with (buttons rewrite it).

        Bookkeeping only: a DB error is logged, never allowed to abort the
        button press that triggered it.
        """
        try:
            self._repo.forget_card(chat_id, message_id)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("could not forget card %s/%s: %s", chat_id, message_id, exc)

    def _discard_if_closed(self, offer: OfferRow) -> None:
        """Closed text on a pre-send offer: discard it, note it, skip the LLM.

        Raises :class:`OfferClosedError`. Later-stage offers (sent...) are never
        touched (a recorded send is not overwritten).
        """
        if offer.estado not in PRE_SEND_ESTADOS and offer.estado != "descartada":
            return
        marker = closed_marker(offer.source_data or "")
        if marker is None:
            return
        self._repo.set_estado(offer.id, "descartada")
        self._repo.append_note(offer.id, CLOSED_NOTE)
        # Timestamp of the closure: the bridge's URL-only reopen window reads it.
        self._repo.add_event(offer.id, "note", note=CLOSED_NOTE)
        self._repo.clear_analyze_retry(offer.id)
        on_discarded(
            self._mirror,
            Path(offer.cv_adaptado) if offer.cv_adaptado else None,
        )
        self._refresh_tracker()
        raise OfferClosedError(offer.id, marker)

    async def analyze(self, offer_id: int) -> MatchResult:
        """Extract (cached) → deterministic score → persist + keyword bump.

        match_mode (llm-offer-judge design): "keyword" (default) is exactly
        this — unchanged. "hybrid" additionally runs the judge: the keyword
        breakdown is KEPT (transparency + CV-emphasis input, unaffected),
        but the returned/persisted ``score`` becomes the judge's score, so
        every existing score-driven caller (the threshold gate, gray zone,
        notifications, round summary) is judge-driven with NO further
        change. A failed judge call falls back to the keyword score and
        logs one WARNING — the analysis is never lost; it ALSO clears any
        previously stored judgment (robustness follow-up), so a re-analysis
        whose judge call fails never leaves an earlier successful judgment
        looking current — :meth:`latest_judgment` reads the same row, so
        clearing it here is the single source of truth for both.
        """
        offer = self._repo.get_offer(offer_id)
        self._discard_if_closed(offer)
        extraction = await extract(
            offer.source_data,
            self._llm,
            cache=self._cache,
            model=self._extraction_model,
        )
        profile = normalize_profile(offer.perfil)
        threshold, _ = self._gate_for(profile)
        result = score(extraction, self._db, self._syn, threshold=threshold)
        # Backend wall: only evaluated for backend-profile offers.
        wall = (
            stack_wall_reason(extraction, offer.source_data, self._prefs.stack_wall)
            if profile == "backend"
            else None
        )
        gates = run_gates(offer.source_data, offer.ubicacion, self._prefs.gates)
        result = result.model_copy(
            update={"gates": gates, **({"stack_wall": wall} if wall else {})}
        )
        # Hard walls (stack wall, gate FAIL) force a discard verdict; gate FLAGs
        # are soft walls shown with a warning marker on the judge card.
        walls = [
            *([Wall(text=wall, hard=True)] if wall else []),
            *(
                Wall(text=g.reason, hard=g.verdict == "FAIL")
                for g in gates
                if g.verdict != "PASS"
            ),
        ]
        if self._match_mode == "hybrid":
            try:
                judgment = await judge_offer(
                    offer.source_data,
                    self._db,
                    self._llm,
                    model=self._judge_model,
                    threshold=threshold,
                    extra_anchors=self._judge_anchors(),
                    anchor_thresholds=self._anchor_thresholds(),
                    persona=self._judge_persona,
                )
            except LlmResponseError as exc:
                _logger.warning(
                    "offer %s: judge failed, falling back to the keyword score: %s: %s",
                    offer_id,
                    type(exc).__name__,
                    exc,
                )
                # Never leave a STALE judgment from an earlier successful
                # analysis looking current (robustness follow-up: T3a).
                self._repo.clear_judge_result(offer_id)
            else:
                if walls:
                    judgment = judgment.model_copy(
                        update={
                            "walls": [*judgment.walls, *walls],
                            "verdict": (
                                "discard"
                                if any(w.hard for w in walls)
                                else judgment.verdict
                            ),
                        }
                    )
                self._repo.save_judge_result(
                    offer_id,
                    score=judgment.score,
                    verdict=judgment.verdict,
                    judgment_json=judgment.model_dump_json(),
                )
                result = result.model_copy(update={"score": judgment.score})
        self._repo.save_match_result(
            offer_id,
            score=result.score,
            threshold=result.threshold,
            breakdown_json=result.model_dump_json(),
        )
        self._repo.record_offer_keywords(
            offer_id, DEFAULT_PROFILE, self._keyword_log(extraction)
        )
        self._repo.set_estado(offer_id, "analizada")
        # Pacing D3: any success (auto OR manual) defuses the retry sweep.
        self._repo.clear_analyze_retry(offer_id)
        self._refresh_tracker()
        return result

    def latest_judgment(self, offer_id: int) -> tuple[OfferJudgment | None, bool]:
        """(judgment, fallback) for the card (bot/cli, design llm-offer-judge).

        ``match_mode == "keyword"``: always ``(None, False)`` — no judge is
        ever attempted, so the card stays exactly :func:`breakdown_message`.
        Otherwise: the persisted judgment when one exists, else
        ``(None, True)`` — the judge failed on the most recent ``analyze()``
        call for this offer (which always clears any stale prior row on
        failure — see :meth:`analyze`), meaning the score shown is the
        keyword fallback, never a leftover judgment from an earlier run.
        """
        if self._match_mode == "keyword":
            return None, False
        row = self._repo.latest_judge_result(offer_id)
        if row is None:
            return None, True
        return OfferJudgment.model_validate_json(row.judgment_json), False

    async def judge_eval(self, labels_path: Path) -> str:
        """Run the judge against the labeled eval bank (owner-approved: spends
        LLM quota). Never invoked by tests against a real LLM — see
        ``cva/match/judge_eval.py`` and ``cli.py judge-eval``.

        Forwards the SAME configured ``threshold`` ``analyze()`` uses
        (robustness follow-up) — without this, the harness graded verdict
        agreement against a hardcoded default, silently diverging from
        whatever the owner actually configured.
        """
        return await run_judge_eval(
            labels_path,
            self._db,
            self._llm,
            model=self._judge_model,
            threshold=self._threshold,
        )

    async def rejudge_backlog(
        self,
        *,
        since: str | None = None,
        title_filter: bool = False,
        include_discarded: bool = False,
        force: bool = False,
        limit: int | None = None,
        dry_run: bool = False,
        report_only: bool = False,
        min_score: int = 50,
        pace_seconds: float = 20.0,
        max_consecutive_failures: int = _REJUDGE_DEFAULT_MAX_CONSECUTIVE_FAILURES,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
        progress_fn: Callable[[str], None] | None = None,
        report_dir: Path | None = None,
    ) -> RejudgeResult:
        """Re-judge stored backlog offers with the LLM judge only (owner-
        approved: spends LLM quota unless ``dry_run``/``report_only``; see
        ``odd/tasks/rejudge-backlog.md`` and its "Hardening" section).

        Judge-only: no extraction, no re-scraping — reuses ``offer.source_data``
        exactly as stored. Selection (in order, each reason counted for the
        dry-run/summary breakdown): skip owner-decided states
        (``_REJUDGE_DEFAULT_SKIP_STATES``, minus ``descartada`` when
        ``include_discarded``); require real text
        (:func:`_is_real_offer_text`); optional ``since`` (``YYYY-MM-DD``,
        parsed with :func:`datetime.date.fromisoformat` and compared as a
        DATE against ``fecha_update`` — see :func:`_offer_age_days`'s
        caveat); optional ``title_filter`` (the SAME default lists as
        ``extension/filter.js``'s ``DEFAULT_TITLE_FILTER`` unless the profile's
        ``preferences.toml`` overrides it, via :mod:`cva.title_filter`). This "eligible" set is then split into
        already-judged (skipped unless ``force`` — but STILL shown in the
        report from its stored result, see below) and to-judge. ``limit``
        caps the to-judge list (``total_candidates`` is the unlimited count,
        ``planned_candidates`` is after ``limit``).

        ``estado`` is NEVER touched — this is purely additive re-scoring;
        only ``judge_results`` (:meth:`OfferRepository.save_judge_result`)
        is written, exactly like ``analyze()``'s hybrid-mode judge call.

        Report covers the WHOLE eligible backlog, not only this run
        (hardening): every eligible offer with a stored ``judge_results``
        row — whether judged just now or in an earlier run — appears in
        ``rows``/``report_text``, so a resumed run's report is never missing
        earlier results (:class:`RejudgeRow`.``judged_this_run`` marks
        which). ``report_only=True`` makes ZERO LLM calls and builds this
        same report purely from stored results (no judging, no ``force``/
        ``limit`` effect on judging since nothing is judged).

        Never lost (hardening): the report file is rewritten after every
        judged offer, and a run stopped early — either the circuit breaker
        below, or a ``KeyboardInterrupt`` caught here — still returns a
        normal :class:`RejudgeResult` with everything judged so far and
        ``stopped_reason`` explaining why, instead of losing the report or
        crashing the CLI. One offer's judge call failing never aborts the
        run by itself (continue-on-failure, mirrors
        :func:`cva.match.judge_eval.run_judge_eval`): it is recorded in
        ``failures`` and the next candidate still runs — UNLESS
        ``max_consecutive_failures`` (default 3) consecutive failures are
        reached, which trips the circuit breaker (the owner's paced ~170-
        offer run must not burn its whole pace budget calling a dead LLM,
        e.g. after the configured subscription's quota is exhausted).

        Pacing (``pace_seconds`` between calls, never before the first) and
        progress reporting are both injectable (test/CLI seam): ``sleep_fn``
        defaults to :func:`asyncio.sleep` (tests must always override it —
        no real sleeps in tests); ``progress_fn`` defaults to a no-op.
        """
        if report_dir is None:
            report_dir = DEFAULT_REPORT_DIR
        if limit is not None and limit < 1:
            raise ValueError("limit must be >= 1")
        if pace_seconds < 0:
            raise ValueError("pace_seconds must be >= 0")
        if not 0 <= min_score <= 100:
            raise ValueError("min_score must be between 0 and 100")
        if max_consecutive_failures < 1:
            raise ValueError("max_consecutive_failures must be >= 1")
        since_date = date.fromisoformat(since) if since is not None else None

        sleep = sleep_fn if sleep_fn is not None else asyncio.sleep
        progress = progress_fn if progress_fn is not None else (lambda _msg: None)
        skip_states = set(_REJUDGE_DEFAULT_SKIP_STATES)
        if include_discarded:
            skip_states.discard("descartada")

        skipped_state = 0
        skipped_short_text = 0
        skipped_since = 0
        skipped_title_filter = 0
        skipped_already_judged = 0
        eligible: list[OfferRow] = []
        for offer in self._repo.all_offers():
            if offer.estado in skip_states:
                skipped_state += 1
                continue
            if not _is_real_offer_text(offer.source_data):
                skipped_short_text += 1
                continue
            if since_date is not None:
                offer_date = _offer_date(offer.fecha_update)
                if offer_date is None or offer_date < since_date:
                    skipped_since += 1
                    continue
            if title_filter and not _passes_title_filter(
                offer.posicion, offer.empresa, self._prefs.title_filters.ai
            ):
                skipped_title_filter += 1
                continue
            eligible.append(offer)

        today = self._now().date()

        if report_only:
            rows: list[RejudgeRow] = []
            for offer in eligible:
                stored = self._repo.latest_judge_result(offer.id)
                if stored is None:
                    continue
                rows.append(
                    _rejudge_row(
                        offer,
                        score=stored.score,
                        verdict=stored.verdict,
                        walls=_judgment_walls(stored.judgment_json),
                        today=today,
                        judged_this_run=False,
                    )
                )
            rows.sort(key=lambda row: row.score, reverse=True)
            report_text = _format_rejudge_report(
                rows, [], min_score=min_score, total_judged=len(rows)
            )
            report_dir.mkdir(parents=True, exist_ok=True)
            report_path = _unique_report_path(
                report_dir, self._now().strftime("%Y-%m-%d_%H%M%S")
            )
            report_path.write_text(report_text, encoding="utf-8")
            return RejudgeResult(
                dry_run=False,
                report_only=True,
                total_candidates=len(eligible),
                planned_candidates=len(eligible),
                judged=0,
                skipped_state=skipped_state,
                skipped_already_judged=skipped_already_judged,
                skipped_short_text=skipped_short_text,
                skipped_since=skipped_since,
                skipped_title_filter=skipped_title_filter,
                estimated_tokens=0,
                rows=rows,
                report_text=report_text,
                report_path=report_path,
            )

        already_judged: list[tuple[OfferRow, Any]] = []
        to_judge: list[OfferRow] = []
        for offer in eligible:
            stored = self._repo.latest_judge_result(offer.id)
            if stored is not None and not force:
                skipped_already_judged += 1
                already_judged.append((offer, stored))
                continue
            to_judge.append(offer)

        total_candidates = len(to_judge)
        if limit is not None:
            to_judge = to_judge[:limit]
        planned_candidates = len(to_judge)
        estimated_tokens = planned_candidates * _REJUDGE_ESTIMATED_TOKENS_PER_OFFER

        if dry_run:
            return RejudgeResult(
                dry_run=True,
                total_candidates=total_candidates,
                planned_candidates=planned_candidates,
                judged=0,
                skipped_state=skipped_state,
                skipped_already_judged=skipped_already_judged,
                skipped_short_text=skipped_short_text,
                skipped_since=skipped_since,
                skipped_title_filter=skipped_title_filter,
                estimated_tokens=estimated_tokens,
            )

        baseline_rows = [
            _rejudge_row(
                offer,
                score=stored.score,
                verdict=stored.verdict,
                walls=_judgment_walls(stored.judgment_json),
                today=today,
                judged_this_run=False,
            )
            for offer, stored in already_judged
        ]
        fresh_rows: list[RejudgeRow] = []
        failures: list[RejudgeFailure] = []
        consecutive_failures = 0
        stopped_reason: str | None = None
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = _unique_report_path(
            report_dir, self._now().strftime("%Y-%m-%d_%H%M%S")
        )

        def _write_report() -> str:
            all_rows = sorted(
                baseline_rows + fresh_rows, key=lambda row: row.score, reverse=True
            )
            text = _format_rejudge_report(
                all_rows,
                failures,
                min_score=min_score,
                total_judged=len(all_rows),
                stopped_reason=stopped_reason,
            )
            report_path.write_text(text, encoding="utf-8")
            return text

        anchors = self._judge_anchors()
        for index, offer in enumerate(to_judge):
            progress(
                f"[{index + 1}/{len(to_judge)}] oferta #{offer.id} — "
                f"{offer.posicion or '(sin título)'} ({offer.empresa or '(sin empresa)'})"
            )
            try:
                if index > 0 and pace_seconds > 0:
                    await sleep(pace_seconds)
                judgment = await judge_offer(
                    offer.source_data,
                    self._db,
                    self._llm,
                    model=self._judge_model,
                    threshold=self._threshold,
                    extra_anchors=anchors,
                    anchor_thresholds=self._anchor_thresholds(),
                    persona=self._judge_persona,
                )
            except KeyboardInterrupt:
                stopped_reason = "interrumpido por el usuario (KeyboardInterrupt)"
                break
            except Exception as exc:  # noqa: BLE001 — continue-on-failure by design
                failures.append(
                    RejudgeFailure(
                        offer_id=offer.id, error=f"{type(exc).__name__}: {exc}"
                    )
                )
                consecutive_failures += 1
                _write_report()
                if consecutive_failures >= max_consecutive_failures:
                    stopped_reason = (
                        f"{consecutive_failures} fallos consecutivos del juez — "
                        f"último error: {type(exc).__name__}: {exc}"
                    )
                    break
                continue
            consecutive_failures = 0
            fresh_rows.append(
                _rejudge_row(
                    offer,
                    score=judgment.score,
                    verdict=judgment.verdict,
                    walls=[wall.text for wall in judgment.walls if wall.hard],
                    today=today,
                    judged_this_run=True,
                )
            )
            self._repo.save_judge_result(
                offer.id,
                score=judgment.score,
                verdict=judgment.verdict,
                judgment_json=judgment.model_dump_json(),
            )
            _write_report()

        report_text = _write_report()
        all_rows = sorted(
            baseline_rows + fresh_rows, key=lambda row: row.score, reverse=True
        )

        return RejudgeResult(
            dry_run=False,
            total_candidates=total_candidates,
            planned_candidates=planned_candidates,
            judged=len(fresh_rows),
            skipped_state=skipped_state,
            skipped_already_judged=skipped_already_judged,
            skipped_short_text=skipped_short_text,
            skipped_since=skipped_since,
            skipped_title_filter=skipped_title_filter,
            estimated_tokens=estimated_tokens,
            stopped_reason=stopped_reason,
            rows=all_rows,
            failures=failures,
            report_text=report_text,
            report_path=report_path,
        )

    async def generate_cv(
        self,
        offer_id: int,
        role: Role | None = None,
        lang: Language | None = None,
    ) -> Path:
        """Deliver one fixed base CV; explicit lang beats ``infer_language`` (D10).

        ``role=None`` follows the offer profile: backend offers get the Backend
        CV, everything else (ai / legacy NULL) the AI one. Backend has no
        Catalan CV, so an INFERRED Catalan offer gets the Spanish Backend
        CV; an explicit Backend + ``ca`` request is rejected.

        fixed-base-cvs: nothing is rendered. The owner's hand-reviewed base
        PDF for the variant is verified against ``cv_base_pdfs/manifest.json``
        (sha256) and copied byte-for-byte to the tailored name. A missing file
        or hash mismatch blocks delivery with ``CvgenError``.
        """
        offer = self._repo.get_offer(offer_id)
        if role is None:
            role = role_for_profile(offer.perfil)  # type: ignore[assignment]
        if role not in _ROLE_LABEL:
            raise ServiceError(f"unknown variant role: {role}")
        resolved = lang if lang is not None else infer_language(offer.source_data)
        if role == "BACKEND" and lang is None and resolved == "ca":
            resolved = "es"
        variant = _variant(role, resolved)
        out = generic_output_path(
            variant,
            offer_id,
            offer.empresa,
            self._output_dir,
            self._identity.file_slug,
        )
        pdf_path = deliver_base_pdf(variant, out, self._base_pdfs_dir)
        self._repo.record_cv(
            offer_id,
            cv_base=variant.value,
            cv_path=str(pdf_path),
            cambios=base_cambios(variant, self._base_pdfs_dir),
        )
        # Mirror projection AFTER the DB write (D7): best-effort copy to
        # nuevos/, warn-only, never blocks or fails the delivery.
        on_generated(self._mirror, pdf_path)
        self._refresh_tracker()
        return pdf_path

    def mark_sent(self, offer_id: int, cv_path: Path | None = None) -> None:
        """Record the send: estado=enviada + fecha_envio + CV fields + match.

        An offer applied to without a generated CV (a base CV, or a portal that
        needs none) is recorded too: no CV fields, nothing to move in the mirror.
        """
        offer = self._repo.get_offer(offer_id)
        path = cv_path
        if path is None and offer.cv_adaptado is not None:
            path = Path(offer.cv_adaptado)
        match_row = self._repo.latest_match(offer_id)
        if match_row is None:
            raise ServiceError(f"offer {offer_id} has no analysis; call analyze first")
        first_send = self._repo.mark_sent(
            offer_id,
            cv_path=None if path is None else str(path),
            cambios=offer.cambios or (None if path is None else NEUTRAL_CAMBIOS),
            match=match_row.score,
            judgment=self._judgment_snapshot(offer),
        )
        if not first_send:
            # The first snapshot is kept; the re-send leaves a timeline trace.
            self._repo.add_event(offer_id, "note", note="reenvío")
        # Mirror move AFTER the DB write (D7): nuevos/ → enviados/, warn-only.
        if path is not None:
            on_sent(self._mirror, path)
        self._refresh_tracker()

    def _judgment_snapshot(self, offer: OfferRow) -> SentJudgment | None:
        """What the judge thought of the offer right now (stored at send time).

        ``None`` without a valid judge row (keyword mode or a failed judge): a
        keyword score is never presented to the judge as its own.
        """
        row = self._repo.latest_judge_result(offer.id)
        if row is None:
            return None
        with suppress(ValidationError):
            judgment = OfferJudgment.model_validate_json(row.judgment_json)
            return SentJudgment(
                score=judgment.score,
                verdict=judgment.verdict,
                core_role=sanitize_core_role(judgment.core_role) or None,
                hard_walls=sum(1 for w in judgment.walls if w.hard),
                perfil=offer.perfil,
            )
        return None

    def _require_sent(self, offer_id: int) -> OfferRow:
        offer = self._repo.get_offer(offer_id)
        if offer.fecha_envio is None:
            raise ServiceError(
                f"La oferta #{offer_id} todavía no está marcada como enviada."
            )
        return offer

    def record_outcome(self, offer_id: int, estado: str) -> None:
        """Move a SENT offer to a post-send estado and log it on the timeline."""
        if estado not in OUTCOME_ESTADOS:
            raise ServiceError(f"Estado no válido: {estado}")
        self._require_sent(offer_id)
        self._repo.record_estado_event(offer_id, estado)
        self._refresh_tracker()

    def add_outcome_note(self, offer_id: int, note: str) -> None:
        """Store free-text feedback on a sent offer's timeline."""
        text = note.strip()[:_OUTCOME_NOTE_MAX]
        if not text:
            raise ServiceError("La nota está vacía.")
        self._require_sent(offer_id)
        self._repo.add_event(offer_id, "note", note=text)

    # --- outcome calibration (feature outcome-calibration) -----------------

    def _calibration_judgment(self, offer: OfferRow) -> SentJudgment | None:
        """The judge's view at send time; legacy sends fall back to the current
        judge row. ``None`` when no valid judge score exists (never a keyword)."""
        snapshot = self._repo.get_sent_judgment(offer.id)
        if snapshot is not None:
            return snapshot
        return self._judgment_snapshot(offer)

    def calibration_needs_reason(self, offer_id: int, estado: str) -> bool:
        """True when ``estado`` is a rejection not asked about yet for this offer."""
        return (
            outcome_polarity(estado) == "negative"
            and self._repo.calibration_for(offer_id, "negative") is None
        )

    def propose_calibration(
        self, offer_id: int, estado: str, reason: str | None = None
    ) -> CalibrationExample | None:
        """Create the ``pending`` example for a calibration signal, once per offer
        and polarity. ``None`` when the outcome is not a signal, the polarity was
        already asked, or the rejection was not fit-related (that case stores a
        ``declined`` marker so it is not asked again)."""
        polarity = outcome_polarity(estado)
        if polarity is None:
            return None
        offer = self._require_sent(offer_id)
        existing = self._repo.calibration_for(offer_id, polarity)
        if existing is not None:
            # Still unanswered (e.g. the question never reached the owner):
            # hand it back so it can be asked again.
            return existing if existing.status == "pending" else None
        if polarity == "negative":
            if reason is None or (reason not in FIT_REASONS and reason != REASON_OTHER):
                raise ServiceError(f"Motivo no válido: {reason}")
        else:
            reason = None
        judgment = self._calibration_judgment(offer)
        if judgment is None:
            return None
        fit_related = polarity == "positive" or reason in FIT_REASONS
        example_id = self._repo.add_calibration(
            offer_id,
            outcome=estado,
            polarity=polarity,
            reason=reason,
            judgment=judgment,
            status="pending" if fit_related else "declined",
        )
        if example_id is None or not fit_related:
            return None
        return self._repo.get_calibration(example_id)

    def get_calibration(self, example_id: int) -> CalibrationExample | None:
        return self._repo.get_calibration(example_id)

    def approve_calibration(self, example_id: int) -> CalibrationExample | None:
        """pending -> active; ``None`` for a stale or repeated press."""
        if not self._repo.transition_calibration(example_id, "pending", "active"):
            return None
        return self._repo.get_calibration(example_id)

    def decline_calibration(self, example_id: int) -> CalibrationExample | None:
        """pending -> declined; ``None`` for a stale or repeated press."""
        if not self._repo.transition_calibration(example_id, "pending", "declined"):
            return None
        return self._repo.get_calibration(example_id)

    def deactivate_calibration(self, example_id: int) -> bool:
        """active -> inactive; False when it was not active."""
        return self._repo.transition_calibration(example_id, "active", "inactive")

    def _anchor_thresholds(self) -> dict[str, int]:
        """Each profile's notify threshold, for rendering its own anchors."""
        return {"ai": self._threshold, "backend": self._backend_threshold}

    def _judge_anchors(self) -> list[CalibrationExample]:
        """The owner-approved examples handed to the judge prompt (most recent 8)."""
        return self._repo.list_calibrations("active", limit=MAX_ANCHORS)

    def active_calibrations(self, limit: int | None = None) -> list[CalibrationExample]:
        """Owner-approved examples, oldest approval first (the most recent
        ``limit`` when given)."""
        return self._repo.list_calibrations("active", limit=limit)

    def calibration_summary(self) -> CalibrationSummary:
        """Active examples vs. each profile's notify threshold: positives the
        judge scored under it, negatives it scored over it."""
        examples = self._repo.list_calibrations("active")
        below = above = 0
        for example in examples:
            threshold = self._gate_for(normalize_profile(example.profile))[0]
            if example.polarity == "positive" and example.score_at_send < threshold:
                below += 1
            elif example.polarity == "negative" and example.score_at_send >= threshold:
                above += 1
        return CalibrationSummary(
            total=len(examples), positives_below=below, negatives_above=above
        )

    def outcome_events(self, offer_id: int) -> list[OfferEvent]:
        """The offer's outcome timeline, oldest first."""
        return self._repo.list_events(offer_id)

    def discard(self, offer_id: int) -> None:
        """Descartar sin PDF (Discard path): idempotent on ``descartada``;
        refuses ``enviada`` (a recorded send is never overwritten)."""
        offer = self._repo.get_offer(offer_id)
        estado = offer.estado
        if estado == "enviada":
            raise ServiceError("Ya está marcada como enviada.")
        if estado != "descartada":
            self._repo.set_estado(offer_id, "descartada")
            # Mirror removal AFTER the DB write (D7); silent if no CV exists.
            on_discarded(
                self._mirror,
                Path(offer.cv_adaptado) if offer.cv_adaptado else None,
            )
            self._refresh_tracker()

    # --- STAR story bank (no LLM: the owner's own words, stored verbatim) ----

    def star_candidates(self, *, unused_only: bool = False) -> list[StarCandidate]:
        """Story candidates from the claims DB (optionally those not started)."""
        candidates = star_candidates(self._db)
        if not unused_only:
            return candidates
        used = {tuple(s.source_claim_ids) for s in self._repo.list_stars()}
        return [c for c in candidates if c.source_claim_ids not in used]

    def star_stories(self) -> list[StarStory]:
        return self._repo.list_stars()

    def star_story(self, star_id: int) -> StarStory:
        return self._repo.get_star(star_id)

    def start_star(self, key: str) -> tuple[StarStory, bool]:
        """(story, created): the candidate's story; reused if already started."""
        candidate = next((c for c in star_candidates(self._db) if c.key == key), None)
        if candidate is None:
            raise ServiceError("Esa candidata ya no existe; usa /star de nuevo.")
        for story in self._repo.list_stars():
            if tuple(story.source_claim_ids) == candidate.source_claim_ids:
                return story, False
        star_id = self._repo.add_star(candidate.title, list(candidate.source_claim_ids))
        return self._repo.get_star(star_id), True

    def star_source_texts(self, story: StarStory) -> list[str]:
        """The real claim texts a story rests on (shown as context)."""
        return [
            self._db.by_id[i].text
            for i in story.source_claim_ids
            if i in self._db.by_id
        ]

    def answer_star(self, star_id: int, field: str, text: str) -> StarStory:
        """Store one answer verbatim (whitespace stripped, nothing else)."""
        if field not in STAR_TEXT_FIELDS:
            raise ServiceError(f"Campo desconocido: {field}")
        cleaned = text.strip()
        if not cleaned:
            raise ServiceError("La respuesta está vacía.")
        self._repo.update_star(star_id, **{field: cleaned})
        return self._repo.get_star(star_id)

    def skip_star(self, star_id: int, field: str) -> StarStory:
        """Leave an unanswered field as the placeholder (story stays incomplete).

        An already-answered field (single-field edit) is kept unchanged.
        """
        if field not in STAR_TEXT_FIELDS:
            raise ServiceError(f"Campo desconocido: {field}")
        story = self._repo.get_star(star_id)
        if getattr(story, field) != STAR_PLACEHOLDER:
            return story
        self._repo.update_star(star_id, **{field: STAR_PLACEHOLDER})
        return self._repo.get_star(star_id)

    def delete_star(self, star_id: int) -> None:
        self._repo.get_star(star_id)  # fail fast on a stale button
        self._repo.delete_star(star_id)

    def offer(self, offer_id: int) -> OfferRow:
        """One tracker row (bot /status <id>)."""
        return self._repo.get_offer(offer_id)

    def recent_offers(self, limit: int = 10) -> list[OfferRow]:
        """The most recent offers, newest first (bot /status listing)."""
        return self._repo.recent_offers(limit)

    def keywords(self, profile: str | None = None) -> list[KeywordRow]:
        """Demand-log rows (bot /keywords [profile])."""
        return self._repo.keyword_rows(profile)

    def import_history(
        self, path: Path, *, dry_run: bool = False
    ) -> HistoryImportResult:
        """Import the manual offer log (JSON list) into the tracker.

        Idempotent: merges by LinkedIn job id, dedupes by the ``[log #N]``
        marker. Invalid records are reported and skipped. ``dry_run`` applies
        the run to the real rows then rolls back, so the counts are exact.
        No analysis, notification or CV generation happens for imported rows.
        """
        try:
            records, invalid = parse_history(path)
        except ValueError as exc:
            raise ServiceError(str(exc)) from exc
        result = HistoryImportResult(invalid=invalid, dry_run=dry_run)
        try:
            for record in records:
                outcome = self._repo.import_history_record(record)
                if outcome == "inserted":
                    result.inserted += 1
                elif outcome == "merged":
                    result.merged += 1
                else:
                    result.skipped += 1
        except Exception:
            self._repo.rollback()
            raise
        if dry_run:
            self._repo.rollback()
            return result
        self._repo.commit()
        if result.inserted or result.merged:
            self._refresh_tracker()
        return result

    def export(self, path: Path | None = None) -> Path:
        """Sent-only xlsx export (design D12 ``export`` use-case)."""
        target = path if path is not None else _DEFAULT_EXPORT_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        return export_xlsx(self._repo, target)

    def panel_rows(self) -> list[OfferRow]:
        """Tracker rows for :meth:`panel_html` (read on the caller's thread)."""
        return self._repo.tracker_offers()

    def panel_discarded(self) -> list[DiscardedOffer]:
        """Offers analysed in the last 7 days that never got a card.

        Same decision the bot takes: a stored ``walled_reason`` (stack wall or
        gate FAIL) is "walled"; otherwise :func:`score_zone` with the profile's
        own gate and gray zone ("below" = nothing was sent). A judge hard wall
        only counts as a wall for a below-zone score (an above/gray offer was
        notified). The analysis date is ``match_results.created_at``, the stamp
        every successful ``analyze`` writes (the judge row is saved in the same
        call); the repository stores a calendar date only, so the 7-day window
        is compared by day (the 7th day back is still inside). Closed/manually discarded (``descartada``), duplicates and any
        offer past ``analizada`` are excluded. Reads SQLite: call it on the
        thread that owns the connection.
        """
        now = self._now()
        cutoff = (now - timedelta(days=DISCARDED_DAYS)).date()
        found: list[DiscardedOffer] = []
        for offer_id in self._repo.analysed_offer_ids_since(cutoff.isoformat()):
            item = self._discarded_item(offer_id, now, cutoff)
            if item is not None:
                found.append(item)
        # Below-score rows: closest to their threshold first; walled rows: the
        # most recently analysed first (the renderer keeps this order).
        return sorted(
            found,
            key=lambda d: (
                d.kind != "below",
                d.threshold - d.score if d.kind == "below" else d.days_ago,
                -d.offer_id,
            ),
        )

    def _discarded_item(
        self, offer_id: int, now: datetime, cutoff: date
    ) -> DiscardedOffer | None:
        offer = self._repo.get_offer(offer_id)
        match = self._repo.latest_match(offer_id)
        if match is None or _is_stored_duplicate(offer.notas):
            return None
        if any(e.note == RESCUED_NOTE for e in self._repo.list_events(offer_id)):
            return None
        analysed = _parse_stamp(match.created_at)
        if analysed is None or analysed.date() < cutoff:
            return None
        profile = normalize_profile(offer.perfil)
        gate, gray = self._gate_for(profile)
        zone = score_zone(match.score, gate, gray)
        try:
            walled = MatchResult.model_validate_json(match.breakdown_json).walled_reason
        except ValidationError:
            walled = None
        judge = self._repo.latest_judge_result(offer_id)
        judgment: OfferJudgment | None = None
        if judge is not None:
            try:
                judgment = OfferJudgment.model_validate_json(judge.judgment_json)
            except ValidationError:
                judgment = None
        hard = [w.text for w in judgment.walls if w.hard] if judgment else []
        if walled:
            reason, kind = walled, "walled"
        elif zone == "below" and hard:
            reason, kind = hard[0], "walled"
        elif zone == "below":
            reason, kind = self._below_reason(judgment), "below"
        else:
            return None
        return DiscardedOffer(
            offer_id=offer_id,
            title=offer.posicion or "",
            company=offer.empresa or "",
            portal=source_label(offer.origen) if offer.origen else "",
            url=offer.url_oferta,
            location=offer.ubicacion,
            kind=kind,  # type: ignore[arg-type]
            score=match.score,
            threshold=gate,
            profile=profile,
            reason=reason,
            days_ago=max(0, (now.date() - analysed.date()).days),
        )

    @staticmethod
    def _below_reason(judgment: OfferJudgment | None) -> str:
        """Main reason for a below-threshold score: the judge's first rationale
        sentence, else its first gap, else its first soft wall, else empty."""
        if judgment is None:
            return ""
        if sentence := _first_sentence(judgment.rationale):
            return sentence
        if judgment.gaps:
            return judgment.gaps[0]
        return next((w.text for w in judgment.walls if not w.hard), "")

    def panel_html(
        self,
        rows: list[OfferRow] | None = None,
        discarded: list[DiscardedOffer] | None = None,
    ) -> str:
        """Render the ``/panel`` dashboard from the tracker rows.

        Pass ``rows`` (from :meth:`panel_rows`) and ``discarded`` (from
        :meth:`panel_discarded`) to run this CPU/IO part in a worker thread
        without sharing the SQLite connection across threads. Without
        ``discarded`` the audit cards are read here only when ``rows`` is too.

        Also drops a copy next to the synced tracker xlsx when that path is
        configured (best-effort: a write failure is logged, never raised).
        """
        if discarded is None:
            discarded = self.panel_discarded() if rows is None else []
        html_text = render_panel(
            self._repo.tracker_offers() if rows is None else rows,
            self._now(),
            discarded,
        )
        if self._tracker_xlsx is not None:
            copy = self._tracker_xlsx.parent / PANEL_FILENAME
            if not write_panel_atomic(html_text, copy):
                _logger.warning("panel copy skipped (could not write %s)", copy)
        return html_text

    async def run_due_beacons(self) -> SweepResult:
        """One heartbeat sweep: check every DUE beacon sequentially (design D11).

        Due = enabled + not paused + now ≥ stored ``next_check_after`` (never
        checked → immediately due). Caps: per-beacon ``checks_per_day`` skip
        until the next day; the global daily cap stops the sweep across beacons.
        Fetches are strictly sequential (single loop, one page per check); D3
        wall degradation lives inside :func:`check_beacon` (no retries here).
        """
        now = self._now()
        sweep = SweepResult()
        beacons = self._repo.list_beacons()
        global_done = sum(checks_done_today(beacon, now) for beacon in beacons)
        for beacon in beacons:
            if not _due(beacon, now):
                continue
            if checks_done_today(beacon, now) >= self._checks_per_day:
                continue
            if global_done >= self._global_daily_cap:
                break
            result = await check_beacon(
                beacon,
                self._fetcher,
                self._repo,
                now=now,
                interval_minutes=self._interval_minutes,
                jitter=self._jitter_fn,
            )
            global_done += 1
            sweep.notifications.extend(result.notifications)
            if result.paused_alert is not None:
                sweep.paused_alerts.append(result.paused_alert)
        return sweep

    @property
    def judge_persona(self) -> JudgePersona:
        """The judge's candidate-specific text (the owner's when no profile)."""
        return self._judge_persona

    @property
    def preferences(self) -> ProfilePrefs:
        """The profile's loaded ``preferences.toml`` (engine defaults when absent)."""
        return self._prefs

    @property
    def threshold(self) -> int:
        """Configured Top-Choice threshold (notification context, CLI parity)."""
        return self._threshold

    @property
    def repository(self) -> OfferRepository:
        """The tracker repository (wiring for the Gmail sync job)."""
        return self._repo

    # --- Gmail proposals (feature gmail-sync) -------------------------------

    def gmail_proposal(self, message_id: str) -> GmailProposalRow | None:
        """The stored proposal of a Gmail message (``None`` when unknown)."""
        return self._repo.get_gmail_proposal(message_id)

    def resolve_gmail_proposal(
        self, message_id: str, decision: str, *, expect: str | None = None
    ) -> bool:
        """Set a proposal's decision; with ``expect`` only from that state."""
        return self._repo.set_gmail_decision(message_id, decision, expect=expect)

    @property
    def analyze_retry_max(self) -> int:
        """Retry budget per auto-analysis (heartbeat sweep input, pacing D4)."""
        return self._analyze_retry_max

    def list_retry_pending(self, now_iso: str, max_attempts: int) -> list[OfferRow]:
        """Retry-pending offers due now (heartbeat sweep source, pacing D4)."""
        return self._repo.list_retry_pending(now_iso, max_attempts)

    def beacons(self) -> list[BeaconRow]:
        """All beacons in id order (bot /beacons, CLI ``beacon list``)."""
        return self._repo.list_beacons()

    def create_beacon(self, name: str, url: str) -> int:
        """Persist a new enabled beacon; return its id (job-discovery spec)."""
        return self._repo.create_beacon(name, url)

    def resume_beacon(self, beacon_id: int) -> None:
        """Resume a paused beacon: active + wall counter reset (D3); fails fast."""
        self._repo.get_beacon(beacon_id)
        self._repo.resume_beacon(beacon_id)

    def disable_beacon(self, beacon_id: int) -> None:
        """Disable a beacon: kept in the list, never checked again; fails fast."""
        self._repo.get_beacon(beacon_id)
        self._repo.disable_beacon(beacon_id)

    def _keyword_log(self, extraction: OfferExtraction) -> list[tuple[str, str]]:
        """Deduped (keyword, coverage-status) pairs for the §5 demand log."""
        pairs: list[tuple[str, str]] = []
        seen: set[str] = set()
        for keyword in extraction.keywords:
            stripped = keyword.strip()
            key = stripped.casefold()
            if stripped and key not in seen:
                seen.add(key)
                pairs.append(
                    (stripped, classify_keyword(stripped, self._db, self._syn))
                )
        return pairs
