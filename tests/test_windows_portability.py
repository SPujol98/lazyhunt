"""Windows portability: paths shown or stored use ``/``, files keep their bytes.

Reproduced on any OS: ``PureWindowsPath`` stands for a path built on Windows
and the ``windows_newlines`` fixture (``tests/conftest.py``) makes text-mode
writes translate ``\\n`` to ``\\r\\n`` as Windows does.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath

import pytest

from cva.fsutil import atomic_write, display_path
from cva.notifier import cv_ready_message
from cva.profile_writer import read_document, write_preferences
from cva.service import RejudgeResult
from tests.demo import DEMO_DIR
from tests.test_cli import FakeService, run
from tests.test_sent_without_cv import _pasted_offer, _service
from tests.test_service import (
    _judgment,
    _long_text,
    _noop_sleep,
    _rejudge_service,
    _RejudgeLlm,
    _seed_offer,
)

# --- paths: one stable ``/`` form wherever a path is shown or stored ----------


def test_display_path_uses_forward_slashes() -> None:
    assert display_path(PureWindowsPath(r"data\rejudge.md")) == "data/rejudge.md"
    assert display_path(PureWindowsPath(r"C:\x\cv.pdf")) == "C:/x/cv.pdf"
    assert display_path(Path("adaptadas/cv.pdf")) == "adaptadas/cv.pdf"


def test_cv_ready_card_shows_the_path_with_forward_slashes() -> None:
    text = cv_ready_message(PureWindowsPath(r"C:\x\cv.pdf"), lang="en")  # type: ignore[arg-type]
    assert text.startswith("📄 CV ready: C:/x/cv.pdf. Mark it")


def test_cli_rejudge_report_line_uses_forward_slashes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = FakeService()
    service.rejudge_result = RejudgeResult(
        dry_run=False,
        total_candidates=0,
        judged=0,
        skipped_state=0,
        skipped_already_judged=0,
        skipped_short_text=0,
        estimated_tokens=0,
        rows=[],
        report_text="# r",
    ).model_copy(  # a path built on Windows (no validation: not a ``Path`` here)
        update={"report_path": PureWindowsPath(r"data\rejudge_2026-09-27_1200.md")}
    )

    out = run(["rejudge"], service)

    assert "data/rejudge_2026-09-27_1200.md" in out
    assert "\\" not in out


def test_sent_cv_path_is_stored_with_forward_slashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    service, repo, _clock = _service(tmp_path)
    offer_id = _pasted_offer(service)
    asyncio.run(service.generate_cv(offer_id, "AI"))
    windows_path = PureWindowsPath(r"adaptadas\1_offer\CV.pdf")

    service.mark_sent(offer_id, cv_path=windows_path)  # type: ignore[arg-type]

    assert service.offer(offer_id).cv_adaptado == "adaptadas/1_offer/CV.pdf"
    snapshot = repo.get_sent_snapshot(offer_id)
    assert snapshot is not None and snapshot.cv_file == "CV.pdf"


def test_a_stored_cv_path_reads_back_as_the_same_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    service, _repo, _clock = _service(tmp_path)
    offer_id = _pasted_offer(service)
    pdf_path = asyncio.run(service.generate_cv(offer_id, "AI"))

    stored = service.offer(offer_id).cv_adaptado
    assert stored == pdf_path.as_posix()
    assert stored is not None and Path(stored) == pdf_path


# --- newlines: lazyhunt writes the same bytes on every OS ---------------------


@pytest.mark.usefixtures("windows_newlines")
def test_atomic_write_never_translates_newlines(tmp_path: Path) -> None:
    path = tmp_path / "f.json"

    atomic_write(path, "a\nb\n")

    assert path.read_bytes() == b"a\nb\n"


@pytest.mark.usefixtures("windows_newlines")
def test_atomic_write_keeps_given_crlf(tmp_path: Path) -> None:
    path = tmp_path / "config.local.toml"

    atomic_write(path, "a = 1\r\nb = 2\r\n")

    assert path.read_bytes() == b"a = 1\r\nb = 2\r\n"


def test_bak_is_a_byte_copy_of_a_crlf_file(tmp_path: Path) -> None:
    path = tmp_path / "preferences.toml"
    original = (DEMO_DIR / "preferences.toml").read_bytes().replace(b"\n", b"\r\n")
    path.write_bytes(original)
    doc = read_document(path)
    doc["search"] = {"work_modes": ["remote"]}

    write_preferences(path, doc)

    assert (tmp_path / "preferences.toml.bak").read_bytes() == original
    assert b"\r" not in path.read_bytes()


@pytest.mark.usefixtures("windows_newlines")
def test_written_profile_and_bak_have_no_translated_newlines(tmp_path: Path) -> None:
    path = tmp_path / "preferences.toml"
    original = (DEMO_DIR / "preferences.toml").read_bytes()
    path.write_bytes(original)
    doc = read_document(path)
    doc["search"] = {"work_modes": ["remote"]}

    write_preferences(path, doc)

    assert (tmp_path / "preferences.toml.bak").read_bytes() == original
    assert b"\r" not in path.read_bytes()


@pytest.mark.usefixtures("windows_newlines")
def test_rejudge_report_file_has_no_translated_newlines(tmp_path: Path) -> None:
    llm = _RejudgeLlm([_judgment(score=70, verdict="apply")])
    now_fn = lambda: datetime(2026, 9, 27, 14, 5, 3, tzinfo=UTC)
    service, repo = _rejudge_service(tmp_path, llm, now_fn=now_fn)
    _seed_offer(repo, url="https://x/1", text=_long_text())

    result = asyncio.run(service.rejudge_backlog(pace_seconds=0, sleep_fn=_noop_sleep))

    assert result.report_path is not None
    assert result.report_path.read_bytes() == result.report_text.encode("utf-8")


@pytest.mark.usefixtures("windows_newlines")
def test_rejudge_report_only_file_has_no_translated_newlines(tmp_path: Path) -> None:
    service, repo = _rejudge_service(tmp_path, _RejudgeLlm([]))
    offer = _seed_offer(repo, url="https://x/1", text=_long_text())
    repo.save_judge_result(
        offer,
        score=77,
        verdict="apply",
        judgment_json=_judgment(score=77, verdict="apply").model_dump_json(),
    )

    result = asyncio.run(service.rejudge_backlog(report_only=True, min_score=0))

    assert result.report_path is not None
    assert result.report_path.read_bytes() == result.report_text.encode("utf-8")
