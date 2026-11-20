"""Gmail client layer (M1): read-only scope, token file, parsing, error mapping.

Everything here is offline: the real Google client is exercised only through
injected fakes and fake token files under ``tmp_path`` (never real credentials).
"""

import base64
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from cva.gmail.client import (
    GMAIL_SCOPE,
    GmailApiError,
    GmailAuthError,
    GmailMessage,
    GoogleGmailClient,
    build_search_query,
    extract_plain_text,
    parse_message,
    run_auth_flow,
    write_token_file,
)
from tests.portability import has_posix_modes, posix_modes


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def test_scope_is_exactly_read_only() -> None:
    assert GMAIL_SCOPE == "https://www.googleapis.com/auth/gmail.readonly"


def test_search_query_skips_promotions_social_sent_and_spam() -> None:
    after = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    query = build_search_query(after)
    for token in (
        "-category:promotions",
        "-category:social",
        "-in:sent",
        "-in:spam",
        f"after:{int(after.timestamp())}",
    ):
        assert token in query.split()


def test_write_token_file_is_0600_even_over_a_loose_file(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "token.json"
    path.parent.mkdir()
    path.write_text("old", encoding="utf-8")
    path.chmod(0o644)
    write_token_file(path, '{"a": 1}')
    assert path.read_text(encoding="utf-8") == '{"a": 1}'
    if has_posix_modes():
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


@posix_modes
def test_write_token_file_creates_missing_directory(tmp_path: Path) -> None:
    path = tmp_path / "new" / "token.json"
    write_token_file(path, "{}")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_extract_plain_text_prefers_text_plain_part() -> None:
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": _b64("<b>html</b>")}},
            {"mimeType": "text/plain", "body": {"data": _b64("hola texto")}},
        ],
    }
    assert extract_plain_text(payload) == "hola texto"


def test_extract_plain_text_walks_nested_parts_and_ignores_html_only() -> None:
    nested = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [{"mimeType": "text/plain", "body": {"data": _b64("deep")}}],
            }
        ],
    }
    assert extract_plain_text(nested) == "deep"
    assert (
        extract_plain_text({"mimeType": "text/html", "body": {"data": _b64("x")}}) == ""
    )
    assert extract_plain_text({}) == ""


def _raw(**overrides: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": "abc123",
        "internalDate": str(
            int(datetime(2026, 11, 2, 9, 30, tzinfo=UTC).timestamp() * 1000)
        ),
        "snippet": "snippet text",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Ana RH <ana@acme.com>"},
                {"name": "Subject", "value": "Tu candidatura"},
            ],
            "body": {"data": _b64("cuerpo del mensaje")},
        },
    }
    raw.update(overrides)
    return raw


def test_parse_message_reads_headers_date_and_text() -> None:
    message = parse_message(_raw())
    assert message == GmailMessage(
        message_id="abc123",
        sender="Ana RH <ana@acme.com>",
        subject="Tu candidatura",
        received_at=datetime(2026, 11, 2, 9, 30, tzinfo=UTC),
        text="cuerpo del mensaje",
    )


def test_parse_message_falls_back_to_snippet_without_plain_body() -> None:
    raw = _raw()
    raw["payload"] = {
        "mimeType": "text/html",
        "headers": [{"name": "from", "value": "x@y.com"}],
        "body": {"data": _b64("<p>h</p>")},
    }
    message = parse_message(raw)
    assert message.text == "snippet text"
    assert message.subject == ""
    assert message.sender == "x@y.com"


class _Exec:
    def __init__(self, result: Any) -> None:
        self._result = result

    def execute(self) -> Any:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _FakeMessages:
    def __init__(self, pages: list[Any], messages: dict[str, Any]) -> None:
        self.pages = pages
        self.messages = messages
        self.list_calls: list[dict[str, Any]] = []

    def list(self, **kwargs: Any) -> _Exec:
        self.list_calls.append(kwargs)
        return _Exec(self.pages.pop(0))

    def get(self, *, userId: str, id: str, format: str) -> _Exec:
        assert userId == "me" and format == "full"
        return _Exec(self.messages[id])


class _FakeService:
    def __init__(self, messages: _FakeMessages) -> None:
        self._messages = messages

    def users(self) -> "_FakeService":
        return self

    def messages(self) -> _FakeMessages:
        return self._messages


def _client(fake: _FakeMessages) -> GoogleGmailClient:
    return GoogleGmailClient(
        Path("unused-credentials.json"),
        Path("unused-token.json"),
        service_factory=lambda: _FakeService(fake),
    )


def test_list_message_ids_paginates_and_uses_the_query() -> None:
    fake = _FakeMessages(
        pages=[
            {"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "t2"},
            {"messages": [{"id": "c"}]},
        ],
        messages={},
    )
    after = datetime(2026, 11, 1, tzinfo=UTC)
    assert _client(fake).list_message_ids(after) == (["a", "b", "c"], False)
    assert fake.list_calls[0]["q"] == build_search_query(after)
    assert fake.list_calls[1]["pageToken"] == "t2"


def test_list_message_ids_handles_empty_result() -> None:
    fake = _FakeMessages(pages=[{}], messages={})
    assert _client(fake).list_message_ids(datetime(2026, 11, 1, tzinfo=UTC)) == (
        [],
        False,
    )


def test_get_message_parses_the_raw_message() -> None:
    fake = _FakeMessages(pages=[], messages={"abc123": _raw()})
    assert _client(fake).get_message("abc123").subject == "Tu candidatura"


def test_api_errors_are_mapped_to_gmail_api_error() -> None:
    fake = _FakeMessages(pages=[RuntimeError("boom")], messages={})
    with pytest.raises(GmailApiError):
        _client(fake).list_message_ids(datetime(2026, 11, 1, tzinfo=UTC))


def test_auth_failure_is_mapped_to_gmail_auth_error() -> None:
    def factory() -> Any:
        raise GmailAuthError("revoked")

    client = GoogleGmailClient(Path("c"), Path("t"), service_factory=factory)
    with pytest.raises(GmailAuthError):
        client.list_message_ids(datetime(2026, 11, 1, tzinfo=UTC))


def test_missing_token_file_is_an_auth_error(tmp_path: Path) -> None:
    client = GoogleGmailClient(tmp_path / "c.json", tmp_path / "missing-token.json")
    with pytest.raises(GmailAuthError, match="gmail-auth"):
        client.list_message_ids(datetime(2026, 11, 1, tzinfo=UTC))


def _fake_token(path: Path, *, expired: bool) -> None:
    expiry = "2020-01-01T00:00:00Z" if expired else "2999-01-01T00:00:00Z"
    path.write_text(
        json.dumps(
            {
                "token": "fake-access",
                "refresh_token": "fake-refresh",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "fake-id",
                "client_secret": "fake-secret",
                "scopes": [GMAIL_SCOPE],
                "expiry": expiry,
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def test_expired_token_is_refreshed_and_written_back_0600(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from google.oauth2.credentials import Credentials

    token = tmp_path / "token.json"
    _fake_token(token, expired=True)
    refreshed: list[bool] = []

    def fake_refresh(self: Credentials, request: object) -> None:
        refreshed.append(True)
        self.token = "new-access"

    monkeypatch.setattr(Credentials, "refresh", fake_refresh)
    monkeypatch.setattr(
        "cva.gmail.client._build_service",
        lambda creds: _FakeService(_FakeMessages([{}], {})),
    )
    client = GoogleGmailClient(tmp_path / "c.json", token)
    assert client.list_message_ids(datetime(2026, 11, 1, tzinfo=UTC)) == ([], False)
    assert refreshed == [True]
    assert json.loads(token.read_text(encoding="utf-8"))["token"] == "new-access"
    if has_posix_modes():
        assert stat.S_IMODE(os.stat(token).st_mode) == 0o600


def test_revoked_refresh_token_raises_auth_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials

    token = tmp_path / "token.json"
    _fake_token(token, expired=True)

    def failing(self: Credentials, request: object) -> None:
        raise RefreshError("invalid_grant: Token has been expired or revoked.")

    monkeypatch.setattr(Credentials, "refresh", failing)
    client = GoogleGmailClient(tmp_path / "c.json", token)
    with pytest.raises(GmailAuthError, match="gmail-auth"):
        client.list_message_ids(datetime(2026, 11, 1, tzinfo=UTC))


class _FakeFlow:
    def __init__(self) -> None:
        self.ran = False

    def run_local_server(self, **kwargs: Any) -> Any:
        self.ran = True

        class _Creds:
            refresh_token = "fake-refresh"

            def to_json(self) -> str:
                return '{"token": "fake"}'

        return _Creds()


def test_run_auth_flow_writes_the_token_0600_with_the_read_only_scope(
    tmp_path: Path,
) -> None:
    seen: dict[str, Any] = {}
    flow = _FakeFlow()

    def factory(credentials_path: Path, scopes: list[str]) -> _FakeFlow:
        seen["credentials"] = credentials_path
        seen["scopes"] = scopes
        return flow

    token = tmp_path / "out" / "token.json"
    (tmp_path / "credentials.json").write_text("{}", encoding="utf-8")
    run_auth_flow(tmp_path / "credentials.json", token, flow_factory=factory)
    assert flow.ran
    assert seen["scopes"] == [GMAIL_SCOPE]
    assert seen["credentials"] == tmp_path / "credentials.json"
    assert token.read_text(encoding="utf-8") == '{"token": "fake"}'
    if has_posix_modes():
        assert stat.S_IMODE(os.stat(token).st_mode) == 0o600


def test_run_auth_flow_reports_a_missing_credentials_file(tmp_path: Path) -> None:
    with pytest.raises(GmailAuthError, match="credenciales"):
        run_auth_flow(tmp_path / "nope.json", tmp_path / "token.json")


# --- review follow-ups: auth flow, error mapping, pagination, charset ---------------


class _CredsWithRefresh:
    refresh_token = "fake-refresh"

    def to_json(self) -> str:
        return '{"token": "fresh"}'


class _CredsNoRefresh:
    refresh_token = None

    def to_json(self) -> str:
        return '{"token": "short-lived"}'


class _FlowReturning:
    def __init__(self, creds: Any) -> None:
        self.creds = creds
        self.kwargs: dict[str, Any] = {}

    def run_local_server(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return self.creds


def _secrets(tmp_path: Path) -> Path:
    path = tmp_path / "credentials.json"
    path.write_text("{}", encoding="utf-8")
    return path


def test_auth_flow_requests_offline_access_with_forced_consent(tmp_path: Path) -> None:
    flow = _FlowReturning(_CredsWithRefresh())
    run_auth_flow(
        _secrets(tmp_path), tmp_path / "t.json", flow_factory=lambda p, s: flow
    )
    assert flow.kwargs["prompt"] == "consent"
    assert flow.kwargs["access_type"] == "offline"


def test_auth_flow_refuses_credentials_without_a_refresh_token(
    tmp_path: Path,
) -> None:
    token = tmp_path / "t.json"
    token.write_text('{"token": "old-but-working"}', encoding="utf-8")
    with pytest.raises(GmailAuthError, match="refresh"):
        run_auth_flow(
            _secrets(tmp_path),
            token,
            flow_factory=lambda p, s: _FlowReturning(_CredsNoRefresh()),
        )
    assert token.read_text(encoding="utf-8") == '{"token": "old-but-working"}'


def test_auth_flow_maps_malformed_client_secrets(tmp_path: Path) -> None:
    def factory(path: Path, scopes: list[str]) -> Any:
        raise ValueError("Client secrets must be for a web or installed app.")

    with pytest.raises(GmailAuthError, match="credenciales"):
        run_auth_flow(_secrets(tmp_path), tmp_path / "t.json", flow_factory=factory)


@pytest.mark.parametrize("error", [Warning("access_denied"), RuntimeError("oauth")])
def test_auth_flow_maps_consent_denied_and_oauth_errors(
    tmp_path: Path, error: Exception
) -> None:
    class Flow:
        def run_local_server(self, **kwargs: Any) -> Any:
            raise error

    with pytest.raises(GmailAuthError, match="autoriz"):
        run_auth_flow(
            _secrets(tmp_path), tmp_path / "t.json", flow_factory=lambda p, s: Flow()
        )


def test_auth_flow_maps_a_token_write_failure(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(GmailAuthError, match="token"):
        run_auth_flow(
            _secrets(tmp_path),
            blocker / "token.json",
            flow_factory=lambda p, s: _FlowReturning(_CredsWithRefresh()),
        )


def test_corrupt_token_file_is_an_auth_error(tmp_path: Path) -> None:
    token = tmp_path / "token.json"
    token.write_text("{not json", encoding="utf-8")
    client = GoogleGmailClient(tmp_path / "c.json", token)
    with pytest.raises(GmailAuthError, match="gmail-auth"):
        client.get_message("m1")


def test_expired_token_without_refresh_token_is_an_auth_error(tmp_path: Path) -> None:
    token = tmp_path / "token.json"
    token.write_text(
        json.dumps(
            {
                "token": "t",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "i",
                "client_secret": "s",
                "scopes": [GMAIL_SCOPE],
                "expiry": "2020-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    client = GoogleGmailClient(tmp_path / "c.json", token)
    with pytest.raises(GmailAuthError, match="gmail-auth"):
        client.list_message_ids(datetime(2026, 11, 1, tzinfo=UTC))


@pytest.mark.parametrize(
    ("retryable", "expected"), [(True, GmailApiError), (False, GmailAuthError)]
)
def test_refresh_error_is_classified_by_its_retryable_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retryable: bool,
    expected: type[Exception],
) -> None:
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials

    token = tmp_path / "token.json"
    _fake_token(token, expired=True)

    def failing(self: Credentials, request: object) -> None:
        raise RefreshError("token endpoint failure", retryable=retryable)

    monkeypatch.setattr(Credentials, "refresh", failing)
    client = GoogleGmailClient(tmp_path / "c.json", token)
    with pytest.raises(expected):
        client.list_message_ids(datetime(2026, 11, 1, tzinfo=UTC))


def _http_error(status: int, reason: str | None = None) -> Exception:
    from googleapiclient.errors import HttpError

    resp = type("Resp", (), {"status": status, "reason": "x"})()
    body = b"{}"
    if reason is not None:
        body = json.dumps({"error": {"errors": [{"reason": reason}]}}).encode()
    return HttpError(resp, body)  # type: ignore[arg-type]


def _refresh_error() -> Exception:
    from google.auth.exceptions import RefreshError

    return RefreshError("revoked")


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_http_error(401), GmailAuthError),
        (_http_error(403), GmailAuthError),
        (_http_error(403, "insufficientPermissions"), GmailAuthError),
        (_http_error(403, "rateLimitExceeded"), GmailApiError),
        (_http_error(403, "userRateLimitExceeded"), GmailApiError),
        (_http_error(403, "dailyLimitExceeded"), GmailApiError),
        (_http_error(403, "quotaExceeded"), GmailApiError),
        (_http_error(403, "backendError"), GmailApiError),
        (_refresh_error(), GmailAuthError),
        (_http_error(503), GmailApiError),
        (RuntimeError("boom"), GmailApiError),
    ],
)
def test_errors_during_execute_are_mapped_for_list_and_get(
    error: Exception, expected: type[Exception]
) -> None:
    listing = _FakeMessages(pages=[error], messages={})
    with pytest.raises(expected):
        _client(listing).list_message_ids(datetime(2026, 11, 1, tzinfo=UTC))
    getting = _FakeMessages(pages=[], messages={"m1": error})
    with pytest.raises(expected):
        _client(getting).get_message("m1")


def test_list_message_ids_reports_truncation_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cva.gmail.client.MAX_MESSAGES_PER_LIST", 3)
    fake = _FakeMessages(
        pages=[
            {"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "t2"},
            {"messages": [{"id": "c"}, {"id": "d"}], "nextPageToken": "t3"},
        ],
        messages={},
    )
    ids, truncated = _client(fake).list_message_ids(datetime(2026, 11, 1, tzinfo=UTC))
    assert ids == ["a", "b", "c"]
    assert truncated is True


def test_list_message_ids_not_truncated_when_the_last_page_fits() -> None:
    fake = _FakeMessages(pages=[{"messages": [{"id": "a"}]}], messages={})
    ids, truncated = _client(fake).list_message_ids(datetime(2026, 11, 1, tzinfo=UTC))
    assert (ids, truncated) == (["a"], False)


def test_list_message_ids_can_be_bounded_by_a_before_date() -> None:
    fake = _FakeMessages(pages=[{}], messages={})
    after = datetime(2026, 11, 1, tzinfo=UTC)
    before = datetime(2026, 11, 3, tzinfo=UTC)
    _client(fake).list_message_ids(after, before)
    assert f"before:{int(before.timestamp())}" in fake.list_calls[0]["q"].split()


def test_body_is_decoded_with_the_declared_charset() -> None:
    body = "Lamentablemente, ñandú único"
    payload = {
        "mimeType": "text/plain",
        "headers": [
            {"name": "Content-Type", "value": 'text/plain; charset="ISO-8859-1"'}
        ],
        "body": {"data": base64.urlsafe_b64encode(body.encode("latin-1")).decode()},
    }
    assert extract_plain_text(payload) == body


def test_unknown_or_wrong_charset_falls_back_to_utf8() -> None:
    def part(charset: str) -> dict[str, Any]:
        return {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Content-Type", "value": f"text/plain; charset={charset}"}
            ],
            "body": {"data": _b64("café")},
        }

    assert extract_plain_text(part("x-no-such-charset")) == "café"
    assert extract_plain_text(part("us-ascii")) == "café"  # strict decode fails
    # Some codecs raise only the base UnicodeError (never UnicodeDecodeError).
    assert extract_plain_text(part("undefined")) == "café"
