"""LinkedIn "Easy Apply" / "Solicitud sencilla" (pending-maintenance P4, S3).

The extension flags a card whose text carries the badge (``easyApply``), the
bridge accepts the optional boolean ``easy_apply`` and stores it (schema v24,
``offers.easy_apply``: NULL = unknown), a later ingest without the flag never
clears it, and a LinkedIn card shows one short line when it is set.
"""

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from cva import bridge, notifier
from cva.service import JobService
from cva.tracker import OfferInput, OfferRepository
from cva.tracker.repo import SCHEMA_VERSION
from tests.test_service import (
    NOW,
    FakeFetcher,
    FakeLlmClient,
    _extraction,
    make_service,
)

LI_URL = "https://www.linkedin.com/jobs/view/4200000001/"
IJ_URL = (
    "https://www.infojobs.net/barcelona/ai-engineer/of-0f8d7e6c5b4a39281706f5e4d3c2b1a0"
)


def _job(**extra: Any) -> dict[str, Any]:
    return {"title": "AI Engineer", "company": "Acme", "url": LI_URL, **extra}


# --- bridge validation ----------------------------------------------------------


@pytest.mark.parametrize("value", [True, False, None])
def test_bridge_accepts_a_boolean_or_null_easy_apply(value: bool | None) -> None:
    _, jobs = bridge._validated({"beacon": "b", "jobs": [_job(easy_apply=value)]})
    assert jobs[0]["easy_apply"] is value


def test_bridge_accepts_a_job_without_easy_apply() -> None:
    _, jobs = bridge._validated({"beacon": "b", "jobs": [_job()]})
    assert "easy_apply" not in jobs[0]


@pytest.mark.parametrize("value", ["true", 1, 0, [], {}])
def test_bridge_rejects_a_non_boolean_easy_apply(value: Any) -> None:
    with pytest.raises(bridge._BadRequest, match="'easy_apply' must be a boolean"):
        bridge._validated({"beacon": "b", "jobs": [_job(easy_apply=value)]})


# --- storage --------------------------------------------------------------------


def _repo(tmp_path: Path) -> OfferRepository:
    return OfferRepository(sqlite3.connect(tmp_path / "app.db"))


def _ingest(repo: OfferRepository, job: dict[str, Any]) -> int:
    _, jobs = bridge._validated({"beacon": "b", "jobs": [job]})
    bridge._process_payload("b", jobs, repo=repo, sender=lambda *_: None, threshold=65)
    (offer_id,) = repo._conn.execute("SELECT max(id) FROM offers").fetchone()
    return int(offer_id)


def test_bridge_stores_the_flag_and_unknown_stays_null(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    flagged = _ingest(repo, _job(easy_apply=True))
    plain = _ingest(repo, _job(url="https://www.linkedin.com/jobs/view/4200000002/"))
    assert repo.get_offer(flagged).easy_apply is True
    assert repo.get_offer(plain).easy_apply is None


def test_a_refresh_without_the_flag_keeps_it(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = {"job_id": "1", "url": LI_URL, "text": "t", "source": "link"}
    offer_id = repo.upsert_offer(OfferInput(**base, easy_apply=True))
    assert repo.upsert_offer(OfferInput(**base)) == offer_id
    assert repo.get_offer(offer_id).easy_apply is True
    repo.upsert_offer(OfferInput(**base, easy_apply=False))
    assert repo.get_offer(offer_id).easy_apply is False


def _columns(conn: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in conn.execute("PRAGMA table_info(offers)")]


def _offers_sql(conn: sqlite3.Connection) -> str:
    (sql,) = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='offers'"
    ).fetchone()
    return str(sql)


def test_v23_to_v24_adds_a_null_column_and_keeps_every_row(tmp_path: Path) -> None:
    assert SCHEMA_VERSION == 24
    path = tmp_path / "app.db"
    repo = OfferRepository(sqlite3.connect(path))
    for n in range(3):
        repo.upsert_offer(
            OfferInput(
                job_id=str(n),
                title=f"T{n}",
                company="Acme",
                url=f"https://www.linkedin.com/jobs/view/{n}/",
                text="t",
                source="link",
                location="Madrid",
            )
        )
    repo._conn.close()
    old = sqlite3.connect(path)
    old.execute("ALTER TABLE offers DROP COLUMN easy_apply")
    old.execute("PRAGMA user_version = 23")
    old.commit()
    before_rows = [tuple(r) for r in old.execute("SELECT * FROM offers ORDER BY id")]
    before_cols = _columns(old)
    old.close()

    migrated = sqlite3.connect(path)
    OfferRepository(migrated)
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == 24
    cols = _columns(migrated)
    assert cols[:-1] == before_cols
    assert cols[-1][1:3] == ("easy_apply", "INTEGER")
    rows = [tuple(r) for r in migrated.execute("SELECT * FROM offers ORDER BY id")]
    assert [r[:-1] for r in rows] == before_rows
    assert [r[-1] for r in rows] == [None, None, None]

    fresh = sqlite3.connect(tmp_path / "fresh.db")
    OfferRepository(fresh)
    assert _columns(fresh) == cols
    assert _offers_sql(fresh) == _offers_sql(migrated)

    # Re-opening a v24 database is a no-op.
    migrated.close()
    again = sqlite3.connect(path)
    OfferRepository(again)
    assert again.execute("PRAGMA user_version").fetchone()[0] == 24
    assert _columns(again) == cols


# --- card line ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lang", "line"), [("en", "⚡ Easy Apply"), ("es", "⚡ Solicitud sencilla")]
)
def test_easy_apply_line_only_for_linkedin_and_true(lang: Any, line: str) -> None:
    assert notifier.easy_apply_line("linkedin", True, lang=lang) == line
    assert notifier.easy_apply_line("linkedin", None, lang=lang) == ""
    assert notifier.easy_apply_line("linkedin", False, lang=lang) == ""
    assert notifier.easy_apply_line("infojobs", True, lang=lang) == ""
    assert notifier.easy_apply_line(None, True, lang=lang) == ""


def _service(tmp_path: Path) -> tuple[JobService, OfferRepository]:
    return make_service(
        tmp_path,
        FakeLlmClient(_extraction()),
        FakeFetcher(None),  # type: ignore[arg-type]
        now_fn=lambda: NOW,
    )


@pytest.mark.parametrize(
    ("lang", "line"), [("en", "⚡ Easy Apply"), ("es", "⚡ Solicitud sencilla")]
)
def test_card_extras_show_the_line_for_a_linkedin_easy_apply_offer(
    tmp_path: Path, lang: Any, line: str
) -> None:
    service, repo = _service(tmp_path)
    service._lang = lang
    base = {"text": "t", "source": "link"}
    flagged = repo.upsert_offer(OfferInput(url=LI_URL, easy_apply=True, **base))
    plain = repo.upsert_offer(
        OfferInput(url="https://www.linkedin.com/jobs/view/7/", **base)
    )
    other = repo.upsert_offer(OfferInput(url=IJ_URL, easy_apply=True, **base))
    assert service.card_extras(flagged) == line
    assert service.card_extras(plain) == ""
    assert service.card_extras(other) == ""
