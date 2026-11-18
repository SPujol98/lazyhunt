"""Manual offer-history import: record contract + parsing (no DB access).

The owner's hand-kept log is converted to a JSON list of
:class:`HistoryRecord` by a human/LLM step; ``JobService.import_history``
applies it through :meth:`OfferRepository.import_history_record`. Invalid
records are reported with their ``log_id`` and skipped, never fatal.
"""

import json
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

Estado = Literal[
    "pendiente_analisis",
    "analizada",
    "preparada",
    "descartada",
    "enviada",
    "screening",
    "entrevista_tecnica",
    "entrevista_final",
    "oferta",
    "aceptada",
    "rechazada",
    "retirada",
    "sin_respuesta",
]
HistoryCvBase = Literal[
    "AI_ES", "AI_EN", "DS_ES", "DS_EN", "BACKEND_ES", "BACKEND_EN"
]

# Estados before an application went out: only these may be advanced by an
# import; a post-send estado is the bot's (or the owner's) truth and is kept.
PRE_SEND_ESTADOS: Final[frozenset[str]] = frozenset(
    {"pendiente_analisis", "analizada", "preparada"}
)


def log_marker(log_id: int) -> str:
    """Traceability marker appended to ``notas``; also the idempotency key."""
    return f"[log #{log_id}]"


class HistoryRecord(BaseModel):
    """One entry of the manual log (feature contract)."""

    log_id: int
    empresa: str
    posicion: str
    url_oferta: str | None = None
    estado: Estado
    fecha_envio: str | None = None
    fecha_update: str | None = None
    match: Annotated[int, Field(ge=0, le=100)] | None = None
    cv_base: HistoryCvBase | None = None
    cv_adaptado: str | None = None
    sueldo_rango: str | None = None
    contacto: str | None = None
    notas: str | None = None

    @field_validator(
        "url_oferta",
        "cv_adaptado",
        "sueldo_rango",
        "contacto",
        "notas",
        "fecha_envio",
        "fecha_update",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("fecha_envio", "fecha_update")
    @classmethod
    def _iso_date(cls, value: str | None) -> str | None:
        if value is not None:
            date.fromisoformat(value)  # ValueError -> validation error
        return value


class HistoryImportResult(BaseModel):
    """Outcome of one import run (also what ``--dry-run`` would do)."""

    inserted: int = 0
    merged: int = 0
    skipped: int = 0
    invalid: list[str] = []
    dry_run: bool = False


def _describe(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or 'registro'}: {err['msg']}"
        for err in exc.errors()
    )


def parse_history(path: Path) -> tuple[list[HistoryRecord], list[str]]:
    """Read the JSON file; return ``(valid records, invalid-record reports)``.

    Raises ``ValueError`` when the file is unreadable, not JSON, or not a list
    (nothing can be imported then). A bad record only produces a report line.
    """
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError(f"{path} must contain a JSON list of records")  # noqa: TRY004
    records: list[HistoryRecord] = []
    invalid: list[str] = []
    for index, item in enumerate(raw):
        label = item.get("log_id") if isinstance(item, dict) else None
        try:
            records.append(HistoryRecord.model_validate(item))
        except ValidationError as exc:
            where = f"#{label}" if label is not None else f"posición {index}"
            invalid.append(f"{where}: {_describe(exc)}")
    return records, invalid
