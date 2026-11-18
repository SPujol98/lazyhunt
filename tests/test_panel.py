"""/panel: pure HTML renderer, service method and command wiring (zero network)."""

import asyncio
import json
import os
import re
import stat
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

import bot
from cva.fetch import FetchResponse
from cva.report.panel import (
    PANEL_FILENAME,
    compute_stats,
    render_panel,
    write_panel_atomic,
)
from cva.tracker import OfferRow
from cva.tracker.xlsx import ESTADO_FILLS
from tests.test_auto_triage import (
    JSONLD_BODY,
    OFFER_TEXT,
    OFFER_URL,
    FakeFetcher,
    FakeLlmClient,
    _extraction,
    make_service,
)
from tests.test_bot import FakeContext, FakeMessage, FakeUpdate

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def row(n: int, estado: str = "analizada", **kw: object) -> OfferRow:
    return OfferRow(id=n, estado=estado, source_data="x", **kw)  # type: ignore[arg-type]


def sample_rows() -> list[OfferRow]:
    return [
        row(1, "pendiente_analisis", origen="linkedin"),
        row(2, "analizada", origen="linkedin", match=70, fecha_limite="2026-10-31"),
        row(3, "preparada", origen="infojobs", match=45, fecha_limite="2026-11-20"),
        row(
            4,
            "enviada",
            origen="linkedin",
            match=82,
            fecha_envio="2026-10-01",
            fecha_update="2026-10-10",
        ),
        row(
            5,
            "enviada",
            origen="infojobs",
            match=66,
            fecha_envio="2026-10-26",
            fecha_update="2026-10-26",
        ),
        row(6, "screening", origen="linkedin", fecha_envio="2026-10-20"),
        row(7, "entrevista_tecnica", origen="linkedin", fecha_envio="2026-10-21"),
        row(8, "oferta", origen="linkedin", fecha_envio="2026-10-22", match=90),
        row(9, "rechazada", fecha_envio="2026-10-02"),
        row(10, "descartada", fecha_limite="2026-10-30"),  # discarded: no alert
    ]


def test_aggregation_numbers() -> None:
    stats = compute_stats(sample_rows(), NOW)
    assert stats["analyzed"] == 9  # everything except pendiente_analisis
    assert stats["sent"] == 6  # 4,5,6,7,8,9
    assert stats["responded"] == 4  # 6,7,8,9
    assert stats["interviews"] == 2  # 7,8
    assert stats["offers"] == 1
    assert stats["response_rate"] == "67%"
    assert stats["by_estado"]["enviada"] == 2  # type: ignore[index]
    assert stats["by_origen"]["linkedin"] == 6  # type: ignore[index]
    assert stats["by_origen"]["—"] == 2  # type: ignore[index]
    assert stats["by_match"] == {"0-49": 1, "50-64": 0, "65-79": 2, "80-100": 2}
    weeks = stats["by_week"]
    assert len(weeks) == 8  # type: ignore[arg-type]
    assert sum(weeks.values()) == 6  # type: ignore[union-attr]
    assert weeks["2026-W44"] == 1  # type: ignore[index]  # Oct 26
    assert weeks["2026-W43"] == 3  # type: ignore[index]  # Oct 20-22
    assert weeks["2026-W40"] == 2  # type: ignore[index]  # Oct 1-2


def test_alerts_logic() -> None:
    stats = compute_stats(sample_rows(), NOW)
    deadline_ids = [r.id for _, r in stats["deadline_alerts"]]  # type: ignore[union-attr]
    assert deadline_ids == [2]  # #3 too far, #10 discarded
    silent_ids = [r.id for _, r in stats["silent_alerts"]]  # type: ignore[union-attr]
    assert silent_ids == [4]  # 18 days; #5 only 2 days
    html_out = render_panel(sample_rows(), NOW)
    assert "vence en 3 día(s)" in html_out
    assert "sin novedades desde hace 18 días" in html_out


def test_alerts_boundaries() -> None:
    rows = [
        row(1, "analizada", fecha_limite="2026-11-04"),  # +7: in
        row(2, "analizada", fecha_limite="2026-11-05"),  # +8: out
        row(3, "analizada", fecha_limite="2026-10-27"),  # past: out
        row(4, "enviada", fecha_update="2026-10-18"),  # 10 days: in
        row(5, "enviada", fecha_update="2026-10-19"),  # 9 days: out
        row(6, "enviada", fecha_envio="2026-10-01"),  # falls back to fecha_envio
    ]
    stats = compute_stats(rows, NOW)
    assert [r.id for _, r in stats["deadline_alerts"]] == [1]  # type: ignore[union-attr]
    assert sorted(r.id for _, r in stats["silent_alerts"]) == [4, 6]  # type: ignore[union-attr]


def test_empty_rows_render() -> None:
    out = render_panel([], NOW)
    assert "Panel de candidaturas" in out
    assert "Sin alertas" in out


def test_structure_offline_and_mobile_first() -> None:
    out = render_panel(sample_rows(), NOW)
    assert out.startswith("<!doctype html>")
    assert 'name="viewport"' in out
    assert "prefers-color-scheme:dark" in out
    assert "<svg viewBox" in out
    assert 'id="q"' in out and 'id="f-estado"' in out
    assert 'id="f-perfil"' in out and 'id="f-portal"' in out
    for estado, colour in ESTADO_FILLS.items():
        assert f".e-{estado}{{background:#{colour}}}" in out
    for forbidden in ("<link", "src=", "@import", "url(", "fetch(", "XMLHttpRequest"):
        assert forbidden not in out


def test_no_external_urls_except_offer_links() -> None:
    plain = render_panel(sample_rows(), NOW)
    assert "http" not in plain
    linked = render_panel([row(1, url_oferta="https://example.com/job/1")], NOW)
    assert linked.count("http") == 1
    assert "https://example.com/job/1" in linked


def test_offer_links_are_safe() -> None:
    out = render_panel(
        [
            row(1, url_oferta="javascript:alert(1)"),
            row(2, url_oferta="https://ok.example/x"),
        ],
        NOW,
    )
    assert "javascript:" not in out
    assert "noopener noreferrer" in out
    assert "innerHTML" not in out


def _blob(out: str) -> list[dict[str, object]]:
    match = re.search(
        r'<script id="panel-data" type="application/json">(.*?)</script>',
        out,
        re.DOTALL,
    )
    assert match
    return json.loads(match.group(1))  # type: ignore[no-any-return]


def test_xss_escaping() -> None:
    evil = '<script>alert("x")</script>'
    out = render_panel(
        [
            row(
                1,
                "enviada",
                empresa=evil,
                posicion="</script><img src=x onerror=alert(1)>",
                origen=evil,
                fecha_envio="2026-10-01",
                fecha_limite="2026-10-30",
            ),
            row(2, "analizada", empresa=evil, posicion=evil, fecha_limite="2026-10-30"),
        ],
        NOW,
    )
    assert "<script>alert" not in out
    assert "</script><img" not in out
    assert "<img" not in out
    assert out.count("<script") == 2  # data blob + app script only
    assert out.count("</script>") == 2
    data = _blob(out)  # round-trips: the JSON stays valid and faithful
    assert data[0]["empresa"] == evil
    assert "&lt;script&gt;" in out  # server-side parts (charts/alerts) escaped


def test_service_panel_writes_synced_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "sync" / "tracker.xlsx"
    service, _repo = make_service(
        tmp_path,
        FakeLlmClient(_extraction()),
        FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
        tracker_xlsx=target,
    )
    asyncio.run(service.ingest(None, OFFER_TEXT))
    html_out = service.panel_html()
    copy = target.parent / PANEL_FILENAME
    assert copy.read_text(encoding="utf-8") == html_out
    assert not list(target.parent.glob(".*.tmp"))


def test_service_panel_without_synced_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    service, _repo = make_service(
        tmp_path,
        FakeLlmClient(_extraction()),
        FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
    )
    assert "Panel de candidaturas" in service.panel_html()
    assert not list(tmp_path.rglob(PANEL_FILENAME))


class DocMessage(FakeMessage):
    """FakeMessage that also captures the document bytes."""

    def __init__(self) -> None:
        super().__init__()
        self.payloads: list[bytes] = []

    async def reply_document(  # type: ignore[override]
        self, document: object, filename: str | None = None
    ) -> None:
        self.payloads.append(document.read())  # type: ignore[attr-defined]
        await super().reply_document(document, filename=filename)


def test_panel_command_sends_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    service, _repo = make_service(
        tmp_path,
        FakeLlmClient(_extraction()),
        FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
    )
    message = DocMessage()
    asyncio.run(bot.panel_command(FakeUpdate(message=message), FakeContext(service)))
    assert message.documents == [PANEL_FILENAME]
    assert message.payloads[0].decode().startswith("<!doctype html>")


def test_light_estado_bars_stay_visible() -> None:
    # "analizada" is tinted white like the xlsx; on a white card the bar
    # would vanish, so every bar carries an outline.
    html = render_panel([row(1, "analizada")], NOW)
    assert re.search(r"svg \.bar\{[^}]*stroke:", html)


def test_service_panel_copy_failure_returns_html_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "sync" / "tracker.xlsx"
    service, _repo = make_service(
        tmp_path,
        FakeLlmClient(_extraction()),
        FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
        tracker_xlsx=target,
    )
    asyncio.run(service.ingest(None, OFFER_TEXT))

    def boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    html_out = service.panel_html()
    assert html_out.startswith("<!doctype html>")
    assert not (target.parent / PANEL_FILENAME).exists()
    assert not list(target.parent.glob(".*.tmp"))


def test_write_panel_atomic_keeps_previous_mode(tmp_path: Path) -> None:
    path = tmp_path / PANEL_FILENAME
    path.write_text("old", encoding="utf-8")
    path.chmod(0o640)
    assert write_panel_atomic("new", path)
    assert path.read_text(encoding="utf-8") == "new"
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_write_panel_atomic_new_file_is_world_readable(tmp_path: Path) -> None:
    path = tmp_path / "sub" / PANEL_FILENAME
    assert write_panel_atomic("x", path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_panel_command_renders_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cva.service as service_module

    monkeypatch.chdir(tmp_path)
    service, _repo = make_service(
        tmp_path,
        FakeLlmClient(_extraction()),
        FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
    )
    seen: list[int] = []
    real = service_module.render_panel

    def spy(rows: object, now: object, discarded: object, **kw: object) -> str:
        seen.append(threading.get_ident())
        return real(rows, now, discarded, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(service_module, "render_panel", spy)
    message = DocMessage()
    asyncio.run(bot.panel_command(FakeUpdate(message=message), FakeContext(service)))
    assert seen
    assert seen[0] != threading.get_ident()
    assert message.documents == [PANEL_FILENAME]
