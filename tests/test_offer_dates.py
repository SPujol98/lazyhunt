"""T2: v10 migration, date persistence, card lines, deadline sweep."""

import asyncio
import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cva import bridge
from cva.ingest.guest import FetchedOffer
from cva.notifier import (
    breakdown_message,
    date_card_lines,
    deadline_alert_message,
)
from cva.scheduler import heartbeat
from cva.service import _offer_age_days, _offer_input
from cva.tracker import OfferInput, OfferRepository
from cva.tracker.repo import SCHEMA_VERSION
from tests.test_scheduler import FakeBot, RecordingFetcher, make_service

TODAY = date(2026, 10, 18)
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
NEW_COLUMNS = ["fecha_alta", "fecha_publicacion", "fecha_limite"]


def _repo(tmp_path: Path, clock: str = "2026-10-18") -> OfferRepository:
    return OfferRepository(sqlite3.connect(tmp_path / "t.db"), clock=lambda: clock)


# --- migration -----------------------------------------------------------------


def test_fresh_db_is_v10_with_nullable_date_columns(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert SCHEMA_VERSION == 24
    cols = [r[1] for r in repo._conn.execute("PRAGMA table_info(offers)")]
    assert all(c in cols for c in NEW_COLUMNS)


def test_v9_db_migrates_leaving_old_rows_null(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    repo = OfferRepository(sqlite3.connect(path))
    repo.upsert_offer(OfferInput(title="t", url="https://x/1", text="x", source="link"))
    for col in NEW_COLUMNS:
        repo._conn.execute(f"ALTER TABLE offers DROP COLUMN {col}")
    repo._conn.execute("DROP TABLE app_state")
    repo._conn.execute("PRAGMA user_version = 9")
    repo._conn.commit()
    repo._conn.close()
    migrated = OfferRepository(sqlite3.connect(path))
    assert migrated._conn.execute("PRAGMA user_version").fetchone()[0] == 24
    row = migrated.get_offer(1)
    assert (row.fecha_alta, row.fecha_publicacion, row.fecha_limite) == (None,) * 3


def test_migration_v10_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cva.tracker.repo as repo_mod

    path = tmp_path / "old.db"
    repo = OfferRepository(sqlite3.connect(path))
    for col in NEW_COLUMNS:
        repo._conn.execute(f"ALTER TABLE offers DROP COLUMN {col}")
    repo._conn.execute("DROP TABLE app_state")
    repo._conn.execute("PRAGMA user_version = 9")
    repo._conn.commit()
    repo._conn.close()
    broken = repo_mod._MIGRATION_V10.replace(
        "PRAGMA user_version = 10;",
        "SELECT * FROM no_such_table;\nPRAGMA user_version = 10;",
    )
    assert broken != repo_mod._MIGRATION_V10
    monkeypatch.setattr(repo_mod, "_MIGRATION_V10", broken)
    with pytest.raises(sqlite3.OperationalError):
        OfferRepository(sqlite3.connect(path))
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 9
    cols = [r[1] for r in conn.execute("PRAGMA table_info(offers)")]
    assert "fecha_alta" not in cols


# --- persistence ---------------------------------------------------------------


def test_insert_sets_fecha_alta_and_dates_refresh_keeps_alta(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "2026-10-01")
    oid = repo.upsert_offer(
        OfferInput(
            title="t",
            url="https://x/1",
            text="x",
            source="link",
            fecha_publicacion="2026-09-20",
            fecha_limite="2026-10-25",
        )
    )
    row = repo.get_offer(oid)
    assert (row.fecha_alta, row.fecha_publicacion, row.fecha_limite) == (
        "2026-10-01",
        "2026-09-20",
        "2026-10-25",
    )
    repo._clock = lambda: "2026-10-10"
    repo.upsert_offer(
        OfferInput(title="t", url="https://x/1", text="x2", source="link")
    )
    again = repo.get_offer(oid)
    assert again.fecha_alta == "2026-10-01"  # never moved
    assert again.fecha_limite == "2026-10-25"  # not wiped by a date-less refresh


def test_app_state_roundtrip(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert repo.get_state("k") is None
    repo.set_state("k", "v1")
    repo.set_state("k", "v2")
    assert repo.get_state("k") == "v2"


def test_list_unsent_deadlines_window_and_estado(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    def add(n: int, limite: str | None, estado: str = "analizada") -> int:
        oid = repo.upsert_offer(
            OfferInput(
                title=f"t{n}",
                url=f"https://x/{n}",
                text="x",
                source="link",
                fecha_limite=limite,
            )
        )
        repo.set_estado(oid, estado)
        return oid

    today_id = add(1, "2026-10-18")
    soon_id = add(2, "2026-10-21")
    add(3, "2026-10-22")  # beyond the window
    add(4, "2026-10-17")  # already past
    add(5, "2026-10-19", "enviada")  # already sent
    add(6, None)
    rows = repo.list_unsent_with_deadline("2026-10-18", "2026-10-21")
    assert [r.id for r in rows] == [today_id, soon_id]


# --- ingestion wiring ----------------------------------------------------------


def test_offer_input_extracts_dates_from_text_and_jsonld() -> None:
    fetched = FetchedOffer(
        job_id="1",
        title="t",
        company="c",
        url="https://x/1",
        text="Fecha límite: 30/10/2026. Publicada hace 3 días",
        source="link",
        date_posted="2026-10-02T08:00:00Z",
    )
    offer = _offer_input(fetched, today=TODAY)
    assert offer.fecha_publicacion == "2026-10-02"  # JSON-LD wins over text
    assert offer.fecha_limite == "2026-10-30"


def test_bridge_inline_offer_stores_dates(tmp_path: Path) -> None:
    repo = OfferRepository(sqlite3.connect(tmp_path / "b.db"))
    jobs: list[dict[str, Any]] = [
        {
            "url": "https://www.linkedin.com/jobs/view/4200000001",
            "title": "t",
            "company": "c",
            "text": "Ingeniero de IA. Fecha límite: 30/11/2026.",
        }
    ]
    bridge._process_payload(
        "b", jobs, repo=repo, sender=lambda *_a, **_k: None, threshold=65
    )
    row = repo.find_offer_by_url("https://linkedin.com/jobs/view/4200000001")
    assert row is not None
    assert row.fecha_limite == "2026-11-30"
    assert row.fecha_alta is not None


# --- card lines ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("limite", "publicacion", "expected", "absent"),
    [
        ("2026-10-18", None, "⏰ Cierra hoy", None),
        ("2026-10-21", None, "⏰ Cierra en 3 días (21/10)", None),
        ("2026-10-19", None, "⏰ Cierra en 1 día (19/10)", None),
        ("2026-10-25", None, "⏰ Cierra en 7 días (25/10)", None),
        ("2026-10-26", None, None, "Cierra"),
        ("2026-10-17", None, None, "Cierra"),  # past: no alert line
        (None, "2026-09-18", None, "Publicada"),  # exactly 30 days: not stale
        (None, "2026-09-17", "🕰️ Publicada hace 31 días (puede estar cerrada)", None),
        (None, "2026-10-10", None, "Publicada"),
    ],
)
def test_date_card_lines(
    limite: str | None,
    publicacion: str | None,
    expected: str | None,
    absent: str | None,
) -> None:
    line = date_card_lines(publicacion, limite, TODAY)
    if expected is not None:
        assert expected in line
    if absent is not None:
        assert absent not in line
    if expected is None and absent is not None:
        assert line == ""


def test_date_lines_ride_the_card_extras() -> None:
    from cva.match import MatchResult

    result = MatchResult(score=70, threshold=65, breakdown=[])
    text = breakdown_message(result, 1, salary_line="💶 x\n⏰ Cierra hoy")
    assert text.endswith("⏰ Cierra hoy")


# --- service: card extras and age ------------------------------------------------


def test_offer_age_prefers_fecha_alta() -> None:
    assert _offer_age_days("2026-10-17", TODAY, fecha_alta="2026-09-18") == 30
    assert _offer_age_days("2026-10-17", TODAY) == 1
    assert _offer_age_days(None, TODAY, fecha_alta=None) is None
    assert _offer_age_days("2026-10-17", TODAY, fecha_alta="garbage") == 1


def test_service_card_extras_include_date_lines(tmp_path: Path) -> None:
    service = make_service(
        sqlite3.connect(tmp_path / "s.db"), RecordingFetcher(""), lambda: NOW
    )
    oid = service._repo.upsert_offer(
        OfferInput(
            title="t",
            url="https://x/1",
            text="x",
            source="link",
            fecha_limite="2026-10-20",
        )
    )
    assert "⏰ Cierra en 2 días (20/10)" in service.card_extras(oid)


# --- heartbeat sweep ---------------------------------------------------------------


def _ctx(service: Any, bot: FakeBot) -> SimpleNamespace:
    return SimpleNamespace(
        application=SimpleNamespace(bot_data={"service": service, "owner_id": 5}),
        bot=bot,
    )


def _service_with_deadline(
    tmp_path: Path, limite: str, clock: Callable[[], datetime] | None = None
) -> Any:
    service = make_service(
        sqlite3.connect(tmp_path / "h.db"), RecordingFetcher(""), clock or (lambda: NOW)
    )
    oid = service._repo.upsert_offer(
        OfferInput(
            title="Ingeniero IA",
            company="Acme",
            url="https://x/1",
            text="x",
            source="link",
            fecha_limite=limite,
        )
    )
    service._repo.set_estado(oid, "analizada")
    return service


def test_heartbeat_alerts_each_offer_once(tmp_path: Path) -> None:
    service = _service_with_deadline(tmp_path, "2026-10-20")
    bot = FakeBot()
    asyncio.run(heartbeat(_ctx(service, bot)))
    asyncio.run(heartbeat(_ctx(service, bot)))
    assert len(bot.sent) == 1
    text = bot.sent[0][1]
    assert "Acme" in text and "Ingeniero IA" in text and "2 días" in text


class FlakyBot(FakeBot):
    """Fails the first ``failures`` sends, then behaves like FakeBot."""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures

    async def send_message(
        self, chat_id: int, text: str, reply_markup: object = None
    ) -> None:
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("telegram down")
        await super().send_message(chat_id, text, reply_markup)


class _Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


def test_deadline_alert_retries_after_send_failure(tmp_path: Path) -> None:
    clock = _Clock()
    service = _service_with_deadline(tmp_path, "2026-10-20", clock)
    bot = FlakyBot(failures=1)
    asyncio.run(heartbeat(_ctx(service, bot)))
    assert bot.sent == []
    clock.advance(minutes=10)
    asyncio.run(heartbeat(_ctx(service, bot)))
    assert len(bot.sent) == 1
    asyncio.run(heartbeat(_ctx(service, bot)))
    assert len(bot.sent) == 1


def test_deadline_alert_backs_off_exponentially_and_resets(tmp_path: Path) -> None:
    clock = _Clock()
    service = _service_with_deadline(tmp_path, "2026-10-20", clock)
    bot = FlakyBot(failures=99)
    attempts: list[int] = []

    def tick() -> None:
        before = bot.failures
        asyncio.run(heartbeat(_ctx(service, bot)))
        attempts.append(before - bot.failures)

    tick()  # fail 1 -> next in 10 min
    clock.advance(minutes=5)
    tick()  # still backing off: no send attempt
    clock.advance(minutes=5)
    tick()  # fail 2 -> next in 1 h
    clock.advance(minutes=30)
    tick()  # backing off
    clock.advance(minutes=30)
    tick()  # fail 3 -> next in 6 h
    clock.advance(hours=5)
    tick()  # backing off
    clock.advance(hours=1)
    tick()  # fail 4 -> capped at 6 h
    clock.advance(hours=5, minutes=59)
    tick()  # still capped
    assert attempts == [1, 0, 1, 0, 1, 0, 1, 0]
    clock.advance(minutes=1)
    bot.failures = 0
    tick()
    assert len(bot.sent) == 1  # success after the cap
    # reset: a new offer is alerted on the very next tick (no stale backoff)
    late = service._repo.upsert_offer(
        OfferInput(
            title="DE",
            company="Beta",
            url="https://x/2",
            text="y",
            source="link",
            fecha_limite="2026-10-19",
        )
    )
    service._repo.set_estado(late, "analizada")
    asyncio.run(heartbeat(_ctx(service, bot)))
    assert len(bot.sent) == 2


def test_deadline_mark_failure_after_send_does_not_resend(tmp_path: Path) -> None:
    service = _service_with_deadline(tmp_path, "2026-10-20")
    bot = FakeBot()
    real = service._repo.set_state
    broken = {"on": True}

    def flaky_set_state(key: str, value: str) -> None:
        if broken["on"] and key == "deadline_alerted_ids":
            raise sqlite3.OperationalError("database is locked")
        real(key, value)

    service._repo.set_state = flaky_set_state  # type: ignore[method-assign]
    asyncio.run(heartbeat(_ctx(service, bot)))
    asyncio.run(heartbeat(_ctx(service, bot)))
    assert len(bot.sent) == 1  # sent once, not every tick
    broken["on"] = False
    asyncio.run(heartbeat(_ctx(service, bot)))  # flush persists the mark
    assert len(bot.sent) == 1
    assert service._deadline_alerted() == {1}
    assert service._repo.get_state("deadline_alerted_ids") == "[1]"


def test_deadline_alert_lists_only_what_fits_and_rest_follows(tmp_path: Path) -> None:
    service = _service_with_deadline(tmp_path, "2026-10-20")
    for n in range(2, 40):
        oid = service._repo.upsert_offer(
            OfferInput(
                title=f"Ingeniero de datos {n} " + "x" * 120,
                company=f"Empresa {n}",
                url=f"https://example.com/jobs/{n}/" + "y" * 100,
                text="x",
                source="link",
                fecha_limite="2026-10-20",
            )
        )
        service._repo.set_estado(oid, "analizada")
    bot = FakeBot()
    for _ in range(6):
        asyncio.run(heartbeat(_ctx(service, bot)))
    assert len(bot.sent) >= 2
    assert all(len(text) <= 4096 for _c, text, _k in bot.sent)
    listed = " ".join(text for _c, text, _k in bot.sent)
    for n in range(2, 40):  # nothing marked without being listed
        assert f"· Empresa {n} —" in listed
    assert service.deadline_alert() is None


def test_deadline_alert_covers_late_arrival_without_repeating(tmp_path: Path) -> None:
    service = _service_with_deadline(tmp_path, "2026-10-20")
    bot = FakeBot()
    asyncio.run(heartbeat(_ctx(service, bot)))
    late = service._repo.upsert_offer(
        OfferInput(
            title="Data Engineer",
            company="Beta",
            url="https://x/2",
            text="y",
            source="link",
            fecha_limite="2026-10-19",
        )
    )
    service._repo.set_estado(late, "analizada")
    asyncio.run(heartbeat(_ctx(service, bot)))
    assert len(bot.sent) == 2
    second = bot.sent[1][1]
    assert "Beta" in second and "Acme" not in second
    asyncio.run(heartbeat(_ctx(service, bot)))
    assert len(bot.sent) == 2


def test_heartbeat_no_alert_without_urgent_offers(tmp_path: Path) -> None:
    service = _service_with_deadline(tmp_path, "2026-10-30")
    bot = FakeBot()
    asyncio.run(heartbeat(_ctx(service, bot)))
    assert bot.sent == []


def test_deadline_alert_message_lists_each_offer() -> None:
    from cva.tracker import OfferRow

    rows = [
        OfferRow(
            id=1,
            estado="analizada",
            source_data="x",
            empresa="A",
            posicion="P1",
            fecha_limite="2026-10-18",
            url_oferta="https://x/1",
        ),
        OfferRow(
            id=2,
            estado="preparada",
            source_data="x",
            empresa="B",
            posicion="P2",
            fecha_limite="2026-10-20",
            url_oferta="https://x/2",
        ),
    ]
    text = deadline_alert_message(rows, TODAY)
    assert "hoy" in text and "2 días" in text and "https://x/2" in text
