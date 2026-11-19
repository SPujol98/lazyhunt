"""Gmail access (gmail-sync): read-only scope, token file, thin client seam.

The rest of the app only sees the :class:`GmailClient` protocol; the real
implementation on ``google-api-python-client`` lives here so every test can
use a fake. Security contract: the scope is exactly
``gmail.readonly``; the token file is always written with ``0600``; message
text is returned for in-memory classification only and never persisted or
logged by this module; credentials/token contents are never logged.
"""

import base64
import contextlib
import json
import logging
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Protocol, cast

from cva.i18n import LEGACY_LANG, Lang, t

_LOG = logging.getLogger(__name__)
_CHARSET = re.compile(r"charset\s*=\s*[\"']?([\w.:-]+)", re.IGNORECASE)

GMAIL_SCOPE: Final[str] = "https://www.googleapis.com/auth/gmail.readonly"
DEFAULT_CREDENTIALS_PATH: Final[str] = "data/gmail_credentials.json"
DEFAULT_TOKEN_PATH: Final[str] = "data/gmail_token.json"
AUTH_COMMAND: Final[str] = "uv run cli.py gmail-auth"
# Hard cap per listing so a huge backlog stays bounded; hitting it is reported
# (``truncated``) so the caller can narrow the window instead of losing mail.
MAX_MESSAGES_PER_LIST: Final[int] = 1000
_PAGE_SIZE: Final[int] = 500
_AUTH_HTTP_STATUSES: Final[frozenset[int]] = frozenset({401, 403})
# Gmail also answers 403 for throttling/quota: those are transient, not a lost
# authorization, so the owner must not be told to re-run the auth command.
_TRANSIENT_403_REASONS: Final[frozenset[str]] = frozenset(
    {
        "ratelimitexceeded",
        "userratelimitexceeded",
        "dailylimitexceeded",
        "quotaexceeded",
        "backenderror",
        "internalerror",
    }
)


class GmailAuthError(Exception):
    """The mailbox cannot be reached with the stored authorization."""


class GmailApiError(Exception):
    """A transient/other Gmail API or network failure (retried next run)."""


@dataclass(frozen=True)
class GmailMessage:
    """One message as seen in memory. ``text`` is never stored or logged."""

    message_id: str
    sender: str
    subject: str
    received_at: datetime
    text: str


class GmailClient(Protocol):
    """Seam over the mailbox: tests use fakes, production the Google client."""

    def list_message_ids(
        self, after: datetime, before: datetime | None = None
    ) -> tuple[list[str], bool]:
        """``(ids, truncated)``: messages received after ``after`` (and before
        ``before`` when given), newest first. ``truncated`` means the bound was
        hit and the OLDEST matching ids are missing."""
        ...

    def get_message(self, message_id: str) -> GmailMessage:
        """Headers plus plain text of one message (text for memory only)."""
        ...


def build_search_query(after: datetime, before: datetime | None = None) -> str:
    """Gmail search: inbox-type mail since ``after`` minus promos/social/sent/spam."""
    query = (
        "-category:promotions -category:social -in:sent -in:spam "
        f"after:{int(after.timestamp())}"
    )
    if before is not None:
        query += f" before:{int(before.timestamp())}"
    return query


def write_token_file(path: Path, content: str) -> None:
    """Write the token with ``0600`` permissions (replacing a looser file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()


def _declared_charset(payload: Mapping[str, Any]) -> str | None:
    for header in payload.get("headers") or []:
        if str(header.get("name", "")).lower() == "content-type":
            match = _CHARSET.search(str(header.get("value", "")))
            return match.group(1) if match else None
    return None


def _decode(data: str, charset: str | None = None) -> str:
    """Decode a body with its declared charset; UTF-8 when absent/unknown/wrong."""
    padded = data + "=" * (-len(data) % 4)
    raw = base64.urlsafe_b64decode(padded)
    if charset:
        try:
            return raw.decode(charset)
        except (LookupError, UnicodeError):
            pass
    return raw.decode("utf-8", errors="replace")


def extract_plain_text(payload: Mapping[str, Any]) -> str:
    """First ``text/plain`` body found depth-first in the MIME tree, else ``""``."""
    if payload.get("mimeType") == "text/plain":
        data = (payload.get("body") or {}).get("data")
        if isinstance(data, str) and data:
            return _decode(data, _declared_charset(payload))
    for part in payload.get("parts") or []:
        found = extract_plain_text(part)
        if found:
            return found
    return ""


def parse_message(raw: Mapping[str, Any]) -> GmailMessage:
    """Map a ``format=full`` Gmail message to :class:`GmailMessage`."""
    payload: Mapping[str, Any] = raw.get("payload") or {}
    headers = {
        str(h.get("name", "")).lower(): str(h.get("value", ""))
        for h in payload.get("headers") or []
    }
    millis = int(raw.get("internalDate") or 0)
    text = extract_plain_text(payload) or str(raw.get("snippet") or "")
    return GmailMessage(
        message_id=str(raw["id"]),
        sender=headers.get("from", ""),
        subject=headers.get("subject", ""),
        received_at=datetime.fromtimestamp(millis / 1000, tz=UTC),
        text=text,
    )


def _build_service(credentials: Any) -> Any:
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def _load_credentials(token_path: Path, lang: Lang = LEGACY_LANG) -> Any:
    """Load the stored token, refreshing (and writing back) when expired."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not token_path.exists():
        raise GmailAuthError(t("panel.gmail.no_token", lang, command=AUTH_COMMAND))
    try:
        # google-auth leaves this classmethod unannotated; typed at the seam.
        credentials = cast(Any, Credentials).from_authorized_user_file(
            str(token_path), [GMAIL_SCOPE]
        )
    except (ValueError, OSError) as exc:
        raise GmailAuthError(
            t("panel.gmail.bad_token", lang, command=AUTH_COMMAND)
        ) from exc
    if not credentials.valid:
        if not credentials.refresh_token:
            raise GmailAuthError(
                t("panel.gmail.no_refresh", lang, command=AUTH_COMMAND)
            )
        try:
            credentials.refresh(Request())
        except RefreshError as exc:
            if exc.retryable:  # token-endpoint outage, not a revocation
                raise GmailApiError(
                    f"token refresh failed temporarily: {type(exc).__name__}"
                ) from exc
            raise GmailAuthError(
                t("panel.gmail.refresh_rejected", lang, command=AUTH_COMMAND)
            ) from exc
        write_token_file(token_path, credentials.to_json())
    return credentials


class GoogleGmailClient:
    """Real :class:`GmailClient` on ``google-api-python-client`` (blocking)."""

    def __init__(
        self,
        credentials_path: Path,
        token_path: Path,
        *,
        service_factory: Callable[[], Any] | None = None,
        lang: Lang = LEGACY_LANG,
    ) -> None:
        self._lang = lang
        self._credentials_path = credentials_path
        self._token_path = token_path
        self._service_factory = service_factory
        self._service: Any = None

    def _api(self) -> Any:
        # Built per call: the token may have been refreshed or replaced.
        if self._service_factory is not None:
            return self._service_factory()
        return _build_service(_load_credentials(self._token_path, self._lang))

    def list_message_ids(
        self, after: datetime, before: datetime | None = None
    ) -> tuple[list[str], bool]:
        ids: list[str] = []
        page_token: str | None = None
        try:
            messages = self._api().users().messages()
            while True:
                kwargs: dict[str, Any] = {
                    "userId": "me",
                    "q": build_search_query(after, before),
                    "maxResults": _PAGE_SIZE,
                }
                if page_token:
                    kwargs["pageToken"] = page_token
                response = messages.list(**kwargs).execute()
                ids.extend(str(m["id"]) for m in response.get("messages") or [])
                page_token = response.get("nextPageToken")
                if not page_token or len(ids) >= MAX_MESSAGES_PER_LIST:
                    break
        except GmailAuthError:
            raise
        except Exception as exc:
            raise _map_api_error("list", exc, self._lang) from exc
        truncated = len(ids) > MAX_MESSAGES_PER_LIST or bool(page_token)
        return ids[:MAX_MESSAGES_PER_LIST], truncated

    def get_message(self, message_id: str) -> GmailMessage:
        try:
            raw = (
                self._api()
                .users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
            return parse_message(raw)
        except GmailAuthError:
            raise
        except Exception as exc:
            raise _map_api_error("get", exc, self._lang) from exc


def _http_reasons(exc: Exception) -> set[str]:
    """Lower-cased ``reason`` values of a Google HTTP error body (empty if none)."""
    content = getattr(exc, "content", b"")
    try:
        data = json.loads(content)
        errors = data["error"]["errors"]
        return {str(e["reason"]).lower() for e in errors}
    except (ValueError, KeyError, TypeError):
        return set()


def _map_api_error(
    action: str, exc: Exception, lang: Lang = LEGACY_LANG
) -> GmailAuthError | GmailApiError:
    """401/403 and a non-retryable ``RefreshError`` mean the authorization is
    gone (warn the owner); anything else is transient (retry next run)."""
    from google.auth.exceptions import RefreshError

    status = getattr(getattr(exc, "resp", None), "status", None)
    if status == 403 and _http_reasons(exc) & _TRANSIENT_403_REASONS:
        return GmailApiError(f"{action} throttled: {type(exc).__name__}")
    if status in _AUTH_HTTP_STATUSES or (
        isinstance(exc, RefreshError) and not exc.retryable
    ):
        return GmailAuthError(
            t("panel.gmail.auth_rejected", lang, action=action, command=AUTH_COMMAND)
        )
    return GmailApiError(f"{action} failed: {type(exc).__name__}")


def _installed_app_flow(credentials_path: Path, scopes: list[str]) -> Any:
    from google_auth_oauthlib.flow import InstalledAppFlow

    return InstalledAppFlow.from_client_secrets_file(str(credentials_path), scopes)


def run_auth_flow(
    credentials_path: Path,
    token_path: Path,
    *,
    flow_factory: Callable[[Path, list[str]], Any] | None = None,
    lang: Lang = LEGACY_LANG,
) -> None:
    """Interactive installed-app OAuth (browser) writing the token as ``0600``."""
    if not credentials_path.exists():
        raise GmailAuthError(
            t("panel.gmail.no_credentials", lang, path=credentials_path)
        )
    factory = flow_factory or _installed_app_flow
    try:
        flow = factory(credentials_path, [GMAIL_SCOPE])
    except Exception as exc:  # any malformed-secrets failure
        raise GmailAuthError(
            t(
                "panel.gmail.bad_credentials",
                lang,
                path=credentials_path,
                error=type(exc).__name__,
            )
        ) from exc
    try:
        credentials = flow.run_local_server(
            port=0, prompt="consent", access_type="offline"
        )
    except Exception as exc:  # denied consent, OAuth/browser errors
        raise GmailAuthError(
            t("panel.gmail.flow_failed", lang, error=type(exc).__name__)
        ) from exc
    if not getattr(credentials, "refresh_token", None):
        raise GmailAuthError(t("panel.gmail.no_refresh_token", lang))
    try:
        write_token_file(token_path, credentials.to_json())
    except OSError as exc:
        raise GmailAuthError(
            t(
                "panel.gmail.save_failed",
                lang,
                path=token_path,
                error=type(exc).__name__,
            )
        ) from exc
