"""HTTP fetch seam (design Interfaces, task 5.5): response value, protocol, httpx impl.

Exactly one HTTP attempt per :meth:`HttpxFetcher.get` — retry/backoff policy
belongs to callers (beacons/scheduler), never here (design D3). Transport
failures (timeouts, connection errors) are encoded as ``status=0`` responses,
which the wall classifier maps to ``TRANSIENT``.
"""

from dataclasses import dataclass
from typing import Protocol

import httpx

USER_AGENT = "cva-job-tracker/0.1 (personal job-search assistant)"


@dataclass(frozen=True)
class FetchResponse:
    """One HTTP response; ``status == 0`` encodes a transport failure."""

    status: int
    body: str
    final_url: str


class Fetcher(Protocol):
    """Async single-attempt fetch seam (injectable for tests)."""

    async def get(self, url: str) -> FetchResponse: ...


class HttpxFetcher:
    """httpx-backed :class:`Fetcher`: descriptive UA, timeouts, redirects, zero retries."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        user_agent: str = USER_AGENT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._user_agent = user_agent
        self._transport = transport

    async def get(self, url: str) -> FetchResponse:
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": self._user_agent},
                timeout=self._timeout_seconds,
                transport=self._transport,
                follow_redirects=True,
            ) as client:
                response = await client.get(url)
        except httpx.TransportError:
            # Transport failure after exactly one attempt → TRANSIENT via classify.
            return FetchResponse(status=0, body="", final_url=url)
        return FetchResponse(
            status=response.status_code, body=response.text, final_url=str(response.url)
        )
