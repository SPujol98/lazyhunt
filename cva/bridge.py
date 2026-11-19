"""Loopback offer bridge (beacon-bridge): extension POSTs → offers + cards.

A stdlib ``ThreadingHTTPServer`` in a daemon thread, bound to ``127.0.0.1``
only, receives the browser extension's new-jobs payloads and, per
job: derives the job id (D5), canonicalizes to the exact URL the Analyze flow
stores (D4), dedupes via ``find_offer_by_url``, and upserts a URL-only offer
row. With ``auto_analyze=false`` each new job gets the legacy new-job card
scheduled on the PTB loop captured in ``post_init`` (D2). With
``auto_analyze=true`` the card is suppressed and, AFTER the 200 response, the
full ``an:`` flow (``JobService.analyze_new_bridge_job``) is scheduled
fire-and-forget on that same loop (auto-triage D1/D2/D3): success sends the
breakdown card, any failure sends the fallback ``[Analizar]`` card — exactly
one card per new job, never both.

Lifecycle mirrors ``scheduler.register``: ``register(app, settings, service)``
is the single bot.py wiring point. Fail-safe defaults (D1/D6): unset secret or
unset owner → warning, no port bound, bot continues; bind failure → warning,
bot continues. Handler exceptions answer 500 and never kill the server. The
bridge never imports ``telegram`` — the card sender is duck-typed and the
card builders come from the pure notifier.

Safe defaults (kill switch + portal opt-in). ``GET /config`` (secret-protected)
returns ``{"paused": bool, "enabledPortals": [portal ids]}`` so the extension
can follow the bot: ``paused`` is the persistent global pause (Telegram
``/pausa`` / ``/reanudar``, ``app_state``; ``Settings.paused`` is only its
default until the toggle is used) and ``enabledPortals`` is
``Settings.enabled_portals`` (unset = every registered portal). While paused,
``POST /bridge`` answers 503 (nothing stored) and queued auto-analyses are
deferred to the retry sweep; ``/scan-report`` and the GETs keep answering. A
job from a portal that is not enabled makes the payload a 400 naming it.
"""

import asyncio
import atexit
import hmac
import io
import json
import logging
import sqlite3
import threading
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal, get_args

from cva.beacons.health import AlertClaims, Claim
from cva.config import Settings
from cva.families import BUILTIN_REGISTRY, FamilyRegistry
from cva.i18n import LEGACY_LANG, Lang, t
from cva.ingest import canonical_url, job_id_from_url, source_from_url
from cva.ingest.closed import closed_marker
from cva.ingest.dates import extract_dates
from cva.ingest.dedup import dedup_key
from cva.ingest.posting_guard import clean_text
from cva.ingest.sources import SOURCES, is_inline_source, source_label
from cva.notifier import (
    ClosedOffer,
    DiscardedOffer,
    DuplicateOffer,
    SentMessage,
    WalledOffer,
    canonical_job_url,
    keyboard_from_json,
    new_job_keyboard,
    new_job_message,
    round_summary_message,
    with_also_on,
)
from cva.profile_paths import resolve_profile_paths
from cva.profile_prefs import ProfilePrefs, TitleFilterPrefs, load_preferences
from cva.service import (
    DEFAULT_DB_PATH,
    BridgeJobOutcome,
    BridgeZone,
    display_reason,
)
from cva.suggest import suggested_searches_payload
from cva.title_filter import TitleFilter
from cva.tracker import OfferInput, OfferRepository, RepositoryError, duplicate_note
from cva.tracker.repo import OfferRow, ScanStatus

if TYPE_CHECKING:
    from cva.service import JobService

_LOG = logging.getLogger(__name__)

BRIDGE_PATH: Final[str] = "/bridge"
# Per-scan health report from the extension (offer-gates-and-health T5): a
# separate endpoint because /bridge only fires for scans that found NEW jobs,
# and a scan that finds nothing is exactly what health monitoring needs to see.
SCAN_REPORT_PATH: Final[str] = "/scan-report"
# Profile-derived search suggestions for the extension options page (GET only).
SUGGESTED_SEARCHES_PATH: Final[str] = "/searches/suggested"
# Pause + enabled portals for the extension to sync from (GET only).
CONFIG_PATH: Final[str] = "/config"
SECRET_HEADER: Final[str] = "X-CVA-Bridge-Secret"
MAX_BODY_BYTES: Final[int] = 256 * 1024
MAX_DRAIN_BYTES: Final[int] = 16 * 1024 * 1024
_DRAIN_CHUNK_BYTES: Final[int] = 64 * 1024
BOT_DATA_KEY: Final[str] = "bridge"


# Round-summary reason of a walled offer that came without one.
_WALL: Final[str] = "muro"


class _RoundTally:
    """Per-payload round-summary accumulator (round-summary amendment).

    The POST handler creates ONE tally per auto-mode payload (it is the only
    place that knows the batch size); the single worker folds each settled
    item in and closes the round when the last item finishes — AFTER that
    item's pacing delay. Single-writer after construction: only the worker
    mutates counters, so no cross-thread coordination is needed.
    """

    __slots__ = (
        "above",
        "beacon",
        "below",
        "closed",
        "closed_offers",
        "discarded",
        "duplicates",
        "gray",
        "lang",
        "pending",
        "remaining",
        "walled",
        "walled_offers",
    )

    def __init__(self, beacon: str, total: int, lang: Lang = LEGACY_LANG) -> None:
        self.beacon = beacon
        self.lang: Lang = lang  # Settings.ui_language of the round summary
        self.remaining = total
        self.above = 0
        self.gray = 0
        self.below = 0
        self.walled = 0
        self.walled_offers: list[WalledOffer] = []
        self.closed = 0
        self.closed_offers: list[ClosedOffer] = []
        self.pending = 0
        self.discarded: list[DiscardedOffer] = []
        self.duplicates: list[DuplicateOffer] = []

    def record(
        self,
        zone: BridgeZone,
        score: int | None,
        title: str | None,
        company: str | None,
        url: str | None,
        wall_reason: str | None = None,
    ) -> None:
        """Fold one settled item into the counters (zone from the service).

        ``url`` (match-calibration D4) is the canonical offer URL, stored per
        discarded offer so the round summary can render it as a clickable
        link.
        """
        if zone == "above":
            self.above += 1
        elif zone == "gray":
            self.gray += 1
        elif zone == "below":
            self.below += 1
            self.discarded.append(
                DiscardedOffer(score if score is not None else 0, title, company, url)
            )
        elif zone == "walled":
            self.walled += 1
            self.walled_offers.append(
                WalledOffer(wall_reason or _WALL, title, company, url)
            )
        elif zone == "closed":
            self.closed += 1
            self.closed_offers.append(ClosedOffer(title, company, url))
        else:  # failure: retry-pending or exhausted — both ♻️ for the owner
            self.pending += 1


# One paced-queue item (notification-pacing D1): offer row id, job id, the
# raw payload job (title/company context), the drain kind, and the payload's
# round tally (None for manual/sweep enqueues — those never summarize).
Item = tuple[int, str, dict[str, Any], Literal["new", "retry"], "_RoundTally | None"]


async def _pacing_sleep(seconds: float) -> None:
    """Pacing wait between consecutive queued analyses (D2).

    Module-level indirection so tests can patch it: production awaits
    :func:`asyncio.sleep`; pacing tests inject a recorder instead.
    """
    await asyncio.sleep(seconds)


def _drain(rfile: io.BufferedIOBase, n: int) -> bool:
    """Consume up to ``n`` request-body bytes before an early error response.

    Windows fix: answering 413 without reading the declared body and closing
    the socket makes the peer's TCP stack reset the connection while the
    client is still sending, so the client sees a connection abort (httpx
    ``WinError 10053``) instead of the response. Draining first makes the
    order deterministic: the client finishes sending, then reads the 413.

    The drain is capped at ``MAX_DRAIN_BYTES`` so a hostile ``Content-Length``
    cannot pin a handler thread reading forever; past the cap the connection
    is closed anyway. Returns ``True`` when the (capped) body was fully
    consumed. Returns ``False`` when the client sent less than declared or
    hung up mid-drain: the socket is dead, so no response can be delivered —
    just close (logged at DEBUG).
    """
    remaining = min(n, MAX_DRAIN_BYTES)
    try:
        while remaining > 0:
            chunk = rfile.read(min(remaining, _DRAIN_CHUNK_BYTES))
            if not chunk:
                return False
            remaining -= len(chunk)
    except OSError as exc:
        _LOG.debug("bridge body drain interrupted (%s); closing", exc)
        return False
    return True


class _BadRequest(ValueError):
    """Payload rejected with 400 before any ingest (atomic validation)."""


MAX_INLINE_TEXT_CHARS: Final[int] = 20000


def _validated(
    payload: Any,
    dropped: list[str] | None = None,
    enabled_portals: frozenset[str] | None = None,
    *,
    families: FamilyRegistry = BUILTIN_REGISTRY,
) -> tuple[str, list[dict[str, Any]]]:
    """Validate the whole payload up front; every job must be processable.

    Multi-source (infojobs-source D4): any URL whose ``source_from_url`` is
    known is accepted; unknown domains are 400. Optional per-job ``text``:
    string, non-empty after strip, ≤ ``MAX_INLINE_TEXT_CHARS`` — stored
    stripped. Inline-only portals REQUIRE the inline text (the server-side
    fetch is dead — challenge shell): a job of such a portal without text is
    dropped on its own (its URL appended to ``dropped``, a warning logged) so
    it never blocks the rest of the payload; any other violation is a 400.
    ``enabled_portals`` (None = every registered portal) rejects, with a 400
    naming the portal, any job whose portal is not enabled.
    """
    if not isinstance(payload, dict):
        raise _BadRequest("payload must be a JSON object")
    beacon = payload.get("beacon")
    if not isinstance(beacon, str) or not beacon:
        raise _BadRequest("'beacon' must be a non-empty string")
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise _BadRequest("'jobs' must be a list")
    # backend-profile / role-families T5: the beacon's filter profile (a
    # family id of the profile's families) applies to every job in the payload
    # unless its title matches a family's title terms; unknown/missing -> the
    # default family. Stamped on each job so the 2-tuple return shape (and its
    # callers) stays unchanged.
    profile = payload.get("profile")
    kept: list[dict[str, Any]] = []
    for job in jobs:
        if not isinstance(job, dict):
            raise _BadRequest("each job must be an object")
        for field in ("title", "company", "location"):
            value = job.get(field)
            if value is not None and not isinstance(value, str):
                raise _BadRequest(f"'{field}' must be a string or null")
        # A title matching a family's title terms wins over the beacon (built-
        # ins: an AI title beats a backend beacon, whose filter passes any
        # "Engineer"); otherwise the beacon's profile applies. An 'ai' beacon
        # already requires an AI term in the extension filter, so its payload
        # profile is kept even when the title shows none.
        job["profile"] = families.route(profile, job.get("title"))
        url = job.get("url")
        if not isinstance(url, str) or source_from_url(url) is None:
            raise _BadRequest(f"'url' must be a {_portal_label()} job URL")
        if job_id_from_url(url) is None:
            raise _BadRequest(
                f"'url' must be a {_portal_label()} job URL with a job id"
            )
        job_source = source_from_url(url)
        if (
            enabled_portals is not None
            and job_source is not None
            and job_source not in enabled_portals
        ):
            raise _BadRequest(
                f"portal '{job_source}' is not enabled "
                "(add it to enabled_portals in the bot config)"
            )
        text = job.get("text")
        if text is not None:
            if not isinstance(text, str) or not text.strip():
                raise _BadRequest("'text' must be a non-empty offer text")
            if len(text.strip()) > MAX_INLINE_TEXT_CHARS:
                raise _BadRequest(
                    f"'text' must be at most {MAX_INLINE_TEXT_CHARS} chars"
                )
            job["text"] = text.strip()
        source = source_from_url(url)
        if is_inline_source(source) and (
            not isinstance(job.get("text"), str) or not job["text"].strip()
        ):
            _LOG.warning(
                "bridge dropped a %s job without inline offer text: %s", source, url
            )
            if dropped is not None:
                dropped.append(url)
            continue
        kept.append(job)
    return beacon, kept


# One definition of the statuses: the ScanStatus model the history is stored with.
_SCAN_STATUSES: Final[tuple[str, ...]] = get_args(ScanStatus)
_SCAN_STATUS_CHOICES: Final[str] = (
    f"{', '.join(_SCAN_STATUSES[:-1])} or {_SCAN_STATUSES[-1]}"
)
_MAX_BEACON_ID_CHARS: Final[int] = 100
_MAX_BEACON_NAME_CHARS: Final[int] = 200
# A search page holds a few dozen cards; the cap keeps hostile values storable.
_MAX_SCAN_COUNT: Final[int] = 10_000
# The extension's clock is only trusted up to this far ahead of the server's.
_MAX_CLOCK_SKEW_AHEAD: Final[timedelta] = timedelta(days=1)


def _count(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _MAX_SCAN_COUNT
    ):
        raise _BadRequest(f"'{field}' must be an integer from 0 to {_MAX_SCAN_COUNT}")
    return value


def _scan_time(raw: Any, now: datetime) -> str:
    """The reported scan time as a UTC ISO string, never ahead of ``now`` + a day.

    ``at`` comes from the extension's clock: a time more than a day ahead of the
    server's is replaced by ``now`` (it is informational; history order is the
    insertion order).
    """
    message = "'at' must be an ISO-8601 timestamp"
    if not isinstance(raw, str):
        raise _BadRequest(message)
    try:
        moment = datetime.fromisoformat(raw)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        if moment > now + _MAX_CLOCK_SKEW_AHEAD:
            moment = now
        return moment.astimezone(UTC).isoformat(timespec="milliseconds")
    except (ValueError, OverflowError):  # unparsable, or outside the UTC range
        raise _BadRequest(message) from None


def _validated_scan(payload: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Validate one scan report; ``at`` is normalized to a UTC ISO string.

    Every field is type-checked before it is compared or stored, so a wrong JSON
    type is a 400 (``_BadRequest``), never an unhandled error.
    """
    if not isinstance(payload, dict):
        raise _BadRequest("payload must be a JSON object")
    for field, limit in (
        ("beacon_id", _MAX_BEACON_ID_CHARS),
        ("beacon_name", _MAX_BEACON_NAME_CHARS),
    ):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise _BadRequest(f"'{field}' must be a non-empty string (max {limit})")
    status = payload.get("status")
    if not isinstance(status, str) or status not in _SCAN_STATUSES:
        raise _BadRequest(f"'status' must be {_SCAN_STATUS_CHOICES}")
    at = _scan_time(payload.get("at"), now or datetime.now(tz=UTC))
    jobs_found = _count(payload, "jobs_found")
    empty_company = _count(payload, "empty_company")
    if empty_company > jobs_found:
        raise _BadRequest("'empty_company' cannot exceed 'jobs_found'")
    return {
        "beacon_id": payload["beacon_id"],
        "beacon_name": payload["beacon_name"].strip(),
        "at": at,
        "status": status,
        "jobs_found": jobs_found,
        "empty_company": empty_company,
    }


def _record_scan(
    report: dict[str, Any],
    repo: OfferRepository,
    claims: AlertClaims,
    lang: Lang = LEGACY_LANG,
) -> list[Claim]:
    """Store the scan and claim the health alerts it makes due (send them after)."""
    repo.record_beacon_scan(**report)
    beacon_id = report["beacon_id"]
    return claims.claim(
        beacon_id, report["beacon_name"], repo.recent_beacon_scans(beacon_id), lang
    )


def _portal_label() -> str:
    """Registered portal ids for the 400 messages (``linkedin, infojobs, ...``)."""
    return "supported portal (" + ", ".join(SOURCES) + ")"


def _guarded_inline_text(raw: object) -> str | None:
    """Guarded inline offer text, or ``None`` when there is none to keep.

    Empty-after-guard rule: text that is absent, blank, or consists only of
    hidden/invisible characters counts as NO inline text, so the offer takes
    the URL-only note path (``source="link"``) instead of storing an empty
    body. The early stored row never carries hidden characters; the service
    re-guards and reports the payload text.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    return clean_text(raw.strip()).text.strip() or None


# Upper bound on one silent "También en" card edit (blocks the bridge POST).
_EDIT_TIMEOUT_S: Final[float] = 10.0


def _update_also_on(
    repo: OfferRepository, original_id: int, editor: Any, lang: Lang = LEGACY_LANG
) -> None:
    """Silently refresh the "También en" line on the original offer's card.

    No stored card → nothing to edit. Telegram errors (message too old,
    deleted, not modified) are logged and swallowed: a card edit never breaks
    the payload.
    """
    try:
        card = repo.get_card(original_id)
        if card is None:
            return
        text = with_also_on(
            card.text,
            repo.duplicate_portal_labels(original_id),
            parse_mode=card.parse_mode,
            lang=lang,
        )
        if text == card.text:
            return
        editor(
            card.chat_id,
            card.message_id,
            text,
            card.parse_mode,
            keyboard_from_json(card.keyboard),
        )
        repo.update_card_text(original_id, text)
    except Exception as exc:  # noqa: BLE001 — a card edit never breaks the POST
        _LOG.warning("bridge also-on edit failed for offer #%s: %s", original_id, exc)


def _clean_location(value: object) -> str | None:
    """The portal location field, stripped and capped; ``None`` when blank."""
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:200]


# A closed-discarded offer listed again WITHOUT inline text (LinkedIn) is only
# reconsidered once it has been closed this long, so a portal that keeps listing
# a dead offer does not cause a fetch/analyze loop every round.
REOPEN_AFTER_DAYS: Final[int] = 3
# With inline text the text decides, but a portal snippet can keep looking open
# while the full page says closed. A closure therefore cannot be reopened the
# same day: at most one reopen -> analyse -> re-close cycle per day.
REOPEN_INLINE_MIN_DAYS: Final[int] = 1


def _should_reopen(
    repo: OfferRepository, existing: OfferRow, inline_text: str | None
) -> bool:
    """Whether a job at an already-stored URL is a closed offer that came back.

    Only closed-discarded offers qualify (never sent / outcome / manually
    discarded ones). With inline text the text itself decides (a closed marker
    keeps it closed) but waits ``REOPEN_INLINE_MIN_DAYS`` since the closure so a
    snippet that looks open cannot loop reopen/close every round; URL-only jobs
    wait ``REOPEN_AFTER_DAYS``.
    """
    if not repo.is_closed_discarded(existing):
        return False
    days = repo.days_since_closed(existing.id)
    if days is None:
        return False
    if inline_text is not None:
        return closed_marker(inline_text) is None and days >= REOPEN_INLINE_MIN_DAYS
    return days >= REOPEN_AFTER_DAYS


def _process_payload(
    beacon: str,
    jobs: list[dict[str, Any]],
    *,
    repo: OfferRepository,
    sender: Any,
    threshold: int,
    auto_jobs: list[tuple[int, str, dict[str, Any]]] | None = None,
    duplicates: list[DuplicateOffer] | None = None,
    editor: Any = None,
    families: FamilyRegistry = BUILTIN_REGISTRY,
    lang: Lang = LEGACY_LANG,
) -> dict[str, int]:
    """Dedupe + upsert + notify per job (D3/D4); returns the 200 counters.

    Multi-source (infojobs-source D2/D4): the canonical URL comes from the
    per-source registry (LinkedIn id-derived shape unchanged; InfoJobs =
    the payload URL normalized) and dedupe hits that canonical. When a job
    carries inline ``text`` the upsert stores the REAL text with the
    ``bridge-inline`` marker (origen = the portal id of the URL host); otherwise the legacy
    URL-only note with ``source="link"``.

    Legacy mode (``auto_jobs is None``): each new job gets the new-job card
    through ``sender`` inline. Auto mode (auto-triage D3): the card send is
    REPLACED by collecting ``(offer_id, job_id, job)`` for post-200
    fire-and-forget analysis — the two cards are mutually exclusive here.

    Cross-source dedup (offer-gates-and-health T4): a new URL whose
    company+title key matches an offer first seen in the last 30 days at
    another URL is stored as ``descartada`` with a "duplicada de #<id>" note —
    never analyzed, never carded — and appended to ``duplicates`` for the round
    summary. It counts as ``skipped``. When ``editor`` is given, the original
    offer's stored card is edited in place with a "También en" line.
    """
    received, new, skipped = len(jobs), 0, 0
    for job in jobs:
        url = job["url"]
        source = source_from_url(url)
        job_id = job_id_from_url(url)
        if source is None or job_id is None:  # pragma: no cover — _validated
            raise _BadRequest(f"'url' must be a {_portal_label()} job URL")
        canonical = canonical_url(source, job_id, original=url)
        inline_text = _guarded_inline_text(job.get("text"))
        existing = repo.find_offer_by_url(canonical)
        reopen = existing is not None and _should_reopen(repo, existing, inline_text)
        if existing is not None and not reopen:
            skipped += 1
            _LOG.debug("bridge duplicate skipped: job_id=%s url=%s", job_id, canonical)
            continue
        dates = extract_dates(inline_text or "", datetime.now(tz=UTC).date())
        # A reopened offer is checked too: a live sibling at another URL makes
        # it a duplicate (it is reopened, then re-discarded as "duplicada de").
        original = repo.find_duplicate(
            dedup_key(job.get("company"), job.get("title")), canonical
        )
        offer_id = repo.upsert_offer(
            OfferInput(
                job_id=job_id,
                title=job.get("title"),
                company=job.get("company"),
                url=canonical,
                text=(
                    inline_text
                    if inline_text is not None
                    else f"bridge: {beacon}\n{canonical}"
                ),
                source="bridge-inline" if inline_text is not None else "link",
                profile=families.normalize(job.get("profile")),
                fecha_publicacion=dates.publicacion,
                fecha_limite=dates.limite,
                location=_clean_location(job.get("location")),
            )
        )
        if reopen:
            repo.reopen_closed(offer_id, source_label(source))
            _LOG.info("bridge reopened closed offer #%s (%s)", offer_id, canonical)
        if original is not None:
            repo.set_estado(offer_id, "descartada")
            repo.append_note(offer_id, duplicate_note(original.id))
            if duplicates is not None:
                duplicates.append(
                    DuplicateOffer(
                        job.get("title"), job.get("company"), original.id, canonical
                    )
                )
            if editor is not None:
                _update_also_on(repo, original.id, editor, lang)
            skipped += 1
            _LOG.info(
                "bridge cross-source duplicate: #%s duplicates #%s",
                offer_id,
                original.id,
            )
            continue
        if auto_jobs is not None:
            auto_jobs.append((offer_id, job_id, job))
        else:
            sender(
                new_job_message(
                    title=job.get("title"),
                    company=job.get("company"),
                    age=t("notifier.age_recent", lang),
                    job_id=job_id,
                    threshold=threshold,
                    url=canonical,
                    lang=lang,
                ),
                new_job_keyboard(job_id, lang=lang),
            )
        new += 1
    _LOG.info(
        "bridge payload from beacon %r: received=%d new=%d skipped=%d",
        beacon,
        received,
        new,
        skipped,
    )
    return {"received": received, "new": new, "skipped": skipped}


def _title_filter_json(filt: TitleFilter) -> dict[str, Any]:
    """A title filter in the extension's shape (``TitleFilterPrefs.to_json``)."""
    return {
        "enabled": filt.enabled,
        "includeGroups": [list(g) for g in filt.include_groups],
        "excludeTerms": list(filt.exclude_terms),
    }


def families_payload(
    families: FamilyRegistry, title_filters: TitleFilterPrefs | None = None
) -> list[dict[str, Any]] | None:
    """The profile's role families for the extension (role-families T6).

    ``[{id, label, default, titleFilter}]`` in declared order, served with the
    suggested searches next to ``titleFilters``. ``None`` when the list is the
    built-in ``ai``/``backend`` pair (same ids, labels and default) whose title
    filters are the ones ``[title_filters]`` already serves: the extension falls
    back to it, so a default profile's response stays byte-identical. A built-in
    family with its own ``title_filter`` is served (T6 verifier advisory 3).
    """

    def head(registry: FamilyRegistry) -> list[tuple[str, str, bool]]:
        default = registry.default.id
        return [(f.id, f.label, f.id == default) for f in registry.families]

    expected = title_filters if title_filters is not None else TitleFilterPrefs()
    filters_as_served = all(
        f.title_filter == (expected.ai if f.id == "ai" else expected.backend)
        for f in families.families
    )
    if head(families) == head(BUILTIN_REGISTRY) and filters_as_served:
        return None
    return [
        {
            "id": fid,
            "label": label,
            "default": is_default,
            "titleFilter": _title_filter_json(family.title_filter),
        }
        for (fid, label, is_default), family in zip(
            head(families), families.families, strict=True
        )
    ]


def _make_handler(
    *,
    secret: str,
    repo: OfferRepository,
    sender: Any,
    threshold: int,
    lock: threading.Lock,
    schedule_auto: Callable[[int, str, dict[str, Any], _RoundTally | None], None]
    | None = None,
    round_summary: bool = True,
    editor: Any = None,
    claims_path: Path | None = None,
    preferences: ProfilePrefs | None = None,
    is_paused: Callable[[], bool] = lambda: False,
    enabled_portals: frozenset[str] | None = None,
    lang: Lang = LEGACY_LANG,
) -> type[BaseHTTPRequestHandler]:
    """Handler class closed over the bridge collaborators (no globals)."""
    claims = AlertClaims(repo)  # health alerts in flight (guarded by ``lock``)
    # The profile's role families route beacon offers (role-families T5).
    families = preferences.families if preferences is not None else BUILTIN_REGISTRY

    def _suggested_body() -> dict[str, Any]:
        """Suggested searches; plus the profile's title filters when it has its own."""
        prefs = preferences if preferences is not None else ProfilePrefs()
        body: dict[str, Any] = {
            "searches": suggested_searches_payload(
                claims_path, prefs.search_targets, enabled_portals
            )
        }
        if prefs.title_filters != TitleFilterPrefs():
            body["titleFilters"] = prefs.title_filters.to_json()
        served = families_payload(prefs.families, prefs.title_filters)
        if served is not None:
            body["families"] = served
        return body

    def _config_body() -> dict[str, Any]:
        """What the extension syncs every round: the pause, the resolved enabled
        portals and, when the profile declares its own, the role families."""
        body: dict[str, Any] = {
            "paused": is_paused(),
            "enabledPortals": [
                p for p in SOURCES if enabled_portals is None or p in enabled_portals
            ],
        }
        served = families_payload(
            families,
            preferences.title_filters if preferences is not None else None,
        )
        if served is not None:
            body["families"] = served
        return body

    class _Handler(BaseHTTPRequestHandler):
        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            _LOG.debug("bridge http: " + format, *args)

        def _authorized(self) -> bool:
            provided = self.headers.get(SECRET_HEADER)
            return provided is not None and hmac.compare_digest(
                provided.encode("utf-8"), secret.encode("utf-8")
            )

        def do_GET(self) -> None:
            if self.path in (SUGGESTED_SEARCHES_PATH, CONFIG_PATH):
                try:
                    if not self._authorized():
                        self._send_json(401, {"error": "unauthorized"})
                    elif self.path == CONFIG_PATH:
                        self._send_json(200, _config_body())
                    else:
                        self._send_json(200, _suggested_body())
                except Exception:  # noqa: BLE001 — the 500 IS the surface
                    _LOG.exception("bridge handler error")
                    self._send_json(500, {"error": "internal error"})
            elif self.path not in (BRIDGE_PATH, SCAN_REPORT_PATH):
                self._send_json(404, {"error": "not found"})
            else:
                self._send_json(405, {"error": "method not allowed"})

        def do_POST(self) -> None:
            # Per-request guard: any exception answers 500 and the server
            # (daemon, threading) keeps serving (spec: bot never dies).
            try:
                self._handle_post()
            except Exception:  # noqa: BLE001 — the 500 IS the surface
                _LOG.exception("bridge handler error")
                self._send_json(500, {"error": "internal error"})

        def _handle_scan_report(self, body: bytes) -> None:
            try:
                report = _validated_scan(json.loads(body))
            except _BadRequest as exc:
                self._send_json(400, {"error": str(exc)})
                return
            except ValueError as exc:
                self._send_json(400, {"error": f"invalid json: {exc}"})
                return
            with lock:
                due = _record_scan(report, repo, claims, lang)
            try:
                self._send_json(200, {"recorded": True})
                for claim in due:
                    try:
                        sender(claim.alert.message, None)
                    except Exception:  # noqa: BLE001 — released below, retried next scan
                        _LOG.warning("beacon health alert failed to send")
                        continue
                    with lock:
                        claims.confirm(claim)
            finally:
                # Whatever was not confirmed (failed send, or an exception before
                # it) gives its claim back, so no alert is held forever.
                with lock:
                    for claim in due:
                        claims.release(claim)

        def _handle_post(self) -> None:
            # Order (design Interfaces): 404 path → 405 method → 401 auth →
            # 413 size → 400 JSON/schema → process.
            if self.path not in (BRIDGE_PATH, SCAN_REPORT_PATH):
                self._send_json(404, {"error": "not found"})
                return
            provided = self.headers.get(SECRET_HEADER)
            if provided is None or not hmac.compare_digest(
                provided.encode("utf-8"), secret.encode("utf-8")
            ):
                self._send_json(401, {"error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._send_json(400, {"error": "invalid content length"})
                return
            if length > MAX_BODY_BYTES:
                # Windows RST fix: drain the declared body BEFORE answering.
                # Replying 413 + early close while the peer is still sending
                # resets the connection on Windows and the client never reads
                # the 413 (httpx WinError 10053). See _drain.
                if _drain(self.rfile, length):
                    self._send_json(413, {"error": "payload too large"})
                return  # failed drain: socket is dead, just close
            body = self.rfile.read(length)
            if self.path == SCAN_REPORT_PATH:
                self._handle_scan_report(body)
                return
            if is_paused():
                # Kill switch: the body is already consumed (no RST on the
                # peer); nothing is validated, stored or analyzed.
                self._send_json(
                    503,
                    {
                        "error": "bot paused via /pausa; "
                        "send /reanudar in Telegram to resume"
                    },
                )
                return
            dropped: list[str] = []
            try:
                beacon, jobs = _validated(
                    json.loads(body), dropped, enabled_portals, families=families
                )
            except _BadRequest as exc:
                self._send_json(400, {"error": str(exc)})
                return
            except ValueError as exc:
                self._send_json(400, {"error": f"invalid json: {exc}"})
                return
            # D3: dedupe+upsert+notify atomic per payload under the lock.
            auto_jobs: list[tuple[int, str, dict[str, Any]]] = []
            dupes: list[DuplicateOffer] = []
            with lock:
                result = _process_payload(
                    beacon,
                    jobs,
                    repo=repo,
                    sender=sender,
                    threshold=threshold,
                    auto_jobs=auto_jobs if schedule_auto is not None else None,
                    duplicates=dupes,
                    editor=editor,
                    families=families,
                    lang=lang,
                )
            if dropped:
                result["dropped"] = len(dropped)
            self._send_json(200, result)
            # D2: analysis is scheduled strictly AFTER the 200 is on the wire;
            # a slow or failing analysis can never delay or time out a POST.
            if schedule_auto is not None:
                # Round-summary amendment: ONE tally per payload batch —
                # created here because only the handler knows the count;
                # 0 new jobs → no tally → no summary, ever.
                tally = (
                    _RoundTally(beacon, len(auto_jobs), lang)
                    if round_summary and auto_jobs
                    else None
                )
                if tally is not None:
                    tally.duplicates = dupes
                for offer_id, job_id, job in auto_jobs:
                    schedule_auto(offer_id, job_id, job, tally)

    return _Handler


class _Bridge:
    """One bound server + its own sqlite connection and cross-thread sender."""

    def __init__(
        self,
        *,
        secret: str,
        port: int,
        owner_id: int,
        threshold: int,
        auto_analyze: bool = False,
        service: "JobService | None" = None,
        analyze_delay_seconds: int = 0,
        notify_min_score: int | None = None,
        round_summary: bool = True,
        claims_path: Path | None = None,
        preferences: ProfilePrefs | None = None,
        paused_default: bool = False,
        enabled_portals: frozenset[str] | None = None,
        ui_language: Lang = LEGACY_LANG,
    ) -> None:
        self._lang: Lang = ui_language
        self._paused_default = paused_default
        self._enabled_portals = enabled_portals
        self._claims_path = claims_path
        self._preferences = preferences
        self._secret = secret
        self._port = port
        self._owner_id = owner_id
        self._threshold = threshold
        self._auto_analyze = auto_analyze
        self._service = service
        self._delay = analyze_delay_seconds
        self._notify_min_score = notify_min_score
        self._round_summary = round_summary
        # D3: own connection (the service one is main-thread-bound);
        # check_same_thread=False because handlers run on server threads.
        DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(DEFAULT_DB_PATH, check_same_thread=False)
        self._repo = OfferRepository(self._conn)
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bot: Any = None
        self._httpd: ThreadingHTTPServer | None = None
        # Paced queue (notification-pacing D1): created + drained in post_init,
        # when the PTB loop finally exists.
        self._queue: asyncio.Queue[Item] | None = None
        self._worker: asyncio.Task[None] | None = None
        self._in_flight: int | None = None
        # Set by shutdown(): puts that land afterwards defer instead of queueing.
        self._closing = False

    @property
    def port(self) -> int:
        if self._httpd is None:
            raise RuntimeError("bridge server is not running")
        return int(self._httpd.server_address[1])

    def start(self) -> None:
        handler = _make_handler(
            secret=self._secret,
            repo=self._repo,
            sender=self._send_card,
            threshold=self._threshold,
            lock=self._lock,
            schedule_auto=(
                self._schedule_auto_analysis if self._auto_analyze else None
            ),
            round_summary=self._round_summary,
            editor=self._edit_card,
            claims_path=self._claims_path,
            preferences=self._preferences,
            is_paused=self._is_paused,
            enabled_portals=self._enabled_portals,
            lang=self._lang,
        )
        self._httpd = ThreadingHTTPServer(("127.0.0.1", self._port), handler)
        thread = threading.Thread(
            target=self._httpd.serve_forever, name="cva-bridge", daemon=True
        )
        thread.start()

    def _is_paused(self) -> bool:
        """Persistent kill switch, read through the bridge's own connection
        (the Telegram toggle writes ``app_state``; the config value is only
        the default until it is used)."""
        with self._lock:
            return self._repo.is_paused(self._paused_default)

    async def post_init(self, app: Any) -> None:
        """PTB public post_init hook (D2): capture loop + bot for the sender.

        Assigned post-construction by :func:`register`; ``run_polling`` awaits
        it after initialization, when the loop finally exists. Also creates
        the paced queue and starts the ONE worker task on this loop (D1).
        """
        self._loop = asyncio.get_running_loop()
        self._bot = app.bot
        self._queue = asyncio.Queue()
        self._worker = asyncio.create_task(
            self._drain_queue(), name="cva-bridge-worker"
        )

    def _send_card(self, message: str, keyboard: Any) -> None:
        """Schedule the card on the PTB loop (D2); blocks until delivered."""
        if self._loop is None or self._bot is None:
            raise RuntimeError("bridge not initialized: post_init has not run")
        asyncio.run_coroutine_threadsafe(
            self._bot.send_message(self._owner_id, message, reply_markup=keyboard),
            self._loop,
        ).result()

    def _edit_card(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode: str | None,
        keyboard: Any,
    ) -> None:
        """Edit a sent card in place on the PTB loop (silent: no notification).

        Re-sends the keyboard: an edit without ``reply_markup`` would strip the
        card's buttons. Waits at most ``_EDIT_TIMEOUT_S`` for Telegram so a hung
        edit can't hold the POST; errors (timeout included) propagate to the
        caller, which logs them and retries on the next duplicate.
        """
        if self._loop is None or self._bot is None:
            raise RuntimeError("bridge not initialized: post_init has not run")
        asyncio.run_coroutine_threadsafe(
            self._bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=keyboard,
            ),
            self._loop,
        ).result(timeout=_EDIT_TIMEOUT_S)

    async def _notify_card(
        self, message: str, keyboard: Any, *, parse_mode: str | None = None
    ) -> SentMessage | None:
        """Send one card from INSIDE the loop coroutine (auto-analysis path).

        ``parse_mode`` (match-calibration D4) is forwarded to ``send_message``
        only when set, so every card other than the round summary stays a
        byte-identical call — the ``notify`` callable type
        (``Callable[[str, Any], Awaitable[SentMessage | None]]``) still holds for callers
        that never pass it.
        """
        if self._bot is None:
            raise RuntimeError("bridge not initialized: post_init has not run")
        kwargs: dict[str, Any] = {"reply_markup": keyboard}
        if parse_mode is not None:
            kwargs["parse_mode"] = parse_mode
        sent = await self._bot.send_message(self._owner_id, message, **kwargs)
        message_id = getattr(sent, "message_id", None)
        if not isinstance(message_id, int):
            return None  # fakes / transports that report no message
        chat_id = getattr(sent, "chat_id", None)
        return SentMessage(
            chat_id if isinstance(chat_id, int) else self._owner_id, message_id
        )

    async def _auto_analyze_job(
        self,
        offer_id: int,
        job_id: str,
        job: dict[str, Any],
        tally: _RoundTally | None = None,
    ) -> None:
        """The ONE coroutine the worker runs per queued job (pacing D4/D5).

        The service owns the whole card tree now: success above the gate →
        breakdown card; failure → retry-pending, or the single final fallback
        card once the attempt budget is exhausted. This seam only forwards
        the call and LOGs any escape (the worker backstop must never be
        silent) — it never cards by itself.
        """
        if self._service is None:  # pragma: no cover — register always wires it
            raise RuntimeError("bridge auto-analyze misconfigured: no service")
        # Per-source canonical (infojobs-source): the raw payload url decides
        # the source; a LinkedIn payload canonicalizes to the id-derived shape
        # (identical to the legacy canonical_job_url), an InfoJobs payload to
        # the normalized original. Sweep-driven retries pass the STORED
        # canonical as url — normalizing it is a no-op for both sources.
        raw_url = str(job.get("url") or "")
        source = source_from_url(raw_url)
        canonical = (
            canonical_url(source, job_id, original=raw_url)
            if source is not None
            else canonical_job_url(job_id)
        )
        try:
            outcome = await self._service.analyze_new_bridge_job(
                offer_id,
                # The CANONICAL url (D4), never the raw payload url: the
                # re-ingest upsert must hit the row just created so
                # source_data is refreshed on offer_id, not duplicated.
                url=canonical,
                title=job.get("title"),
                company=job.get("company"),
                notify=self._notify_card,
                min_score=self._notify_min_score,
                # Inline durability (infojobs-source D5): present only for
                # infojobs payloads — the service upserts it without fetching.
                text=job.get("text"),
            )
        except Exception as exc:  # noqa: BLE001 — chat surfaces live in the service
            _LOG.warning("bridge auto-analysis failed for offer #%s: %s", offer_id, exc)
            outcome = BridgeJobOutcome(zone="pending", score=None)
        if tally is not None:
            tally.record(
                outcome.zone,
                outcome.score,
                job.get("title"),
                job.get("company"),
                canonical,
                outcome.reason,
            )

    def _schedule_auto_analysis(
        self,
        offer_id: int,
        job_id: str,
        job: dict[str, Any],
        tally: _RoundTally | None = None,
    ) -> None:
        """Handler seam: new bridged jobs join the paced queue post-200."""
        self.enqueue_auto_analysis(offer_id, job_id, job, kind="new", tally=tally)

    def enqueue_auto_analysis(
        self,
        offer_id: int,
        job_id: str,
        job: dict[str, Any],
        *,
        kind: Literal["new", "retry"] = "new",
        tally: _RoundTally | None = None,
    ) -> None:
        """Thread-safe, non-blocking enqueue (notification-pacing D1).

        ``call_soon_threadsafe(q.put_nowait, ...)`` keeps the handler thread
        lock-free and the FIFO order; callers outside the loop (the heartbeat
        sweep) pass ``kind="retry"``. Loop not yet captured (payload before
        ``post_init``) → warning; the offer row persists and the owner can
        still press ``[Analizar]``.
        """
        if self._loop is None or self._queue is None or self._bot is None:
            _LOG.warning(
                "bridge auto-analyze skipped: PTB loop not captured yet "
                "(post_init pending); offer #%s persists without a card",
                offer_id,
            )
            return
        item: Item = (offer_id, job_id, job, kind, tally)
        self._loop.call_soon_threadsafe(self._accept, item)

    def _accept(self, item: Item) -> None:
        """Loop-side put: after shutdown nobody drains the queue, so a put that
        was scheduled before it (the post-200 race) defers its row instead."""
        if self._closing or self._queue is None:
            self._defer_retry(item[0], datetime.now(tz=UTC).isoformat())
            return
        self._queue.put_nowait(item)

    async def _drain_queue(self) -> None:
        """The ONE consumer (D1/D2): strictly sequential, paced.

        Pacing (D2): sleep AFTER finishing an item, only when more work is
        queued — a burst of N starts at t, t+d, t+2d; the first item and the
        tail never wait. ``analyze_delay_seconds=0`` → immediate sequential.
        """
        queue = self._queue
        if queue is None:  # pragma: no cover — post_init always creates it
            return
        while True:
            offer_id, job_id, job, kind, tally = await queue.get()
            try:
                await self._process_item(offer_id, job_id, job, kind, tally)
            except Exception as exc:  # noqa: BLE001 — the worker survives any item
                _LOG.warning("bridge auto-analysis task failed: %s", exc)
            finally:
                queue.task_done()
            if self._delay > 0 and not queue.empty():
                await _pacing_sleep(self._delay)
            if tally is not None:
                # Round-summary amendment: settled AFTER the item's pacing
                # delay — the summary never lands before the batch's last
                # job has fully drained.
                await self._settle_round(tally)

    async def _process_item(
        self,
        offer_id: int,
        job_id: str,
        job: dict[str, Any],
        kind: str,
        tally: _RoundTally | None,
    ) -> None:
        """Drain one item: retry-guard first, then the analysis coroutine."""
        if self._is_paused():
            # Kill switch: queued work waits in retry-pending (attempts
            # untouched) and the sweep re-drives it after /reanudar.
            self._defer_retry(offer_id, datetime.now(tz=UTC).isoformat())
            if tally is not None:
                tally.record(
                    "pending", None, job.get("title"), job.get("company"), None
                )
            return
        if kind == "retry" and not self._still_retry_pending(offer_id):
            # D-tree guard: resolved elsewhere (manual success cleared it /
            # final card already sent) — skip silently, never card again.
            _LOG.debug("bridge retry skipped (resolved): offer #%s", offer_id)
            return
        self._in_flight = offer_id
        try:
            await self._auto_analyze_job(offer_id, job_id, job, tally)
        finally:
            self._in_flight = None

    async def _settle_round(self, tally: _RoundTally) -> None:
        """Countdown the batch; on the LAST item send the round summary.

        Never delays or blocks an analysis: this runs after the item's
        processing (and pacing delay) already happened. A failed send is
        warn-logged — the summary must never kill the worker.
        """
        tally.remaining -= 1
        if tally.remaining > 0:
            return
        analyzed = tally.above + tally.gray + tally.below + tally.walled
        if analyzed == tally.walled and tally.pending == 0:
            # Nothing scored and nothing retrying (all walled or closed): no noise.
            return
        walled_offers = [
            w._replace(reason=display_reason(w.reason, tally.lang))
            if w.reason != _WALL
            else w._replace(reason=t("bot.wall", tally.lang))
            for w in tally.walled_offers
        ]
        message = round_summary_message(
            beacon=tally.beacon,
            analyzed=analyzed,
            above=tally.above,
            gray=tally.gray,
            below=tally.below,
            walled=tally.walled,
            walled_offers=walled_offers,
            closed=tally.closed,
            closed_offers=tally.closed_offers,
            pending=tally.pending,
            discarded=tally.discarded,
            duplicates=len(tally.duplicates),
            duplicate_offers=tally.duplicates,
            lang=tally.lang,
        )
        try:
            await self._notify_card(message, None, parse_mode="HTML")
        except Exception:  # noqa: BLE001 — recap is best-effort, never fatal
            _LOG.warning(
                "bridge round summary failed to send (beacon %s)", tally.beacon
            )

    def _still_retry_pending(self, offer_id: int) -> bool:
        """True while the row still carries a pending ``next_analyze_at``."""
        try:
            row = self._repo.get_offer(offer_id)
        except RepositoryError:
            return False
        return row.next_analyze_at is not None

    async def shutdown(self) -> None:
        """PTB post_shutdown hook (notification-pacing D9): cancel the worker
        and defer unfinished rows to retry-pending → the sweep re-drives them.

        Deferral (attempts untouched, ``next_analyze_at = now``) covers the
        in-flight item (captured BEFORE the cancel so the coroutine's finally
        cannot hide it) and everything still queued. A shutdown is not a
        failure: the attempt counter stays as-is.
        """
        self._closing = True
        in_flight = self._in_flight
        if self._worker is not None:
            self._worker.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None
        now = datetime.now(tz=UTC).isoformat()
        if in_flight is not None:
            self._defer_retry(in_flight, now)
        queue = self._queue
        if queue is not None:
            while not queue.empty():
                deferred = queue.get_nowait()[0]
                self._defer_retry(deferred, now)

    def _defer_retry(self, offer_id: int, now_iso: str) -> None:
        self._repo.defer_analyze_retry(offer_id, next_analyze_at=now_iso)

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


def register(app: Any, settings: Settings, service: "JobService") -> _Bridge | None:
    """Wire the bridge to the bot (the whole bot.py integration; D1/D2).

    Disabled — warning, nothing bound, bot continues — when the secret or the
    owner id is unset, or when the loopback bind fails. Otherwise starts the
    daemon server now and assigns PTB's public ``post_init`` so the card
    sender captures the polling loop at startup. Returns the handle (or
    ``None`` when disabled) so tests and callers can stop it.
    """
    secret = settings.bridge_secret.get_secret_value()
    if not secret:
        _LOG.warning(
            "CVA_BRIDGE_SECRET unset: bridge endpoint disabled (no port bound)"
        )
        return None
    if settings.owner_telegram_id == 0:
        _LOG.warning(
            "owner_telegram_id is 0: bridge endpoint disabled (no card target)"
        )
        return None
    profile_paths = resolve_profile_paths(settings.profile_dir)
    bridge = _Bridge(
        secret=secret,
        port=settings.bridge_port,
        owner_id=settings.owner_telegram_id,
        threshold=service.threshold,
        auto_analyze=settings.auto_analyze,
        service=service,
        analyze_delay_seconds=settings.analyze_delay_seconds,
        notify_min_score=settings.notify_min_score,
        round_summary=settings.round_summary,
        claims_path=profile_paths.claims,
        preferences=load_preferences(profile_paths.preferences),
        paused_default=settings.paused,
        enabled_portals=(
            None
            if settings.enabled_portals is None
            else frozenset(settings.enabled_portals)
        ),
        ui_language=settings.ui_language,
    )
    try:
        bridge.start()
    except OSError as exc:
        _LOG.warning(
            "bridge bind failed on 127.0.0.1:%d (%s); endpoint disabled",
            settings.bridge_port,
            exc,
        )
        return None
    # D9 wiring: worker starts in post_init (loop captured there); the bridge
    # stashes itself for the heartbeat sweep and owns the post_shutdown hook
    # (PTB honors both attribute assignments in __run).
    app.post_init = bridge.post_init
    app.post_shutdown = bridge.shutdown
    app.bot_data[BOT_DATA_KEY] = bridge
    atexit.register(bridge.stop)
    return bridge
