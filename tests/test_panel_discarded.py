"""/panel "Descartadas" cards: 7-day below/walled offers (service + renderer)."""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cva.fetch import FetchResponse
from cva.match import MatchResult
from cva.match.gates import GateResult
from cva.match.judge import OfferJudgment, Wall
from cva.report.panel import DiscardedOffer, render_panel
from cva.service import RESCUED_NOTE, JobService
from cva.tracker import OfferRepository
from cva.tracker.repo import OfferInput
from tests.test_auto_triage import (
    JSONLD_BODY,
    OFFER_URL,
    FakeFetcher,
    FakeLlmClient,
    _extraction,
    make_service,
)

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


@pytest.fixture()
def env(tmp_path: Path) -> tuple[JobService, OfferRepository]:
    service, repo = make_service(
        tmp_path,
        FakeLlmClient(_extraction()),
        FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
    )
    return service, repo


def _offer(
    repo: OfferRepository,
    n: int,
    *,
    score: int,
    profile: str = "ai",
    days_ago: float = 1,
    gates: list[GateResult] | None = None,
    judgment: OfferJudgment | None = None,
    estado: str = "analizada",
    title: str | None = None,
    location: str | None = None,
    url: str | None = None,
) -> int:
    offer_id = repo.upsert_offer(
        OfferInput(
            title=title or f"Puesto {n}",
            company=f"Empresa {n}",
            url=url or f"https://www.linkedin.com/jobs/view/{1000 + n}/",
            text=f"texto {n}",
            source="link",
            profile=profile,  # type: ignore[arg-type]
            location=location,
        )
    )
    result = MatchResult(score=score, threshold=65, breakdown=[], gates=gates or [])
    repo.save_match_result(
        offer_id,
        score=score,
        threshold=65,
        breakdown_json=result.model_dump_json(),
    )
    if judgment is not None:
        repo.save_judge_result(
            offer_id,
            score=judgment.score,
            verdict=judgment.verdict,
            judgment_json=judgment.model_dump_json(),
        )
    stamp = (NOW - timedelta(days=days_ago)).isoformat()
    repo._conn.execute(
        "UPDATE match_results SET created_at = ? WHERE offer_id = ?", (stamp, offer_id)
    )
    repo._conn.execute(
        "UPDATE judge_results SET created_at = ? WHERE offer_id = ?", (stamp, offer_id)
    )
    repo._conn.commit()
    repo.set_estado(offer_id, estado)
    return offer_id


def _judgment(
    score: int, rationale: str = "Encaje flojo. Segunda frase.", **kw: object
) -> OfferJudgment:
    return OfferJudgment(
        score=score,
        verdict="discard",
        core_role="x",
        cv_base="AI_ES",
        rationale=rationale,
        **kw,  # type: ignore[arg-type]
    )


def _fail(reason: str) -> GateResult:
    return GateResult(gate="location", verdict="FAIL", reason=reason)


def _fresh(service: JobService) -> list[DiscardedOffer]:
    service._now = lambda: NOW  # type: ignore[method-assign]
    return service.panel_discarded()


def test_below_zone_listed_closest_first_with_profile_threshold(
    env: tuple[JobService, OfferRepository],
) -> None:
    service, repo = env
    far = _offer(repo, 1, score=20, judgment=_judgment(20))
    near = _offer(repo, 2, score=45, judgment=_judgment(45, "Casi. Resto."))
    backend = _offer(repo, 3, score=32, profile="backend")  # gate 50, zone 15
    out = _fresh(service)
    below = [d for d in out if d.kind == "below"]
    # ai gate 65: 45 below 50; backend gate 50: 32 below 35; closest first
    assert [d.offer_id for d in below] == [backend, near, far]
    top = below[0]
    assert top.threshold == 50 and top.profile == "backend"
    assert (below[1].score, below[1].threshold, below[1].profile) == (45, 65, "ai")
    assert below[1].reason == "Casi."
    assert below[1].portal == "LinkedIn"


def test_gray_and_above_zone_offers_are_not_discarded(
    env: tuple[JobService, OfferRepository],
) -> None:
    service, repo = env
    _offer(repo, 1, score=50)  # gray: [50, 65) was notified
    _offer(repo, 2, score=80)  # above: notified
    _offer(repo, 3, score=49)  # below
    assert [d.offer_id for d in _fresh(service)] == [3]


def test_walled_by_gate_fail_even_with_high_score(
    env: tuple[JobService, OfferRepository],
) -> None:
    service, repo = env
    walled = _offer(
        repo,
        1,
        score=90,
        gates=[_fail("Ubicación fuera de España")],
        location="Berlin",
    )
    out = _fresh(service)
    assert [(d.offer_id, d.kind) for d in out] == [(walled, "walled")]
    assert out[0].reason == "Ubicación fuera de España"
    assert out[0].location == "Berlin"


def test_judge_hard_wall_below_threshold_is_walled_soft_wall_is_reason(
    env: tuple[JobService, OfferRepository],
) -> None:
    service, repo = env
    hard = _offer(
        repo,
        1,
        score=10,
        judgment=_judgment(10, walls=[Wall(text="Pide 6 años", hard=True)]),
    )
    soft = _offer(
        repo,
        2,
        score=12,
        judgment=_judgment(
            12, "Sin frase.", walls=[Wall(text="Híbrido lejos", hard=False)]
        ),
    )
    out = {d.offer_id: d for d in _fresh(service)}
    assert out[hard].kind == "walled" and out[hard].reason == "Pide 6 años"
    assert out[soft].kind == "below"


def test_reason_falls_back_to_gap_then_empty(
    env: tuple[JobService, OfferRepository],
) -> None:
    service, repo = env
    gap = _offer(repo, 1, score=5, judgment=_judgment(5, " ", gaps=["Falta Rust"]))
    none = _offer(repo, 2, score=6)
    out = {d.offer_id: d for d in _fresh(service)}
    assert out[gap].reason == "Falta Rust"
    assert out[none].reason == ""


def test_window_is_seven_days_and_reports_age(
    env: tuple[JobService, OfferRepository],
) -> None:
    service, repo = env
    _offer(repo, 1, score=10, days_ago=8)
    edge = _offer(repo, 2, score=10, days_ago=7)  # dates only: day 7 is inside
    inside = _offer(repo, 3, score=10, days_ago=0)
    out = _fresh(service)
    assert [d.offer_id for d in out] == [inside, edge]
    assert [d.days_ago for d in out] == [0, 7]


def test_duplicates_closed_and_progressed_offers_are_excluded(
    env: tuple[JobService, OfferRepository],
) -> None:
    service, repo = env
    dup = _offer(repo, 1, score=10)
    repo.append_note(dup, "duplicada de #99")
    _offer(repo, 2, score=10, estado="descartada")
    _offer(repo, 3, score=10, estado="enviada")
    keep = _offer(repo, 4, score=10)
    assert [d.offer_id for d in _fresh(service)] == [keep]


def _disc(**kw: object) -> DiscardedOffer:
    base: dict[str, object] = {
        "offer_id": 7,
        "title": "Dev",
        "company": "Acme",
        "portal": "LinkedIn",
        "url": "https://example.com/j/1",
        "location": None,
        "kind": "below",
        "score": 55,
        "threshold": 65,
        "profile": "ai",
        "reason": "Motivo",
        "days_ago": 2,
    }
    base.update(kw)
    return DiscardedOffer(**base)  # type: ignore[arg-type]


def test_render_below_section_summary_row_and_hint() -> None:
    out = render_panel(
        [],
        NOW,
        [
            _disc(offer_id=7, score=58),
            _disc(offer_id=8, score=45, profile="backend", threshold=50),
            _disc(offer_id=9, score=20),
        ],
    )
    assert "Descartadas por nota" in out
    assert (
        "3 descartadas por nota esta semana · 2 a menos de 10 puntos del umbral" in out
    )
    assert "58 / 65 (IA)" in out and "45 / 50 (Backend)" in out
    assert "/rescatar 7" in out
    assert "hace 2 días" in out
    assert 'href="https://example.com/j/1"' in out


def test_render_walled_section_is_collapsed_details() -> None:
    out = render_panel(
        [],
        NOW,
        [_disc(offer_id=3, kind="walled", location="Berlin", reason="Fuera de UE")],
    )
    match = re.search(r"<details[^>]*>(.*?)</details>", out, re.DOTALL)
    assert match is not None
    assert "Descartadas por muro" in match.group(1)
    assert "Berlin" in match.group(1) and "Fuera de UE" in match.group(1)
    assert "/rescatar 3" in match.group(1)
    assert "<details open" not in out


def test_render_empty_states_and_default() -> None:
    out = render_panel([], NOW)
    assert "Nada descartado por nota esta semana" in out
    assert "Descartadas por muro" in out


def test_render_escapes_and_truncates_and_blocks_bad_urls() -> None:
    evil = "<script>alert(1)</script>"
    out = render_panel(
        [],
        NOW,
        [
            _disc(title=evil, company=evil, reason=evil + "x" * 300),
            _disc(offer_id=2, url="javascript:alert(1)"),
        ],
    )
    assert evil not in out.split('<script id="panel-data"')[0]
    assert "&lt;script&gt;" in out
    assert "javascript:" not in out
    assert "x" * 200 not in out
    blob = re.search(r'<script id="panel-data"[^>]*>(.*?)</script>', out, re.DOTALL)
    assert blob is not None
    assert json.loads(blob.group(1)) == []


def test_reanalysed_offer_is_listed_once(
    env: tuple[JobService, OfferRepository],
) -> None:
    service, repo = env
    offer_id = _offer(repo, 1, score=20)
    repo.save_match_result(
        offer_id, score=20, threshold=65,
        breakdown_json=MatchResult(score=20, threshold=65, breakdown=[]).model_dump_json(),
    )  # fmt: skip
    stamp = (NOW - timedelta(days=1)).isoformat()
    repo._conn.execute("UPDATE match_results SET created_at = ?", (stamp,))
    repo._conn.commit()
    assert [d.offer_id for d in _fresh(service)] == [offer_id]


def test_rescued_offer_leaves_the_discarded_list(
    env: tuple[JobService, OfferRepository],
) -> None:
    service, repo = env
    offer_id = _offer(repo, 1, score=20)
    repo.add_event(offer_id, "note", note=RESCUED_NOTE)
    assert _fresh(service) == []
