"""The "duplicada de #<id>" note marker has one definition (offer-gates-and-health T5b).

The bridge writes it and the repository reads it back; both go through the same
helper and the repository's note separator.
"""

import sqlite3
from pathlib import Path
from typing import Any

import pytest

import cva.tracker.repo as repo_mod
from cva import bridge
from cva.tracker import OfferInput, OfferRepository, duplicate_note


def _repo(tmp_path: Path) -> OfferRepository:
    return OfferRepository(
        sqlite3.connect(tmp_path / "dup.db", check_same_thread=False),
        clock=lambda: "2026-10-18",
    )


def test_duplicate_note_is_the_marker_text() -> None:
    assert duplicate_note(7) == "duplicada de #7"
    assert duplicate_note(7).startswith(repo_mod.DUPLICATE_NOTE_PREFIX)


def test_marker_is_recognized_as_a_whole_entry() -> None:
    assert repo_mod._is_stored_duplicate(duplicate_note(3))
    assert repo_mod._is_stored_duplicate(f"nota a mano · {duplicate_note(12)}")
    assert not repo_mod._is_stored_duplicate("me escribieron: duplicada de #3 hoy")
    assert not repo_mod._is_stored_duplicate("duplicada de #")
    assert not repo_mod._is_stored_duplicate(None)


def test_marker_matching_uses_the_note_separator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repo_mod, "NOTE_SEPARATOR", " | ")
    assert repo_mod._is_stored_duplicate(f"nota a mano | {duplicate_note(5)}")
    assert not repo_mod._is_stored_duplicate(f"nota a mano · {duplicate_note(5)}")


def test_bridge_writes_the_marker_the_repository_reads(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = repo.upsert_offer(
        OfferInput(
            job_id="1",
            title="AI Engineer",
            company="Acme",
            url="https://www.linkedin.com/jobs/view/1/",
            text="t",
            source="link",
        )
    )
    jobs: list[dict[str, Any]] = [
        {
            "url": "https://www.infojobs.net/barcelona/ai/of-0f8d7e6c5b4a39281706f5e4d3c2b1a0",
            "title": "AI Engineer",
            "company": "Acme",
            "text": "t",
        }
    ]
    bridge._process_payload(
        "b", jobs, repo=repo, sender=lambda *a: None, threshold=65, duplicates=[]
    )
    row = repo.find_offer_by_url(
        bridge.canonical_url(
            "infojobs",
            "0f8d7e6c5b4a39281706f5e4d3c2b1a0",
            original=jobs[0]["url"],
        )
    )
    assert row is not None
    assert duplicate_note(first) in (row.notas or "").split(repo_mod.NOTE_SEPARATOR)
    assert repo_mod._is_stored_duplicate(row.notas)
