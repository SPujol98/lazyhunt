"""Beacon heartbeat scheduling (design D11, tasks 8b.3–8b.4).

One heartbeat job every 10 minutes; its FIRST run is the startup catch-up —
beacons that became due while the process was down run exactly once, because
the sweep (:meth:`JobService.run_due_beacons`) is the unit of catch-up, not the
missed slots. The due rule is pure and reads the STORED due time:
``next_check_after`` already contains ``last_check + interval + jitter_offset``
(±30 min drawn at each completing check) or the D3 backoff floor after a wall,
so jitter survives restarts and no clock arithmetic is duplicated here.

The PTB wiring (:func:`register`, :func:`heartbeat`) imports ``telegram``
lazily so CLI/service imports stay light; notification copy comes from the
notifier (Spanish by the slice-8a decision).
"""

import logging
from datetime import UTC, datetime, timedelta
from random import uniform
from typing import TYPE_CHECKING, Any, Final

from cva.beacons import SweepResult
from cva.i18n import Lang, t
from cva.ingest.sources import is_inline_source
from cva.tracker import BeaconRow, OfferRow

if TYPE_CHECKING:
    from cva.service import JobService

_LOG = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS: Final[int] = 600
JITTER_SPAN: Final[timedelta] = timedelta(minutes=30)
SERVICE_KEY: Final[str] = "service"
OWNER_KEY: Final[str] = "owner_id"
BRIDGE_KEY: Final[str] = "bridge"
GMAIL_KEY: Final[str] = "gmail_sync"


def default_jitter() -> timedelta:
    """Uniform offset in ±30 min, drawn at each completing check (design D11)."""
    span = JITTER_SPAN.total_seconds()
    return timedelta(seconds=uniform(-span, span))


def due(beacon: BeaconRow, now: datetime) -> bool:
    """Pure due rule: enabled, not paused, and now ≥ the stored due time."""
    if not beacon.enabled or beacon.status == "paused":
        return False
    return beacon.next_check_after is None or now >= datetime.fromisoformat(
        beacon.next_check_after
    )


async def heartbeat(context: Any) -> SweepResult:
    """JobQueue tick: sweep due beacons and deliver cards/alerts to the owner.

    After the beacon work it also re-drives retry-pending offers
    (notification-pacing D4): due rows within the attempt budget re-enter
    through the SAME paced queue (``kind="retry"``, never direct analysis).
    The bridge key is imported lazily inside the function so the
    scheduler→bridge import cycle never exists at module level.
    """
    from cva.bridge import BOT_DATA_KEY
    from cva.ingest import job_id_from_url
    from cva.notifier import (
        beacon_alert_keyboard,
        beacon_paused_message,
        new_job_keyboard,
        new_job_message,
    )

    service: JobService = context.application.bot_data[SERVICE_KEY]
    owner = int(context.application.bot_data[OWNER_KEY])
    lang = service.ui_language
    # Catch up a tracker.xlsx refresh that failed on a locked file (no-op when
    # the tracker is disabled or clean); first so a failing sweep cannot skip it.
    await service.retry_tracker_refresh()
    # Kill switch (safe-defaults S2): paused → no beacon sweep, no retry sweep,
    # no Gmail poll. Housekeeping (tracker refresh, deadline/follow-up alerts)
    # still runs: it touches no portal, LLM or mailbox.
    paused = service.is_paused()
    sweep = SweepResult() if paused else await service.run_due_beacons()
    bridge = context.application.bot_data.get(BOT_DATA_KEY)
    if bridge is not None and not paused:
        now_iso = datetime.now(tz=UTC).isoformat()
        for row in service.list_retry_pending(now_iso, service.analyze_retry_max):
            if not row.url_oferta:
                continue
            # Registry-derived job id (infojobs-source): the InfoJobs
            # canonical tail is "of-<id>", so the tail fallback alone would
            # file the retry under the wrong id; LinkedIn shapes are
            # unaffected (the registry returns the same numeric id).
            job_id = (
                job_id_from_url(row.url_oferta)
                or row.url_oferta.rstrip("/").rsplit("/", 1)[-1]
            )
            job: dict[str, Any] = {
                "url": row.url_oferta,
                "title": row.posicion,
                "company": row.empresa,
            }
            if _retry_inline(row):
                # Inline durability (infojobs-source D7): shutdown-deferred
                # inline rows re-analyze without the dead server fetch — the
                # stored text IS the analysis text. Link-ingested rows (any
                # portal) and LinkedIn rows keep the note→refetch lifecycle
                # (no "text" key).
                job["text"] = row.source_data
            bridge.enqueue_auto_analysis(row.id, job_id, job, kind="retry")
    if owner == 0:
        _LOG.warning(
            "heartbeat produced %d notifications but owner id is unset",
            len(sweep.notifications),
        )
        return sweep
    for notification in sweep.notifications:
        await context.bot.send_message(
            owner,
            new_job_message(
                title=notification.title,
                company=notification.company,
                age=notification.age or t("notifier.age_recent", lang),
                job_id=notification.job_id,
                threshold=service.threshold,
                lang=lang,
            ),
            reply_markup=new_job_keyboard(notification.job_id, lang=lang),
        )
    for alert in sweep.paused_alerts:
        await context.bot.send_message(
            owner,
            beacon_paused_message(alert, lang=lang),
            reply_markup=beacon_alert_keyboard(alert.beacon_id, lang=lang),
        )
    try:
        due = service.deadline_alert()
        if due:
            try:
                await context.bot.send_message(owner, due[0])
            except Exception:
                # Exponential backoff (10 min -> 1 h -> 6 h) instead of a
                # resend on every tick while Telegram keeps failing.
                service.record_deadline_alert_failure()
                raise
            # Only after a successful send; a failing store write is retried
            # without resending (the service remembers the ids in memory).
            service.mark_deadline_alerted(due[1])
    except Exception:  # noqa: BLE001 — a failing alert must not break the sweep
        _LOG.exception("deadline alert failed")
    try:
        await _send_followups(context.bot, service, owner, lang)
    except Exception:  # noqa: BLE001 — a failing follow-up must not break the sweep
        _LOG.exception("follow-up reminders failed")
    if not paused:
        try:
            await _run_gmail_sync(
                context.bot, context.application.bot_data.get(GMAIL_KEY), owner, lang
            )
        except Exception:  # noqa: BLE001 — Gmail sync must never break the heartbeat
            _LOG.exception("gmail sync failed")
    return sweep


async def _run_gmail_sync(bot: Any, sync: Any, owner: int, lang: Lang) -> None:
    """Gmail poll behind its own every-N-minutes gate (the heartbeat ticks
    every 10 minutes; ``sync.due()`` decides). Proposals and the one-time auth
    warning go to the owner; the sync itself never raises."""
    if sync is None or not sync.due():
        return
    from cva.notifier import gmail_proposal_keyboard, gmail_proposal_message

    async def send_proposal(proposal: Any) -> None:
        await bot.send_message(
            owner,
            gmail_proposal_message(proposal, lang=lang),
            reply_markup=gmail_proposal_keyboard(proposal.message_id, lang=lang),
        )

    async def send_warning(text: str) -> None:
        await bot.send_message(owner, text)

    await sync.run(send_proposal, send_warning)


async def _send_followups(
    bot: Any, service: "JobService", owner: int, lang: Lang
) -> None:
    """Daily follow-up job: message the owner about silent sent offers.

    Nothing is sent externally. An item is marked only after its message was
    delivered; a failed send backs that item off (10 min -> 1 h -> 6 h, three
    attempts) while the rest of the batch continues; chunks already delivered
    are not resent on retry.
    """
    from cva.notifier import (
        chunk_message,
        followup_keyboard,
        followup_message,
        followup_proposal_keyboard,
        followup_proposal_message,
    )

    for item in service.followup_due():
        # Per-item handling: a failing item backs off on its own and never
        # starves the rest of the batch.
        delivered = 0
        text = ""
        parts: list[str] = []
        try:
            resume = service.followup_resume(item)
            if item.kind == "proposal":
                markup = followup_proposal_keyboard(item.offer.id, lang=lang)
            else:
                markup = followup_keyboard(item.offer.id, lang=lang)
            if resume is not None:
                text, delivered = resume
            elif item.kind == "proposal":
                text = followup_proposal_message(
                    item.offer, days_silent=item.days_silent, lang=lang
                )
            else:
                draft = await service.followup_draft(
                    item.offer.id,
                    days_silent=item.days_silent,
                    number=item.followups + 1,
                )
                text = followup_message(
                    item.offer,
                    days_silent=item.days_silent,
                    number=item.followups + 1,
                    draft=draft.text,
                    warnings=draft.warnings,
                    lang=lang,
                    recipient=service.followup_recipient(item.offer),
                )
            stored = service.followup_chunks(item) if resume is not None else None
            parts = stored if stored is not None else chunk_message(text)
            for index in range(delivered, len(parts)):
                await bot.send_message(
                    owner,
                    parts[index],
                    reply_markup=markup if index == len(parts) - 1 else None,
                )
                delivered = index + 1
        except Exception:  # noqa: BLE001 — one bad item must not stop the batch
            _LOG.warning("follow-up for #%s failed", item.offer.id, exc_info=True)
            service.record_followup_failure(
                item,
                text=text or None,
                delivered=delivered,
                chunks=parts or None,
            )
            continue
        service.mark_followup_notified(item)
    service.complete_followup_run()


def _retry_inline(row: OfferRow) -> bool:
    """Replay the stored text only for rows ingested inline on an inline portal.

    Decided by the row's ingest source: a ``link`` row was fetched from its
    URL and is refetched on retry. Legacy rows (no ingest source) fall back to
    the portal in ``origen``.
    """
    if row.ingest_source is None:
        return is_inline_source(row.origen)
    return row.ingest_source == "bridge-inline" and is_inline_source(row.origen)


def register(
    app: Any, service: "JobService", owner_chat_id: int, gmail: Any = None
) -> None:
    """Schedule the single heartbeat on the app's JobQueue (first tick = now).

    ``gmail`` (a ``GmailSync``) is optional: absent, the heartbeat does no
    Gmail work at all.
    """
    app.bot_data[SERVICE_KEY] = service
    app.bot_data[OWNER_KEY] = owner_chat_id
    # Bot mode: tracker xlsx rewrites leave the event loop (debounced, in a
    # worker thread); the heartbeat below stays their backstop and PTB's
    # post_shutdown writes whatever is still pending.
    service.enable_deferred_tracker_writes()
    previous_shutdown = getattr(app, "post_shutdown", None)

    async def _flush_tracker_on_shutdown(application: Any) -> None:
        if previous_shutdown is not None:
            await previous_shutdown(application)
        await service.flush_tracker()

    app.post_shutdown = _flush_tracker_on_shutdown
    if gmail is not None:
        app.bot_data[GMAIL_KEY] = gmail
    app.job_queue.run_repeating(
        heartbeat,
        interval=HEARTBEAT_INTERVAL_SECONDS,
        first=0,  # startup catch-up: due beacons run once, immediately
        name="beacon-heartbeat",
    )
