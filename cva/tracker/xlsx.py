"""xlsx exports (tracker spec): sent-only (``/export``) and the full synced copy.

:func:`export_xlsx` writes only rows with fecha_envio set. The full-tracker
projection (:func:`export_all_xlsx` + :func:`refresh_tracker_xlsx`) lists
EVERY offer and is rewritten after each service mutation into a folder the
owner syncs between machines (Syncthing / Drive). Like the CV mirror it is
best-effort: a failure is logged (WARNING) and NEVER raised into the action.
"""

import contextlib
import logging
import os
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from cva.mirror import mirror_root
from cva.tracker.repo import OfferRepository, OfferRow

_LOG = logging.getLogger(__name__)

S1_COLUMNS: Final[tuple[str, ...]] = (
    "id",
    "fecha_envio",
    "empresa",
    "posicion",
    "origen",
    "url_oferta",
    "cv_base",
    "cv_adaptado",
    "cambios",
    "match",
    "sueldo_rango",
    "sueldo_ofrecido",
    "estado",
    "fecha_update",
    "contacto",
    "notas",
    "source_data",
)

# The synced phone-friendly copy omits ``source_data`` (up to ~11k chars of
# offer text per row): it stays in the DB, never in the projection. It adds
# the backend-profile ``perfil`` column (empty = legacy) and the on-demand
# ``salario_investigado`` JSON (salary-estimate).
SYNCED_COLUMNS: Final[tuple[str, ...]] = (
    *(column for column in S1_COLUMNS if column != "source_data"),
    "perfil",
    "salario_investigado",
    "carta_presentacion",
)


def export_xlsx(repo: OfferRepository, path: Path) -> Path:
    """Write sent offers (fecha_envio IS NOT NULL) as one §1-mirroring sheet."""
    return _write_sheet(repo.sent_offers(), path)


def _write_sheet(
    offers: list[OfferRow], path: Path, columns: tuple[str, ...] = S1_COLUMNS
) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(list(columns))
    for offer in offers:
        sheet.append([getattr(offer, column) for column in columns])
    workbook.save(path)
    return path


# Synced workbook layout (owner decision 2026-10-11): it opens on the sent
# offers; the full list is the second tab, each row tinted by its estado.
SENT_SHEET: Final[str] = "Enviadas"
ALL_SHEET: Final[str] = "Todas"

# An offer counts as sent once it has a send date or any post-send estado
# (imported history may carry a state without fecha_envio).
_POST_SEND: Final[frozenset[str]] = frozenset(
    {
        "enviada",
        "screening",
        "entrevista_tecnica",
        "entrevista_final",
        "oferta",
        "aceptada",
        "rechazada",
        "retirada",
        "sin_respuesta",
    }
)

# Row tint per estado on the "Todas" tab (RGB hex, light so text stays legible).
ESTADO_FILLS: Final[dict[str, str]] = {
    "pendiente_analisis": "FFF2CC",  # pale yellow: waiting for analysis
    "analizada": "FFFFFF",  # white: analysed, no action yet
    "preparada": "DDEBF7",  # pale blue: CV generated, not sent
    "enviada": "C6EFCE",  # green: sent
    "screening": "A9D08E",  # stronger greens: process moving
    "entrevista_tecnica": "A9D08E",
    "entrevista_final": "A9D08E",
    "oferta": "70AD47",
    "aceptada": "70AD47",
    "sin_respuesta": "FCE4D6",  # pale orange: sent, no answer
    "rechazada": "FFC7CE",  # pale red: rejected
    "retirada": "E7E6E6",  # grey: withdrawn
    "descartada": "E7E6E6",  # grey: discarded
}


def _is_sent(offer: OfferRow) -> bool:
    return bool(offer.fecha_envio) or offer.estado in _POST_SEND


def _write_tracker_book(offers: list[OfferRow], path: Path) -> Path:
    """Two tabs: "Enviadas" (active on open) and "Todas" (row tint by estado)."""
    workbook = Workbook()
    sent = workbook.active
    assert sent is not None
    sent.title = SENT_SHEET
    every = workbook.create_sheet(ALL_SHEET)
    bold = Font(bold=True)
    for sheet, rows in ((sent, [o for o in offers if _is_sent(o)]), (every, offers)):
        sheet.append(list(SYNCED_COLUMNS))
        for cell in sheet[1]:
            cell.font = bold
        sheet.freeze_panes = "A2"
        for offer in rows:
            sheet.append([getattr(offer, column) for column in SYNCED_COLUMNS])
            if sheet is every and (colour := ESTADO_FILLS.get(offer.estado)):
                fill = PatternFill("solid", fgColor=colour)
                for cell in sheet[sheet.max_row]:
                    cell.fill = fill
        sheet.auto_filter.ref = sheet.dimensions
    workbook.active = 0
    workbook.save(path)
    return path


def export_all_xlsx(repo: OfferRepository, path: Path) -> Path:
    """Write the synced tracker: sent offers first, then EVERY offer.

    Newest first, without ``source_data``. ``match`` falls back to the
    latest analysis score when ``offers.match`` is unset (it is only stamped
    on mark_sent).
    """
    return _write_tracker_book(repo.tracker_offers(), path)


# Pauses between attempts when the target is locked (Dropbox / Excel on
# Windows). Callers on the event loop keep the default single attempt; only
# the heartbeat catch-up retries, and does so off-loop.
RETRY_BACKOFF_SECONDS: Final[tuple[float, ...]] = (0.5, 1.0, 2.0)


def refresh_tracker_xlsx(
    repo: OfferRepository,
    target: Path | str | None,
    *,
    attempts: int = 1,
    backoff: Sequence[float] = RETRY_BACKOFF_SECONDS,
) -> bool:
    """Best-effort atomic rewrite of the synced full tracker; never raises.

    Disabled (``None``/blank) → zero filesystem activity. Otherwise the
    workbook is saved to a temp file in the target's folder and swapped in
    with ``os.replace`` so a sync tool never sees a half-written file.

    A write that fails with ``OSError`` (a sync tool or Excel holding the
    file on Windows) is retried up to ``attempts`` times in total, sleeping
    ``backoff[i]`` between tries. The default is ONE attempt and no sleep, so
    mutators running on the event loop never block; the service marks the
    tracker dirty on failure and the heartbeat retries with more attempts.
    Any other failure is not retried. After the last failure one WARNING is
    logged and the temp file is removed.
    """
    path = mirror_root(target)
    if path is None:
        return False
    try:
        offers = repo.tracker_offers()
    except Exception as exc:  # noqa: BLE001 — best-effort projection, never raises
        _LOG.warning("tracker xlsx refresh skipped (%s: %s)", type(exc).__name__, exc)
        return False
    return write_tracker_xlsx(offers, path, attempts=attempts, backoff=backoff)


def write_tracker_xlsx(
    offers: list[OfferRow],
    path: Path,
    *,
    attempts: int = 1,
    backoff: Sequence[float] = RETRY_BACKOFF_SECONDS,
) -> bool:
    """Atomic write of an already-read snapshot (pure file I/O, thread-safe).

    Split from :func:`refresh_tracker_xlsx` so the heartbeat can read the rows
    on the event loop (SQLite stays single-threaded) and run this, including
    its backoff sleeps, in a worker thread.
    """
    total = max(1, attempts)
    for attempt in range(1, total + 1):
        tmp_name: str | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp"
            )
            os.close(fd)
            _write_tracker_book(offers, Path(tmp_name))
            os.replace(tmp_name, path)
            return True
        except Exception as exc:  # noqa: BLE001 — best-effort projection, never raises
            if tmp_name is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)
            if isinstance(exc, OSError) and attempt < total:
                _LOG.debug(
                    "tracker xlsx locked (attempt %d/%d): %s", attempt, total, exc
                )
                time.sleep(
                    backoff[min(attempt - 1, len(backoff) - 1)] if backoff else 0
                )
                continue
            _LOG.warning(
                "tracker xlsx refresh skipped (%s: %s)", type(exc).__name__, exc
            )
            return False
    return False  # pragma: no cover — loop always returns
