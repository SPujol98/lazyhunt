"""AI outage detection (llm-outage): pause instead of failing offer by offer.

Two failure classes stop every analysis until the owner (or time) fixes them:

- ``auth``: the provider has no usable login or key (Claude Code login
  expired / not logged in, Codex not logged in, HTTP 401/403, invalid key);
- ``quota``: a usage limit was reached (Claude "hit your 5-hour limit",
  Codex "usage limit", OpenAI ``insufficient_quota``, Anthropic low credit),
  optionally with a reset time parsed from the provider's own wording.

Everything else (timeouts, 5xx/529, a plain HTTP 429 rate limit, malformed
replies) is NOT an outage: it keeps the beta.5 retry-later behaviour.

The outage state is one JSON value in ``app_state`` (no migration), so a
restart keeps it and never re-alerts. Pure helpers only: the service owns
the state transitions.
"""

import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, tzinfo
from typing import Final, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cva.llm import (
    AnthropicLlmClient,
    ClaudeCodeLlmClient,
    CodexCliLlmClient,
    LlmResponseError,
)

OutageKind = Literal["auth", "quota"]
Provider = Literal["claude_code", "codex_cli", "anthropic", "openai_compatible"]

STATE_KEY: Final[str] = "ai_outage"
PROBE_INTERVAL: Final[timedelta] = timedelta(minutes=15)
# Probe slightly after a known reset so the provider has really reset.
RESET_GRACE: Final[timedelta] = timedelta(minutes=1)
# On recovery, offers first seen this long before the outage started (and
# still unanalysed) are re-driven too: the cards that failed before the pause.
RESCUE_LOOKBACK: Final[timedelta] = timedelta(days=3)
_DETAIL_LIMIT: Final[int] = 200

_AUTH_PATTERN: Final = re.compile(
    r"failed to authenticate|not logged in|please run /login|claude auth login"
    r"|reached its time limit and cannot be renewed|oauth token has expired"
    r"|invalid api key|invalid x-api-key|incorrect api key|authentication_error"
    r"|authentication failed|invalid bearer token|api_error_status=40[13]\b"
)
_QUOTA_PATTERN: Final = re.compile(
    r"usage limit|hit your [\w\s-]{0,20}limit|(?<!rate )limit reached|insufficient_quota"
    r"|exceeded your current quota|quota exceeded|credit balance is too low"
    r"|out of credits"
)
_SECRET_PATTERNS: Final = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)bearer\s+\S+"),
    re.compile(r"(?i)(api[_-]?key|token|secret)=\S+"),
    re.compile(r"[A-Za-z0-9_\-]{32,}"),
)


@dataclass(frozen=True)
class AiOutage:
    """The persisted outage (one ``app_state`` JSON value)."""

    kind: OutageKind
    provider: Provider
    since: datetime
    next_probe: datetime
    reset_at: datetime | None = None
    alerted: bool = False
    detail: str = ""

    def to_json(self) -> str:
        data = asdict(self)
        for key in ("since", "next_probe", "reset_at"):
            value = data[key]
            data[key] = value.isoformat() if value is not None else None
        return json.dumps(data)

    @classmethod
    def from_json(cls, raw: str | None) -> "AiOutage | None":
        """``None`` for no outage (absent/blank) and for an unreadable value."""
        if not raw:
            return None
        try:
            data = json.loads(raw)
            reset = data.get("reset_at")
            return cls(
                kind=data["kind"],
                provider=data["provider"],
                since=datetime.fromisoformat(data["since"]),
                next_probe=datetime.fromisoformat(data["next_probe"]),
                reset_at=datetime.fromisoformat(reset) if reset else None,
                alerted=bool(data.get("alerted", False)),
                detail=str(data.get("detail", "")),
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            return None

    def rescheduled(self, now: datetime, reset_at: datetime | None) -> "AiOutage":
        """The same outage with its next probe moved forward."""
        reset = reset_at if reset_at is not None and reset_at > now else None
        return replace(self, reset_at=reset, next_probe=next_probe_at(now, reset))


def next_probe_at(now: datetime, reset_at: datetime | None) -> datetime:
    """At the known reset (plus a minute), else one probe interval from now."""
    if reset_at is not None and reset_at > now:
        return reset_at + RESET_GRACE
    return now + PROBE_INTERVAL


def provider_of(llm: object, exc: BaseException | None = None) -> Provider:
    """The provider that failed: named by the error text when it says so
    (``claude CLI ...``, ``codex CLI ...``, ``Anthropic ...``), else the
    configured client type."""
    text = str(exc).lower() if exc is not None else ""
    if text.startswith("claude"):
        return "claude_code"
    if text.startswith("codex"):
        return "codex_cli"
    if text.startswith("anthropic"):
        return "anthropic"
    if isinstance(llm, ClaudeCodeLlmClient):
        return "claude_code"
    if isinstance(llm, CodexCliLlmClient):
        return "codex_cli"
    if isinstance(llm, AnthropicLlmClient):
        return "anthropic"
    return "openai_compatible"


def classify_ai_failure(
    exc: BaseException, now: datetime
) -> tuple[OutageKind, datetime | None] | None:
    """``(kind, reset_at)`` for an outage-class LLM failure, else ``None``."""
    if not isinstance(exc, LlmResponseError):
        return None
    text = str(exc).lower()
    if exc.status in (401, 403) or _AUTH_PATTERN.search(text):
        return "auth", None
    if _QUOTA_PATTERN.search(text):
        return "quota", parse_reset(str(exc), now)
    return None


def parse_reset(text: str, now: datetime) -> datetime | None:
    """The reset time a usage-limit message names, or ``None``.

    Understands the epoch suffix (``limit reached|1764079200``), "resets 3pm
    (Europe/Madrid)" / "resets at 15:30", "try again at 3:45 PM" and "try
    again in 2 hours 13 minutes". A clock time without a zone is read in the
    machine's local zone; a time already past means tomorrow.
    """
    lowered = text.lower()
    if epoch := re.search(r"\|(\d{10})\b", lowered):
        return datetime.fromtimestamp(int(epoch.group(1)), tz=now.tzinfo)
    if relative := re.search(
        r"try again in (\d+)\s*(hours?|hrs?|h|minutes?|mins?|m)\b"
        r"(?:\s*(?:and\s*)?(\d+)\s*(?:minutes?|mins?|m)\b)?",
        lowered,
    ):
        amount = int(relative.group(1))
        unit = relative.group(2)
        delta = (
            timedelta(hours=amount)
            if unit.startswith("h")
            else timedelta(minutes=amount)
        )
        if relative.group(3):
            delta += timedelta(minutes=int(relative.group(3)))
        return now + delta
    clock = re.search(
        r"(?:resets?|try again)\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"
        r"(?:\s*\(([a-z_]+(?:/[a-z_]+)*)\))?",
        lowered,
    )
    if clock is None or (clock.group(2) is None and clock.group(3) is None):
        return None
    hour, minute = int(clock.group(1)), int(clock.group(2) or 0)
    if clock.group(3) == "pm" and hour < 12:
        hour += 12
    elif clock.group(3) == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    zone = _zone(text, clock.group(4)) or now.astimezone().tzinfo
    local_now = now.astimezone(zone)
    candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate


def _zone(original: str, lowered_name: str | None) -> tzinfo | None:
    """The IANA zone named in the message (case restored from the original)."""
    if not lowered_name:
        return None
    match = re.search(re.escape(lowered_name), original, flags=re.IGNORECASE)
    name = match.group(0) if match else lowered_name
    try:
        return ZoneInfo("UTC" if name.lower() == "utc" else name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def scrub(text: str) -> str:
    """One line, secrets masked, trimmed: safe to show in a chat message."""
    flat = " ".join(text.split())
    for pattern in _SECRET_PATTERNS:
        flat = pattern.sub("***", flat)
    if len(flat) > _DETAIL_LIMIT:
        flat = flat[:_DETAIL_LIMIT] + "…"
    return flat
