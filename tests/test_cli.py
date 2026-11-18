"""CLI tests (tasks 8b.5–8b.6): argparse verbs over the SAME JobService seam.

Parity with bot.py (design D12): the CLI is a thin plain-text shell — the
breakdown text is the notifier's ``breakdown_message``, so bot and CLI render
identical analysis output. Tests run against a duck-typed fake service (zero
network, zero LLM, zero real files beyond tmp paths).
"""

import sys
from collections.abc import Callable
from pathlib import Path

import pytest

import cli
from cva.beacons import BeaconPausedAlert, NewJobNotification, SweepResult
from cva.config import Settings
from cva.match import MatchResult, Requirement, RequirementMatch
from cva.match.judge import OfferJudgment
from cva.profile_paths import resolve_profile_paths
from cva.service import RejudgeResult, RejudgeRow, ServiceError
from cva.tracker import (
    BeaconRow,
    HistoryImportResult,
    KeywordRow,
    OfferRow,
    RepositoryError,
)


class FakeService:
    """Duck-typed JobService double recording verb dispatch."""

    threshold = 65

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        # llm-offer-judge: keyword-mode default (None, False) — matches
        # JobService.latest_judgment's own default for match_mode="keyword".
        self.judgment: OfferJudgment | None = None
        self.judgment_fallback = False
        self.judge_eval_report = "report"
        self.rejudge_result = RejudgeResult(
            dry_run=False,
            total_candidates=0,
            judged=0,
            skipped_state=0,
            skipped_already_judged=0,
            skipped_short_text=0,
            estimated_tokens=0,
        )

    async def run_due_beacons(self) -> SweepResult:
        self.calls.append(("run_due_beacons", ()))
        return SweepResult(
            notifications=[
                NewJobNotification(
                    beacon_id=1,
                    beacon_name="ia-espana",
                    job_id="4001122334",
                    title="Ingeniero de IA",
                    company="Acme",
                    age="2 hours ago",
                )
            ],
            paused_alerts=[
                BeaconPausedAlert(
                    beacon_id=2,
                    beacon_name="ds-remoto",
                    wall_kind="js_shell",
                    consecutive_walls=3,
                )
            ],
        )

    async def ingest(self, url: str | None, paste: str | None) -> int:
        self.calls.append(("ingest", (url, paste)))
        return 7

    async def analyze(self, offer_id: int) -> MatchResult:
        self.calls.append(("analyze", (offer_id,)))
        return MatchResult(
            score=50,
            threshold=self.threshold,
            breakdown=[
                RequirementMatch(
                    requirement=Requirement(
                        name="Python",
                        category="skill",
                        must_have=True,
                        evidence="experiencia con Python",
                    ),
                    claim_ids=["skill-python"],
                    status="✓",
                )
            ],
        )

    def latest_judgment(self, offer_id: int) -> tuple[OfferJudgment | None, bool]:
        self.calls.append(("latest_judgment", (offer_id,)))
        return self.judgment, self.judgment_fallback

    async def judge_eval(self, labels_path: Path) -> str:
        self.calls.append(("judge_eval", (labels_path,)))
        return self.judge_eval_report

    async def rejudge_backlog(self, **kwargs: object) -> RejudgeResult:
        self.calls.append(("rejudge_backlog", (kwargs,)))
        return self.rejudge_result

    async def generate_cv(self, offer_id: int, role: str, lang: str | None) -> Path:
        self.calls.append(("generate_cv", (offer_id, role, lang)))
        return Path("/tmp/adaptadas/Alex_Rivera_AI_Engineer_Acme.pdf")

    def mark_sent(self, offer_id: int, cv_path: object = None) -> None:
        self.calls.append(("mark_sent", (offer_id,)))

    def offer(self, offer_id: int) -> OfferRow:
        self.calls.append(("offer", (offer_id,)))
        return OfferRow(
            id=offer_id,
            estado="enviada",
            match=50,
            source_data="",
        )

    def keywords(self, profile: str | None = None) -> list[KeywordRow]:
        self.calls.append(("keywords", (profile,)))
        return [
            KeywordRow(profile="default", keyword="Python", frequency=2, status="✓")
        ]

    def import_history(
        self, path: Path, *, dry_run: bool = False
    ) -> HistoryImportResult:
        self.calls.append(("import_history", (path, dry_run)))
        return HistoryImportResult(
            inserted=3,
            merged=2,
            skipped=1,
            invalid=["#9: estado inválido"],
            dry_run=dry_run,
        )

    def export(self, path: Path | None = None) -> Path:
        self.calls.append(("export", (path,)))
        return path or Path("data/export.xlsx")

    def beacons(self) -> list[BeaconRow]:
        self.calls.append(("beacons", ()))
        return [
            BeaconRow(
                id=1, name="ia-espana", url="u", status="paused", consecutive_walls=3
            )
        ]

    def create_beacon(self, name: str, url: str) -> int:
        self.calls.append(("create_beacon", (name, url)))
        return 3

    def resume_beacon(self, beacon_id: int) -> None:
        if beacon_id == 99:
            raise RepositoryError(f"beacon {beacon_id} not found")
        self.calls.append(("resume_beacon", (beacon_id,)))

    def disable_beacon(self, beacon_id: int) -> None:
        self.calls.append(("disable_beacon", (beacon_id,)))


def run(argv: list[str], service: FakeService | None = None) -> str:
    return cli.run(argv, service if service is not None else FakeService())


# --- Analysis parity with the bot (same seam, same plain-text breakdown) -------


def test_analyze_prints_breakdown_parity_with_bot() -> None:
    service = FakeService()
    out = run(["analyze", "12"], service)
    assert service.calls == [("analyze", (12,)), ("latest_judgment", (12,))]
    assert "puntuación 50/100" in out
    assert "umbral 65" in out
    assert "✓ Python" in out  # notifier.breakdown_message rendering


def test_analyze_shows_judge_card_when_a_judgment_is_present() -> None:
    """hybrid/judge match_mode: the CLI renders the same judge-aware card as
    the bot (judge_breakdown_message), sourced from service.latest_judgment."""
    from cva.match.judge import FitItem

    service = FakeService()
    service.judgment = OfferJudgment(
        score=76,
        verdict="apply",
        core_role="AI Engineer junior",
        fit=[FitItem(requirement="RAG", claim_ids=["skill-rag"])],
        walls=[],
        gaps=[],
        cv_base="AI_ES",
        tailoring_hints=[],
        rationale="Buen encaje.",
    )

    out = run(["analyze", "12"], service)

    assert service.calls == [("analyze", (12,)), ("latest_judgment", (12,))]
    assert "76/100" in out
    assert "AI Engineer junior" in out
    assert "✓ Python" in out  # keyword breakdown still shown below


def test_analyze_notes_fallback_when_judge_failed() -> None:
    service = FakeService()
    service.judgment_fallback = True

    out = run(["analyze", "12"], service)

    assert "50/100" in out  # keyword fallback score
    assert "falló" in out.lower()


# --- judge-eval harness (llm-offer-judge design) -----------------------------


def test_judge_eval_prints_quota_warning_before_the_report(
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = FakeService()
    service.judge_eval_report = "file  label  judge\nMAE: 3.0"

    out = run(["judge-eval"], service)

    assert out == "file  label  judge\nMAE: 3.0"
    printed = capsys.readouterr().out
    assert "cuota" in printed.lower() or "quota" in printed.lower()


def test_judge_eval_forwards_labels_path() -> None:
    service = FakeService()

    run(["judge-eval", "--labels", "eval/labels.json"], service)

    assert service.calls == [("judge_eval", (Path("eval/labels.json"),))]


# --- rejudge (feature: rejudge-backlog) --------------------------------------


def test_rejudge_dry_run_skips_the_quota_warning_and_shows_the_estimate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = FakeService()
    service.rejudge_result = RejudgeResult(
        dry_run=True,
        total_candidates=12,
        judged=0,
        skipped_state=3,
        skipped_already_judged=2,
        skipped_short_text=1,
        estimated_tokens=120_000,
    )

    out = run(["rejudge", "--dry-run"], service)

    printed = capsys.readouterr().out
    assert "cuota" not in printed.lower() and "quota" not in printed.lower()
    assert "12" in out
    assert "120000" in out or "120,000" in out or "120 000" in out


def test_rejudge_prints_llm_banner_before_the_dry_run_line(
    capsys: pytest.CaptureFixture[str], clean_settings: Callable[..., Settings]
) -> None:
    """shared-config T2: the effective-LLM banner prints even for a pure
    --dry-run (which makes zero LLM calls), before the dry-run summary."""
    service = FakeService()
    service.rejudge_result = RejudgeResult(
        dry_run=True,
        total_candidates=1,
        judged=0,
        skipped_state=0,
        skipped_already_judged=0,
        skipped_short_text=0,
        estimated_tokens=1_000,
    )
    settings = clean_settings(llm_provider="claude_code")

    out = cli.run(["rejudge", "--dry-run"], service, settings=settings)

    printed = capsys.readouterr().out.strip()
    assert printed == "LLM: proveedor=claude_code · extracción=default · juez=default"
    assert "Dry-run" in out


def test_judge_eval_prints_llm_banner_before_the_quota_warning(
    capsys: pytest.CaptureFixture[str], clean_settings: Callable[..., Settings]
) -> None:
    service = FakeService()
    settings = clean_settings(llm_provider="openai_compatible")

    cli.run(["judge-eval"], service, settings=settings)

    printed = capsys.readouterr().out
    lines = printed.splitlines()
    assert lines[0].startswith("LLM: proveedor=openai_compatible")
    assert "cuota" in lines[1].lower()


def test_rejudge_real_run_prints_quota_warning_before_the_report(
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = FakeService()
    service.rejudge_result = RejudgeResult(
        dry_run=False,
        total_candidates=1,
        judged=1,
        skipped_state=0,
        skipped_already_judged=0,
        skipped_short_text=0,
        estimated_tokens=10_000,
        rows=[
            RejudgeRow(
                offer_id=1,
                score=82,
                verdict="apply",
                title="AI Engineer",
                company="Acme",
                url="https://x/1",
                age_days=5,
                age_flag=False,
                walls=[],
            )
        ],
        report_text="# Rejudge del backlog\n\n- **82** [apply] AI Engineer — Acme",
        report_path=Path("data/rejudge_2026-09-27_1200.md"),
    )

    out = run(["rejudge"], service)

    printed = capsys.readouterr().out
    assert "cuota" in printed.lower() or "quota" in printed.lower()
    assert "AI Engineer" in out
    assert "data/rejudge_2026-09-27_1200.md" in out


def test_rejudge_forwards_flags_to_the_service() -> None:
    service = FakeService()

    run(
        [
            "rejudge",
            "--since",
            "2026-09-01",
            "--title-filter",
            "--include-discarded",
            "--force",
            "--limit",
            "5",
            "--pace",
            "3",
            "--min-score",
            "40",
        ],
        service,
    )

    assert len(service.calls) == 1
    name, (kwargs,) = service.calls[0]
    assert name == "rejudge_backlog"
    assert kwargs["since"] == "2026-09-01"
    assert kwargs["title_filter"] is True
    assert kwargs["include_discarded"] is True
    assert kwargs["force"] is True
    assert kwargs["limit"] == 5
    assert kwargs["dry_run"] is False
    assert kwargs["pace_seconds"] == 3.0
    assert kwargs["min_score"] == 40
    assert callable(kwargs["progress_fn"])


def test_rejudge_defaults_match_the_feature_contract() -> None:
    service = FakeService()

    run(["rejudge", "--dry-run"], service)

    _, (kwargs,) = service.calls[0]
    assert kwargs["since"] is None
    assert kwargs["title_filter"] is False
    assert kwargs["include_discarded"] is False
    assert kwargs["force"] is False
    assert kwargs["limit"] is None
    assert kwargs["dry_run"] is True
    assert kwargs["pace_seconds"] == 20.0
    assert kwargs["min_score"] == 50


# --- rejudge hardening (odd/tasks/rejudge-backlog.md "Hardening" section) ---


def test_rejudge_forwards_report_only_and_max_consecutive_failures() -> None:
    service = FakeService()

    run(["rejudge", "--report-only", "--max-consecutive-failures", "5"], service)

    _, (kwargs,) = service.calls[0]
    assert kwargs["report_only"] is True
    assert kwargs["max_consecutive_failures"] == 5


def test_rejudge_default_max_consecutive_failures_is_three() -> None:
    service = FakeService()

    run(["rejudge", "--dry-run"], service)

    _, (kwargs,) = service.calls[0]
    assert kwargs["report_only"] is False
    assert kwargs["max_consecutive_failures"] == 3


def test_rejudge_report_only_skips_the_quota_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = FakeService()
    service.rejudge_result = RejudgeResult(
        dry_run=False,
        report_only=True,
        total_candidates=0,
        planned_candidates=0,
        judged=0,
        skipped_state=0,
        skipped_already_judged=0,
        skipped_short_text=0,
        estimated_tokens=0,
        report_text="# Rejudge del backlog\n\nsin resultados",
        report_path=Path("data/rejudge_2026-09-27_120000.md"),
    )

    run(["rejudge", "--report-only"], service)

    printed = capsys.readouterr().out
    assert "cuota" not in printed.lower() and "quota" not in printed.lower()


def test_rejudge_since_rejects_a_malformed_date() -> None:
    service = FakeService()
    with pytest.raises(SystemExit):
        run(["rejudge", "--since", "01-09-2026"], service)


def test_rejudge_limit_rejects_non_positive_values() -> None:
    service = FakeService()
    with pytest.raises(SystemExit):
        run(["rejudge", "--limit", "0"], service)


def test_rejudge_pace_rejects_negative_values() -> None:
    service = FakeService()
    with pytest.raises(SystemExit):
        run(["rejudge", "--pace", "-1"], service)


def test_rejudge_min_score_rejects_out_of_range_values() -> None:
    service = FakeService()
    with pytest.raises(SystemExit):
        run(["rejudge", "--min-score", "101"], service)


def test_rejudge_max_consecutive_failures_rejects_non_positive_values() -> None:
    service = FakeService()
    with pytest.raises(SystemExit):
        run(["rejudge", "--max-consecutive-failures", "0"], service)


def test_rejudge_dry_run_reports_exclusion_reasons_and_limit_capping(
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = FakeService()
    service.rejudge_result = RejudgeResult(
        dry_run=True,
        total_candidates=10,
        planned_candidates=4,
        judged=0,
        skipped_state=3,
        skipped_already_judged=2,
        skipped_short_text=1,
        skipped_since=2,
        skipped_title_filter=1,
        estimated_tokens=40_000,
    )

    out = run(["rejudge", "--dry-run", "--limit", "4"], service)

    assert "4" in out and "10" in out  # "would judge 4 of 10"
    assert "3" in out  # skipped_state
    assert "2" in out  # skipped_already_judged / skipped_since
    assert "1" in out  # skipped_short_text / skipped_title_filter


def test_cv_prints_pdf_path() -> None:
    service = FakeService()
    out = run(["cv", "12", "BACKEND", "en"], service)
    assert service.calls == [("generate_cv", (12, "BACKEND", "en"))]
    assert "Alex_Rivera_AI_Engineer_Acme.pdf" in out


def test_cv_accepts_catalan_lang() -> None:
    """v3 part B: the CLI surface takes the AI_CA combination too."""
    service = FakeService()
    out = run(["cv", "12", "AI", "ca"], service)
    assert service.calls == [("generate_cv", (12, "AI", "ca"))]
    assert "Alex_Rivera" in out


def test_ingest_paste_registers_offer() -> None:
    service = FakeService()
    out = run(["ingest", "--paste", "texto de oferta"], service)
    assert service.calls == [("ingest", (None, "texto de oferta"))]
    assert "#7" in out


def test_check_prints_new_jobs_and_pause_alert() -> None:
    service = FakeService()
    out = run(["check"], service)
    assert service.calls == [("run_due_beacons", ())]
    assert "linkedin.com/jobs/view/4001122334" in out  # new-job card
    assert "ia-espana" in out
    assert "pausado" in out and "ds-remoto" in out  # one-shot pause alert


def test_sent_export_keywords_wire_to_service(tmp_path: Path) -> None:
    service = FakeService()
    out = run(["sent", "12"], service)
    assert ("mark_sent", (12,)) in service.calls
    assert "enviada" in out

    target = tmp_path / "out.xlsx"
    out = run(["export", "--out", str(target)], service)
    assert ("export", (target,)) in service.calls
    assert str(target) in out

    out = run(["keywords", "default"], service)
    assert ("keywords", ("default",)) in service.calls
    assert "Python" in out and "×2" in out


def test_import_history_wires_to_service_and_prints_counts(tmp_path: Path) -> None:
    service = FakeService()
    path = tmp_path / "historial.json"
    out = run(["import-history", str(path)], service)
    assert ("import_history", (path, False)) in service.calls
    assert "insertadas: 3" in out and "fusionadas: 2" in out
    assert "omitidas: 1" in out and "inválidas: 1" in out
    assert "#9: estado inválido" in out
    assert "Dry-run" not in out


def test_import_history_dry_run_flag(tmp_path: Path) -> None:
    service = FakeService()
    path = tmp_path / "historial.json"
    out = run(["import-history", str(path), "--dry-run"], service)
    assert ("import_history", (path, True)) in service.calls
    assert "Dry-run" in out and "insertadas: 3" in out


def test_import_history_reports_unreadable_input(tmp_path: Path) -> None:
    class Failing(FakeService):
        def import_history(
            self, path: Path, *, dry_run: bool = False
        ) -> HistoryImportResult:
            raise ServiceError("cannot read x")

    out = run(["import-history", str(tmp_path / "x.json")], Failing())
    assert "cannot read x" in out


def test_beacon_verbs_wire_to_service() -> None:
    service = FakeService()
    out = run(["beacon", "list"], service)
    assert "ia-espana" in out and "paused" in out

    out = run(["beacon", "add", "ds-remoto", "https://x.example"], service)
    assert service.calls[-1] == ("create_beacon", ("ds-remoto", "https://x.example"))
    assert "#3" in out

    out = run(["beacon", "resume", "1"], service)
    assert ("resume_beacon", (1,)) in service.calls
    assert "reactivado" in out

    out = run(["beacon", "disable", "1"], service)
    assert ("disable_beacon", (1,)) in service.calls
    assert "desactivado" in out


def test_beacon_unknown_id_and_bad_usage() -> None:
    service = FakeService()
    out = run(["beacon", "resume", "99"], service)
    assert "99" in out and "⚠️" in out
    with pytest.raises(SystemExit):  # invalid verb = argparse usage error
        run(["beacon", "freeze", "1"], service)


def test_missing_command_is_a_usage_error() -> None:
    with pytest.raises(SystemExit):
        run([])


# --- Real entrypoint reads sys.argv (not a hardcoded []) --------------------


def test_main_reads_sys_argv_when_no_argv_given(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression: main() used to always call run([]) regardless of the
    process arguments, so `uv run python cli.py analyze 187` failed with
    "the following arguments are required: command" even though argv WAS
    ``["analyze", "187"]``. The real entrypoint (``if __name__ ==
    "__main__": main()``) must read ``sys.argv[1:]`` when no argv is passed
    explicitly; ``run(argv, service)`` itself stays argv-injectable for tests.
    """
    service = FakeService()
    monkeypatch.setattr(
        cli.JobService, "from_settings", lambda settings, **kwargs: service
    )
    monkeypatch.setattr(sys, "argv", ["cli.py", "analyze", "12"])
    monkeypatch.setenv("CVA_TELEGRAM_TOKEN", "123456:TEST")
    monkeypatch.setenv("CVA_LLM_API_KEY", "sk-test")

    cli.main()

    assert service.calls == [("analyze", (12,)), ("latest_judgment", (12,))]
    out = capsys.readouterr().out
    assert "puntuación 50/100" in out


def test_cv_rejects_ds_role() -> None:
    with pytest.raises(SystemExit):
        run(["cv", "12", "DS", "en"], FakeService())


def test_judge_eval_uses_the_profile_eval_labels_when_profile_dir_is_set(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    service = FakeService()
    settings = clean_settings(profile_dir=tmp_path)

    cli.run(["judge-eval"], service, settings=settings)

    assert service.calls == [("judge_eval", (tmp_path / "eval" / "labels.json",))]


@pytest.mark.usefixtures("legacy_profile")
@pytest.mark.parametrize(
    "with_settings", [True, False], ids=["settings", "no-settings"]
)
def test_judge_eval_falls_back_to_the_legacy_eval_labels_without_profile_dir(
    with_settings: bool, clean_settings: Callable[..., Settings]
) -> None:
    service = FakeService()
    settings = clean_settings() if with_settings else None
    if settings is not None:
        assert settings.profile_dir is None

    cli.run(["judge-eval"], service, settings=settings)

    # One definition of "legacy": the resolver's repo-anchored eval/labels.json.
    legacy = resolve_profile_paths(None).eval_labels
    assert legacy == Path(cli.__file__).resolve().parent / "eval" / "labels.json"
    assert service.calls == [("judge_eval", (legacy,))]


def test_judge_eval_explicit_labels_beat_the_profile_eval(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    service = FakeService()
    settings = clean_settings(profile_dir=tmp_path)

    cli.run(["judge-eval", "--labels", "x/labels.json"], service, settings=settings)

    assert service.calls == [("judge_eval", (Path("x/labels.json"),))]
