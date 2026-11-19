"""Two processes opening the same pre-v24 database at once must both succeed:
the column check runs inside the write transaction, so the loser of the race
sees the column and only bumps the version (P4 verifier advisory)."""

import multiprocessing
import shutil
import sqlite3
from pathlib import Path

from cva.tracker import OfferInput, OfferRepository


def _v23_database(path: Path) -> None:
    repo = OfferRepository(sqlite3.connect(path))
    repo.upsert_offer(
        OfferInput(
            job_id="1",
            title="T",
            company="Acme",
            url="https://www.linkedin.com/jobs/view/1/",
            text="t",
            source="link",
        )
    )
    repo._conn.close()
    old = sqlite3.connect(path)
    old.execute("ALTER TABLE offers DROP COLUMN easy_apply")
    old.execute("PRAGMA user_version = 23")
    old.commit()
    old.close()


def _open(path: str, start: object) -> None:
    start.wait()  # type: ignore[attr-defined]
    conn = sqlite3.connect(path)
    OfferRepository(conn)
    conn.close()


def test_concurrent_openers_of_an_old_database_all_succeed(tmp_path: Path) -> None:
    template = tmp_path / "v23.db"
    _v23_database(template)
    ctx = multiprocessing.get_context("spawn")
    failures: list[str] = []
    for n in range(12):
        path = tmp_path / f"race{n}.db"
        shutil.copy(template, path)
        start = ctx.Event()
        procs = [ctx.Process(target=_open, args=(str(path), start)) for _ in range(3)]
        for proc in procs:
            proc.start()
        start.set()
        for proc in procs:
            proc.join(30)
            if proc.exitcode != 0:
                failures.append(f"round {n}: exit {proc.exitcode}")
        conn = sqlite3.connect(path)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 24
        cols = [r[1] for r in conn.execute("PRAGMA table_info(offers)")]
        assert cols.count("easy_apply") == 1
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        conn.close()
    assert failures == []
