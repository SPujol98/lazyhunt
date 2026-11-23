"""judge-eval uses the configured profile (beta5-reliability S1(4)).

``JobService.judge_eval`` must judge with exactly the inputs ``analyze()``
uses: the profile's persona, families, ``lang`` and the deterministic gates
(never the approved outcome examples: the eval stays independent) — and keep the owner's persona when ``profile_dir`` is
unset. Fake LLM only; the demo profile's own eval bank.
"""

import asyncio
import sqlite3
from pathlib import Path

import pytest

import cli
from cva.claimsdb import load_claims
from cva.match.extraction import ExtractionCache
from cva.match.judge import FitItem, OfferJudgment, build_system_prompt
from cva.match.persona_defaults import OWNER_JUDGE_PERSONA
from cva.match.scorer import load_synonyms
from cva.profile_paths import resolve_profile_paths
from cva.profile_persona import load_judge_persona
from cva.service import JobService
from cva.tracker import OfferRepository
from tests.demo import DEMO_DIR

LABELS = DEMO_DIR / "eval" / "labels.json"
US_ONLY = "offers/summit-ai-engineer-us-only.txt"


class _PromptRecordingLlm:
    """Answers every judge call with one fixed apply-band judgment and keeps
    each system prompt (the persona lives there)."""

    def __init__(self, claim_id: str, score: int = 80) -> None:
        self.model = "test-model"
        self.systems: list[str] = []
        self._claim_id = claim_id
        self._score = score

    async def json_completion(
        self,
        system: str,
        user: str,
        schema: type[object],
        *,
        model: str | None = None,
    ) -> object:
        self.systems.append(system)
        return OfferJudgment(
            score=self._score,
            verdict="apply",
            core_role="AI Engineer",
            fit=[FitItem(requirement="Python", claim_ids=[self._claim_id])],
            walls=[],
            gaps=[],
            cv_base="AI_EN",
            tailoring_hints=[],
            rationale="Fits the profile.",
        )


def _service(tmp_path: Path, profile_dir: Path | None) -> tuple[JobService, str]:
    paths = resolve_profile_paths(profile_dir)
    conn = sqlite3.connect(tmp_path / "app.db")
    db = load_claims(paths.claims)
    claim_id = next(c.id for c in db.claims if c.category == "skill")
    llm = _PromptRecordingLlm(claim_id)
    service = JobService(
        repo=OfferRepository(conn),
        llm=llm,
        fetcher=None,  # type: ignore[arg-type]
        db=db,
        syn=load_synonyms(paths.synonyms, db),
        cache=ExtractionCache(conn),
        profile_paths=paths,
        match_mode="hybrid",
    )
    return service, claim_id


def _llm(service: JobService) -> _PromptRecordingLlm:
    llm = service._llm
    assert isinstance(llm, _PromptRecordingLlm)
    return llm


def test_judge_eval_uses_the_profile_persona(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, DEMO_DIR)
    demo = load_judge_persona(DEMO_DIR / "judge")

    asyncio.run(service.judge_eval(LABELS))

    systems = _llm(service).systems
    assert systems
    assert all(demo.facts in s for s in systems)
    assert all(OWNER_JUDGE_PERSONA.facts not in s for s in systems)


def test_judge_eval_prompt_matches_the_analysis_prompt(tmp_path: Path) -> None:
    """Same persona, families and lang as ``analyze()``: with no approved
    outcome example the system prompt of both is identical."""
    service, _ = _service(tmp_path, DEMO_DIR)
    text = (DEMO_DIR / "eval" / US_ONLY).read_text(encoding="utf-8")
    labels = tmp_path / "labels.json"
    (tmp_path / "offers").mkdir()
    (tmp_path / US_ONLY).write_text(text, encoding="utf-8")
    labels.write_text(
        '{"offers": [{"file": "' + US_ONLY + '", "score": 20, "verdict": "discard"}]}',
        encoding="utf-8",
    )
    asyncio.run(service.judge_eval(labels))
    eval_system = _llm(service).systems[-1]

    analyze_system = build_system_prompt(
        service._db,
        threshold=service._threshold,
        extra_anchors=service._judge_anchors(),
        anchor_thresholds=service._anchor_thresholds(),
        persona=service._judge_persona,
        families=service._prefs.families,
        lang=service.ui_language,
    )
    assert eval_system == analyze_system


def test_judge_eval_applies_the_deterministic_gates(tmp_path: Path) -> None:
    """The US-only offer trips the eligibility gate: a hard wall forces
    ``discard`` even though the (fake) judge scored it in the apply band."""
    service, _ = _service(tmp_path, DEMO_DIR)

    report = asyncio.run(service.judge_eval(LABELS))

    line = next(row for row in report.splitlines() if row.startswith(US_ONLY))
    columns = line.split()
    assert columns[5] == "discard"  # judge_verdict
    assert columns[6] != "-"  # the gate's hard wall is listed


@pytest.mark.owner  # the legacy layout reads the owner's own claims
def test_judge_eval_keeps_the_owner_persona_without_a_profile(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, None)
    bank = tmp_path / "bank"
    (bank / "offers").mkdir(parents=True)
    (bank / "offers" / "a.txt").write_text("AI Engineer, Python.", encoding="utf-8")
    (bank / "labels.json").write_text(
        '{"offers": [{"file": "offers/a.txt", "score": 80, "verdict": "apply"}]}',
        encoding="utf-8",
    )

    asyncio.run(service.judge_eval(bank / "labels.json"))

    assert OWNER_JUDGE_PERSONA.facts in _llm(service).systems[0]


def test_judge_eval_details_prints_each_offer_judgment(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, DEMO_DIR)

    plain = asyncio.run(service.judge_eval(LABELS))
    detailed = asyncio.run(service.judge_eval(LABELS, details=True))

    assert "Fits the profile." not in plain
    assert detailed.startswith(plain)
    assert detailed.count("Fits the profile.") == 19
    assert f"{US_ONLY}: judge 80 discard | label 20 discard" in detailed


def test_cli_judge_eval_details_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    service, _ = _service(tmp_path, DEMO_DIR)

    out = cli.run(["judge-eval", "--labels", str(LABELS), "--details"], service)

    assert "Fits the profile." in out
