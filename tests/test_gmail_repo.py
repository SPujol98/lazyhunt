"""Migration v22 (``gmail_messages``) and its repository methods (M3)."""

import sqlite3
from pathlib import Path

import pytest

from cva.tracker import OfferInput, OfferRepository
from cva.tracker.repo import SCHEMA_VERSION


def _repo(tmp_path: Path) -> OfferRepository:
    return OfferRepository(sqlite3.connect(tmp_path / "t.db"))


def _offer(repo: OfferRepository) -> int:
    return repo.upsert_offer(
        OfferInput(
            job_id="1",
            title="AI Engineer",
            company="Acme",
            url="https://x/1",
            text="t",
            source="link",
        )
    )


def _add(repo: OfferRepository, offer_id: int, message_id: str = "m1") -> bool:
    return repo.add_gmail_proposal(
        message_id=message_id,
        offer_id=offer_id,
        sender="Ana <ana@acme.com>",
        subject="Entrevista",
        received_at="2026-11-03T10:00:00+00:00",
        proposed_estado="screening",
    )


def test_schema_version_is_23_and_table_has_no_body_column(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert SCHEMA_VERSION == 23
    columns = [r[1] for r in repo._conn.execute("PRAGMA table_info(gmail_messages)")]
    assert columns == [
        "message_id",
        "offer_id",
        "sender",
        "subject",
        "received_at",
        "proposed_estado",
        "decision",
        "created_at",
    ]


def test_v21_database_migrates_to_v22_keeping_data(tmp_path: Path) -> None:
    path = tmp_path / "v21.db"
    first = sqlite3.connect(path)
    repo = OfferRepository(first)
    offer_id = _offer(repo)
    first.execute("DROP TABLE gmail_messages")
    first.execute("PRAGMA user_version = 21")
    first.commit()
    first.close()

    migrated = sqlite3.connect(path)
    again = OfferRepository(migrated)
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == 23
    assert again.get_offer(offer_id).empresa == "Acme"
    assert _add(again, offer_id) is True


def test_migration_is_idempotent_when_the_table_already_exists(
    tmp_path: Path,
) -> None:
    path = tmp_path / "half.db"
    first = sqlite3.connect(path)
    OfferRepository(first)
    first.execute("PRAGMA user_version = 21")  # table exists, version not bumped
    first.commit()
    first.close()
    migrated = sqlite3.connect(path)
    OfferRepository(migrated)
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == 23


def test_add_is_idempotent_per_message(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    offer_id = _offer(repo)
    assert _add(repo, offer_id) is True
    assert _add(repo, offer_id) is False


def test_proposal_roundtrip_and_default_pending(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    offer_id = _offer(repo)
    _add(repo, offer_id)
    row = repo.get_gmail_proposal("m1")
    assert row is not None
    assert (row.offer_id, row.proposed_estado, row.decision) == (
        offer_id,
        "screening",
        "pending",
    )
    assert row.subject == "Entrevista" and row.sender.startswith("Ana")
    assert repo.get_gmail_proposal("nope") is None


def test_set_decision_only_when_pending(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _add(repo, _offer(repo))
    assert repo.set_gmail_decision("m1", "accepted", expect="pending") is True
    assert repo.set_gmail_decision("m1", "ignored", expect="pending") is False
    row = repo.get_gmail_proposal("m1")
    assert row is not None and row.decision == "accepted"
    assert repo.set_gmail_decision("missing", "ignored", expect="pending") is False


def test_decision_must_be_a_known_value(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _add(repo, _offer(repo))
    with pytest.raises(sqlite3.IntegrityError):
        repo.set_gmail_decision("m1", "bogus")


def test_delete_only_removes_pending_rows(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    offer_id = _offer(repo)
    _add(repo, offer_id, "m1")
    _add(repo, offer_id, "m2")
    repo.set_gmail_decision("m2", "accepted")
    repo.delete_pending_gmail_proposal("m1")
    repo.delete_pending_gmail_proposal("m2")
    assert repo.get_gmail_proposal("m1") is None
    assert repo.get_gmail_proposal("m2") is not None
