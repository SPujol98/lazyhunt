"""JobService use-cases (telegram-bot spec at the service seam; design D12, tasks 8.1–8.2).

Zero network, zero real LLM: FakeLlmClient (call-counting) + FakeFetcher (canned
responses) over a tmp SQLite repository. The claims DB and synonym map load
from the real curated files (read-only inputs), so breakdown statuses are the
deterministic scorer's output against the curated data.
"""

import asyncio
import hashlib
import json
import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pypdf import PdfReader

from cva.claimsdb import load_claims
from cva.fetch import FetchResponse
from cva.ingest import WallError
from cva.llm import LlmResponseError
from cva.match import OfferExtraction, Requirement
from cva.match.extraction import ExtractionCache
from cva.match.judge import FitItem, OfferJudgment, Wall
from cva.match.scorer import load_synonyms
from cva.service import BASE_CAMBIOS, JobService, ServiceError
from cva.tracker import OfferInput, OfferRepository
from cva.wall import WallKind, WallVerdict
from tests.demo import DEMO_BASE_PDFS, DEMO_CLAIMS, DEMO_MASTERS, DEMO_SYNONYMS

CLAIMS_PATH = DEMO_CLAIMS
SYNONYMS_PATH = DEMO_SYNONYMS
MASTERS_DIR = DEMO_MASTERS
PROFILE = "default"

OFFER_TEXT = (
    "AI Engineer en Acme (Barcelona). Imprescindible experiencia con Python. "
    "Se valoran conocimientos de containers para las integraciones de datos. "
    "También gestionarás clusters de Kubernetes en producción. Ofrecemos "
    "formación continua y contrato indefinido."
)

JSONLD_BODY = (
    '<html><head><script type="application/ld+json">'
    '{"@type": "JobPosting", "title": "AI Engineer", '
    '"hiringOrganization": {"name": "Acme"}, '
    '"description": "Imprescindible experiencia con Python y Kubernetes."}'
    "</script></head></html>"
)
SHELL_BODY = (
    '<html><head><title>LinkedIn</title></head><body><div id="app-js-root"></div>'
    "</body></html>"
)
OFFER_URL = "https://www.linkedin.com/jobs/view/4459804598/"


class FakeLlmClient:
    """Call-counting stand-in for the LlmClient protocol (zero network).

    ``judge_reply``/``judge_error`` (llm-offer-judge hybrid mode): a request
    for the ``OfferJudgment`` schema (the judge step) returns/raises them
    instead of the extraction ``reply`` — every other schema (extraction) is
    unaffected, so every pre-existing keyword-mode test using this class
    keeps working unmodified.
    """

    def __init__(
        self,
        reply: OfferExtraction,
        *,
        judge_reply: OfferJudgment | None = None,
        judge_error: Exception | None = None,
    ) -> None:
        self.model = "test-model"
        self.calls = 0
        self.judge_calls = 0
        self.last_model: str | None = None
        # Per-step model seen, kept SEPARATE from last_model (which the last
        # call overwrites) — needed to prove extraction_model and
        # judge_model are forwarded to their OWN call, independently of each
        # other and of call order.
        self.extraction_model_seen: str | None = None
        self.judge_model_seen: str | None = None
        self._reply = reply
        self._judge_reply = judge_reply
        self._judge_error = judge_error

    async def json_completion(
        self,
        system: str,
        user: str,
        schema: type[object],
        *,
        model: str | None = None,
    ) -> object:
        self.last_model = model
        if schema is OfferJudgment:
            self.judge_calls += 1
            self.judge_model_seen = model
            if self._judge_error is not None:
                raise self._judge_error
            assert self._judge_reply is not None, (
                "OfferJudgment requested but no judge_reply was configured"
            )
            return self._judge_reply
        self.calls += 1
        self.extraction_model_seen = model
        return self._reply


class _SequencedJudgeLlm(FakeLlmClient):
    """Like ``FakeLlmClient``, but each judge call pops the NEXT outcome
    (reply or exception) off a fixed sequence — needed to prove behavior
    ACROSS two ``analyze()`` calls for the same offer (e.g. a re-analysis
    whose judge call fails after an earlier one succeeded)."""

    def __init__(
        self, reply: OfferExtraction, *, judge_outcomes: list[OfferJudgment | Exception]
    ) -> None:
        super().__init__(reply)
        self._judge_outcomes = list(judge_outcomes)

    async def json_completion(
        self,
        system: str,
        user: str,
        schema: type[object],
        *,
        model: str | None = None,
    ) -> object:
        self.last_model = model
        if schema is OfferJudgment:
            self.judge_calls += 1
            outcome = self._judge_outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        self.calls += 1
        return self._reply


class FakeFetcher:
    """Canned fetcher counting calls — proves single-attempt semantics."""

    def __init__(self, response: FetchResponse) -> None:
        self._response = response
        self.calls: list[str] = []

    async def get(self, url: str) -> FetchResponse:
        self.calls.append(url)
        return self._response


def _extraction() -> OfferExtraction:
    return OfferExtraction(
        title="AI Engineer",
        company="Acme",
        requirements=[
            Requirement(
                name="Python",
                category="skill",
                must_have=True,
                evidence="experiencia con Python",
            ),
            Requirement(
                name="containers",
                category="tool",
                must_have=False,
                evidence="conocimientos de containers",
            ),
            Requirement(
                name="Kubernetes",
                category="tool",
                must_have=True,
                evidence="clusters de Kubernetes",
            ),
        ],
        keywords=["Python", "containers", "Kubernetes", "Python"],
    )


_SCORE_RECIPES: dict[int, dict[str, int]] = {
    # must ✓ = 3 credit, nice ✓ = 1, ✗ = 0 → round(100 · credit / weight)
    65: {"must_yes": 13, "must_no": 7},  # 39/60 → 65 (at-gate)
    64: {"must_yes": 16, "must_no": 9},  # 48/75 → 64 (one below gate)
    50: {"must_yes": 5, "must_no": 5},  # 15/30 → 50 (zone floor, gate 65 zone 15)
    49: {"must_yes": 24, "must_no": 25},  # 72/147 → 49 (one below floor)
    0: {"must_no": 3},  # 0/9 → 0 (nothing like a match)
}


def _extraction_scored(target: int) -> OfferExtraction:
    """Extraction whose deterministic score against the curated DB is `target`."""
    recipe = _SCORE_RECIPES[target]
    requirements = (
        [
            Requirement(
                name="Python", category="skill", must_have=True, evidence="Python"
            )
            for _ in range(recipe.get("must_yes", 0))
        ]
        + [
            Requirement(
                name="Zorblop",
                category="skill",
                must_have=True,
                evidence="Imprescindible",
            )
            for _ in range(recipe.get("must_no", 0))
        ]
        + [
            Requirement(
                name="containers", category="tool", must_have=False, evidence="ev"
            )
            for _ in range(recipe.get("nice_yes", 0))
        ]
        + [
            Requirement(name="Wubble", category="tool", must_have=False, evidence="ev")
            for _ in range(recipe.get("nice_no", 0))
        ]
    )
    return OfferExtraction(
        title="AI Engineer", company="Acme", requirements=requirements, keywords=[]
    )


def _connect(tmp_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(tmp_path / "app.db")


def make_service(
    tmp_path: Path,
    llm: FakeLlmClient,
    fetcher: FakeFetcher,
    *,
    threshold: int = 65,
    analyze_retry_max: int = 5,
    gray_zone: int = 15,
    now_fn: Callable[[], datetime] | None = None,
    match_mode: str = "keyword",
    extraction_model: str | None = None,
    judge_model: str | None = None,
) -> tuple[JobService, OfferRepository]:
    conn = _connect(tmp_path)
    repo = OfferRepository(conn)
    db = load_claims(CLAIMS_PATH)
    syn = load_synonyms(SYNONYMS_PATH, db)
    service = JobService(
        repo=repo,
        llm=llm,
        fetcher=fetcher,
        db=db,
        syn=syn,
        cache=ExtractionCache(conn),
        threshold=threshold,
        masters_dir=MASTERS_DIR,
        analyze_retry_max=analyze_retry_max,
        gray_zone=gray_zone,
        now_fn=now_fn,
        match_mode=match_mode,  # type: ignore[arg-type]
        extraction_model=extraction_model,
        judge_model=judge_model,
    )
    return service, repo


def _paste_service(tmp_path: Path) -> tuple[JobService, OfferRepository, FakeLlmClient]:
    llm = FakeLlmClient(_extraction())
    fetcher = FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    service, repo = make_service(tmp_path, llm, fetcher)
    return service, repo, llm


def test_ingest_paste_persists_offer(tmp_path: Path) -> None:
    service, repo, _llm = _paste_service(tmp_path)
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    row = repo.get_offer(offer_id)
    assert row.estado == "pendiente_analisis"
    assert row.source_data == OFFER_TEXT
    assert row.origen == "otro"


def test_ingest_url_parses_jsonld(tmp_path: Path) -> None:
    service, repo, _llm = _paste_service(tmp_path)
    offer_id = asyncio.run(service.ingest(OFFER_URL, None))
    row = repo.get_offer(offer_id)
    assert row.posicion == "AI Engineer"
    assert row.empresa == "Acme"
    assert row.origen == "linkedin"
    assert row.url_oferta == OFFER_URL


def test_walled_fetch_propagates_wallerror(tmp_path: Path) -> None:
    llm = FakeLlmClient(_extraction())
    fetcher = FakeFetcher(FetchResponse(200, SHELL_BODY, OFFER_URL))
    service, repo = make_service(tmp_path, llm, fetcher)
    with pytest.raises(WallError):
        asyncio.run(service.ingest(OFFER_URL, None))
    with pytest.raises(Exception, match="not found"):
        repo.get_offer(1)


def test_analyze_returns_breakdown(tmp_path: Path) -> None:
    service, _repo, llm = _paste_service(tmp_path)
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    result = asyncio.run(service.analyze(offer_id))
    assert llm.calls == 1
    assert result.threshold == 65
    assert isinstance(result.score, int)
    assert 0 <= result.score <= 100
    statuses = {m.status for m in result.breakdown}
    assert statuses <= {"✓", "~", "✗"}
    by_name = {m.requirement.name: m.status for m in result.breakdown}
    assert by_name["Python"] == "✓"
    assert by_name["containers"] == "~"
    assert by_name["Kubernetes"] == "✗"
    row = asyncio.run(_get_row(service, offer_id))
    assert row.estado == "analizada"


async def _get_row(service: JobService, offer_id: int):
    return service.offer(offer_id)


def _judgment(
    *, score: int = 80, verdict: str = "apply", hard_wall: str | None = None
) -> OfferJudgment:
    return OfferJudgment(
        score=score,
        verdict=verdict,  # type: ignore[arg-type]
        core_role="AI Engineer junior orientado a RAG/LLMs",
        fit=[FitItem(requirement="Python", claim_ids=["skill-python"])],
        walls=[Wall(text=hard_wall, hard=True)] if hard_wall else [],
        gaps=[],
        cv_base="AI_ES",
        tailoring_hints=[],
        rationale="Buen encaje.",
    )


# --- match_mode "hybrid" (llm-offer-judge design) ---------------------------


def test_analyze_keyword_mode_never_calls_the_judge(tmp_path: Path) -> None:
    """Default match_mode="keyword": byte-identical to pre-feature behavior —
    the judge is never invoked, so a fake with no judge_reply never explodes."""
    service, _repo, llm = _paste_service(tmp_path)
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    result = asyncio.run(service.analyze(offer_id))
    assert result.score == 50  # the plain keyword score, unchanged
    assert llm.judge_calls == 0


def test_analyze_hybrid_mode_uses_judge_score_for_the_result(tmp_path: Path) -> None:
    llm = FakeLlmClient(_extraction(), judge_reply=_judgment(score=80))
    fetcher = FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    service, _repo = make_service(tmp_path, llm, fetcher, match_mode="hybrid")
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))

    result = asyncio.run(service.analyze(offer_id))

    assert result.score == 80  # judge score, not the keyword score (50)
    assert llm.judge_calls == 1
    # The keyword breakdown is KEPT for transparency (design: "breakdown kept").
    by_name = {m.requirement.name: m.status for m in result.breakdown}
    assert by_name["Python"] == "✓"


def test_analyze_hybrid_mode_persists_the_judgment(tmp_path: Path) -> None:
    llm = FakeLlmClient(_extraction(), judge_reply=_judgment(score=80, verdict="apply"))
    fetcher = FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    service, repo = make_service(tmp_path, llm, fetcher, match_mode="hybrid")
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))

    asyncio.run(service.analyze(offer_id))

    row = repo.latest_judge_result(offer_id)
    assert row is not None
    assert row.score == 80
    assert row.verdict == "apply"
    judgment = OfferJudgment.model_validate_json(row.judgment_json)
    assert judgment.core_role == "AI Engineer junior orientado a RAG/LLMs"


def test_analyze_hybrid_mode_falls_back_to_keyword_score_on_judge_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Judge failure in hybrid mode: never lose the analysis — fall back to
    the keyword score and log exactly one WARNING (design: "never lose the
    analysis")."""
    llm = FakeLlmClient(
        _extraction(), judge_error=LlmResponseError("claude CLI exited with status 1")
    )
    fetcher = FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    service, repo = make_service(tmp_path, llm, fetcher, match_mode="hybrid")
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))

    with caplog.at_level(logging.WARNING, logger="cva.service"):
        result = asyncio.run(service.analyze(offer_id))

    assert result.score == 50  # keyword fallback score
    assert repo.latest_judge_result(offer_id) is None  # nothing persisted
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert str(offer_id) in warnings[0].getMessage()


def test_analyze_hybrid_mode_clears_stale_judgment_on_reanalysis_failure(
    tmp_path: Path,
) -> None:
    """T3a robustness follow-up: a re-analysis whose judge call fails must
    NEVER leave an earlier successful judgment looking current — the score
    falls back to keyword AND the stored judgment is cleared, so
    latest_judgment() reports the fallback truthfully instead of a stale
    "current" judgment from the first (successful) analysis."""
    llm = _SequencedJudgeLlm(
        _extraction(),
        judge_outcomes=[_judgment(score=80), LlmResponseError("boom")],
    )
    fetcher = FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    service, repo = make_service(tmp_path, llm, fetcher, match_mode="hybrid")
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))

    first = asyncio.run(service.analyze(offer_id))
    assert first.score == 80
    assert repo.latest_judge_result(offer_id) is not None

    second = asyncio.run(service.analyze(offer_id))

    assert second.score == 50  # keyword fallback, NOT the stale judge score
    assert repo.latest_judge_result(offer_id) is None  # stale row cleared

    judgment, fallback = service.latest_judgment(offer_id)
    assert judgment is None
    assert fallback is True


class _TimedLlm(FakeLlmClient):
    """Extraction and judge calls that each take ``delay`` seconds of real
    async waiting and succeed or raise as configured — proves the two LLM
    calls of a hybrid ``analyze()`` overlap, and pins every failure combo."""

    def __init__(
        self,
        *,
        extract_delay: float = 0.0,
        judge_delay: float = 0.0,
        extract_error: Exception | None = None,
        judge_error: Exception | None = None,
    ) -> None:
        super().__init__(_extraction(), judge_reply=_judgment(score=80))
        self._extract_delay = extract_delay
        self._judge_delay = judge_delay
        self._extract_error = extract_error
        self._judge_error_timed = judge_error
        self.judge_finished = False

    async def json_completion(
        self,
        system: str,
        user: str,
        schema: type[object],
        *,
        model: str | None = None,
    ) -> object:
        if schema is OfferJudgment:
            self.judge_calls += 1
            await asyncio.sleep(self._judge_delay)
            self.judge_finished = True
            if self._judge_error_timed is not None:
                raise self._judge_error_timed
            return self._judge_reply
        self.calls += 1
        await asyncio.sleep(self._extract_delay)
        if self._extract_error is not None:
            raise self._extract_error
        return self._reply


def _seed_prior_analysis(repo: OfferRepository, offer_id: int) -> None:
    """An earlier successful hybrid analysis, stored straight in the DB."""
    repo.save_judge_result(
        offer_id,
        score=77,
        verdict="apply",
        judgment_json=_judgment(score=77).model_dump_json(),
    )
    repo.save_match_result(offer_id, score=77, threshold=65, breakdown_json="{}")


def _persisted(repo: OfferRepository, offer_id: int) -> tuple[Any, Any, Any]:
    return (
        repo.get_offer(offer_id),
        repo.latest_match(offer_id),
        repo.latest_judge_result(offer_id),
    )


def _timed_service(
    tmp_path: Path, llm: _TimedLlm
) -> tuple[JobService, OfferRepository, int]:
    fetcher = FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    service, repo = make_service(tmp_path, llm, fetcher, match_mode="hybrid")
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    return service, repo, offer_id


def test_analyze_hybrid_runs_extraction_and_judge_concurrently(
    tmp_path: Path,
) -> None:
    """The judge never reads the extraction, so both LLM calls overlap: the
    analysis takes about the slower call, not the sum of both."""
    import time

    llm = _TimedLlm(extract_delay=0.3, judge_delay=0.3)
    service, repo, offer_id = _timed_service(tmp_path, llm)

    started = time.perf_counter()
    result = asyncio.run(service.analyze(offer_id))
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5  # sequential would be >= 0.6
    assert result.score == 80
    judge_row = repo.latest_judge_result(offer_id)
    assert judge_row is not None and judge_row.score == 80


def test_analyze_hybrid_judge_fails_while_extraction_pending_falls_back(
    tmp_path: Path,
) -> None:
    """Judge fails first (extraction still in flight): same keyword fallback
    and stale-judgment clearing as the sequential path."""
    llm = _TimedLlm(extract_delay=0.1, judge_error=LlmResponseError("boom"))
    service, repo, offer_id = _timed_service(tmp_path, llm)
    _seed_prior_analysis(repo, offer_id)

    result = asyncio.run(service.analyze(offer_id))

    assert result.score == 50  # keyword fallback
    assert repo.latest_judge_result(offer_id) is None  # stale row cleared
    match = repo.latest_match(offer_id)
    assert match is not None and match.score == 50
    assert repo.get_offer(offer_id).estado == "analizada"


def test_analyze_hybrid_extraction_fails_judge_ok_persists_nothing(
    tmp_path: Path,
) -> None:
    """Extraction fails, judge would succeed: the extraction error propagates
    and NOTHING is written — the earlier analysis stays exactly as it was
    (the sequential path never reached the judge)."""
    llm = _TimedLlm(extract_error=LlmResponseError("extract down"))
    service, repo, offer_id = _timed_service(tmp_path, llm)
    _seed_prior_analysis(repo, offer_id)
    before = _persisted(repo, offer_id)

    with pytest.raises(LlmResponseError, match="extract down"):
        asyncio.run(service.analyze(offer_id))

    assert _persisted(repo, offer_id) == before


def test_analyze_hybrid_extraction_fails_cancels_the_inflight_judge(
    tmp_path: Path,
) -> None:
    """A judge still running when extraction fails is cancelled: its result
    is never persisted and the error surfaces without waiting for it."""
    import time

    llm = _TimedLlm(
        extract_delay=0.05, judge_delay=1.0, extract_error=LlmResponseError("x")
    )
    service, repo, offer_id = _timed_service(tmp_path, llm)
    _seed_prior_analysis(repo, offer_id)
    before = _persisted(repo, offer_id)

    started = time.perf_counter()
    with pytest.raises(LlmResponseError):
        asyncio.run(service.analyze(offer_id))

    assert time.perf_counter() - started < 0.5
    assert llm.judge_finished is False
    assert _persisted(repo, offer_id) == before


def test_analyze_hybrid_both_fail_raises_extraction_error_keeps_judgment(
    tmp_path: Path,
) -> None:
    """Both calls fail: the extraction error wins (as sequentially) and the
    earlier judgment is NOT cleared — clearing only happens when the
    analysis itself succeeds with a keyword fallback."""
    llm = _TimedLlm(
        extract_delay=0.05,
        extract_error=LlmResponseError("extract down"),
        judge_error=LlmResponseError("judge down"),
    )
    service, repo, offer_id = _timed_service(tmp_path, llm)
    _seed_prior_analysis(repo, offer_id)
    before = _persisted(repo, offer_id)

    with pytest.raises(LlmResponseError, match="extract down"):
        asyncio.run(service.analyze(offer_id))

    assert _persisted(repo, offer_id) == before


def test_analyze_hybrid_unexpected_judge_error_propagates_persisting_nothing(
    tmp_path: Path,
) -> None:
    """A non-LLM judge failure (a bug) is not swallowed into the fallback:
    it propagates after a successful extraction and nothing is written."""
    llm = _TimedLlm(extract_delay=0.05, judge_error=RuntimeError("bug"))
    service, repo, offer_id = _timed_service(tmp_path, llm)
    _seed_prior_analysis(repo, offer_id)
    before = _persisted(repo, offer_id)

    with pytest.raises(RuntimeError, match="bug"):
        asyncio.run(service.analyze(offer_id))

    assert _persisted(repo, offer_id) == before


def test_latest_judgment_ignores_a_stale_row_when_mode_is_keyword(
    tmp_path: Path,
) -> None:
    """T3b: even a leftover judge_results row from an earlier hybrid-mode
    run must never produce a fallback note once match_mode is "keyword" —
    the mode check wins over row presence."""
    llm = FakeLlmClient(_extraction(), judge_reply=_judgment(score=80))
    fetcher = FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    hybrid_service, repo = make_service(tmp_path, llm, fetcher, match_mode="hybrid")
    offer_id = asyncio.run(hybrid_service.ingest(None, OFFER_TEXT))
    asyncio.run(hybrid_service.analyze(offer_id))
    assert repo.latest_judge_result(offer_id) is not None  # stale row present

    keyword_service, _repo = make_service(tmp_path, llm, fetcher, match_mode="keyword")

    judgment, fallback = keyword_service.latest_judgment(offer_id)
    assert judgment is None
    assert fallback is False


def test_analyze_forwards_extraction_and_judge_model_overrides(
    tmp_path: Path,
) -> None:
    """Each step's model override must reach its OWN call — extraction gets
    extraction_model, the judge gets judge_model, independently of each
    other (previously only the judge's model was ever asserted, since it
    happens last and simply overwrote a single shared `last_model`)."""
    llm = FakeLlmClient(_extraction(), judge_reply=_judgment())
    fetcher = FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    service, _repo = make_service(
        tmp_path,
        llm,
        fetcher,
        match_mode="hybrid",
        extraction_model="haiku",
        judge_model="sonnet",
    )
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))

    asyncio.run(service.analyze(offer_id))

    assert llm.extraction_model_seen == "haiku"
    assert llm.judge_model_seen == "sonnet"


def test_judge_eval_forwards_the_configured_threshold(tmp_path: Path) -> None:
    """T2d robustness follow-up: judge_eval() must grade against the SAME
    threshold analyze() uses (Settings.threshold), never a hardcoded
    default — otherwise the eval harness silently disagrees with
    production about verdict bands."""
    offers_dir = tmp_path / "offers"
    offers_dir.mkdir()
    (offers_dir / "a.txt").write_text("Offer A", encoding="utf-8")
    labels_path = tmp_path / "labels.json"
    labels_path.write_text(
        json.dumps(
            {
                "tolerance": 10,
                "offers": [{"file": "offers/a.txt", "score": 60, "verdict": "apply"}],
            }
        ),
        encoding="utf-8",
    )
    llm = FakeLlmClient(_extraction(), judge_reply=_judgment(score=60, verdict="apply"))
    fetcher = FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    service, _repo = make_service(tmp_path, llm, fetcher, threshold=55)

    report = asyncio.run(service.judge_eval(labels_path))

    assert "apply_with_caveats" not in report  # score 60 >= threshold 55 -> "apply"


def test_latest_judgment_is_none_in_keyword_mode(tmp_path: Path) -> None:
    service, _repo, _llm = _paste_service(tmp_path)
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    asyncio.run(service.analyze(offer_id))

    judgment, fallback = service.latest_judgment(offer_id)
    assert judgment is None
    assert fallback is False


def test_latest_judgment_returns_the_stored_judgment_in_hybrid_mode(
    tmp_path: Path,
) -> None:
    llm = FakeLlmClient(_extraction(), judge_reply=_judgment(score=80))
    fetcher = FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    service, _repo = make_service(tmp_path, llm, fetcher, match_mode="hybrid")
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    asyncio.run(service.analyze(offer_id))

    judgment, fallback = service.latest_judgment(offer_id)
    assert judgment is not None
    assert judgment.score == 80
    assert fallback is False


def test_latest_judgment_reports_fallback_when_hybrid_judge_failed(
    tmp_path: Path,
) -> None:
    llm = FakeLlmClient(_extraction(), judge_error=LlmResponseError("boom"))
    fetcher = FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    service, _repo = make_service(tmp_path, llm, fetcher, match_mode="hybrid")
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    asyncio.run(service.analyze(offer_id))

    judgment, fallback = service.latest_judgment(offer_id)
    assert judgment is None
    assert fallback is True


def test_analyze_deterministic_score_against_curated_data(tmp_path: Path) -> None:
    service, _repo, _llm = _paste_service(tmp_path)
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    result = asyncio.run(service.analyze(offer_id))
    # must(3,✓) + nice(1,~) + must(3,✗): 100·(3·1.0 + 1·0.5 + 3·0.0)/7 = 50.
    assert result.score == 50


def test_analyze_bumps_keywords_with_status_once_per_offer(tmp_path: Path) -> None:
    service, _repo, _llm = _paste_service(tmp_path)
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    asyncio.run(service.analyze(offer_id))
    rows = {r.keyword: r for r in service.keywords()}
    assert set(rows) == {"Python", "containers", "Kubernetes"}
    assert rows["Python"].status == "✓"
    assert rows["containers"].status == "~"
    assert rows["Kubernetes"].status == "✗"
    assert all(r.frequency == 1 for r in rows.values())


def test_reanalyze_does_not_double_count_and_caches(tmp_path: Path) -> None:
    service, _repo, llm = _paste_service(tmp_path)
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    asyncio.run(service.analyze(offer_id))
    synonyms_sha = hashlib.sha256(SYNONYMS_PATH.read_bytes()).hexdigest()
    result = asyncio.run(service.analyze(offer_id))
    assert llm.calls == 1  # cache hit: zero extra LLM calls
    rows = {r.keyword: r for r in service.keywords()}
    assert all(r.frequency == 1 for r in rows.values())  # no double counting
    assert result.score == 50
    assert hashlib.sha256(SYNONYMS_PATH.read_bytes()).hexdigest() == synonyms_sha


def test_threshold_flows_from_settings_value(tmp_path: Path) -> None:
    llm = FakeLlmClient(_extraction())
    fetcher = FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    service, _repo = make_service(tmp_path, llm, fetcher, threshold=40)
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    result = asyncio.run(service.analyze(offer_id))
    assert result.threshold == 40
    assert result.score >= result.threshold  # 50 clears a lowered bar


def test_explicit_lang_beats_inference(tmp_path: Path) -> None:
    service, repo, _llm = _paste_service(tmp_path)
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))  # Spanish text
    pdf_path = asyncio.run(service.generate_cv(offer_id, "AI", "en"))
    row = repo.get_offer(offer_id)
    assert row.cv_base == "AI_EN"  # explicit "en" overrides Spanish inference
    assert pdf_path.is_file()


def test_generate_cv_defaults_to_inferred_language(tmp_path: Path) -> None:
    service, repo, _llm = _paste_service(tmp_path)
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    pdf_path = asyncio.run(service.generate_cv(offer_id, "AI"))
    row = repo.get_offer(offer_id)
    assert row.cv_base == "AI_ES"  # Spanish offer text → inferred es
    assert pdf_path.is_file()
    assert row.estado == "preparada"


def test_generate_cv_catalan_end_to_end(tmp_path: Path) -> None:
    """v3 part B: explicit lang="ca" renders the AI_CA master end-to-end."""
    service, repo, _llm = _paste_service(tmp_path)
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    pdf_path = asyncio.run(service.generate_cv(offer_id, "AI", "ca"))
    row = repo.get_offer(offer_id)
    assert row.cv_base == "AI_CA"
    assert pdf_path.is_file()
    assert len(PdfReader(str(pdf_path)).pages) == 1
    assert row.estado == "preparada"


def test_generate_cv_ds_role_is_rejected(tmp_path: Path) -> None:
    """DS is manual-only (fixed-base-cvs): the bot has no DS variant."""
    service, _repo, _llm = _paste_service(tmp_path)
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    with pytest.raises(ServiceError, match="DS"):
        asyncio.run(service.generate_cv(offer_id, "DS"))  # type: ignore[arg-type]


def test_generate_cv_generic_name_in_per_offer_folder(tmp_path: Path) -> None:
    """AI offer -> adaptadas/<id>_<company>/Alex_Rivera_AI_Engineer.pdf."""
    service, repo, _llm = _paste_service(tmp_path)
    offer_id = asyncio.run(service.ingest(OFFER_URL, None))  # JSONLD: company Acme
    pdf_path = asyncio.run(service.generate_cv(offer_id, "AI", "en"))
    row = repo.get_offer(offer_id)
    assert pdf_path.name == "Alex_Rivera_AI_Engineer.pdf"
    assert pdf_path.parent.name == f"{offer_id}_Acme"
    assert row.cv_adaptado == str(pdf_path)
    assert pdf_path.is_file()


def test_generate_cv_generic_name_without_position(tmp_path: Path) -> None:
    """The offer title never reaches the file name, with or without posicion."""
    service, repo, _llm = _paste_service(tmp_path)
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    asyncio.run(service.generate_cv(offer_id, "AI"))
    path = Path(repo.get_offer(offer_id).cv_adaptado or "")
    assert path.name == "Alex_Rivera_Ingeniero_IA.pdf"
    assert path.parent.name.startswith(f"{offer_id}_")


# --- Fixed base CV: no per-offer changes (owner decision 2026-10-07) -----------


def test_generate_cv_analyzed_offer_gets_the_unchanged_base_cv(tmp_path: Path) -> None:
    """Even when the offer was analyzed, the delivered file is the base PDF
    byte-for-byte and ``cambios`` records the fixed base."""
    service, repo, _llm = _paste_service(tmp_path)
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    asyncio.run(service.analyze(offer_id))
    pdf_path = asyncio.run(service.generate_cv(offer_id, "AI"))
    base = DEMO_BASE_PDFS
    expected = base / "CV_AI_Engineer_Alex_Rivera.pdf"
    assert (
        hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        == hashlib.sha256(expected.read_bytes()).hexdigest()
    )
    assert (repo.get_offer(offer_id).cambios or "").startswith(BASE_CAMBIOS)


def test_generate_cv_without_stored_match_records_base_cambios(tmp_path: Path) -> None:
    """No analysis on file → same fixed base CV and cambios."""
    service, repo, _llm = _paste_service(tmp_path)
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    pdf_path = asyncio.run(service.generate_cv(offer_id, "AI"))
    row = repo.get_offer(offer_id)
    assert pdf_path.is_file()
    assert (row.cambios or "").startswith(BASE_CAMBIOS)


def test_mark_sent_records_enviada(tmp_path: Path) -> None:
    service, repo, _llm = _paste_service(tmp_path)
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    asyncio.run(service.analyze(offer_id))
    pdf_path = asyncio.run(service.generate_cv(offer_id, "AI"))
    service.mark_sent(offer_id, pdf_path)
    row = repo.get_offer(offer_id)
    assert row.estado == "enviada"
    assert row.fecha_envio is not None
    assert row.cv_adaptado == str(pdf_path)
    assert row.match == 50


def test_mark_sent_snapshots_once_and_logs_a_resend(tmp_path: Path) -> None:
    service, repo, _llm = _paste_service(tmp_path)
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    asyncio.run(service.analyze(offer_id))
    pdf_path = asyncio.run(service.generate_cv(offer_id, "AI"))
    service.mark_sent(offer_id, pdf_path)
    snap = repo.get_sent_snapshot(offer_id)
    assert snap is not None
    assert snap.cv_file == pdf_path.name
    assert snap.posting_text == repo.get_offer(offer_id).source_data
    assert "reenvío" not in (repo.get_offer(offer_id).notas or "")
    service.mark_sent(offer_id, pdf_path)
    assert repo.get_sent_snapshot(offer_id) == snap
    # The re-send trace lives on the offer_events timeline, not in notas.
    assert "reenvío" not in (repo.get_offer(offer_id).notas or "")
    assert [e.note for e in repo.list_events(offer_id)] == ["reenvío"]


def test_mark_sent_without_analysis_fails_fast(tmp_path: Path) -> None:
    service, _repo, _llm = _paste_service(tmp_path)
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    with pytest.raises(ServiceError):
        service.mark_sent(offer_id, None)


def test_discard_sets_descartada_without_pdf(tmp_path: Path) -> None:
    service, repo, _llm = _paste_service(tmp_path)
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    service.discard(offer_id)
    row = repo.get_offer(offer_id)
    assert row.estado == "descartada"
    assert row.cv_adaptado is None


def test_discard_after_sent_refuses_and_keeps_enviada(tmp_path: Path) -> None:
    """Estado protection: an enviada offer can never become descartada."""
    service, repo, _llm = _paste_service(tmp_path)
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    asyncio.run(service.analyze(offer_id))
    pdf_path = asyncio.run(service.generate_cv(offer_id, "AI"))
    service.mark_sent(offer_id, pdf_path)
    with pytest.raises(ServiceError, match="Ya está marcada como enviada"):
        service.discard(offer_id)
    assert repo.get_offer(offer_id).estado == "enviada"


def test_discard_is_idempotent_on_descartada(tmp_path: Path) -> None:
    service, repo, _llm = _paste_service(tmp_path)
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    service.discard(offer_id)
    service.discard(offer_id)  # second call: no-op success
    assert repo.get_offer(offer_id).estado == "descartada"


def test_export_excludes_descartada(tmp_path: Path) -> None:
    """Pin: descartada rows (fecha_envio IS NULL) never reach the xlsx export."""
    import openpyxl

    service, _repo, _llm = _paste_service(tmp_path)
    sent_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    asyncio.run(service.analyze(sent_id))
    pdf_path = asyncio.run(service.generate_cv(sent_id, "AI"))
    service.mark_sent(sent_id, pdf_path)
    discarded_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    service.discard(discarded_id)
    out = service.export(tmp_path / "export.xlsx")
    rows = list(openpyxl.load_workbook(out).active.iter_rows(values_only=True))
    ids = {row[0] for row in rows[1:]}
    assert ids == {sent_id}
    assert discarded_id not in ids


def test_service_exposes_retry_pending_and_max_for_the_sweep(
    tmp_path: Path,
) -> None:
    """D4: the heartbeat sweep reads retry state through the service seam."""
    service, repo, offer_id = _bridged(
        tmp_path,
        FakeLlmClient(_extraction()),
        FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
    )
    repo.record_analyze_failure(offer_id, next_analyze_at=NOW.isoformat())

    assert service.analyze_retry_max == 5
    pending = service.list_retry_pending(NOW.isoformat(), service.analyze_retry_max)
    assert [row.id for row in pending] == [offer_id]


def test_run_due_beacons_sweep_without_beacons_is_empty(tmp_path: Path) -> None:
    """8a's `[]` stub became the 8b SweepResult; no beacons → empty sweep."""
    service, _repo, _llm = _paste_service(tmp_path)
    sweep = asyncio.run(service.run_due_beacons())
    assert sweep.notifications == []
    assert sweep.paused_alerts == []


# --- notification-pacing: failure→retry, gate, URL (D3/D5/D6/D7) --------------

NOW = datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC)
NEXT_AFTER_FAILURE = (NOW + timedelta(seconds=600)).isoformat()
CANONICAL = "https://linkedin.com/jobs/view/4459804598"  # what the bridge passes


class _RecordingNotify:
    """Async ``notify(text, keyboard)`` double recording every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def __call__(self, text: str, keyboard: Any) -> None:
        self.calls.append((text, keyboard))


class _WallFetcher(FakeFetcher):
    """Fetcher whose every get hits a LinkedIn login wall."""

    async def get(self, url: str) -> FetchResponse:
        self.calls.append(url)
        raise WallError(WallVerdict(WallKind.LOGIN_WALL, "auth wall detected"))


class _ExplodingLlm(FakeLlmClient):
    """LLM whose every completion fails (e.g. 429 after its single retry)."""

    def __init__(self, exc: Exception) -> None:
        super().__init__(_extraction())
        self._exc = exc

    async def json_completion(
        self, system: str, user: str, schema: type[object]
    ) -> object:
        self.calls += 1
        raise self._exc


class _BadLlm(FakeLlmClient):
    """LLM reply that fails schema parsing at the service boundary."""

    async def json_completion(
        self, system: str, user: str, schema: type[object]
    ) -> object:
        self.calls += 1
        return SimpleNamespace()  # no .requirements → extraction explodes


def _bridged(
    tmp_path: Path,
    llm: FakeLlmClient,
    fetcher: FakeFetcher,
    *,
    gray_zone: int = 15,
) -> tuple[JobService, OfferRepository, int]:
    """Service + a URL-only bridge row (what _process_payload upserts)."""
    service, repo = make_service(
        tmp_path, llm, fetcher, now_fn=lambda: NOW, gray_zone=gray_zone
    )
    offer_id = repo.upsert_offer(
        OfferInput(
            job_id="4459804598",
            title="ML Engineer",
            company="Acme",
            url=CANONICAL,
            text=f"bridge: Test\n{CANONICAL}",
            source="link",
        )
    )
    return service, repo, offer_id


@pytest.mark.parametrize(
    "llm_factory",
    ["wall", "llm429", "parse"],
)
def test_failure_marks_retry_pending_without_card(
    tmp_path: Path, llm_factory: str
) -> None:
    """Spec: WallError / 429-exhausted / parse → retry-pending (attempts+1,
    next=now+600s), NO card of any kind, no exception escapes."""
    if llm_factory == "wall":
        fetcher: FakeFetcher = _WallFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
        llm = FakeLlmClient(_extraction())
    else:
        fetcher = FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
        llm = (
            _ExplodingLlm(
                LlmResponseError("LLM endpoint returned HTTP 429", status=429)
            )
            if llm_factory == "llm429"
            else _BadLlm(_extraction())
        )
    service, repo, offer_id = _bridged(tmp_path, llm, fetcher)
    notify = _RecordingNotify()

    asyncio.run(  # must NOT raise — the service swallows after bookkeeping
        service.analyze_new_bridge_job(
            offer_id,
            url=OFFER_URL,
            title="ML Engineer",
            company="Acme",
            notify=notify,
            min_score=None,
        )
    )

    row = repo.get_offer(offer_id)
    assert row.analyze_attempts == 1
    assert row.next_analyze_at == NEXT_AFTER_FAILURE
    assert row.estado == "pendiente_analisis"
    assert notify.calls == []


def test_bridge_failure_logs_one_warning_with_offer_id_attempts_and_cause(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """T2: bridge auto-analysis failures used to be silent in data/bot.log —
    only the retry bookkeeping happened, so an exhausted HTTP 429 never
    appeared anywhere. Exactly one WARNING per failed attempt, carrying the
    offer id, the attempt number, the configured max, and the exception
    type/message — never the offer text or secrets."""
    fetcher = FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    llm = _ExplodingLlm(LlmResponseError("LLM endpoint returned HTTP 429", status=429))
    service, _repo, offer_id = _bridged(tmp_path, llm, fetcher)
    notify = _RecordingNotify()

    with caplog.at_level(logging.WARNING, logger="cva.service"):
        asyncio.run(
            service.analyze_new_bridge_job(
                offer_id,
                url=OFFER_URL,
                title="ML Engineer",
                company="Acme",
                notify=notify,
                min_score=None,
            )
        )

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert str(offer_id) in message
    assert f"attempt {1}/{service.analyze_retry_max}" in message
    assert "429" in message
    assert "Kubernetes" not in message  # never the offer text


def test_exhausted_attempts_clears_latch_then_sends_one_final_card(
    tmp_path: Path,
) -> None:
    """Spec: attempts+1 > analyze_retry_max → clear (latch) THEN exactly one
    final [Analizar] card with Spanish cause + URL; row becomes sweep-inert."""
    fetcher = _WallFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    service, repo, offer_id = _bridged(tmp_path, FakeLlmClient(_extraction()), fetcher)
    service._analyze_retry_max = 0  # zero-equivalence: first failure → final card
    notify = _RecordingNotify()

    asyncio.run(
        service.analyze_new_bridge_job(
            offer_id,
            url=OFFER_URL,
            title="ML Engineer",
            company="Acme",
            notify=notify,
            min_score=None,
        )
    )

    assert len(notify.calls) == 1
    text, keyboard = notify.calls[0]
    assert "🔔 ML Engineer · Acme" in text
    assert "https://linkedin.com/jobs/view/4459804598" in text  # canonical URL
    assert "El análisis automático falló" in text  # brief Spanish cause
    assert keyboard.inline_keyboard[0][0].callback_data == "an:4459804598"
    row = repo.get_offer(offer_id)
    assert row.analyze_attempts == 0  # latch cleared BEFORE the card
    assert row.next_analyze_at is None  # sweep never lists it again


def test_exhausted_attempt_warning_never_shows_a_fraction_above_the_budget(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """T4 cleanup: on the FINAL (exhausted) attempt, the warning used to
    show "attempt n/max" with n > max (e.g. "attempt 1/0"), which reads as
    a fraction greater than one and is confusing — the exhausted case gets
    its own clearer wording instead."""
    fetcher = _WallFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    service, _repo, offer_id = _bridged(tmp_path, FakeLlmClient(_extraction()), fetcher)
    service._analyze_retry_max = 0  # zero-equivalence: first failure exhausts it
    notify = _RecordingNotify()

    with caplog.at_level(logging.WARNING, logger="cva.service"):
        asyncio.run(
            service.analyze_new_bridge_job(
                offer_id,
                url=OFFER_URL,
                title="ML Engineer",
                company="Acme",
                notify=notify,
                min_score=None,
            )
        )

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "1/0" not in message  # never a misleading n>max fraction
    assert "exhausted" in message.lower()


def test_retry_then_success_sends_breakdown_and_clears_state(
    tmp_path: Path,
) -> None:
    """Spec: sweep retry succeeds → exactly one late breakdown card (with the
    canonical URL); retry state cleared by the success."""
    service, repo, offer_id = _bridged(
        tmp_path,
        FakeLlmClient(_extraction()),
        _WallFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
    )
    notify = _RecordingNotify()

    asyncio.run(  # failure #1: retry-pending
        service.analyze_new_bridge_job(
            offer_id,
            url=CANONICAL,
            title="ML Engineer",
            company="Acme",
            notify=notify,
            min_score=40,  # 50-score extraction must card
        )
    )
    assert repo.get_offer(offer_id).analyze_attempts == 1

    service2, _repo2 = make_service(
        tmp_path,
        FakeLlmClient(_extraction()),
        FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
        now_fn=lambda: NOW,
    )
    asyncio.run(
        service2.analyze_new_bridge_job(
            offer_id,
            url=CANONICAL,
            title="ML Engineer",
            company="Acme",
            notify=notify,
            min_score=40,
        )
    )

    assert len(notify.calls) == 1  # exactly the late breakdown card
    text, keyboard = notify.calls[0]
    assert f"📊 Oferta #{offer_id}" in text
    assert "https://linkedin.com/jobs/view/4459804598" in text
    assert keyboard.inline_keyboard[0][0].callback_data == f"cv:{offer_id}:AI:es"
    row = repo.get_offer(offer_id)
    assert row.analyze_attempts == 0 and row.next_analyze_at is None
    assert row.estado == "analizada"


def test_analyze_success_clears_retry_state(tmp_path: Path) -> None:
    """D3: clearing lives in analyze() — a MANUAL success also clears, so the
    sweep can never double-card an offer the owner already rescued."""
    service, repo, _llm = _paste_service(tmp_path)
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    repo.record_analyze_failure(offer_id, next_analyze_at=NOW.isoformat())

    asyncio.run(service.analyze(offer_id))

    row = repo.get_offer(offer_id)
    assert row.analyze_attempts == 0
    assert row.next_analyze_at is None


def test_gate_cards_at_and_above_resolved_min_score(tmp_path: Path) -> None:
    """Spec: score ≥ resolved min_score → breakdown card; at-threshold is
    NEVER silent (boundary triangulation)."""
    for min_score in (40, 100):  # bridge flow scores exactly 100 → 100 is AT
        service, repo, offer_id = _bridged(
            tmp_path,
            FakeLlmClient(_extraction()),
            FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
        )
        notify = _RecordingNotify()
        asyncio.run(
            service.analyze_new_bridge_job(
                offer_id,
                url=CANONICAL,
                title="ML",
                company="Acme",
                notify=notify,
                min_score=min_score,
            )
        )
        assert len(notify.calls) == 1, f"min_score={min_score} must card"
        assert repo.get_offer(offer_id).estado == "analizada"


def test_below_gate_stays_silent_but_analizada(tmp_path: Path) -> None:
    """Spec: score < gate → NO card of any kind; estado='analizada' (rescuable
    via /status and /cv)."""
    service, repo, offer_id = _bridged(
        tmp_path,
        FakeLlmClient(_extraction()),
        FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
        gray_zone=0,  # pure threshold semantics under test here
    )
    notify = _RecordingNotify()

    asyncio.run(
        service.analyze_new_bridge_job(
            offer_id,
            url=CANONICAL,
            title="ML",
            company="Acme",
            notify=notify,
            min_score=101,  # gate ABOVE the score (100) → silent
        )
    )

    assert notify.calls == []
    row = repo.get_offer(offer_id)
    assert row.estado == "analizada"
    stored = repo.latest_match(offer_id)
    assert (
        stored is not None and stored.score == 100
    )  # analysis persisted; only the card is suppressed


def test_min_score_none_resolves_threshold_live_at_call_time(
    tmp_path: Path,
) -> None:
    """D8: the gate reads the LIVE threshold at call time, never a startup
    copy — lowering the threshold between calls changes the outcome."""
    service, _repo, offer_id = _bridged(
        tmp_path,
        FakeLlmClient(_extraction()),
        FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
        gray_zone=0,  # isolate live-gate resolution from gray-zone logic
    )
    notify = _RecordingNotify()

    asyncio.run(  # threshold 65 < score 100 → cards
        service.analyze_new_bridge_job(
            offer_id,
            url=CANONICAL,
            title="ML",
            company="Acme",
            notify=notify,
            min_score=None,
        )
    )
    assert len(notify.calls) == 1

    service._threshold = 101  # live value now ABOVE the score
    asyncio.run(  # the SAME call resolves the gate NOW: 100 < 101 → silent
        service.analyze_new_bridge_job(
            offer_id,
            url=CANONICAL,
            title="ML",
            company="Acme",
            notify=notify,
            min_score=None,
        )
    )
    assert len(notify.calls) == 1  # still just the first card


# --- Gray-zone borderline cards (owner amendment) ------------------------------


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (65, "breakdown"),  # at-gate is NEVER silent
        (64, "borderline"),  # one below the gate: inside the zone
        (50, "borderline"),  # zone floor (gate 65 - zone 15), inclusive
        (49, "silent"),  # one below the floor
        (0, "silent"),  # nothing like a match
    ],
)
def test_gray_zone_boundary_matrix(tmp_path: Path, target: int, expected: str) -> None:
    """Owner amendment, boundary matrix with gate=65 + zone=15: score ≥ gate →
    breakdown; gate > score ≥ gate-zone → borderline card; below → silent."""
    service, repo, offer_id = _bridged(
        tmp_path,
        FakeLlmClient(_extraction_scored(target)),
        FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
    )
    notify = _RecordingNotify()

    asyncio.run(
        service.analyze_new_bridge_job(
            offer_id,
            url=CANONICAL,
            title="ML Engineer",
            company="Acme",
            notify=notify,
            min_score=65,  # fixed gate; only the score moves across boundaries
        )
    )

    if expected == "silent":
        assert notify.calls == [], f"score {target} must stay silent"
    else:
        assert len(notify.calls) == 1, f"score {target} must card"
        text, _keyboard = notify.calls[0]
        banner = "📊" if expected == "breakdown" else "🟡"
        assert text.startswith(banner), (
            f"score {target} expected a {expected} card, got: {text[:60]!r}"
        )
    row = repo.get_offer(offer_id)
    assert row.estado == "analizada"  # analyzed either way; only the card gates


def test_gray_zone_borderline_card_payload(tmp_path: Path) -> None:
    """Borderline card: 🟡 + score + title + company + canonical URL, the
    [Generar CV]/[Descartar] keyboard (cv_keyboard reuse), NO ✓/~/✗ breakdown,
    and the offer is 'analizada' with clean retry state."""
    service, repo, offer_id = _bridged(
        tmp_path,
        FakeLlmClient(_extraction_scored(64)),
        FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
    )
    notify = _RecordingNotify()

    asyncio.run(
        service.analyze_new_bridge_job(
            offer_id,
            url=CANONICAL,
            title="ML Engineer",
            company="Acme",
            notify=notify,
            min_score=65,
        )
    )

    assert len(notify.calls) == 1
    text, keyboard = notify.calls[0]
    assert text.startswith("🟡")
    assert "ML Engineer · Acme" in text
    assert "64/100" in text
    assert "umbral 65" in text
    assert "https://linkedin.com/jobs/view/4459804598" in text
    assert all(mark not in text for mark in ("✓", "~", "✗", "«"))
    assert keyboard.inline_keyboard[0][0].callback_data == f"cv:{offer_id}:AI:es"
    # The callback rows end with sent/discard; the URL search row follows.
    assert [b.callback_data for b in keyboard.inline_keyboard[-2]] == [
        f"sent:{offer_id}",
        f"discard:{offer_id}",
    ]
    assert all(b.url for b in keyboard.inline_keyboard[-1])
    row = repo.get_offer(offer_id)
    assert row.estado == "analizada"
    assert row.analyze_attempts == 0
    assert row.next_analyze_at is None


def test_gray_zone_zero_disables_borderline_cards(tmp_path: Path) -> None:
    """notify_gray_zone ≤ 0 → pure threshold behavior: below-gate is silent,
    exactly the pre-amendment semantics."""
    service, repo, offer_id = _bridged(
        tmp_path,
        FakeLlmClient(_extraction_scored(64)),
        FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
        gray_zone=0,
    )
    notify = _RecordingNotify()

    asyncio.run(
        service.analyze_new_bridge_job(
            offer_id,
            url=CANONICAL,
            title="ML Engineer",
            company="Acme",
            notify=notify,
            min_score=65,
        )
    )

    assert notify.calls == []
    assert repo.get_offer(offer_id).estado == "analizada"


# --- Round-summary outcome reporting (round-summary amendment) -----------------


@pytest.mark.parametrize(
    ("scored", "min_score", "gray_zone", "retry_max", "zone", "expected_score"),
    [
        (_extraction(), 65, 15, 5, "above", 100),
        (_extraction_scored(64), 65, 15, 5, "gray", 64),
        (_extraction_scored(49), 65, 15, 5, "below", 49),
        (None, 65, 15, 5, "pending", None),  # wall → retry-pending, no score
        (None, 65, 15, 0, "pending", None),  # exhausted → final card, no score
    ],
)
def test_analyze_new_bridge_job_reports_zone_and_score(
    tmp_path: Path,
    scored: OfferExtraction | None,
    min_score: int,
    gray_zone: int,
    retry_max: int,
    zone: str,
    expected_score: int | None,
) -> None:
    """Round-summary seam (D11): the service RETURNS the per-job outcome —
    zone (above/gray/below/pending) + score — while the card tree stays
    byte-identical. Failures (retry-pending AND exhausted) both report
    'pending': the bridge counts them in the ♻️ line."""
    from cva.service import BridgeJobOutcome

    llm = FakeLlmClient(scored) if scored is not None else FakeLlmClient(_extraction())
    fetcher = (
        _WallFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
        if scored is None
        else FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    )
    service, _repo, offer_id = _bridged(tmp_path, llm, fetcher, gray_zone=gray_zone)
    service._analyze_retry_max = retry_max
    notify = _RecordingNotify()

    outcome = asyncio.run(
        service.analyze_new_bridge_job(
            offer_id,
            url=CANONICAL,
            title="ML Engineer",
            company="Acme",
            notify=notify,
            min_score=min_score,
        )
    )

    assert outcome == BridgeJobOutcome(zone=zone, score=expected_score)
    if zone == "above":
        assert len(notify.calls) == 1 and notify.calls[0][0].startswith("📊")
    elif zone == "gray":
        assert len(notify.calls) == 1 and notify.calls[0][0].startswith("🟡")
    elif zone == "pending" and retry_max == 0:
        assert len(notify.calls) == 1  # exactly the single final fallback card
    else:
        assert notify.calls == []  # below-gate + retry-pending stay silent


# --- Inline ingestion (infojobs-source D5/D7): text= skips the fetch -----------

INFOJOBS_CANONICAL = (
    "https://www.infojobs.net/barcelona/ingeniero-ia/of-"
    "0f8d7e6c5b4a39281706f5e4d3c2b1a0/"
)
INFOJOBS_ID = "0f8d7e6c5b4a39281706f5e4d3c2b1a0"
INFOJOBS_TEXT = (
    "Ingeniero IA en Acme (Barcelona). Imprescindible experiencia con Python. "
    "Se valoran conocimientos de containers para las integraciones de datos."
)


def _bridged_infojobs(
    tmp_path: Path,
    llm: FakeLlmClient,
    fetcher: FakeFetcher,
) -> tuple[JobService, OfferRepository, int]:
    """Service + the row _process_payload upserts for an infojobs job
    (D4: canonical = normalized original, inline text already stored)."""
    service, repo = make_service(
        tmp_path, llm, fetcher, now_fn=lambda: NOW, gray_zone=15
    )
    offer_id = repo.upsert_offer(
        OfferInput(
            job_id=INFOJOBS_ID,
            title="Ingeniero IA",
            company="Acme",
            url=INFOJOBS_CANONICAL,
            text=INFOJOBS_TEXT,
            source="bridge-inline",
        )
    )
    return service, repo, offer_id


def test_analyze_new_bridge_job_inline_text_skips_fetch(tmp_path: Path) -> None:
    """D5 inline branch: text present → FetchedOffer(source="bridge-inline")
    upserted with the payload text, ZERO fetch calls (a wall fetcher would
    fail the analysis if it were ever called); the same gate/card tree runs."""
    service, repo, offer_id = _bridged_infojobs(
        tmp_path,
        FakeLlmClient(_extraction()),
        _WallFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
    )
    notify = _RecordingNotify()

    outcome = asyncio.run(
        service.analyze_new_bridge_job(
            offer_id,
            url=INFOJOBS_CANONICAL,
            title="Ingeniero IA",
            company="Acme",
            notify=notify,
            min_score=None,
            text=INFOJOBS_TEXT,
        )
    )

    assert service._fetcher.calls == []  # inline: the fetcher is never touched
    row = repo.get_offer(offer_id)
    assert row.source_data == INFOJOBS_TEXT  # inline text governs the analysis
    assert row.estado == "analizada"
    assert outcome.zone == "above" and outcome.score == 88
    text, keyboard = notify.calls[0]
    assert text.startswith("📊")
    assert INFOJOBS_CANONICAL in text  # per-source canonical URL on the card
    assert keyboard.inline_keyboard[0][0].callback_data == f"cv:{offer_id}:AI:es"


def test_analyze_new_bridge_job_infojobs_job_id_from_registry(tmp_path: Path) -> None:
    """Registry-derived job_id (task 4.3): the final fallback card's
    ``an:`` callback carries the InfoJobs id, not the URL tail (``of-…``).
    The failure comes from the LLM (inline text bypasses the fetch)."""
    service, _repo, offer_id = _bridged_infojobs(
        tmp_path,
        _BadLlm(_extraction()),
        _WallFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
    )
    service._analyze_retry_max = 0  # first failure → final fallback card
    notify = _RecordingNotify()

    asyncio.run(
        service.analyze_new_bridge_job(
            offer_id,
            url=INFOJOBS_CANONICAL,
            title="Ingeniero IA",
            company="Acme",
            notify=notify,
            min_score=None,
            text=INFOJOBS_TEXT,
        )
    )

    assert len(notify.calls) == 1
    text, keyboard = notify.calls[0]
    assert INFOJOBS_CANONICAL in text  # canonical URL per source
    assert f"an:{INFOJOBS_ID}" == keyboard.inline_keyboard[0][0].callback_data


def test_analyze_new_bridge_job_absent_text_fetches_as_today(tmp_path: Path) -> None:
    """Regression: no text → the legacy URL re-ingest (fetch) flow, byte
    identical semantics — LinkedIn rows keep the note→refetch lifecycle."""
    service, repo, offer_id = _bridged(
        tmp_path,
        FakeLlmClient(_extraction()),
        FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
    )
    notify = _RecordingNotify()

    asyncio.run(
        service.analyze_new_bridge_job(
            offer_id,
            url=CANONICAL,
            title="ML Engineer",
            company="Acme",
            notify=notify,
            min_score=None,
        )
    )

    assert service._fetcher.calls  # the legacy path fetched
    row = repo.get_offer(offer_id)
    assert row.source_data != "bridge: Test\n" + CANONICAL  # refreshed from fetch
    assert row.origen == "linkedin"


# --- rejudge_backlog (feature: rejudge-backlog, judge-only re-evaluation) -----


def _long_text(marker: str = "AI Engineer") -> str:
    """> 500 chars of real offer text (rejudge-backlog selection threshold)."""
    return (
        f"{marker}. Puesto de ingeniería de IA en una empresa que construye "
        "productos con modelos de lenguaje, RAG y agentes. Buscamos experiencia "
        "en Python, buenas prácticas de testing, y ganas de aprender. Se valora "
        "conocimiento de bases de datos vectoriales, prompt engineering y "
        "despliegue de servicios en la nube. Ofrecemos contrato indefinido, "
        "teletrabajo parcial y formación continua para el equipo técnico. " * 2
    )


class _RejudgeLlm:
    """Judge-only fake: pops the next canned outcome by call order.

    An outcome can be a real ``OfferJudgment``, an ``Exception`` (continue-
    on-failure path), or a ``KeyboardInterrupt`` (hardening: the circuit-
    breaker/interrupt-safety tests need to simulate Ctrl+C arriving mid-call
    — ``KeyboardInterrupt`` is a ``BaseException``, not an ``Exception``, so
    it needs its own check). ``queue`` lets a test append a later call's
    outcome AFTER constructing the fake (used by the "report covers the
    whole backlog across two runs" test)."""

    def __init__(self, outcomes: list[OfferJudgment | BaseException]) -> None:
        self.model = "test-model"
        self.calls = 0
        self._outcomes = list(outcomes)

    def queue(self, outcome: OfferJudgment | BaseException) -> None:
        self._outcomes.append(outcome)

    async def json_completion(
        self,
        system: str,
        user: str,
        schema: type[object],
        *,
        model: str | None = None,
    ) -> object:
        assert schema is OfferJudgment, "rejudge_backlog must be judge-only"
        outcome = self._outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _rejudge_service(
    tmp_path: Path,
    llm: object,
    *,
    now_fn: Callable[[], datetime] | None = None,
    threshold: int = 65,
) -> tuple[JobService, OfferRepository]:
    return make_service(
        tmp_path,
        llm,  # type: ignore[arg-type]
        FakeFetcher(FetchResponse(200, "", "https://x")),
        threshold=threshold,
        now_fn=now_fn,
    )


def _seed_offer(
    repo: OfferRepository,
    *,
    url: str,
    text: str = "",
    estado: str | None = None,
    fecha_update: str | None = None,
    title: str = "AI Engineer",
    company: str = "Acme",
) -> int:
    offer_id = repo.upsert_offer(
        OfferInput(title=title, company=company, url=url, text=text, source="link")
    )
    if estado is not None:
        repo.set_estado(offer_id, estado)
    if fecha_update is not None:
        repo._conn.execute(
            "UPDATE offers SET fecha_update = ?, fecha_alta = ? WHERE id = ?",
            (fecha_update, fecha_update, offer_id),
        )
        repo._conn.commit()
    return offer_id


async def _noop_sleep(_seconds: float) -> None:
    return None


def test_rejudge_backlog_dry_run_makes_zero_llm_calls_and_estimates_tokens(
    tmp_path: Path,
) -> None:
    llm = _RejudgeLlm([])
    service, repo = _rejudge_service(tmp_path, llm)
    _seed_offer(repo, url="https://x/1", text=_long_text())
    _seed_offer(repo, url="https://x/2", text=_long_text())

    result = asyncio.run(service.rejudge_backlog(dry_run=True))

    assert llm.calls == 0
    assert result.dry_run is True
    assert result.total_candidates == 2
    assert result.estimated_tokens == 20_000
    assert result.judged == 0
    assert result.report_path is None


def test_rejudge_backlog_skips_default_states(tmp_path: Path) -> None:
    llm = _RejudgeLlm([])
    service, repo = _rejudge_service(tmp_path, llm)
    _seed_offer(repo, url="https://x/1", text=_long_text(), estado="pendiente_analisis")
    _seed_offer(repo, url="https://x/2", text=_long_text(), estado="descartada")
    _seed_offer(repo, url="https://x/3", text=_long_text(), estado="enviada")
    _seed_offer(repo, url="https://x/4", text=_long_text(), estado="entrevista_final")

    result = asyncio.run(service.rejudge_backlog(dry_run=True))

    assert result.total_candidates == 1  # only the pendiente_analisis one
    assert result.skipped_state == 3


def test_rejudge_backlog_include_discarded_includes_descartada(tmp_path: Path) -> None:
    llm = _RejudgeLlm([])
    service, repo = _rejudge_service(tmp_path, llm)
    _seed_offer(repo, url="https://x/1", text=_long_text(), estado="descartada")
    _seed_offer(repo, url="https://x/2", text=_long_text(), estado="enviada")

    result = asyncio.run(service.rejudge_backlog(dry_run=True, include_discarded=True))

    assert result.total_candidates == 1  # descartada included, enviada still skipped
    assert result.skipped_state == 1


def test_rejudge_backlog_skips_short_or_placeholder_text(tmp_path: Path) -> None:
    llm = _RejudgeLlm([])
    service, repo = _rejudge_service(tmp_path, llm)
    _seed_offer(repo, url="https://x/1", text="too short")
    _seed_offer(
        repo, url="https://x/2", text=f"bridge: ia-espana\nhttps://x/2 {'x' * 600}"
    )
    _seed_offer(repo, url="https://x/3", text=_long_text())

    result = asyncio.run(service.rejudge_backlog(dry_run=True))

    assert result.total_candidates == 1
    assert result.skipped_short_text == 2


def test_rejudge_backlog_skips_already_judged_unless_force(tmp_path: Path) -> None:
    llm = _RejudgeLlm([])
    service, repo = _rejudge_service(tmp_path, llm)
    offer_id = _seed_offer(repo, url="https://x/1", text=_long_text())
    repo.save_judge_result(
        offer_id, score=40, verdict="discard", judgment_json='{"score": 40}'
    )
    _seed_offer(repo, url="https://x/2", text=_long_text())

    default_result = asyncio.run(service.rejudge_backlog(dry_run=True))
    forced_result = asyncio.run(service.rejudge_backlog(dry_run=True, force=True))

    assert default_result.total_candidates == 1
    assert default_result.skipped_already_judged == 1
    assert forced_result.total_candidates == 2


def test_rejudge_backlog_since_filters_by_fecha_update(tmp_path: Path) -> None:
    llm = _RejudgeLlm([])
    service, repo = _rejudge_service(tmp_path, llm)
    _seed_offer(repo, url="https://x/1", text=_long_text(), fecha_update="2026-08-01")
    _seed_offer(repo, url="https://x/2", text=_long_text(), fecha_update="2026-09-20")

    result = asyncio.run(service.rejudge_backlog(dry_run=True, since="2026-09-01"))

    assert result.total_candidates == 1
    assert result.skipped_since == 1  # hardening: exclusion reason is counted


def test_rejudge_backlog_limit_caps_candidates_but_not_total(tmp_path: Path) -> None:
    llm = _RejudgeLlm([])
    service, repo = _rejudge_service(tmp_path, llm)
    _seed_offer(repo, url="https://x/1", text=_long_text())
    _seed_offer(repo, url="https://x/2", text=_long_text())
    _seed_offer(repo, url="https://x/3", text=_long_text())

    result = asyncio.run(service.rejudge_backlog(dry_run=True, limit=2))

    assert result.total_candidates == 3  # unlimited count, for the dry-run breakdown
    assert result.estimated_tokens == 20_000  # estimate reflects the LIMITED set


def test_rejudge_backlog_continues_after_one_offer_fails(tmp_path: Path) -> None:
    judgment_ok = _judgment(score=70, verdict="apply")
    llm = _RejudgeLlm([LlmResponseError("boom"), judgment_ok])
    service, repo = _rejudge_service(tmp_path, llm)
    _seed_offer(repo, url="https://x/1", text=_long_text())
    _seed_offer(repo, url="https://x/2", text=_long_text())

    result = asyncio.run(service.rejudge_backlog(pace_seconds=0, sleep_fn=_noop_sleep))

    assert result.judged == 1
    assert len(result.failures) == 1
    assert result.failures[0].offer_id in (1, 2)
    assert "boom" in result.failures[0].error


def test_rejudge_backlog_never_changes_estado(tmp_path: Path) -> None:
    llm = _RejudgeLlm([_judgment(score=70, verdict="apply")])
    service, repo = _rejudge_service(tmp_path, llm)
    offer_id = _seed_offer(
        repo, url="https://x/1", text=_long_text(), estado="analizada"
    )

    asyncio.run(service.rejudge_backlog(pace_seconds=0, sleep_fn=_noop_sleep))

    assert repo.get_offer(offer_id).estado == "analizada"


def test_rejudge_backlog_persists_judge_results(tmp_path: Path) -> None:
    llm = _RejudgeLlm([_judgment(score=82, verdict="apply")])
    service, repo = _rejudge_service(tmp_path, llm)
    offer_id = _seed_offer(repo, url="https://x/1", text=_long_text())

    asyncio.run(service.rejudge_backlog(pace_seconds=0, sleep_fn=_noop_sleep))

    stored = repo.latest_judge_result(offer_id)
    assert stored is not None
    assert stored.score == 82
    assert stored.verdict == "apply"


def test_rejudge_backlog_report_orders_by_score_desc_and_min_score_filter(
    tmp_path: Path,
) -> None:
    llm = _RejudgeLlm(
        [
            _judgment(score=40, verdict="discard"),
            _judgment(score=90, verdict="apply"),
            _judgment(score=55, verdict="apply_with_caveats"),
        ]
    )
    service, repo = _rejudge_service(tmp_path, llm)
    _seed_offer(repo, url="https://x/1", text=_long_text(), title="Low Fit")
    _seed_offer(repo, url="https://x/2", text=_long_text(), title="High Fit")
    _seed_offer(repo, url="https://x/3", text=_long_text(), title="Mid Fit")

    result = asyncio.run(
        service.rejudge_backlog(pace_seconds=0, sleep_fn=_noop_sleep, min_score=50)
    )

    assert [row.score for row in result.rows] == [90, 55, 40]  # all rows, desc
    assert "Low Fit" not in result.report_text  # below min_score=50
    assert "High Fit" in result.report_text
    assert "Mid Fit" in result.report_text
    high_pos = result.report_text.index("High Fit")
    mid_pos = result.report_text.index("Mid Fit")
    assert high_pos < mid_pos  # ranked score desc in the report text too


def test_rejudge_backlog_flags_age_over_21_days(tmp_path: Path) -> None:
    llm = _RejudgeLlm(
        [_judgment(score=70, verdict="apply"), _judgment(score=70, verdict="apply")]
    )
    now_fn = lambda: datetime(2026, 9, 27, tzinfo=UTC)
    service, repo = _rejudge_service(tmp_path, llm, now_fn=now_fn)
    _seed_offer(repo, url="https://x/1", text=_long_text(), fecha_update="2026-09-01")
    _seed_offer(repo, url="https://x/2", text=_long_text(), fecha_update="2026-09-25")

    result = asyncio.run(service.rejudge_backlog(pace_seconds=0, sleep_fn=_noop_sleep))

    by_offer = {row.offer_id: row for row in result.rows}
    assert by_offer[1].age_days == 26
    assert by_offer[1].age_flag is True
    assert by_offer[2].age_days == 2
    assert by_offer[2].age_flag is False


def test_rejudge_backlog_paces_calls_via_injected_sleep_fn(tmp_path: Path) -> None:
    llm = _RejudgeLlm(
        [_judgment(score=70, verdict="apply"), _judgment(score=70, verdict="apply")]
    )
    service, repo = _rejudge_service(tmp_path, llm)
    _seed_offer(repo, url="https://x/1", text=_long_text())
    _seed_offer(repo, url="https://x/2", text=_long_text())
    sleeps: list[float] = []

    async def recording_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    asyncio.run(service.rejudge_backlog(pace_seconds=7, sleep_fn=recording_sleep))

    assert sleeps == [7]  # (n - 1) sleeps between n=2 calls, never before the first


def test_rejudge_backlog_writes_report_file_under_data_dir(tmp_path: Path) -> None:
    llm = _RejudgeLlm([_judgment(score=70, verdict="apply")])
    now_fn = lambda: datetime(2026, 9, 27, 14, 5, 3, tzinfo=UTC)
    service, repo = _rejudge_service(tmp_path, llm, now_fn=now_fn)
    _seed_offer(repo, url="https://x/1", text=_long_text())
    report_dir = tmp_path / "data"

    result = asyncio.run(
        service.rejudge_backlog(
            pace_seconds=0, sleep_fn=_noop_sleep, report_dir=report_dir
        )
    )

    # hardening: seconds in the stamp (not just HHMM) avoid a filename
    # collision between two runs started in the same minute.
    assert result.report_path == report_dir / "rejudge_2026-09-27_140503.md"
    assert result.report_path is not None
    assert result.report_path.read_text(encoding="utf-8") == result.report_text


def test_rejudge_backlog_progress_fn_called_per_offer(tmp_path: Path) -> None:
    llm = _RejudgeLlm(
        [_judgment(score=70, verdict="apply"), _judgment(score=70, verdict="apply")]
    )
    service, repo = _rejudge_service(tmp_path, llm)
    _seed_offer(repo, url="https://x/1", text=_long_text())
    _seed_offer(repo, url="https://x/2", text=_long_text())
    progress: list[str] = []

    asyncio.run(
        service.rejudge_backlog(
            pace_seconds=0, sleep_fn=_noop_sleep, progress_fn=progress.append
        )
    )

    assert len(progress) == 2


def test_rejudge_backlog_hard_walls_appear_in_report(tmp_path: Path) -> None:
    llm = _RejudgeLlm(
        [_judgment(score=30, verdict="discard", hard_wall="Java/Spring core stack")]
    )
    service, repo = _rejudge_service(tmp_path, llm)
    _seed_offer(repo, url="https://x/1", text=_long_text())

    result = asyncio.run(
        service.rejudge_backlog(pace_seconds=0, sleep_fn=_noop_sleep, min_score=0)
    )

    assert "Java/Spring core stack" in result.report_text


# --- Hardening (odd/tasks/rejudge-backlog.md "Hardening" section) -----------


def test_rejudge_backlog_counts_since_and_title_filter_exclusions(
    tmp_path: Path,
) -> None:
    llm = _RejudgeLlm([])
    service, repo = _rejudge_service(tmp_path, llm)
    _seed_offer(
        repo,
        url="https://x/1",
        text=_long_text(),
        title="Senior AI Engineer",  # excluded by DEFAULT_TITLE_FILTER (Senior)
    )
    _seed_offer(repo, url="https://x/2", text=_long_text(), title="AI Engineer")
    _seed_offer(repo, url="https://x/3", text=_long_text(), title="Data Analyst")

    result = asyncio.run(service.rejudge_backlog(dry_run=True, title_filter=True))

    # Integration through rejudge_backlog itself (not just the pure port):
    # only "AI Engineer" passes; the other two are excluded and counted.
    assert result.total_candidates == 1
    assert result.skipped_title_filter == 2


def test_rejudge_backlog_rejects_non_positive_limit(tmp_path: Path) -> None:
    service, repo = _rejudge_service(tmp_path, _RejudgeLlm([]))
    _seed_offer(repo, url="https://x/1", text=_long_text())
    with pytest.raises(ValueError):
        asyncio.run(service.rejudge_backlog(dry_run=True, limit=0))


def test_rejudge_backlog_rejects_negative_pace(tmp_path: Path) -> None:
    service, repo = _rejudge_service(tmp_path, _RejudgeLlm([]))
    _seed_offer(repo, url="https://x/1", text=_long_text())
    with pytest.raises(ValueError):
        asyncio.run(service.rejudge_backlog(dry_run=True, pace_seconds=-1))


def test_rejudge_backlog_rejects_min_score_out_of_range(tmp_path: Path) -> None:
    service, repo = _rejudge_service(tmp_path, _RejudgeLlm([]))
    _seed_offer(repo, url="https://x/1", text=_long_text())
    with pytest.raises(ValueError):
        asyncio.run(service.rejudge_backlog(dry_run=True, min_score=101))
    with pytest.raises(ValueError):
        asyncio.run(service.rejudge_backlog(dry_run=True, min_score=-1))


def test_rejudge_backlog_rejects_malformed_since(tmp_path: Path) -> None:
    service, repo = _rejudge_service(tmp_path, _RejudgeLlm([]))
    _seed_offer(repo, url="https://x/1", text=_long_text())
    with pytest.raises(ValueError):
        asyncio.run(service.rejudge_backlog(dry_run=True, since="09-2026-01"))


def test_rejudge_backlog_rejects_non_positive_max_consecutive_failures(
    tmp_path: Path,
) -> None:
    service, repo = _rejudge_service(tmp_path, _RejudgeLlm([]))
    _seed_offer(repo, url="https://x/1", text=_long_text())
    with pytest.raises(ValueError):
        asyncio.run(
            service.rejudge_backlog(
                pace_seconds=0, sleep_fn=_noop_sleep, max_consecutive_failures=0
            )
        )


def test_rejudge_backlog_circuit_breaker_stops_after_consecutive_failures(
    tmp_path: Path,
) -> None:
    llm = _RejudgeLlm(
        [
            LlmResponseError("quota exceeded (1)"),
            LlmResponseError("quota exceeded (2)"),
            LlmResponseError("quota exceeded (3)"),
            _judgment(score=99, verdict="apply"),  # never reached
        ]
    )
    service, repo = _rejudge_service(tmp_path, llm)
    for i in range(4):
        _seed_offer(repo, url=f"https://x/{i}", text=_long_text())

    result = asyncio.run(
        service.rejudge_backlog(
            pace_seconds=0, sleep_fn=_noop_sleep, max_consecutive_failures=3
        )
    )

    assert llm.calls == 3  # the run stopped before the 4th offer
    assert result.judged == 0
    assert len(result.failures) == 3
    assert result.stopped_reason is not None
    assert "quota exceeded (3)" in result.stopped_reason
    # The report is still produced for what WAS judged (empty here, but not
    # crashed/lost) so a resumed run has somewhere to build on.
    assert result.report_path is not None
    assert result.report_path.exists()


def test_rejudge_backlog_report_reflects_partial_progress_after_circuit_breaker(
    tmp_path: Path,
) -> None:
    llm = _RejudgeLlm(
        [
            _judgment(score=88, verdict="apply"),
            LlmResponseError("e1"),
            LlmResponseError("e2"),
            LlmResponseError("e3"),
        ]
    )
    service, repo = _rejudge_service(tmp_path, llm)
    _seed_offer(repo, url="https://x/1", text=_long_text(), title="Good One")
    for i in range(2, 5):
        _seed_offer(repo, url=f"https://x/{i}", text=_long_text())

    result = asyncio.run(
        service.rejudge_backlog(
            pace_seconds=0,
            sleep_fn=_noop_sleep,
            max_consecutive_failures=3,
            min_score=0,
        )
    )

    assert result.judged == 1
    assert len(result.failures) == 3
    assert result.stopped_reason is not None
    assert result.report_path is not None
    # Never lost: the file on disk matches the in-memory report, and it
    # already contains the one offer that was judged before the breaker
    # tripped.
    assert result.report_path.read_text(encoding="utf-8") == result.report_text
    assert "Good One" in result.report_text


def test_rejudge_backlog_keyboard_interrupt_preserves_the_report(
    tmp_path: Path,
) -> None:
    llm = _RejudgeLlm([_judgment(score=70, verdict="apply"), KeyboardInterrupt()])
    service, repo = _rejudge_service(tmp_path, llm)
    _seed_offer(repo, url="https://x/1", text=_long_text(), title="First")
    _seed_offer(repo, url="https://x/2", text=_long_text(), title="Second")

    result = asyncio.run(
        service.rejudge_backlog(pace_seconds=0, sleep_fn=_noop_sleep, min_score=0)
    )

    assert result.judged == 1
    assert result.stopped_reason is not None
    assert "interrump" in result.stopped_reason.lower()
    assert result.report_path is not None
    assert result.report_path.read_text(encoding="utf-8") == result.report_text
    assert "First" in result.report_text
    assert "Second" not in result.report_text  # never judged (interrupted first)


def test_rejudge_backlog_report_covers_whole_backlog_across_two_runs(
    tmp_path: Path,
) -> None:
    """The report from a SECOND run (default: skip already-judged) still
    shows the offer judged in the FIRST run, marked as not judged in this
    run — a resumed run's report is never missing earlier results."""
    llm = _RejudgeLlm([_judgment(score=60, verdict="apply")])
    service, repo = _rejudge_service(tmp_path, llm)
    offer1 = _seed_offer(repo, url="https://x/1", text=_long_text(), title="Offer1")

    first = asyncio.run(
        service.rejudge_backlog(pace_seconds=0, sleep_fn=_noop_sleep, min_score=0)
    )
    assert first.judged == 1

    llm.queue(_judgment(score=95, verdict="apply"))
    offer2 = _seed_offer(repo, url="https://x/2", text=_long_text(), title="Offer2")

    second = asyncio.run(
        service.rejudge_backlog(pace_seconds=0, sleep_fn=_noop_sleep, min_score=0)
    )

    assert second.judged == 1  # only offer2 judged THIS run
    ids = {row.offer_id for row in second.rows}
    assert ids == {offer1, offer2}
    row1 = next(row for row in second.rows if row.offer_id == offer1)
    row2 = next(row for row in second.rows if row.offer_id == offer2)
    assert row1.judged_this_run is False
    assert row1.score == 60
    assert row2.judged_this_run is True
    assert row2.score == 95


def test_rejudge_backlog_report_only_makes_zero_llm_calls(tmp_path: Path) -> None:
    llm = _RejudgeLlm([])
    service, repo = _rejudge_service(tmp_path, llm)
    offer1 = _seed_offer(repo, url="https://x/1", text=_long_text(), title="Judged")
    repo.save_judge_result(
        offer1,
        score=77,
        verdict="apply",
        judgment_json=_judgment(score=77, verdict="apply").model_dump_json(),
    )
    _seed_offer(repo, url="https://x/2", text=_long_text(), title="NeverJudged")

    result = asyncio.run(service.rejudge_backlog(report_only=True, min_score=0))

    assert llm.calls == 0
    assert result.report_only is True
    assert result.judged == 0
    ids = {row.offer_id for row in result.rows}
    assert ids == {offer1}
    assert "Judged" in result.report_text
    assert "NeverJudged" not in result.report_text
    assert result.report_path is not None
    assert result.report_path.read_text(encoding="utf-8") == result.report_text


def test_rejudge_backlog_dry_run_reports_planned_vs_total_when_limit_caps(
    tmp_path: Path,
) -> None:
    llm = _RejudgeLlm([])
    service, repo = _rejudge_service(tmp_path, llm)
    for i in range(3):
        _seed_offer(repo, url=f"https://x/{i}", text=_long_text())

    result = asyncio.run(service.rejudge_backlog(dry_run=True, limit=2))

    assert result.total_candidates == 3
    assert result.planned_candidates == 2
