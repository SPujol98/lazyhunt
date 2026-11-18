"""demo-profile P2: the synthetic eval bank under ``profiles/demo/eval``.

No real LLM and no network: the harness runs against a fake client, and the
deterministic gates / stack wall are exercised on the offer texts themselves.
"""

import asyncio
import json
from pathlib import Path

import pytest

from cva.claimsdb import ClaimsDB, load_claims
from cva.ingest.posting_guard import clean_text
from cva.match.extraction import OfferExtraction, Requirement
from cva.match.gates import run_gates
from cva.match.judge import OfferJudgment
from cva.match.judge_eval import run_judge_eval
from cva.match.stack_wall import stack_wall_reason
from cva.profile_paths import resolve_profile_paths
from cva.profile_prefs import load_preferences

_DEMO = Path(__file__).resolve().parent.parent / "profiles" / "demo"
_VERDICTS = {"apply", "apply_with_caveats", "discard"}


def _labels() -> dict[str, object]:
    path = resolve_profile_paths(_DEMO).eval_labels
    data: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _entries() -> list[dict[str, object]]:
    offers = _labels()["offers"]
    assert isinstance(offers, list)
    return offers


def test_profile_resolves_the_demo_eval_dir() -> None:
    assert resolve_profile_paths(_DEMO).eval_labels == _DEMO / "eval" / "labels.json"
    assert (_DEMO / "eval" / "README.md").is_file()


def test_labels_cover_15_to_20_offers_and_say_they_are_synthetic() -> None:
    data = _labels()
    assert 15 <= len(_entries()) <= 20
    assert isinstance(data["tolerance"], int)
    assert "synthetic" in str(data["_about"]).lower()


def test_every_labeled_file_exists_and_every_offer_file_is_labeled() -> None:
    labeled = {str(e["file"]) for e in _entries()}
    assert len(labeled) == len(_entries())
    on_disk = {
        p.relative_to(_DEMO / "eval").as_posix()
        for p in (_DEMO / "eval" / "offers").glob("*.txt")
    }
    assert labeled == on_disk


def test_labels_are_well_formed() -> None:
    verdicts = set()
    for e in _entries():
        score = e["score"]
        assert isinstance(score, int) and 0 <= score <= 100
        assert e["verdict"] in _VERDICTS
        assert isinstance(e["walls"], list) and isinstance(e["fit"], list)
        verdicts.add(e["verdict"])
        if e["verdict"] == "apply":
            assert score >= 70
        if e["verdict"] == "discard":
            assert score < 45
    assert verdicts == _VERDICTS


def test_offers_pass_the_posting_guard_and_have_realistic_length() -> None:
    for e in _entries():
        text = (_DEMO / "eval" / str(e["file"])).read_text(encoding="utf-8")
        report = clean_text(text)
        assert report.removed == 0 and not report.tampering, e["file"]
        assert report.text.strip() == text.strip()
        assert 150 <= len(text.split()) <= 450, e["file"]
        assert len(text) > 500  # service._REJUDGE_MIN_TEXT_LEN


def test_hard_wall_offers_trip_the_expected_deterministic_gates() -> None:
    prefs = load_preferences(_DEMO / "preferences.toml")
    checked = 0
    for e in _entries():
        expected = e.get("expected_gates")
        if not expected:
            continue
        assert isinstance(expected, dict)
        text = (_DEMO / "eval" / str(e["file"])).read_text(encoding="utf-8")
        got = {g.gate: g.verdict for g in run_gates(text, None, prefs.gates)}
        for gate, verdict in expected.items():
            assert got[gate] == verdict, (e["file"], gate, got)
        checked += 1
    assert checked >= 3
    gates_seen = {g for e in _entries() for g in (e.get("expected_gates") or {})}
    assert {"eligibility", "language", "location"} <= gates_seen


def test_clear_fits_are_not_blocked_by_any_gate() -> None:
    prefs = load_preferences(_DEMO / "preferences.toml")
    for e in _entries():
        if e["verdict"] != "apply":
            continue
        text = (_DEMO / "eval" / str(e["file"])).read_text(encoding="utf-8")
        verdicts = [g.verdict for g in run_gates(text, None, prefs.gates)]
        assert "FAIL" not in verdicts, e["file"]


def _offer_text(entry: dict[str, object]) -> str:
    return (_DEMO / "eval" / str(entry["file"])).read_text(encoding="utf-8")


def _must_have(*names: str) -> OfferExtraction:
    return OfferExtraction(
        requirements=[
            Requirement(name=n, category="skill", must_have=True, evidence=n)
            for n in names
        ]
    )


def test_java_and_dotnet_core_offers_are_stack_walled_and_python_offers_are_not() -> (
    None
):
    prefs = load_preferences(_DEMO / "preferences.toml")
    walled = []
    for e in _entries():
        text = _offer_text(e)
        wall = e.get("expected_stack_wall")
        if wall:
            # The wall needs two agreeing signals: the extracted must-have AND
            # the language dominating the posting text.
            reason = stack_wall_reason(_must_have(str(wall)), text, prefs.stack_wall)
            assert reason == f"stack principal {wall}, no Python", e["file"]
            # Precision first: the posting text alone never walls an offer.
            assert stack_wall_reason(_must_have(), text, prefs.stack_wall) is None
            # A Python must-have always wins.
            assert (
                stack_wall_reason(
                    _must_have(str(wall), "Python"), text, prefs.stack_wall
                )
                is None
            )
            walled.append(Path(str(e["file"])).stem)
        elif e["verdict"] == "apply":
            assert stack_wall_reason(_must_have(), text, prefs.stack_wall) is None
    assert walled == ["kestrel-java-spring-developer", "sable-dotnet-backend"]


class _CannedLlm:
    """Answers each offer with its own label, picked by the offer text it gets."""

    model = "fake"

    def __init__(self) -> None:
        self.judged: list[str] = []
        self._by_title = {_offer_text(e).splitlines()[0].strip(): e for e in _entries()}
        assert len(self._by_title) == len(_entries()), "offer titles must be unique"

    async def json_completion(
        self, system: str, user: str, schema: type[object], *, model: str | None = None
    ) -> object:
        matches = [t for t in self._by_title if t in user]
        assert len(matches) == 1, f"user prompt must carry exactly one offer: {matches}"
        label = self._by_title[matches[0]]
        self.judged.append(str(label["file"]))
        return OfferJudgment(
            score=int(label["score"]),  # type: ignore[call-overload]
            verdict=label["verdict"],  # type: ignore[arg-type]
            core_role="role",
            fit=[],
            walls=[],
            gaps=[],
            cv_base="AI_ES",
            tailoring_hints=[],
            rationale="canned",
        )


@pytest.fixture(scope="module")
def db() -> ClaimsDB:
    return load_claims(resolve_profile_paths(_DEMO).claims)


def test_judge_eval_runs_end_to_end_on_the_demo_bank(db: ClaimsDB) -> None:
    llm = _CannedLlm()
    report = asyncio.run(
        run_judge_eval(resolve_profile_paths(_DEMO).eval_labels, db, llm)
    )
    assert sorted(llm.judged) == sorted(str(e["file"]) for e in _entries())
    assert "MAE: 0.0" in report
    assert "Failures" not in report
