"""One Gmail sync run: poll, match, classify, propose (never changes an offer).

``GmailSync.run`` is driven by the bot heartbeat behind the :meth:`due` gate
(every ``gmail_poll_minutes``). Per run it lists messages after the stored
checkpoint (first run: last 7 days), matches each to a sent offer, classifies
it, and records + delivers a *proposal* — the owner's button press elsewhere
applies it. Nothing here writes message bodies anywhere, and nothing here may
raise: every failure becomes a :class:`SyncResult` status and a body-free log.

Checkpoint (``app_state`` key ``gmail_checkpoint``): JSON
``{"after": <epoch seconds>, "seen": [ids received exactly at that second]}``.
The next list query starts at ``after``; ``seen`` skips the ties that Gmail's
second-granularity filter returns again. It advances only after a fully
successful run, so any API or delivery failure re-reads the same window next
time (proposals are deduped by message id in ``gmail_messages``). A message
that cannot be fetched stops the run with the checkpoint left at the last
message fully processed; a per-message failure counter (``gmail_failures``)
skips it after ``MAX_MESSAGE_ATTEMPTS`` runs. An LLM outage is a run-level
condition: it stops the run without counting against any message and, after
``OUTAGE_NOTICE_RUNS`` consecutive runs, tells the owner once.

A listing the client reports as truncated lacks its OLDEST ids, so the
checkpoint never moves past it. Instead the window is walked backwards over
successive runs (``gmail_walk``: the next query is bounded by ``before`` the
oldest message processed, plus the newest stamp seen); when a listing finally
is complete the checkpoint jumps to that newest stamp. The owner is told once
that the window is long.

Duplicates: while a proposal for an offer and estado is still unanswered, a
further matching email (a reminder, a calendar invite) is stored as ``stale``
evidence and never sent. A send that times out keeps its row: Telegram may
have delivered it, and re-proposing it next run would duplicate the card. The
returned Telegram message (``chat_id``/``message_id``) is remembered in
``app_state`` (``gmail_card:<id>``) so an answer can close sibling cards.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Literal

from telegram.error import TimedOut

from cva.gmail.classify import ClassificationUnavailable, classify_message
from cva.gmail.client import (
    AUTH_COMMAND,
    GmailApiError,
    GmailAuthError,
    GmailClient,
    GmailMessage,
)
from cva.gmail.matching import match_offer
from cva.i18n import LEGACY_LANG, Lang, t
from cva.llm import LlmClient
from cva.tracker import OfferRepository

_LOG = logging.getLogger(__name__)

CHECKPOINT_KEY: Final[str] = "gmail_checkpoint"
AUTH_WARNED_KEY: Final[str] = "gmail_auth_warned"
FAILURES_KEY: Final[str] = "gmail_failures"
WALK_KEY: Final[str] = "gmail_walk"
WINDOW_WARNED_KEY: Final[str] = "gmail_window_warned"
OUTAGES_KEY: Final[str] = "gmail_llm_outages"
OUTAGE_WARNED_KEY: Final[str] = "gmail_llm_warned"
# Consecutive LLM-outage runs before the owner is told (once).
OUTAGE_NOTICE_RUNS: Final[int] = 3
# A message whose fetch/classification keeps failing is skipped after this
# many failed runs so it cannot block the queue forever.
MAX_MESSAGE_ATTEMPTS: Final[int] = 3
LOOKBACK: Final[timedelta] = timedelta(days=7)
# With nothing new the checkpoint still moves up to "now" minus this margin.
_EMPTY_RUN_MARGIN: Final[timedelta] = timedelta(minutes=10)
_FIELD_LIMIT: Final[int] = 300
CARD_KEY_PREFIX: Final[str] = "gmail_card:"

_RANK: Final[dict[str, int]] = {
    "enviada": 0,
    "sin_respuesta": 0,
    "screening": 1,
    "entrevista_tecnica": 2,
    "entrevista_final": 3,
    "oferta": 4,
}
_TERMINAL: Final[frozenset[str]] = frozenset(
    {"rechazada", "retirada", "aceptada", "descartada"}
)


def auth_warning(lang: Lang = LEGACY_LANG) -> str:
    return t("panel.gmail.auth_warning", lang, command=AUTH_COMMAND)


def window_warning(lang: Lang = LEGACY_LANG) -> str:
    return t("panel.gmail.window_warning", lang)


def outage_warning(lang: Lang = LEGACY_LANG) -> str:
    return t("panel.gmail.outage_warning", lang, runs=OUTAGE_NOTICE_RUNS)


def should_propose(current: str, proposed: str) -> bool:
    """True when ``proposed`` is news for an offer now in ``current``.

    Closed offers never get proposals; an equal or earlier estado is skipped;
    a rejection is always news (until the offer is closed or already rejected).
    """
    if current in _TERMINAL or current == proposed:
        return False
    if proposed == "rechazada":
        return True
    return _RANK.get(proposed, -1) > _RANK.get(current, 0)


@dataclass(frozen=True)
class Proposal:
    """What the owner is asked about (no message text)."""

    message_id: str
    offer_id: int
    company: str
    subject: str
    estado: str


@dataclass(frozen=True)
class SyncResult:
    status: Literal["ok", "auth_error", "api_error", "send_error"]
    proposed: int = 0


def card_ref(repo: OfferRepository, message_id: str) -> tuple[int, int] | None:
    """``(chat_id, telegram message id)`` of a delivered proposal card."""
    raw = repo.get_state(CARD_KEY_PREFIX + message_id)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return int(data["chat"]), int(data["msg"])
    except (ValueError, KeyError, TypeError):
        return None


def forget_card(repo: OfferRepository, message_id: str) -> None:
    """Drop a card reference once the card no longer needs editing."""
    if repo.get_state(CARD_KEY_PREFIX + message_id):
        repo.set_state(CARD_KEY_PREFIX + message_id, "")


def _load_failures(raw: str | None) -> dict[str, int]:
    if not raw:
        return {}
    try:
        return {str(k): int(v) for k, v in json.loads(raw).items()}
    except (ValueError, AttributeError, TypeError):
        return {}


def _load_walk(raw: str | None) -> tuple[int, int, set[str]] | None:
    """``(before, newest, ids at newest)`` of a window walk in progress."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return (
            int(data["before"]),
            int(data["newest"]),
            {str(i) for i in data.get("ids", [])},
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def _load_checkpoint(raw: str | None) -> tuple[int | None, set[str]]:
    if not raw:
        return None, set()
    try:
        data = json.loads(raw)
        return int(data["after"]), {str(i) for i in data.get("seen", [])}
    except (ValueError, KeyError, TypeError):
        return None, set()


class GmailSync:
    """Poll the mailbox and turn replies into stored, delivered proposals."""

    def __init__(
        self,
        *,
        repo: OfferRepository,
        client: GmailClient,
        llm: LlmClient,
        poll_minutes: int = 60,
        now_fn: Callable[[], datetime] | None = None,
        lang: Lang = LEGACY_LANG,
    ) -> None:
        self._lang = lang
        self._repo = repo
        self._client = client
        self._llm = llm
        self._poll = timedelta(minutes=poll_minutes)
        self._now = now_fn or (lambda: datetime.now(tz=UTC))
        self._next_run: datetime | None = None

    def due(self) -> bool:
        """Gate: open at startup, then once per poll interval (memory only)."""
        return self._next_run is None or self._now() >= self._next_run

    async def run(
        self,
        send_proposal: Callable[[Proposal], Awaitable[object]],
        send_warning: Callable[[str], Awaitable[None]],
    ) -> SyncResult:
        """One poll. Never raises; schedules the next one regardless of outcome."""
        started = self._now()
        try:
            return await self._poll_once(started, send_proposal, send_warning)
        except GmailAuthError:
            await self._warn_auth_once(send_warning)
            return SyncResult("auth_error")
        except GmailApiError as exc:
            _LOG.warning("gmail sync will retry: %s", exc)
            return SyncResult("api_error")
        except Exception as exc:  # noqa: BLE001 — the heartbeat must never crash
            _LOG.warning("gmail sync will retry: %s", type(exc).__name__)
            return SyncResult("api_error")
        finally:
            self._next_run = started + self._poll

    async def _warn_once(
        self,
        key: str,
        text: str,
        send_warning: Callable[[str], Awaitable[None]],
    ) -> None:
        """Send ``text`` unless ``key`` says it was already sent; the guard is
        only set after a successful delivery (a failed send is retried)."""
        if self._repo.get_state(key) == "1":
            return
        try:
            await send_warning(text)
        except Exception as exc:  # noqa: BLE001 — retried on the next run
            _LOG.warning("gmail notice not delivered: %s", type(exc).__name__)
            return
        self._repo.set_state(key, "1")

    async def _warn_auth_once(
        self, send_warning: Callable[[str], Awaitable[None]]
    ) -> None:
        await self._warn_once(AUTH_WARNED_KEY, auth_warning(self._lang), send_warning)

    def _clear_guard(self, key: str) -> None:
        if self._repo.get_state(key):
            self._repo.set_state(key, "")

    async def _note_outage(
        self, outage: bool, send_warning: Callable[[str], Awaitable[None]]
    ) -> None:
        """Track consecutive LLM-outage runs (never per message)."""
        if not outage:
            self._clear_guard(OUTAGES_KEY)
            self._clear_guard(OUTAGE_WARNED_KEY)
            return
        try:
            runs = int(self._repo.get_state(OUTAGES_KEY) or 0) + 1
        except ValueError:
            runs = 1
        self._repo.set_state(OUTAGES_KEY, str(runs))
        if runs >= OUTAGE_NOTICE_RUNS:
            await self._warn_once(
                OUTAGE_WARNED_KEY, outage_warning(self._lang), send_warning
            )

    async def _poll_once(
        self,
        started: datetime,
        send_proposal: Callable[[Proposal], Awaitable[object]],
        send_warning: Callable[[str], Awaitable[None]],
    ) -> SyncResult:
        after_ts, seen = _load_checkpoint(self._repo.get_state(CHECKPOINT_KEY))
        if after_ts is None:
            after_ts = int((started - LOOKBACK).timestamp())
        after = datetime.fromtimestamp(after_ts, tz=UTC)
        walk = _load_walk(self._repo.get_state(WALK_KEY))
        before = datetime.fromtimestamp(walk[0], tz=UTC) if walk else None
        ids, truncated = await asyncio.to_thread(
            self._client.list_message_ids, after, before
        )
        offers = self._repo.sent_offers()
        failures = _load_failures(self._repo.get_state(FAILURES_KEY))

        if walk:
            newest, newest_ids = walk[1], set(walk[2])
        else:
            newest, newest_ids = after_ts, set(seen)
        floor: int | None = None  # stamp of the oldest message fetched this run
        floor_is_oldest = False  # nothing older was listed-but-skipped
        progressed = False
        proposed = 0
        stopped = False
        outage = False
        for message_id in reversed(ids):  # oldest first
            if message_id in seen or self._repo.get_gmail_proposal(message_id):
                continue
            if failures.get(message_id, 0) >= MAX_MESSAGE_ATTEMPTS:
                continue  # given up on: skipped, never fetched again
            try:
                message = await asyncio.to_thread(self._client.get_message, message_id)
            except GmailApiError as exc:
                _LOG.warning("gmail message %s not fetched: %s", message_id, exc)
                if self._count_failure(failures, message_id):
                    continue
                stopped = True
                break
            stamp = int(message.received_at.timestamp())
            if floor is None:
                floor = stamp
                floor_is_oldest = message_id == ids[-1]
            offer = match_offer(message, offers)
            estado: str | None = None
            if offer is not None:  # job-looking but unmatched: ignored silently
                try:
                    estado = await classify_message(message, self._llm)
                except ClassificationUnavailable:
                    # Run-level condition: never counted against this message.
                    outage = stopped = True
                    break
            if (
                offer is not None
                and estado is not None
                and should_propose(offer.estado, estado)
            ):
                proposal = Proposal(
                    message_id=message_id,
                    offer_id=offer.id,
                    company=offer.empresa or "",
                    subject=message.subject[:_FIELD_LIMIT],
                    estado=estado,
                )
                outcome = await self._deliver(proposal, message, send_proposal)
                if outcome == "failed":
                    return SyncResult("send_error", proposed)
                if outcome == "sent":
                    proposed += 1
            failures.pop(message_id, None)
            progressed = True
            if stamp > newest:
                newest, newest_ids = stamp, set()
            if stamp == newest:
                newest_ids.add(message_id)

        self._repo.set_state(FAILURES_KEY, json.dumps(failures) if failures else "")
        await self._note_outage(outage, send_warning)
        if stopped:
            # Keep what was fully processed; the failing message is retried.
            # Never while older mail may be unlisted (truncated or mid-walk).
            if progressed and not truncated and walk is None:
                self._save_checkpoint(newest, newest_ids)
            return SyncResult("api_error", proposed)
        if truncated:
            _LOG.warning("gmail listing truncated: walking the window back")
            if floor is not None:
                self._repo.set_state(
                    WALK_KEY,
                    json.dumps(
                        {
                            # Older skipped ids have unknown stamps: step past
                            # the floor second so the walk always progresses.
                            "before": floor + 1 if floor_is_oldest else floor,
                            "newest": newest,
                            "ids": sorted(newest_ids),
                        }
                    ),
                )
            await self._warn_once(
                WINDOW_WARNED_KEY, window_warning(self._lang), send_warning
            )
            return SyncResult("ok", proposed)
        if not progressed and walk is None:
            # Only an unbounded listing proves nothing newer is pending; a
            # finishing walk keeps its own newest stamp (no silent gap).
            margin = int((started - _EMPTY_RUN_MARGIN).timestamp())
            if margin > newest:
                newest, newest_ids = margin, set()
        self._save_checkpoint(newest, newest_ids)
        self._repo.set_state(WALK_KEY, "")
        self._clear_guard(WINDOW_WARNED_KEY)
        self._clear_guard(AUTH_WARNED_KEY)
        return SyncResult("ok", proposed)

    def _save_checkpoint(self, newest: int, newest_ids: set[str]) -> None:
        self._repo.set_state(
            CHECKPOINT_KEY, json.dumps({"after": newest, "seen": sorted(newest_ids)})
        )

    @staticmethod
    def _count_failure(failures: dict[str, int], message_id: str) -> bool:
        """Count one failed run for ``message_id``; True once it is given up on."""
        failures[message_id] = failures.get(message_id, 0) + 1
        if failures[message_id] >= MAX_MESSAGE_ATTEMPTS:
            _LOG.warning("gmail message %s skipped after repeated failures", message_id)
            return True
        return False

    async def _deliver(
        self,
        proposal: Proposal,
        message: GmailMessage,
        send_proposal: Callable[[Proposal], Awaitable[object]],
    ) -> Literal["sent", "skipped", "failed"]:
        """Store the proposal, then deliver it; a failed delivery un-stores it
        so the next run proposes the message again. A duplicate of a pending
        proposal (same offer and estado) is stored as ``stale``, never sent."""
        duplicate = any(
            row.proposed_estado == proposal.estado
            for row in self._repo.pending_gmail_proposals(proposal.offer_id)
        )
        stored = self._repo.add_gmail_proposal(
            message_id=proposal.message_id,
            offer_id=proposal.offer_id,
            sender=message.sender[:_FIELD_LIMIT],
            subject=proposal.subject,
            received_at=message.received_at.isoformat(),
            proposed_estado=proposal.estado,
            decision="stale" if duplicate else "pending",
        )
        if duplicate:
            _LOG.info(
                "gmail message %s: offer #%s already has a pending %s proposal",
                proposal.message_id,
                proposal.offer_id,
                proposal.estado,
            )
            return "skipped"
        if not stored:
            return "skipped"
        try:
            sent = await send_proposal(proposal)
        except TimedOut:
            # Delivery unknown (usually it landed): keep the row, never resend.
            _LOG.warning("gmail proposal %s send timed out", proposal.message_id)
            return "sent"
        except Exception as exc:  # noqa: BLE001 — Telegram hiccup: retry next run
            _LOG.warning("gmail proposal not delivered: %s", type(exc).__name__)
            self._repo.delete_pending_gmail_proposal(proposal.message_id)
            return "failed"
        self._remember_card(proposal.message_id, sent)
        return "sent"

    def _remember_card(self, message_id: str, sent: object) -> None:
        chat, msg = getattr(sent, "chat_id", None), getattr(sent, "message_id", None)
        if isinstance(chat, int) and isinstance(msg, int):
            self._repo.set_state(
                CARD_KEY_PREFIX + message_id, json.dumps({"chat": chat, "msg": msg})
            )
