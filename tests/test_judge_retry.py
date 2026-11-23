"""Judge retry-later (beta5-reliability S1(1)).

In hybrid mode a TRANSIENT judge failure (HTTP 5xx/529/429, timeouts,
connection errors) on the bridge auto-analysis keeps the offer pending with a
backoff (``analyze_attempts``/``next_analyze_at``) and sends no card; the
heartbeat sweep re-drives it when due. After the judge retry budget the offer
falls back to the keyword score as before, with a note. Non-transient judge
failures keep the immediate fallback. Zero network, fake LLM only.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from cva.fetch import FetchResponse
from cva.ingest import job_id_from_url
from cva.llm import LlmResponseError
from cva.match.judge import (
    JUDGE_RETRY_DELAYS,
    JudgeError,
    OfferJudgment,
    is_transient_judge_error,
)
from cva.service import JUDGE_UNAVAILABLE_NOTE, JobService
from cva.tracker import OfferInput, OfferRepository
from tests.test_service import (
    JSONLD_BODY,
    OFFER_TEXT,
    OFFER_URL,
    FakeFetcher,
    _extraction,
    _judgment,
    _SequencedJudgeLlm,
    make_service,
)

NOW = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)


def _http_error(status: int) -> LlmResponseError:
    return LlmResponseError(f"LLM endpoint returned HTTP {status}", status=status)


def _timeout_error() -> LlmResponseError:
    try:
        try:
            raise httpx.ReadTimeout("timed out")
        except httpx.ReadTimeout as exc:
            raise LlmResponseError("LLM unreachable after 3 attempt(s)") from exc
    except LlmResponseError as wrapped:
        return wrapped


class _Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


class _Notify:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def __call__(self, text: str, keyboard: Any) -> None:
        self.calls.append((text, keyboard))


def _setup(
    tmp_path: Path,
    outcomes: list[OfferJudgment | Exception],
    *,
    analyze_retry_max: int = 5,
) -> tuple[JobService, OfferRepository, int, _SequencedJudgeLlm, _Clock]:
    llm = _SequencedJudgeLlm(_extraction(), judge_outcomes=outcomes)
    clock = _Clock()
    service, repo = make_service(
        tmp_path,
        llm,
        FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
        match_mode="hybrid",
        now_fn=clock,
        analyze_retry_max=analyze_retry_max,
    )
    offer_id = repo.upsert_offer(
        OfferInput(
            job_id=job_id_from_url(OFFER_URL),
            title="AI Engineer",
            company="Acme",
            url=OFFER_URL,
            text=OFFER_TEXT,
            source="link",
        )
    )
    return service, repo, offer_id, llm, clock


def _run(service: JobService, offer_id: int, notify: _Notify) -> Any:
    return asyncio.run(
        service.analyze_new_bridge_job(
            offer_id,
            url=OFFER_URL,
            title="AI Engineer",
            company="Acme",
            notify=notify,
            min_score=0,  # every analysed offer gets a card: the card is observable
            text=OFFER_TEXT,
        )
    )


# --- classification -----------------------------------------------------------


def test_transient_classification() -> None:
    for status in (429, 500, 502, 503, 529):
        assert is_transient_judge_error(_http_error(status))
    assert is_transient_judge_error(_timeout_error())
    for status in (400, 401, 403, 404, 422):
        assert not is_transient_judge_error(_http_error(status))
    assert not is_transient_judge_error(JudgeError("judge cited unknown claim id(s)"))
    assert not is_transient_judge_error(LlmResponseError("malformed envelope"))


# --- retry-later ----------------------------------------------------------------


def test_transient_judge_failure_keeps_the_offer_pending_without_a_card(
    tmp_path: Path,
) -> None:
    service, repo, offer_id, llm, _clock = _setup(tmp_path, [_http_error(503)])
    notify = _Notify()

    outcome = _run(service, offer_id, notify)

    assert outcome.zone == "pending" and outcome.score is None
    assert notify.calls == []
    row = repo.get_offer(offer_id)
    assert row.estado != "analizada"
    assert row.analyze_attempts == 1
    assert row.next_analyze_at == (NOW + JUDGE_RETRY_DELAYS[0]).isoformat()
    # All-or-nothing: no score, no judgment, no note persisted.
    assert repo.latest_match(offer_id) is None
    assert repo.latest_judge_result(offer_id) is None
    assert JUDGE_UNAVAILABLE_NOTE not in (row.notas or "")
    assert llm.judge_calls == 1


def test_backoff_grows_with_each_transient_failure(tmp_path: Path) -> None:
    errors: list[OfferJudgment | Exception] = [
        _http_error(503),
        _timeout_error(),
        _http_error(429),
        _http_error(529),
    ]
    service, repo, offer_id, _llm, clock = _setup(tmp_path, errors)
    notify = _Notify()
    for attempt, delay in enumerate(JUDGE_RETRY_DELAYS, start=1):
        _run(service, offer_id, notify)
        row = repo.get_offer(offer_id)
        assert row.analyze_attempts == attempt
        assert row.next_analyze_at == (clock.now + delay).isoformat()
        clock.now += delay
    assert notify.calls == []
    assert [delay.total_seconds() for delay in JUDGE_RETRY_DELAYS] == [
        600,
        1800,
        7200,
        21600,
    ]


def test_pending_offer_is_picked_up_by_the_sweep_only_when_due(tmp_path: Path) -> None:
    service, _repo, offer_id, _llm, _clock = _setup(tmp_path, [_http_error(503)])
    _run(service, offer_id, _Notify())

    early = (NOW + JUDGE_RETRY_DELAYS[0] - timedelta(seconds=1)).isoformat()
    due = (NOW + JUDGE_RETRY_DELAYS[0]).isoformat()
    assert service.list_retry_pending(early, service.analyze_retry_max) == []
    assert [
        r.id for r in service.list_retry_pending(due, service.analyze_retry_max)
    ] == [offer_id]


def test_later_success_judges_and_sends_the_card(tmp_path: Path) -> None:
    service, repo, offer_id, _llm, clock = _setup(
        tmp_path, [_http_error(503), _judgment(score=82)]
    )
    notify = _Notify()
    _run(service, offer_id, notify)
    clock.now += JUDGE_RETRY_DELAYS[0]

    outcome = _run(service, offer_id, notify)

    assert outcome.zone == "above" and outcome.score == 82
    assert len(notify.calls) == 1
    assert "82" in notify.calls[0][0]
    row = repo.get_offer(offer_id)
    assert row.estado == "analizada"
    assert row.analyze_attempts == 0 and row.next_analyze_at is None
    assert repo.latest_judge_result(offer_id) is not None
    assert JUDGE_UNAVAILABLE_NOTE not in (row.notas or "")


def test_exhausted_judge_retries_fall_back_to_the_keyword_score(
    tmp_path: Path,
) -> None:
    budget = len(JUDGE_RETRY_DELAYS)
    errors: list[OfferJudgment | Exception] = [
        _http_error(503) for _ in range(budget + 1)
    ]
    service, repo, offer_id, llm, clock = _setup(tmp_path, errors)
    notify = _Notify()
    for delay in JUDGE_RETRY_DELAYS:
        _run(service, offer_id, notify)
        clock.now += delay
    assert notify.calls == []

    outcome = _run(service, offer_id, notify)

    assert llm.judge_calls == budget + 1
    keyword = repo.latest_match(offer_id)
    assert keyword is not None and outcome.score == keyword.score
    assert repo.latest_judge_result(offer_id) is None
    row = repo.get_offer(offer_id)
    assert row.estado == "analizada"
    assert row.analyze_attempts == 0 and row.next_analyze_at is None
    assert JUDGE_UNAVAILABLE_NOTE in (row.notas or "")
    assert len(notify.calls) == 1
    assert "keyword" in notify.calls[0][0] or "palabras clave" in notify.calls[0][0]


def test_retry_budget_never_exceeds_the_sweep_budget(tmp_path: Path) -> None:
    """``analyze_retry_max = 0`` disables the sweep: no deferral is possible,
    so a transient judge failure falls back at once (today's behaviour)."""
    service, repo, offer_id, _llm, _clock = _setup(
        tmp_path, [_http_error(503)], analyze_retry_max=0
    )
    notify = _Notify()

    _run(service, offer_id, notify)

    assert repo.latest_match(offer_id) is not None
    assert repo.get_offer(offer_id).estado == "analizada"
    assert len(notify.calls) == 1


def test_non_transient_judge_failure_falls_back_immediately(tmp_path: Path) -> None:
    service, repo, offer_id, _llm, _clock = _setup(
        tmp_path, [JudgeError("judge returned blank/placeholder text: x")]
    )
    notify = _Notify()

    _run(service, offer_id, notify)

    row = repo.get_offer(offer_id)
    assert row.estado == "analizada"
    assert row.analyze_attempts == 0 and row.next_analyze_at is None
    assert repo.latest_match(offer_id) is not None
    assert repo.latest_judge_result(offer_id) is None
    assert JUDGE_UNAVAILABLE_NOTE not in (row.notas or "")
    assert len(notify.calls) == 1


def test_manual_analyze_keeps_the_immediate_fallback_on_a_transient_error(
    tmp_path: Path,
) -> None:
    """A direct ``analyze()`` (/analizar, cli analyze) is awaited by the owner:
    it still falls back at once, as before (pin)."""
    service, repo, offer_id, _llm, _clock = _setup(tmp_path, [_http_error(503)])

    result = asyncio.run(service.analyze(offer_id))

    match = repo.latest_match(offer_id)
    assert match is not None and result.score == match.score
    assert repo.get_offer(offer_id).estado == "analizada"


def test_extraction_failure_keeps_the_generic_retry_path(tmp_path: Path) -> None:
    """Extraction failures are unchanged: generic retry with the heartbeat
    interval, no card, nothing persisted."""

    class _BrokenExtraction(_SequencedJudgeLlm):
        async def json_completion(
            self, system: str, user: str, schema: type[object], *, model: Any = None
        ) -> object:
            if schema is OfferJudgment:
                return await super().json_completion(system, user, schema, model=model)
            raise LlmResponseError("LLM endpoint returned HTTP 503", status=503)

    llm = _BrokenExtraction(_extraction(), judge_outcomes=[_judgment(score=80)])
    clock = _Clock()
    service, repo = make_service(
        tmp_path,
        llm,
        FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
        match_mode="hybrid",
        now_fn=clock,
    )
    offer_id = repo.upsert_offer(
        OfferInput(
            job_id=job_id_from_url(OFFER_URL),
            title="AI Engineer",
            company="Acme",
            url=OFFER_URL,
            text=OFFER_TEXT,
            source="link",
        )
    )
    notify = _Notify()

    _run(service, offer_id, notify)

    row = repo.get_offer(offer_id)
    assert notify.calls == []
    assert row.analyze_attempts == 1
    assert row.next_analyze_at == (NOW + timedelta(seconds=600)).isoformat()
    assert repo.latest_match(offer_id) is None


def test_a_provider_that_never_fails_behaves_as_before(tmp_path: Path) -> None:
    service, repo, offer_id, llm, _clock = _setup(tmp_path, [_judgment(score=80)])
    notify = _Notify()

    outcome = _run(service, offer_id, notify)

    assert outcome.zone == "above" and outcome.score == 80
    assert llm.judge_calls == 1
    assert len(notify.calls) == 1
    row = repo.get_offer(offer_id)
    assert row.estado == "analizada"
    assert row.analyze_attempts == 0 and row.next_analyze_at is None
    assert JUDGE_UNAVAILABLE_NOTE not in (row.notas or "")
