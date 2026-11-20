"""judge-eval harness: judge-vs-label comparison over a tiny synthetic eval
bank (never the real ``eval/`` data, and never a real LLM — a fake client
returns canned OfferJudgment replies per offer file, exactly like the
``FakeJudgeLlmClient`` pattern in tests/test_judge.py).
"""

import asyncio
import json
from pathlib import Path

from cva.claimsdb import Claim, ClaimsDB
from cva.llm import LlmResponseError
from cva.match.judge import FitItem, OfferJudgment, Wall
from cva.match.judge_eval import run_judge_eval


class FakeMultiReplyLlmClient:
    """Returns the next canned reply from a fixed sequence, in call order."""

    def __init__(self, replies: list[OfferJudgment]) -> None:
        self.model = "test-model"
        self._replies = replies
        self.calls = 0

    async def json_completion(
        self,
        system: str,
        user: str,
        schema: type[object],
        *,
        model: str | None = None,
    ) -> object:
        reply = self._replies[self.calls]
        self.calls += 1
        return reply


def _claims_db() -> ClaimsDB:
    claims = [
        Claim(id="skill-python", text="Python", category="skill", tags=["python"]),
        Claim(id="skill-rag", text="RAG pipelines", category="skill", tags=["rag"]),
    ]
    return ClaimsDB(claims=claims, by_id={c.id: c for c in claims})


def _judgment(
    *, score: int, verdict: str, hard_wall: str | None = None
) -> OfferJudgment:
    return OfferJudgment(
        score=score,
        verdict=verdict,  # type: ignore[arg-type]
        core_role="role",
        fit=[FitItem(requirement="Python", claim_ids=["skill-python"])],
        walls=[Wall(text=hard_wall, hard=True)] if hard_wall else [],
        gaps=[],
        cv_base="AI_ES",
        tailoring_hints=[],
        rationale="rationale",
    )


def _write_bank(tmp_path: Path) -> Path:
    offers_dir = tmp_path / "offers"
    offers_dir.mkdir()
    (offers_dir / "a.txt").write_text("Offer A text", encoding="utf-8")
    (offers_dir / "b.txt").write_text("Offer B text", encoding="utf-8")
    labels = {
        "tolerance": 10,
        "offers": [
            {"file": "offers/a.txt", "score": 55, "verdict": "apply_with_caveats"},
            {"file": "offers/b.txt", "score": 30, "verdict": "discard"},
        ],
    }
    labels_path = tmp_path / "labels.json"
    labels_path.write_text(json.dumps(labels), encoding="utf-8")
    return labels_path


def test_run_judge_eval_reports_per_offer_scores_and_verdicts(tmp_path: Path) -> None:
    labels_path = _write_bank(tmp_path)
    llm = FakeMultiReplyLlmClient(
        [
            _judgment(score=50, verdict="apply_with_caveats"),
            _judgment(score=35, verdict="discard"),
        ]
    )

    report = asyncio.run(run_judge_eval(labels_path, _claims_db(), llm))

    assert "offers/a.txt" in report
    assert "offers/b.txt" in report
    assert "55" in report and "50" in report  # label vs judge score for a.txt
    assert "apply_with_caveats" in report
    assert "discard" in report
    assert llm.calls == 2


def test_run_judge_eval_computes_mae_and_verdict_agreement(tmp_path: Path) -> None:
    # judge_offer() derives the verdict from score band + hard walls only
    # (the model's own verdict field is informational and always
    # overridden) - so the fake replies' verdict field is irrelevant here;
    # what drives agreement is each score's band vs. the label's verdict.
    labels_path = _write_bank(tmp_path)
    llm = FakeMultiReplyLlmClient(
        [
            # offer a: label score=55/apply_with_caveats; |55-50|=5, band(50)
            # = apply_with_caveats -> agrees.
            _judgment(score=50, verdict="apply_with_caveats"),
            # offer b: label score=30/discard; |30-50|=20, band(50) =
            # apply_with_caveats -> disagrees with "discard".
            _judgment(score=50, verdict="discard"),
        ]
    )

    report = asyncio.run(run_judge_eval(labels_path, _claims_db(), llm))

    assert "MAE: 12.5" in report
    assert "Verdict agreement: 1/2" in report


def test_run_judge_eval_lists_hard_walls(tmp_path: Path) -> None:
    labels_path = _write_bank(tmp_path)
    llm = FakeMultiReplyLlmClient(
        [
            _judgment(score=50, verdict="apply_with_caveats"),
            _judgment(score=30, verdict="discard", hard_wall="Java/Spring core stack"),
        ]
    )

    report = asyncio.run(run_judge_eval(labels_path, _claims_db(), llm))

    assert "Java/Spring core stack" in report


def test_run_judge_eval_forwards_model_override(tmp_path: Path) -> None:
    labels_path = _write_bank(tmp_path)
    recorded: list[str | None] = []

    class RecordingLlmClient(FakeMultiReplyLlmClient):
        async def json_completion(
            self,
            system: str,
            user: str,
            schema: type[object],
            *,
            model: str | None = None,
        ) -> object:
            recorded.append(model)
            return await super().json_completion(system, user, schema, model=model)

    llm = RecordingLlmClient(
        [
            _judgment(score=50, verdict="apply_with_caveats"),
            _judgment(score=30, verdict="discard"),
        ]
    )

    asyncio.run(run_judge_eval(labels_path, _claims_db(), llm, model="sonnet"))

    assert recorded == ["sonnet", "sonnet"]


class FakeFailAtLlmClient(FakeMultiReplyLlmClient):
    """Like ``FakeMultiReplyLlmClient``, but raises ``error`` instead of
    returning a reply on the call at 0-based index ``fail_at``."""

    def __init__(
        self, replies: list[OfferJudgment], *, fail_at: int, error: Exception
    ) -> None:
        super().__init__(replies)
        self._fail_at = fail_at
        self._error = error

    async def json_completion(
        self,
        system: str,
        user: str,
        schema: type[object],
        *,
        model: str | None = None,
    ) -> object:
        if self.calls == self._fail_at:
            self.calls += 1
            raise self._error
        return await super().json_completion(system, user, schema, model=model)


def test_run_judge_eval_continues_after_one_offer_fails(tmp_path: Path) -> None:
    """T2c robustness follow-up: one offer's judge call failing must never
    abort the whole eval run — it is recorded as a failure (with its error)
    and excluded from MAE/agreement, and every OTHER offer still gets
    judged and reported."""
    labels_path = _write_bank(tmp_path)
    llm = FakeFailAtLlmClient(
        [_judgment(score=50, verdict="apply_with_caveats")],  # only offer a
        fail_at=1,
        error=LlmResponseError("boom"),
    )

    report = asyncio.run(run_judge_eval(labels_path, _claims_db(), llm))

    assert "offers/a.txt" in report
    assert "offers/b.txt" in report
    assert "boom" in report
    assert "Failures: 1/2" in report
    # MAE/agreement computed over the ONE successful row only: label a=55,
    # judge=50 -> |55-50|=5, band(50)=apply_with_caveats -> agrees.
    assert "MAE: 5.0" in report
    assert "Verdict agreement: 1/1" in report


def test_run_judge_eval_all_offers_failing_reports_zero_successful_rows(
    tmp_path: Path,
) -> None:
    labels_path = _write_bank(tmp_path)
    llm = FakeFailAtLlmClient([], fail_at=0, error=LlmResponseError("boom"))

    report = asyncio.run(run_judge_eval(labels_path, _claims_db(), llm))

    assert "Failures: 2/2" in report
    assert "offers/a.txt" in report
    assert "offers/b.txt" in report


def test_run_judge_eval_forwards_threshold_to_derive_verdict(tmp_path: Path) -> None:
    """T2d robustness follow-up: run_judge_eval used to always grade against
    judge_offer's hardcoded default threshold (65), ignoring the actually
    configured threshold entirely — duplicated-constant bug.

    A single-offer bank whose LABEL verdict is "discard" (never
    "apply_with_caveats") isolates the assertion to the JUDGE's own derived
    verdict column, so a match can only come from there."""
    offers_dir = tmp_path / "offers"
    offers_dir.mkdir()
    (offers_dir / "a.txt").write_text("Offer A text", encoding="utf-8")
    labels_path = tmp_path / "labels.json"
    labels_path.write_text(
        json.dumps(
            {
                "tolerance": 10,
                "offers": [{"file": "offers/a.txt", "score": 60, "verdict": "discard"}],
            }
        ),
        encoding="utf-8",
    )

    default_report = asyncio.run(
        run_judge_eval(
            labels_path,
            _claims_db(),
            FakeMultiReplyLlmClient([_judgment(score=60, verdict="apply")]),
        )
    )
    lowered_report = asyncio.run(
        run_judge_eval(
            labels_path,
            _claims_db(),
            FakeMultiReplyLlmClient([_judgment(score=60, verdict="apply")]),
            threshold=55,
        )
    )

    assert "apply_with_caveats" in default_report  # default threshold 65: score 60 < 65
    assert "apply_with_caveats" not in lowered_report  # threshold 55: score 60 >= 55


def test_run_judge_eval_with_no_offers_reports_empty_bank(tmp_path: Path) -> None:
    labels_path = tmp_path / "labels.json"
    labels_path.write_text(
        json.dumps({"tolerance": 10, "offers": []}), encoding="utf-8"
    )

    report = asyncio.run(
        run_judge_eval(labels_path, _claims_db(), FakeMultiReplyLlmClient([]))
    )

    assert "No labeled offers" in report
