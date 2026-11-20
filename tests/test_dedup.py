"""T4: cross-source duplicate detection (key helper, v11 migration, ingestion)."""

import asyncio
import hashlib
import sqlite3
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from cva import bridge
from cva.ingest.dedup import dedup_key
from cva.notifier import (
    DuplicateOffer,
    round_summary_message,
)
from cva.tracker import OfferInput, OfferRepository
from cva.tracker.repo import SCHEMA_VERSION

# --- key helper -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("company", "title", "expected"),
    [
        ("Acme", "AI Engineer", "acme_ai-engineer"),
        ("Acme S.L.", "AI Engineer", "acme_ai-engineer"),
        ("ACME, S.L.U.", "AI Engineer", "acme_ai-engineer"),
        ("Acme SA", "AI Engineer", "acme_ai-engineer"),
        ("Acme Inc.", "AI Engineer", "acme_ai-engineer"),
        ("Acme GmbH", "AI Engineer", "acme_ai-engineer"),
        ("Acme Ltd", "AI Engineer", "acme_ai-engineer"),
        ("Acme LLC", "AI Engineer", "acme_ai-engineer"),
        ("Acme B.V.", "AI Engineer", "acme_ai-engineer"),
        ("Acme S.A.S.", "AI Engineer", "acme_ai-engineer"),
        ("Acme S.p.A.", "AI Engineer", "acme_ai-engineer"),
        ("Acme Group S.L.", "AI Engineer", "acme_ai-engineer"),
        ("Grupo Acme", "AI Engineer", "grupo-acme_ai-engineer"),  # only as suffix
        ("Group", "AI Engineer", "group_ai-engineer"),  # never empties the company
        ("Ñandú Café S.L.", "Ingeniero de Datos", "nandu-cafe_ingeniero-de-datos"),
        ("Acme", "Ingeniero/a de IA (m/f/d)", "acme_ingeniero-a-de-ia"),
        ("Acme", "AI Engineer (h/m)", "acme_ai-engineer"),
        ("Acme", "AI Engineer (f/h)", "acme_ai-engineer"),
        ("Acme", "AI Engineer m/f", "acme_ai-engineer"),
        ("Acme", "AI Engineer (all genders)", "acme_ai-engineer"),
        ("Acme", "AI Engineer (remoto)", "acme_ai-engineer"),
        ("Acme", "AI Engineer - Remote", "acme_ai-engineer"),
        ("Acme", "AI Engineer - Híbrido", "acme_ai-engineer"),
        ("Acme", "AI Engineer (m/f/d) - Remote", "acme_ai-engineer"),
        ("Acme", "Remote Sensing Engineer", "acme_remote-sensing-engineer"),
        ("Acme", "Junior AI Engineer", "acme_junior-ai-engineer"),
        ("Acme", "Senior AI Engineer (m/f/d)", "acme_senior-ai-engineer"),
        ("Acme", "Lead AI Engineer", "acme_lead-ai-engineer"),
    ],
)
def test_dedup_key_normalization(company: str, title: str, expected: str) -> None:
    assert dedup_key(company, title) == expected


@pytest.mark.parametrize(
    ("company", "title"),
    [(None, "AI Engineer"), ("", "AI Engineer"), ("  ", "AI Engineer"), ("S.L.", "X")],
)
def test_dedup_key_none_without_company(company: str | None, title: str) -> None:
    assert dedup_key(company, title) is None


@pytest.mark.parametrize("title", [None, "", "(m/f/d)"])
def test_dedup_key_none_without_title(title: str | None) -> None:
    assert dedup_key("Acme", title) is None


def test_dedup_key_truncates_with_stable_hash_suffix() -> None:
    title_a = "Engineer " + "alpha " * 40 + "one"
    title_b = "Engineer " + "alpha " * 40 + "two"
    a, b = dedup_key("Acme", title_a), dedup_key("Acme", title_b)
    assert a is not None and b is not None
    assert len(a) <= 100 and len(b) <= 100 and a != b
    assert a == dedup_key("Acme", title_a)
    full = "acme_" + "-".join(("Engineer " + "alpha " * 40 + "one").lower().split())
    assert a.endswith("-" + hashlib.sha1(full.encode()).hexdigest()[:8])


def test_dedup_key_short_keys_have_no_hash() -> None:
    key = dedup_key("Acme", "AI Engineer")
    assert key == "acme_ai-engineer"


# --- migration v11 -------------------------------------------------------------------


def _repo(
    tmp_path: Path, clock: str = "2026-10-18", name: str = "d.db"
) -> OfferRepository:
    return OfferRepository(sqlite3.connect(tmp_path / name), clock=lambda: clock)


def test_fresh_db_is_v11_with_dedup_column_and_index(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert SCHEMA_VERSION == 24
    cols = [r[1] for r in repo._conn.execute("PRAGMA table_info(offers)")]
    assert "dedup_key" in cols
    idx = [r[1] for r in repo._conn.execute("PRAGMA index_list(offers)")]
    assert "idx_offers_dedup_key" in idx


def test_v10_db_migrates_and_backfills_existing_rows(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    repo = OfferRepository(sqlite3.connect(path))
    for n, (company, title) in enumerate(
        [("Acme S.L.", "AI Engineer (m/f/d)"), (None, "Backend Dev"), ("Beta", "Dev")]
    ):
        repo.upsert_offer(
            OfferInput(
                title=title,
                company=company,
                url=f"https://x/{n}",
                text="x",
                source="link",
            )
        )
    repo._conn.execute("DROP INDEX idx_offers_dedup_key")
    repo._conn.execute("ALTER TABLE offers DROP COLUMN dedup_key")
    repo._conn.execute("PRAGMA user_version = 10")
    repo._conn.commit()
    repo._conn.close()
    migrated = OfferRepository(sqlite3.connect(path))
    assert migrated._conn.execute("PRAGMA user_version").fetchone()[0] == 24
    keys = {r.id: r.dedup_key for r in migrated.all_offers()}
    assert keys == {1: "acme_ai-engineer", 2: None, 3: "beta_dev"}


def test_migration_v11_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "old.db"
    repo = OfferRepository(sqlite3.connect(path))
    repo.upsert_offer(
        OfferInput(
            title="Dev", company="Beta", url="https://x/1", text="x", source="link"
        )
    )
    repo._conn.execute("DROP INDEX idx_offers_dedup_key")
    repo._conn.execute("ALTER TABLE offers DROP COLUMN dedup_key")
    repo._conn.execute("PRAGMA user_version = 10")
    repo._conn.commit()
    repo._conn.close()

    def boom(company: str | None, title: str | None) -> str | None:
        raise RuntimeError("backfill failed")

    monkeypatch.setattr("cva.tracker.repo.dedup_key", boom)
    conn = sqlite3.connect(path)
    with pytest.raises(RuntimeError):
        OfferRepository(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 10
    cols = [r[1] for r in conn.execute("PRAGMA table_info(offers)")]
    assert "dedup_key" not in cols


def test_insert_stores_key_and_refresh_recomputes_it(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oid = repo.upsert_offer(
        OfferInput(title=None, company=None, url="https://x/1", text="x", source="link")
    )
    assert repo.get_offer(oid).dedup_key is None
    repo.upsert_offer(
        OfferInput(
            title="AI Engineer",
            company="Acme",
            url="https://x/1",
            text="y",
            source="link",
        )
    )
    assert repo.get_offer(oid).dedup_key == "acme_ai-engineer"


# --- repository lookup ---------------------------------------------------------------


def _add(repo: OfferRepository, url: str, company: str, title: str) -> int:
    return repo.upsert_offer(
        OfferInput(title=title, company=company, url=url, text="x", source="link")
    )


def test_find_duplicate_other_url_within_window(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = _add(repo, "https://linkedin/1", "Acme", "AI Engineer")
    dup = repo.find_duplicate("acme_ai-engineer", "https://infojobs/9")
    assert dup is not None and dup.id == first


def test_find_duplicate_ignores_same_url_old_missing_and_other_seniority(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    first = _add(repo, "https://linkedin/1", "Acme", "AI Engineer")
    assert repo.find_duplicate("acme_ai-engineer", "https://linkedin/1") is None
    assert repo.find_duplicate("acme_senior-ai-engineer", "https://x/2") is None
    assert repo.find_duplicate(None, "https://x/2") is None
    repo._conn.execute(
        "UPDATE offers SET fecha_alta = '2026-09-10', fecha_update = '2026-10-17' "
        "WHERE id = ?",
        (first,),
    )
    assert repo.find_duplicate("acme_ai-engineer", "https://x/2") is None  # 38 days
    repo._conn.execute(
        "UPDATE offers SET fecha_alta = NULL, fecha_update = '2026-10-01' WHERE id = ?",
        (first,),
    )
    assert (
        repo.find_duplicate("acme_ai-engineer", "https://x/2") is not None
    )  # fallback


def test_find_duplicate_of_sent_offer_still_counts(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = _add(repo, "https://linkedin/1", "Acme", "AI Engineer")
    repo.set_estado(first, "enviada")
    dup = repo.find_duplicate("acme_ai-engineer", "https://infojobs/9")
    assert dup is not None and dup.id == first


def test_find_duplicate_skips_rows_that_are_themselves_duplicates(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    first = _add(repo, "https://linkedin/1", "Acme", "AI Engineer")
    second = _add(repo, "https://infojobs/2", "Acme", "AI Engineer")
    repo.append_note(second, f"duplicada de #{first}")
    repo._conn.execute(
        "UPDATE offers SET fecha_alta = '2026-08-01' WHERE id = ?", (first,)
    )
    assert repo.find_duplicate("acme_ai-engineer", "https://x/3") is None


# --- bridge ingestion ----------------------------------------------------------------

LI = "https://www.linkedin.com/jobs/view/111"
IJ = (
    "https://www.infojobs.net/barcelona/ai-engineer/of-0f8d7e6c5b4a39281706f5e4d3c2b1a0"
)


def _process(
    repo: OfferRepository, jobs: list[dict[str, Any]]
) -> tuple[dict[str, int], list[Any], list[DuplicateOffer]]:
    auto: list[tuple[int, str, dict[str, Any]]] = []
    dupes: list[DuplicateOffer] = []
    result = bridge._process_payload(
        "b",
        jobs,
        repo=repo,
        sender=lambda *a: None,
        threshold=65,
        auto_jobs=auto,
        duplicates=dupes,
    )
    return result, auto, dupes


def _ij_job(**over: Any) -> dict[str, Any]:
    job = {
        "url": IJ,
        "title": "AI Engineer (m/f/d)",
        "company": "Acme S.L.",
        "text": "t",
    }
    job.update(over)
    return job


def test_cross_source_duplicate_is_stored_discarded_and_not_analyzed(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    first = _add(repo, LI, "Acme", "AI Engineer")
    result, auto, dupes = _process(repo, [_ij_job()])
    assert auto == []
    assert result == {"received": 1, "new": 0, "skipped": 1}
    row = repo.find_offer_by_url(
        bridge.canonical_url(
            bridge.source_from_url(IJ), bridge.job_id_from_url(IJ), original=IJ
        )  # type: ignore[arg-type]
    )
    assert row is not None
    assert row.estado == "descartada" and f"duplicada de #{first}" in (row.notas or "")
    assert dupes == [
        DuplicateOffer("AI Engineer (m/f/d)", "Acme S.L.", first, row.url_oferta)
    ]


def test_duplicate_of_sent_offer_is_still_a_duplicate(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = _add(repo, LI, "Acme", "AI Engineer")
    repo.set_estado(first, "enviada")
    _result, auto, dupes = _process(repo, [_ij_job()])
    assert auto == [] and len(dupes) == 1


def test_duplicate_reposted_url_is_skipped_on_exact_url(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _add(repo, LI, "Acme", "AI Engineer")
    _process(repo, [_ij_job()])
    result, auto, dupes = _process(repo, [_ij_job()])
    assert result == {"received": 1, "new": 0, "skipped": 1}
    assert auto == [] and dupes == []  # exact URL: today's silent behavior


def test_old_or_different_role_is_not_a_duplicate(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = _add(repo, LI, "Acme", "AI Engineer")
    repo._conn.execute(
        "UPDATE offers SET fecha_alta = '2026-08-01', fecha_update = '2026-08-01' "
        "WHERE id = ?",
        (first,),
    )
    _result, auto, dupes = _process(repo, [_ij_job()])
    assert len(auto) == 1 and dupes == []
    repo2 = _repo(tmp_path, name="other.db")
    _add(repo2, LI, "Acme", "AI Engineer")
    _result, auto, dupes = _process(repo2, [_ij_job(title="Senior AI Engineer")])
    assert len(auto) == 1 and dupes == []


def test_job_without_company_is_never_deduplicated(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _add(repo, LI, "Acme", "AI Engineer")
    _result, auto, dupes = _process(repo, [_ij_job(company=None)])
    assert len(auto) == 1 and dupes == []


# --- round summary -------------------------------------------------------------------


def test_round_summary_reports_duplicates_capped() -> None:
    dups = [DuplicateOffer(f"T{n}", f"C{n}", n, f"https://x/{n}") for n in range(8)]
    text = round_summary_message(
        beacon="b",
        analyzed=1,
        above=1,
        gray=0,
        below=0,
        pending=0,
        discarded=[],
        duplicates=len(dups),
        duplicate_offers=dups,
    )
    assert "♻️ 8 duplicadas" in text
    assert text.count("→ #") == 5 and "… y 3 más" in text
    assert "T0" in text and "#0" in text


def test_round_summary_without_duplicates_is_unchanged() -> None:
    text = round_summary_message(
        beacon="b", analyzed=1, above=1, gray=0, below=0, pending=0, discarded=[]
    )
    assert "duplicadas" not in text


def test_tally_carries_duplicates_into_summary_inputs() -> None:
    tally = bridge._RoundTally("b", 1)
    assert tally.duplicates == []


# --- T4b: exact-entry marker matching in find_duplicate -------------------------------


def test_find_duplicate_ignores_only_generated_marker_entries(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = _add(repo, "https://linkedin/1", "Acme", "AI Engineer")
    repo.append_note(first, "me escribieron: duplicada de #5 según el recruiter")
    dup = repo.find_duplicate("acme_ai-engineer", "https://x/2")
    assert dup is not None and dup.id == first  # a sentence is not the marker


def test_find_duplicate_skips_marker_entry_among_other_entries(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = _add(repo, "https://linkedin/1", "Acme", "AI Engineer")
    second = _add(repo, "https://infojobs/2", "Acme", "AI Engineer")
    repo.append_note(second, "nota a mano")
    repo.append_note(second, f"duplicada de #{first}")
    repo._conn.execute(
        "UPDATE offers SET fecha_alta = '2026-08-01' WHERE id = ?", (first,)
    )
    assert repo.find_duplicate("acme_ai-engineer", "https://x/3") is None


class _Server:
    def __init__(self, repo: OfferRepository, **kwargs: Any) -> None:
        self.sent: list[str] = []
        self.scheduled: list[tuple[int, Any]] = []

        def sender(message: str, keyboard: Any) -> None:
            self.sent.append(message)

        def schedule(offer_id: int, _job: str, _data: Any, tally: Any) -> None:
            self.scheduled.append((offer_id, tally))

        self.auto = kwargs.pop("auto", True)
        handler = bridge._make_handler(
            secret="s",
            repo=repo,
            sender=kwargs.pop("sender", sender),
            threshold=65,
            lock=threading.Lock(),
            schedule_auto=schedule if self.auto else None,
            **kwargs,
        )
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(
            target=self.httpd.serve_forever,
            kwargs={"poll_interval": bridge.SERVE_POLL_INTERVAL},
            daemon=True,
        ).start()

    def post(self, jobs: list[dict[str, Any]]) -> httpx.Response:
        port = self.httpd.server_address[1]
        return httpx.post(
            f"http://127.0.0.1:{port}/bridge",
            headers={"X-CVA-Bridge-Secret": "s"},
            json={"beacon": "b", "jobs": jobs},
        )

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def _li(
    job_id: str, title: str = "AI Engineer", company: str = "Acme"
) -> dict[str, Any]:
    return {
        "title": title,
        "company": company,
        "location": "BCN",
        "url": f"https://www.linkedin.com/jobs/view/{job_id}/",
    }


def _wait(cond: Any) -> None:
    deadline = time.monotonic() + 3
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.01)


@pytest.fixture()
def server(tmp_path: Path) -> Any:
    conn = sqlite3.connect(tmp_path / "s.db", check_same_thread=False)
    repo = OfferRepository(conn, clock=lambda: "2026-10-18")
    _add(repo, LI, "Acme", "AI Engineer")
    made: list[_Server] = []

    def make(**kwargs: Any) -> _Server:
        srv = _Server(repo, **kwargs)
        made.append(srv)
        return srv

    yield make
    for srv in made:
        srv.close()


def test_handler_copies_duplicates_onto_the_tally(server: Any) -> None:
    srv = server(round_summary=True)
    assert srv.post([_li("222"), _li("333", title="Other Role")]).status_code == 200
    _wait(lambda: srv.scheduled)
    assert len(srv.scheduled) == 1
    tally = srv.scheduled[0][1]
    assert [d.title for d in tally.duplicates] == ["AI Engineer"]
    assert srv.sent == []  # the round summary carries them: no separate notice


def test_bridge_passes_tally_duplicates_to_round_summary() -> None:
    tally = bridge._RoundTally("b", 1)
    tally.record("above", 80, "Other Role", "Acme", None)
    tally.duplicates = [DuplicateOffer("AI Engineer", "Acme", 7, "https://x/7")]
    sent: list[str] = []

    async def notify(
        message: str, keyboard: Any, *, parse_mode: str | None = None
    ) -> None:
        sent.append(message)

    stub = type("Stub", (), {"_notify_card": staticmethod(notify)})()
    asyncio.run(bridge._Bridge._settle_round(stub, tally))  # type: ignore[arg-type]
    assert "♻️ 1 duplicadas" in sent[0] and "→ #7" in sent[0]


def test_card_mode_duplicates_send_no_standalone_notice(server: Any) -> None:
    srv = server(auto=False)
    assert srv.post([_li("222"), _li("223", title="Dup Two")]).status_code == 200
    _wait(lambda: srv.sent)
    time.sleep(0.2)
    assert len(srv.sent) == 1 and "Dup Two" in srv.sent[0]  # card only, no ♻️ notice


def test_all_duplicates_round_in_auto_mode_is_silent(server: Any) -> None:
    srv = server(round_summary=True)
    assert srv.post([_li("222")]).status_code == 200
    time.sleep(0.2)
    assert srv.scheduled == []
    assert srv.sent == []


def test_summary_disabled_duplicates_are_silent(server: Any) -> None:
    srv = server(round_summary=False)
    assert srv.post([_li("222"), _li("333", title="Other Role")]).status_code == 200
    time.sleep(0.2)
    assert srv.sent == []


def test_notice_send_failure_never_breaks_the_response(server: Any) -> None:
    def boom(message: str, keyboard: Any) -> None:
        raise RuntimeError("telegram down")

    srv = server(auto=False, sender=boom)
    assert srv.post([_li("222")]).status_code == 200
