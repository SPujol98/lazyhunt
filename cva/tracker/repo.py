"""SQLite repository for the tracker (design D4/D5).

Thin ``OfferRepository`` over an injectable :mod:`sqlite3` connection:
WAL journal, ``PRAGMA user_version`` migrations only, DDL from
``schema.sql`` applied once on first open.
"""

import json
import re
import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel

from cva.calibration import CalibrationExample, SentJudgment
from cva.ingest.closed import CLOSED_NOTE
from cva.ingest.dedup import dedup_key
from cva.ingest.sources import (
    canonical_url,
    job_id_from_url,
    source_from_url,
    source_label,
)
from cva.tracker.history import PRE_SEND_ESTADOS, HistoryRecord, log_marker

SCHEMA_VERSION: Final[int] = 22
NOTE_SEPARATOR: Final[str] = " · "
# app_state key of the persistent global pause ("1"/"0"; absent = never toggled).
PAUSED_STATE_KEY: Final[str] = "paused"
# Post-send estados the owner can record from /outcome (all already valid in
# the offers.estado CHECK).
OUTCOME_ESTADOS: Final[tuple[str, ...]] = (
    "screening",
    "entrevista_tecnica",
    "entrevista_final",
    "oferta",
    "aceptada",
    "rechazada",
    "sin_respuesta",
    "retirada",
)
DEDUP_WINDOW_DAYS: Final[int] = 30

_SCHEMA_SQL: Final[str] = (
    Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
)

# Ordered user_version migrations (design D4): v2 adds the beacon daily-cap
# counters that the heartbeat scheduler needs (tasks 8b.3–8b.4).
_MIGRATION_V2: Final[str] = (
    "ALTER TABLE beacons ADD COLUMN checks_today INTEGER NOT NULL DEFAULT 0;\n"
    "ALTER TABLE beacons ADD COLUMN checks_date TEXT;"
)

# v3 (v3 part B): the offers.cv_base CHECK gains the Catalan AI_CA variant.
# SQLite cannot alter a CHECK, so the table rebuilds: create new DDL → copy
# rows → drop → rename. Foreign keys are toggled OFF around the script
# because offer_keywords/match_results reference offers(id).
_OFFERS_V3_DDL: Final[str] = (
    "CREATE TABLE offers_v3 ("
    "id INTEGER PRIMARY KEY, fecha_envio TEXT, empresa TEXT, posicion TEXT, "
    "origen TEXT CHECK(origen IN ('linkedin','infojobs','referido','web_empresa','otro')), "
    "url_oferta TEXT, "
    "cv_base TEXT CHECK(cv_base IN ('AI_ES','AI_EN','DS_ES','DS_EN','AI_CA')), "
    "cv_adaptado TEXT, cambios TEXT, "
    "match INTEGER CHECK(match BETWEEN 0 AND 100), "
    "sueldo_rango TEXT, sueldo_ofrecido TEXT, "
    "estado TEXT NOT NULL DEFAULT 'pendiente_analisis' CHECK(estado IN ("
    "'pendiente_analisis','analizada','preparada','descartada',"
    "'enviada','screening','entrevista_tecnica','entrevista_final',"
    "'oferta','aceptada','rechazada','retirada','sin_respuesta')), "
    "fecha_update TEXT, contacto TEXT, notas TEXT, source_data TEXT NOT NULL)"
)
_MIGRATION_V3: Final[str] = (
    f"{_OFFERS_V3_DDL};\n"
    "INSERT INTO offers_v3 (id, fecha_envio, empresa, posicion, origen, "
    "url_oferta, cv_base, cv_adaptado, cambios, match, sueldo_rango, "
    "sueldo_ofrecido, estado, fecha_update, contacto, notas, source_data) "
    "SELECT id, fecha_envio, empresa, posicion, origen, url_oferta, cv_base, "
    "cv_adaptado, cambios, match, sueldo_rango, sueldo_ofrecido, estado, "
    "fecha_update, contacto, notas, source_data FROM offers;\n"
    "DROP TABLE offers;\n"
    "ALTER TABLE offers_v3 RENAME TO offers"
)

# v4 (notification-pacing D3): two additive retry-pending columns written only
# by the analysis service (analyze_attempts counter + next_analyze_at due time).
_MIGRATION_V4: Final[str] = (
    "ALTER TABLE offers ADD COLUMN analyze_attempts INTEGER NOT NULL DEFAULT 0;\n"
    "ALTER TABLE offers ADD COLUMN next_analyze_at TEXT;"
)

# v5 (llm-offer-judge design): one additive table for the LLM judge's
# holistic OfferJudgment, parallel to match_results (one row per offer, the
# latest judgment only — re-analysis overwrites it, exactly like
# match_results). Only written in match_mode "hybrid".
_MIGRATION_V5: Final[str] = (
    "CREATE TABLE IF NOT EXISTS judge_results ("
    "offer_id INTEGER PRIMARY KEY REFERENCES offers(id), "
    "score INTEGER, verdict TEXT, judgment_json TEXT NOT NULL, created_at TEXT);"
)

# v6 (backend-profile): one nullable column holding the offer profile.
# NULL = legacy row = treated as 'ai'. SQLite allows a CHECK on ADD COLUMN.
_MIGRATION_V6: Final[str] = (
    "ALTER TABLE offers ADD COLUMN perfil TEXT CHECK(perfil IN ('ai','backend'));"
)

# v7 (backend-profile): the cv_base CHECK gains BACKEND_ES / BACKEND_EN. Same
# rebuild recipe as v3 (SQLite cannot alter a CHECK), now with every column
# added since v3 (v4 retry state, v6 perfil) carried over.
_OFFERS_V7_COLUMNS: Final[str] = (
    "id, fecha_envio, empresa, posicion, origen, url_oferta, cv_base, "
    "cv_adaptado, cambios, match, sueldo_rango, sueldo_ofrecido, estado, "
    "fecha_update, contacto, notas, source_data, analyze_attempts, "
    "next_analyze_at, perfil"
)
# One explicit transaction (executescript runs statements in autocommit
# otherwise): an interruption after DROP TABLE rolls the whole rebuild back
# instead of leaving the DB without an offers table. user_version is bumped
# inside it so a half-applied v7 is never recorded as done. `offers` has no
# indexes/triggers (schema.sql), so the rebuild loses none.
_MIGRATION_V7: Final[str] = (
    "BEGIN;\n"
    "CREATE TABLE offers_v7 ("
    "id INTEGER PRIMARY KEY, fecha_envio TEXT, empresa TEXT, posicion TEXT, "
    "origen TEXT CHECK(origen IN ('linkedin','infojobs','referido','web_empresa','otro')), "
    "url_oferta TEXT, "
    "cv_base TEXT CHECK(cv_base IN "
    "('AI_ES','AI_EN','DS_ES','DS_EN','AI_CA','BACKEND_ES','BACKEND_EN')), "
    "cv_adaptado TEXT, cambios TEXT, "
    "match INTEGER CHECK(match BETWEEN 0 AND 100), "
    "sueldo_rango TEXT, sueldo_ofrecido TEXT, "
    "estado TEXT NOT NULL DEFAULT 'pendiente_analisis' CHECK(estado IN ("
    "'pendiente_analisis','analizada','preparada','descartada',"
    "'enviada','screening','entrevista_tecnica','entrevista_final',"
    "'oferta','aceptada','rechazada','retirada','sin_respuesta')), "
    "fecha_update TEXT, contacto TEXT, notas TEXT, source_data TEXT NOT NULL, "
    "analyze_attempts INTEGER NOT NULL DEFAULT 0, next_analyze_at TEXT, "
    "perfil TEXT CHECK(perfil IN ('ai','backend')));\n"
    f"INSERT INTO offers_v7 ({_OFFERS_V7_COLUMNS}) "
    f"SELECT {_OFFERS_V7_COLUMNS} FROM offers;\n"
    "DROP TABLE offers;\n"
    "ALTER TABLE offers_v7 RENAME TO offers;\n"
    "PRAGMA user_version = 7;\n"
    "COMMIT;"
)

# v8 (salary-estimate): one nullable JSON-text column holding the on-demand
# salary web-research result. One transaction with the user_version bump, so
# a failure leaves the DB exactly at v7 (same recipe as v7).
_MIGRATION_V8: Final[str] = (
    "BEGIN;\n"
    "ALTER TABLE offers ADD COLUMN salario_investigado TEXT;\n"
    "PRAGMA user_version = 8;\n"
    "COMMIT;"
)

# v9 (cover-letters): nullable text column holding the cover letter the owner
# chose. Same atomic recipe as v8.
_MIGRATION_V9: Final[str] = (
    "BEGIN;\n"
    "ALTER TABLE offers ADD COLUMN carta_presentacion TEXT;\n"
    "PRAGMA user_version = 9;\n"
    "COMMIT;"
)

# v10 (offer-dates): first-seen / posting / deadline ISO dates (all nullable;
# existing rows stay NULL, nothing is invented) and a tiny key/value table for
# daily-sweep bookkeeping. One transaction with the version bump (v8/v9 recipe).
_MIGRATION_V10: Final[str] = (
    "BEGIN;\n"
    "ALTER TABLE offers ADD COLUMN fecha_alta TEXT;\n"
    "ALTER TABLE offers ADD COLUMN fecha_publicacion TEXT;\n"
    "ALTER TABLE offers ADD COLUMN fecha_limite TEXT;\n"
    "CREATE TABLE IF NOT EXISTS app_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);\n"
    "PRAGMA user_version = 10;\n"
    "COMMIT;"
)

# v11 (offer-gates-and-health T4): cross-source duplicate key, backfilled in
# Python from empresa/posicion so already-stored offers dedupe too. The ALTER is
# skipped when the column already exists (a rewound version on a full schema).
_MIGRATION_V11_DDL: Final[tuple[str, ...]] = (
    "ALTER TABLE offers ADD COLUMN dedup_key TEXT",
    "CREATE INDEX IF NOT EXISTS idx_offers_dedup_key ON offers (dedup_key)",
)

# v12 (offer-gates-and-health T5): per-scan beacon history reported by the
# extension. Atomic with the version bump (v8/v9/v10 recipe).
_MIGRATION_V12: Final[str] = (
    "BEGIN;\n"
    "CREATE TABLE IF NOT EXISTS beacon_scans ("
    "id INTEGER PRIMARY KEY, beacon_id TEXT NOT NULL, beacon_name TEXT NOT NULL, "
    "at TEXT NOT NULL, status TEXT NOT NULL, "
    "jobs_found INTEGER NOT NULL, empty_company INTEGER NOT NULL);\n"
    "CREATE INDEX IF NOT EXISTS idx_beacon_scans_beacon_at "
    "ON beacon_scans (beacon_id, at);\n"
    "PRAGMA user_version = 12;\n"
    "COMMIT;"
)
BEACON_SCANS_KEPT: Final[int] = 50

# v13 (portal-adapters T4b): offers.origen is the portal id taken from the offer
# URL host, so the closed list of portals goes: any lowercase slug up to 40
# chars (referido / web_empresa / otro stay valid). Same atomic rebuild recipe as
# v7 (SQLite cannot alter a CHECK) with every column added since carried over
# and the dedup_key index recreated (DROP TABLE removes it).
_ORIGEN_CHECK_V13: Final[str] = (
    "CHECK(origen IS NULL OR (length(origen) BETWEEN 1 AND 40 "
    "AND origen NOT GLOB '*[^a-z0-9_-]*'))"
)
_OFFERS_V13_COLUMNS: Final[str] = (
    f"{_OFFERS_V7_COLUMNS}, salario_investigado, carta_presentacion, fecha_alta, "
    "fecha_publicacion, fecha_limite, dedup_key"
)
_MIGRATION_V13: Final[str] = (
    "BEGIN;\n"
    "CREATE TABLE offers_v13 ("
    "id INTEGER PRIMARY KEY, fecha_envio TEXT, empresa TEXT, posicion TEXT, "
    f"origen TEXT {_ORIGEN_CHECK_V13}, "
    "url_oferta TEXT, "
    "cv_base TEXT CHECK(cv_base IN "
    "('AI_ES','AI_EN','DS_ES','DS_EN','AI_CA','BACKEND_ES','BACKEND_EN')), "
    "cv_adaptado TEXT, cambios TEXT, "
    "match INTEGER CHECK(match BETWEEN 0 AND 100), "
    "sueldo_rango TEXT, sueldo_ofrecido TEXT, "
    "estado TEXT NOT NULL DEFAULT 'pendiente_analisis' CHECK(estado IN ("
    "'pendiente_analisis','analizada','preparada','descartada',"
    "'enviada','screening','entrevista_tecnica','entrevista_final',"
    "'oferta','aceptada','rechazada','retirada','sin_respuesta')), "
    "fecha_update TEXT, contacto TEXT, notas TEXT, source_data TEXT NOT NULL, "
    "analyze_attempts INTEGER NOT NULL DEFAULT 0, next_analyze_at TEXT, "
    "perfil TEXT CHECK(perfil IN ('ai','backend')), "
    "salario_investigado TEXT, carta_presentacion TEXT, fecha_alta TEXT, "
    "fecha_publicacion TEXT, fecha_limite TEXT, dedup_key TEXT);\n"
    f"INSERT INTO offers_v13 ({_OFFERS_V13_COLUMNS}) "
    f"SELECT {_OFFERS_V13_COLUMNS} FROM offers;\n"
    "DROP TABLE offers;\n"
    "ALTER TABLE offers_v13 RENAME TO offers;\n"
    "CREATE INDEX IF NOT EXISTS idx_offers_dedup_key ON offers (dedup_key);\n"
    "PRAGMA user_version = 13;\n"
    "COMMIT;"
)

# v14: how the row's source_data was ingested ('link' | 'paste' |
# 'bridge-inline'); NULL on legacy rows. The scheduler's retry reads it so a
# link-ingested row is refetched instead of replayed as inline text.
_MIGRATION_V14: Final[str] = "ALTER TABLE offers ADD COLUMN ingest_source TEXT;"

# v15: what was actually sent. One immutable row per offer (the FIRST send);
# a re-send never overwrites it. Idempotent DDL, applied in one transaction
# with the user_version bump.
_MIGRATION_V15: Final[str] = (
    "CREATE TABLE IF NOT EXISTS sent_snapshots ("
    "offer_id INTEGER PRIMARY KEY REFERENCES offers(id), "
    "sent_at TEXT NOT NULL, posting_text TEXT NOT NULL, cover_letter TEXT, "
    "cv_file TEXT, cv_variant TEXT, url TEXT);"
)

# v16: per-offer outcome timeline (estado changes, free-text notes, follow-ups).
_MIGRATION_V16: Final[str] = (
    "CREATE TABLE IF NOT EXISTS offer_events ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "offer_id INTEGER NOT NULL REFERENCES offers(id), at TEXT NOT NULL, "
    "kind TEXT NOT NULL CHECK(kind IN ('estado','note','followup')), "
    "estado TEXT, note TEXT);"
    "CREATE INDEX IF NOT EXISTS idx_offer_events_offer ON offer_events (offer_id);"
)

# v17: the owner's STAR story bank (interview prep). Text fields hold the
# owner's own words (or STAR_PLACEHOLDER while missing); source_claim_ids is a
# JSON array of claims-DB ids the story was proposed from.
STAR_PLACEHOLDER: Final[str] = "[complétalo tú]"
STAR_TEXT_FIELDS: Final[tuple[str, ...]] = (
    "situation",
    "task",
    "action",
    "result",
    "use_for",
)
_STAR_EDITABLE: Final[frozenset[str]] = frozenset(
    {*STAR_TEXT_FIELDS, "title", "metrics"}
)
_MIGRATION_V17: Final[str] = (
    "CREATE TABLE IF NOT EXISTS star_stories ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
    "source_claim_ids TEXT NOT NULL DEFAULT '[]', "
    "situation TEXT NOT NULL, task TEXT NOT NULL, action TEXT NOT NULL, "
    "result TEXT NOT NULL, metrics TEXT, use_for TEXT NOT NULL, "
    "complete INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);"
)

# v18: generated interview prep packs (HTML + structured JSON), one row per
# generation; the latest per (offer, stage) is the current one.
_MIGRATION_V18: Final[str] = (
    "CREATE TABLE IF NOT EXISTS prep_packs ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "offer_id INTEGER NOT NULL REFERENCES offers(id), "
    "stage TEXT NOT NULL, created_at TEXT NOT NULL, "
    "html TEXT NOT NULL, json TEXT NOT NULL);"
    "CREATE INDEX IF NOT EXISTS idx_prep_packs_offer ON prep_packs (offer_id, stage);"
)

# v19: the Telegram analysis card last sent for an offer (chat, message id, text,
# parse mode and inline keyboard as JSON), so it can be edited in place later.
_MIGRATION_V19: Final[str] = (
    "CREATE TABLE IF NOT EXISTS offer_cards ("
    "offer_id INTEGER PRIMARY KEY REFERENCES offers(id), "
    "chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL, "
    "text TEXT NOT NULL, parse_mode TEXT, keyboard TEXT);"
)

# v20: the portal's structured location of the offer (nullable; legacy rows and
# pasted offers stay NULL), read by the location gate.
_MIGRATION_V20: Final[str] = "ALTER TABLE offers ADD COLUMN ubicacion TEXT;"

# v21 (outcome-calibration): the judge's view of an offer when it was SENT
# (the judge row is overwritten by re-analysis) and the owner's calibration
# examples. One example per offer and polarity (UNIQUE) makes the question
# idempotent; status pending -> active | declined, active -> inactive.
_MIGRATION_V21: Final[str] = (
    "CREATE TABLE IF NOT EXISTS sent_judgments ("
    "offer_id INTEGER PRIMARY KEY REFERENCES offers(id), "
    "score INTEGER NOT NULL, verdict TEXT, core_role TEXT, "
    "hard_walls INTEGER NOT NULL DEFAULT 0, perfil TEXT, snapshot_at TEXT NOT NULL);"
    "CREATE TABLE IF NOT EXISTS calibration_examples ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "offer_id INTEGER NOT NULL REFERENCES offers(id), "
    "outcome TEXT NOT NULL, "
    "polarity TEXT NOT NULL CHECK(polarity IN ('positive','negative')), "
    "reason TEXT, score_at_send INTEGER NOT NULL, verdict TEXT, profile TEXT, "
    "core_role TEXT, hard_walls INTEGER NOT NULL DEFAULT 0, "
    "status TEXT NOT NULL CHECK(status IN ('pending','active','declined','inactive')), "
    "created_at TEXT NOT NULL, approved_at TEXT, UNIQUE (offer_id, polarity));"
)

# v22 (gmail-sync): one row per Gmail message that produced a status proposal
# (the message id is the idempotency key: never proposed twice). Metadata only;
# message bodies are never stored. decision: pending -> accepted | other |
# ignored | stale.
_MIGRATION_V22: Final[str] = (
    "CREATE TABLE IF NOT EXISTS gmail_messages ("
    "message_id TEXT PRIMARY KEY, "
    "offer_id INTEGER NOT NULL REFERENCES offers(id), "
    "sender TEXT NOT NULL, subject TEXT NOT NULL, received_at TEXT NOT NULL, "
    "proposed_estado TEXT NOT NULL, "
    "decision TEXT NOT NULL DEFAULT 'pending' "
    "CHECK(decision IN ('pending','accepted','other','ignored','stale')), "
    "created_at TEXT NOT NULL);"
)

# Ordered by provenance: "paste" rows are always 'otro'. "link" and
# "bridge-inline" rows take the portal id from the offer URL host (the sources
# registry), so a new portal needs no change here; without a registered host a
# link keeps the legacy 'linkedin' default and inline text is 'otro'.
_ORIGIN_FALLBACK: Final[dict[str, str]] = {
    "link": "linkedin",
    "paste": "otro",
    "bridge-inline": "otro",
}


def _origen_for(source: str, url: str | None) -> str:
    if source != "paste" and url:
        portal = source_from_url(url)
        if portal is not None:
            return portal
    return _ORIGIN_FALLBACK[source]


class OfferInput(BaseModel):
    """Fields the tracker needs from an ingested offer.

    Mirrors ``cva.ingest.FetchedOffer`` (slice 5); the service layer adapts
    one into the other once ingest lands.
    """

    job_id: str | None = None
    title: str | None = None
    company: str | None = None
    url: str | None = None
    text: str
    source: Literal["link", "paste", "bridge-inline"]
    # backend-profile: 'ai' | 'backend' (None = leave unset / keep the stored one).
    profile: Literal["ai", "backend"] | None = None
    # offer-dates: ISO dates extracted from the offer (None = unknown).
    fecha_publicacion: str | None = None
    fecha_limite: str | None = None
    # v20: the portal's location field (None = unknown / keep the stored one).
    location: str | None = None


class OfferRow(BaseModel):
    """One ``offers`` row: §1's 17 fields verbatim."""

    id: int
    fecha_envio: str | None = None
    empresa: str | None = None
    posicion: str | None = None
    origen: str | None = None
    url_oferta: str | None = None
    cv_base: str | None = None
    cv_adaptado: str | None = None
    cambios: str | None = None
    match: int | None = None
    sueldo_rango: str | None = None
    sueldo_ofrecido: str | None = None
    estado: str
    fecha_update: str | None = None
    contacto: str | None = None
    notas: str | None = None
    source_data: str
    # v4 retry-pending state (notification-pacing D3); written only by the
    # analysis service: failure bumps the counter, success clears both.
    analyze_attempts: int = 0
    next_analyze_at: str | None = None
    # v6 backend-profile: 'ai' | 'backend'; None = legacy row, treated as 'ai'.
    perfil: str | None = None
    # v8 salary-estimate: JSON text of the on-demand salary research (or NULL).
    salario_investigado: str | None = None
    # v9 cover-letters: the cover letter text the owner chose (or NULL).
    carta_presentacion: str | None = None
    # v10 offer-dates: first seen (set on insert), posting date, deadline.
    fecha_alta: str | None = None
    fecha_publicacion: str | None = None
    fecha_limite: str | None = None
    dedup_key: str | None = None
    # v14: 'link' | 'paste' | 'bridge-inline'; None = legacy row.
    ingest_source: str | None = None
    # v20: the portal's structured location (None = unknown / pasted offer).
    ubicacion: str | None = None


class SentSnapshot(BaseModel):
    """The posting and materials as they were when the offer was first sent."""

    offer_id: int
    sent_at: str
    posting_text: str
    cover_letter: str | None = None
    cv_file: str | None = None
    cv_variant: str | None = None
    url: str | None = None


class OfferEvent(BaseModel):
    """One ``offer_events`` row of an offer's outcome timeline."""

    id: int
    offer_id: int
    at: str
    kind: str
    estado: str | None = None
    note: str | None = None


class StarStory(BaseModel):
    """One ``star_stories`` row: a story in the owner's own words."""

    id: int
    title: str
    source_claim_ids: list[str]
    situation: str
    task: str
    action: str
    result: str
    metrics: str | None = None
    use_for: str
    complete: bool
    updated_at: str


class PrepPackRow(BaseModel):
    """One ``prep_packs`` row."""

    id: int
    offer_id: int
    stage: str
    created_at: str
    html: str
    json_text: str


class MatchRow(BaseModel):
    """One ``match_results`` row: the stored analysis for an offer."""

    offer_id: int
    score: int
    threshold: int
    breakdown_json: str
    created_at: str | None = None


class JudgeRow(BaseModel):
    """One ``judge_results`` row: the latest LLM judge output for an offer."""

    offer_id: int
    score: int
    verdict: str
    judgment_json: str
    created_at: str | None = None


class KeywordRow(BaseModel):
    """One ``keyword_registry`` row: the §5 demand log."""

    profile: str
    keyword: str
    frequency: int
    last_seen: str | None = None
    status: str | None = None


class BeaconRow(BaseModel):
    """One ``beacons`` row: a LinkedIn search URL under heartbeat surveillance."""

    id: int
    name: str
    url: str
    enabled: bool = True
    status: str = "active"
    consecutive_walls: int = 0
    next_check_after: str | None = None
    last_checked_at: str | None = None
    last_wall_kind: str | None = None
    checks_today: int = 0
    checks_date: str | None = None


_CARD_COLS: Final[tuple[str, ...]] = (
    "offer_id",
    "chat_id",
    "message_id",
    "text",
    "parse_mode",
    "keyboard",
)


class StoredCard(BaseModel):
    """The latest Telegram analysis card sent for an offer (v19)."""

    offer_id: int
    chat_id: int
    message_id: int
    text: str
    parse_mode: str | None = None
    keyboard: str | None = None


class RepositoryError(ValueError):
    """Raised when a requested row does not exist (fail-fast)."""


def _default_clock() -> str:
    return datetime.now(tz=UTC).date().isoformat()


# The note entry that marks an offer as a stored cross-source duplicate. The
# bridge writes it with ``duplicate_note`` and ``find_duplicate`` reads it back
# through ``_is_stored_duplicate``: one definition for both sides.
DUPLICATE_NOTE_PREFIX: Final[str] = "duplicada de #"
_DUPLICATE_MARKER = re.compile(re.escape(DUPLICATE_NOTE_PREFIX) + r"\d+")


def duplicate_note(original_id: int) -> str:
    """The note entry that marks an offer as a duplicate of ``original_id``."""
    return f"{DUPLICATE_NOTE_PREFIX}{original_id}"


def _has_note_entry(notas: str | None, entry: str) -> bool:
    """True when ``entry`` is a whole ``NOTE_SEPARATOR``-separated note entry."""
    return entry in (e.strip() for e in (notas or "").split(NOTE_SEPARATOR))


def _is_stored_duplicate(notas: str | None) -> bool:
    """True when a note ENTRY is exactly the generated duplicate marker (whole
    entries split on ``NOTE_SEPARATOR``, like ``append_note``); a sentence
    mentioning it does not count."""
    return any(
        _DUPLICATE_MARKER.fullmatch(e.strip())
        for e in (notas or "").split(NOTE_SEPARATOR)
    )


# ``empty_unconfirmed``: a scan that parsed zero cards on a page that did not show
# the portal's "no results" copy. It may be a soft throttle, so health ignores it
# like a rate limit (the extension reports it; see extension/scan_report.js).
ScanStatus = Literal[
    "ok", "empty_unconfirmed", "rate_limited", "session_expired", "error"
]


class BeaconScanRow(BaseModel):
    """One scan of a beacon as reported by the extension (health history)."""

    beacon_id: str
    beacon_name: str
    at: str
    status: ScanStatus
    jobs_found: int
    empty_company: int


class GmailProposalRow(BaseModel):
    """One Gmail-derived status proposal (v22). Never holds a message body."""

    message_id: str
    offer_id: int
    sender: str
    subject: str
    received_at: str
    proposed_estado: str
    decision: str
    created_at: str


def _row_to_star(row: sqlite3.Row) -> StarStory:
    data = dict(row)
    data["source_claim_ids"] = json.loads(data["source_claim_ids"] or "[]")
    data["complete"] = bool(data["complete"])
    return StarStory.model_validate(data)


def _row_to_offer(row: sqlite3.Row) -> OfferRow:
    # dict(row) is not available: sqlite3.Row is not a mapping, so zip keys+values.
    return OfferRow.model_validate(dict(zip(row.keys(), row, strict=True)))


class OfferRepository:
    """Thin data access over the ``offers`` table and its satellites."""

    def __init__(
        self, conn: sqlite3.Connection, *, clock: Callable[[], str] | None = None
    ) -> None:
        self._conn = conn
        self._clock = clock or _default_clock
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        (version,) = conn.execute("PRAGMA user_version").fetchone()
        if version < 1:
            conn.executescript(_SCHEMA_SQL)
        if version < 2:
            conn.executescript(_MIGRATION_V2)
        if version < 3:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.executescript(_MIGRATION_V3)
            conn.execute("PRAGMA foreign_keys=ON")
        if version < 4:
            conn.executescript(_MIGRATION_V4)
        if version < 5:
            conn.executescript(_MIGRATION_V5)
        if version < 6:
            conn.executescript(_MIGRATION_V6)
        if version < 7:
            # PRAGMA foreign_keys is a no-op inside a transaction, so it is
            # toggled outside the BEGIN/COMMIT of the rebuild (same as v3).
            conn.commit()
            conn.execute("PRAGMA foreign_keys=OFF")
            try:
                conn.executescript(_MIGRATION_V7)
            except sqlite3.Error:
                conn.rollback()
                raise
            finally:
                conn.execute("PRAGMA foreign_keys=ON")
        if version < 8:
            conn.commit()
            try:
                conn.executescript(_MIGRATION_V8)
            except sqlite3.Error:
                conn.rollback()
                raise
        if version < 9:
            conn.commit()
            try:
                conn.executescript(_MIGRATION_V9)
            except sqlite3.Error:
                conn.rollback()
                raise
        if version < 10:
            conn.commit()
            try:
                conn.executescript(_MIGRATION_V10)
            except sqlite3.Error:
                conn.rollback()
                raise
        if version < 11:
            conn.commit()
            self._migrate_v11()
        if version < 12:
            conn.commit()
            try:
                conn.executescript(_MIGRATION_V12)
            except sqlite3.Error:
                conn.rollback()
                raise
        if version < 13:
            # foreign_keys is a no-op inside the transaction: toggled outside (v7).
            conn.commit()
            conn.execute("PRAGMA foreign_keys=OFF")
            try:
                conn.executescript(_MIGRATION_V13)
            except sqlite3.Error:
                conn.rollback()
                raise
            finally:
                conn.execute("PRAGMA foreign_keys=ON")
        if version < 14:
            conn.commit()
            self._migrate_v14()
        if version < 15:
            conn.commit()
            self._migrate_v15()
        if version < 16:
            conn.commit()
            self._migrate_v16()
        if version < 17:
            conn.commit()
            self._migrate_v17()
        if version < 18:
            conn.commit()
            self._migrate_v18()
        if version < 19:
            conn.commit()
            self._migrate_v19()
        if version < 20:
            conn.commit()
            self._migrate_v20()
        if version < 21:
            conn.commit()
            self._migrate_v21()
        if version < 22:
            conn.commit()
            self._migrate_v22()
        if version < SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()

    def _migrate_v22(self) -> None:
        """Create ``gmail_messages`` (when absent) and bump the version atomically."""
        try:
            self._conn.executescript(
                f"BEGIN;{_MIGRATION_V22}PRAGMA user_version = 22;COMMIT;"
            )
        except sqlite3.Error:
            self._conn.rollback()
            raise

    def _migrate_v21(self) -> None:
        """Create the calibration tables (when absent) and bump the version."""
        try:
            self._conn.executescript(
                f"BEGIN;{_MIGRATION_V21}PRAGMA user_version = 21;COMMIT;"
            )
        except sqlite3.Error:
            self._conn.rollback()
            raise

    def _migrate_v20(self) -> None:
        """Add ``ubicacion`` (when absent) and bump the version atomically."""
        conn = self._conn
        cols = {r[1] for r in conn.execute("PRAGMA table_info(offers)")}
        body = "" if "ubicacion" in cols else _MIGRATION_V20
        try:
            conn.executescript(f"BEGIN;{body}PRAGMA user_version = 20;COMMIT;")
        except sqlite3.Error:
            conn.rollback()
            raise

    def _migrate_v19(self) -> None:
        """Create ``offer_cards`` (when absent) and bump the version atomically."""
        try:
            self._conn.executescript(
                f"BEGIN;{_MIGRATION_V19}PRAGMA user_version = 19;COMMIT;"
            )
        except sqlite3.Error:
            self._conn.rollback()
            raise

    def _migrate_v18(self) -> None:
        """Create ``prep_packs`` (when absent) and bump the version atomically."""
        try:
            self._conn.executescript(
                f"BEGIN;{_MIGRATION_V18}PRAGMA user_version = 18;COMMIT;"
            )
        except sqlite3.Error:
            self._conn.rollback()
            raise

    def _migrate_v17(self) -> None:
        """Create ``star_stories`` (when absent) and bump the version atomically."""
        try:
            self._conn.executescript(
                f"BEGIN;{_MIGRATION_V17}PRAGMA user_version = 17;COMMIT;"
            )
        except sqlite3.Error:
            self._conn.rollback()
            raise

    def _migrate_v16(self) -> None:
        """Create ``offer_events`` (when absent) and bump the version atomically."""
        try:
            self._conn.executescript(
                f"BEGIN;{_MIGRATION_V16}PRAGMA user_version = 16;COMMIT;"
            )
        except sqlite3.Error:
            self._conn.rollback()
            raise

    def _migrate_v15(self) -> None:
        """Create ``sent_snapshots`` (when absent) and bump the version atomically."""
        try:
            self._conn.executescript(
                f"BEGIN;{_MIGRATION_V15}PRAGMA user_version = 15;COMMIT;"
            )
        except sqlite3.Error:
            self._conn.rollback()
            raise

    def _migrate_v14(self) -> None:
        """Add ``ingest_source`` (when absent) and bump the version atomically."""
        conn = self._conn
        cols = {r[1] for r in conn.execute("PRAGMA table_info(offers)")}
        body = "" if "ingest_source" in cols else _MIGRATION_V14
        try:
            conn.executescript(f"BEGIN;{body}PRAGMA user_version = 14;COMMIT;")
        except sqlite3.Error:
            conn.rollback()
            raise

    def _migrate_v11(self) -> None:
        """Add ``dedup_key`` + index, backfill it and bump the version atomically."""
        conn = self._conn
        conn.execute("BEGIN")
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(offers)")}
            if "dedup_key" not in cols:
                conn.execute(_MIGRATION_V11_DDL[0])
            conn.execute(_MIGRATION_V11_DDL[1])
            for row in conn.execute(
                "SELECT id, empresa, posicion FROM offers"
            ).fetchall():
                conn.execute(
                    "UPDATE offers SET dedup_key = ? WHERE id = ?",
                    (dedup_key(row[1], row[2]), row[0]),
                )
            conn.execute("PRAGMA user_version = 11")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    def upsert_offer(self, o: OfferInput) -> int:
        """Insert the offer, or refresh the row with the same URL; return its id."""
        if o.url is not None:
            existing = self._conn.execute(
                "SELECT id FROM offers WHERE url_oferta = ?", (o.url,)
            ).fetchone()
            if existing is not None:
                offer_id = int(existing[0])
                # perfil is only ever filled in, never wiped by a refresh.
                self._conn.execute(
                    "UPDATE offers SET empresa = ?, posicion = ?, source_data = ?, "
                    "fecha_update = ?, perfil = COALESCE(perfil, ?), "
                    "fecha_publicacion = COALESCE(?, fecha_publicacion), "
                    "fecha_limite = COALESCE(?, fecha_limite), dedup_key = ?, "
                    "ubicacion = COALESCE(?, ubicacion), "
                    "ingest_source = CASE WHEN ingest_source = 'bridge-inline' "
                    "AND ? = 'link' THEN ingest_source ELSE ? END WHERE id = ?",
                    (
                        o.company,
                        o.title,
                        o.text,
                        self._clock(),
                        o.profile,
                        o.fecha_publicacion,
                        o.fecha_limite,
                        dedup_key(o.company, o.title),
                        o.location,
                        o.source,
                        o.source,
                        offer_id,
                    ),
                )
                self._conn.commit()
                return offer_id
        cursor = self._conn.execute(
            "INSERT INTO offers (empresa, posicion, origen, url_oferta, source_data, "
            "fecha_update, perfil, fecha_alta, fecha_publicacion, fecha_limite, "
            "dedup_key, ingest_source, ubicacion) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                o.company,
                o.title,
                _origen_for(o.source, o.url),
                o.url,
                o.text,
                self._clock(),
                o.profile,
                self._clock(),
                o.fecha_publicacion,
                o.fecha_limite,
                dedup_key(o.company, o.title),
                o.source,
                o.location,
            ),
        )
        if cursor.lastrowid is None:
            raise RepositoryError("insert into offers did not return an id")
        self._conn.commit()
        return cursor.lastrowid

    def record_beacon_scan(
        self,
        *,
        beacon_id: str,
        beacon_name: str,
        at: str,
        status: ScanStatus,
        jobs_found: int,
        empty_company: int,
    ) -> None:
        """Insert one scan and prune the beacon to its last ``BEACON_SCANS_KEPT``.

        The history is ordered by insertion (``id``), never by ``at``: ``at`` is
        the extension's own clock, so a skewed or wrong one must not reorder the
        scans or get the row just written pruned.
        """
        self._conn.execute(
            "INSERT INTO beacon_scans "
            "(beacon_id, beacon_name, at, status, jobs_found, empty_company) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (beacon_id, beacon_name, at, status, jobs_found, empty_company),
        )
        self._conn.execute(
            "DELETE FROM beacon_scans WHERE beacon_id = ? AND id NOT IN ("
            "SELECT id FROM beacon_scans WHERE beacon_id = ? "
            "ORDER BY id DESC LIMIT ?)",
            (beacon_id, beacon_id, BEACON_SCANS_KEPT),
        )
        self._conn.commit()

    def recent_beacon_scans(
        self, beacon_id: str, limit: int = BEACON_SCANS_KEPT
    ) -> list[BeaconScanRow]:
        """The beacon's latest scans, newest recorded first (not by ``at``)."""
        rows = self._conn.execute(
            "SELECT beacon_id, beacon_name, at, status, jobs_found, empty_company "
            "FROM beacon_scans WHERE beacon_id = ? ORDER BY id DESC LIMIT ?",
            (beacon_id, limit),
        ).fetchall()
        return [BeaconScanRow.model_validate(dict(r)) for r in rows]

    # --- Gmail proposals (v22) ---------------------------------------------

    def add_gmail_proposal(
        self,
        *,
        message_id: str,
        offer_id: int,
        sender: str,
        subject: str,
        received_at: str,
        proposed_estado: str,
    ) -> bool:
        """Record a pending proposal; False when the message already has one."""
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO gmail_messages (message_id, offer_id, sender, "
            "subject, received_at, proposed_estado, decision, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
            (
                message_id,
                offer_id,
                sender,
                subject,
                received_at,
                proposed_estado,
                self._clock(),
            ),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    def get_gmail_proposal(self, message_id: str) -> GmailProposalRow | None:
        """The stored proposal of a Gmail message, or ``None``."""
        row = self._conn.execute(
            "SELECT * FROM gmail_messages WHERE message_id = ?", (message_id,)
        ).fetchone()
        return None if row is None else GmailProposalRow.model_validate(dict(row))

    def set_gmail_decision(
        self, message_id: str, decision: str, *, expect: str | None = None
    ) -> bool:
        """Set the decision; with ``expect`` only when it currently equals it
        (an atomic claim, so a double press resolves once). True when changed."""
        sql = "UPDATE gmail_messages SET decision = ? WHERE message_id = ?"
        params: tuple[str, ...] = (decision, message_id)
        if expect is not None:
            sql += " AND decision = ?"
            params = (*params, expect)
        cursor = self._conn.execute(sql, params)
        self._conn.commit()
        return cursor.rowcount == 1

    def delete_pending_gmail_proposal(self, message_id: str) -> None:
        """Drop an undelivered proposal (its Telegram send failed)."""
        self._conn.execute(
            "DELETE FROM gmail_messages WHERE message_id = ? AND decision = 'pending'",
            (message_id,),
        )
        self._conn.commit()

    def get_state(self, key: str) -> str | None:
        """Value stored under ``key`` in ``app_state`` (or ``None``)."""
        row = self._conn.execute(
            "SELECT value FROM app_state WHERE key = ?", (key,)
        ).fetchone()
        return None if row is None else str(row[0])

    def set_state(self, key: str, value: str) -> None:
        """Upsert one ``app_state`` value."""
        self._conn.execute(
            "INSERT INTO app_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    def is_paused(self, default: bool = False) -> bool:
        """Persistent global pause (kill switch); ``default`` when never toggled."""
        raw = self.get_state(PAUSED_STATE_KEY)
        return default if raw is None else raw == "1"

    def set_paused(self, paused: bool) -> None:
        """Persist the global pause so it survives restarts."""
        self.set_state(PAUSED_STATE_KEY, "1" if paused else "0")

    def list_unsent_with_deadline(self, first: str, last: str) -> list[OfferRow]:
        """Pre-send offers whose ``fecha_limite`` is in ``[first, last]`` (ISO)."""
        marks = ",".join("?" for _ in PRE_SEND_ESTADOS)
        rows = self._conn.execute(
            f"SELECT * FROM offers WHERE estado IN ({marks}) "
            "AND fecha_limite BETWEEN ? AND ? ORDER BY fecha_limite, id",
            (*sorted(PRE_SEND_ESTADOS), first, last),
        ).fetchall()
        return [_row_to_offer(r) for r in rows]

    def set_salary_research(self, offer_id: int, payload: str) -> None:
        """Persist the salary-research JSON on the offer (overwrites)."""
        self._conn.execute(
            "UPDATE offers SET salario_investigado = ? WHERE id = ?",
            (payload, offer_id),
        )
        self._conn.commit()

    def set_cover_letter(self, offer_id: int, text: str) -> None:
        """Persist the chosen cover letter on the offer (overwrites)."""
        self._conn.execute(
            "UPDATE offers SET carta_presentacion = ? WHERE id = ?",
            (text, offer_id),
        )
        self._conn.commit()

    def set_profile_if_unset(self, offer_id: int, profile: str) -> None:
        """Stamp the offer profile only when none is stored yet (never overwrite)."""
        self._conn.execute(
            "UPDATE offers SET perfil = ? WHERE id = ? AND perfil IS NULL",
            (profile, offer_id),
        )
        self._conn.commit()

    def get_offer(self, offer_id: int) -> OfferRow:
        """Return the offer row; fail fast when it does not exist."""
        row = self._conn.execute(
            "SELECT * FROM offers WHERE id = ?", (offer_id,)
        ).fetchone()
        if row is None:
            raise RepositoryError(f"offer {offer_id} not found")
        return _row_to_offer(row)

    def find_duplicate(
        self, key: str | None, url: str | None, *, days: int = DEDUP_WINDOW_DAYS
    ) -> OfferRow | None:
        """Earliest offer with the same ``dedup_key`` at ANOTHER url, seen recently.

        "Seen" is ``fecha_alta`` (first seen), falling back to ``fecha_update``
        for rows that predate v10; the window is the last ``days`` days. Any
        estado counts (a duplicate of an already sent offer is a duplicate), but
        rows that are themselves stored as duplicates never extend the window.
        """
        if not key:
            return None
        cutoff = (
            date.fromisoformat(self._clock()[:10]) - timedelta(days=days)
        ).isoformat()
        rows = self._conn.execute(
            "SELECT * FROM offers WHERE dedup_key = ? "
            "AND (url_oferta IS NULL OR url_oferta != ?) "
            "AND substr(COALESCE(fecha_alta, fecha_update), 1, 10) >= ? "
            "ORDER BY id",
            (key, url or "", cutoff),
        ).fetchall()
        for row in rows:
            offer = _row_to_offer(row)
            if _is_stored_duplicate(offer.notas):
                continue
            if _has_note_entry(offer.notas, CLOSED_NOTE):
                continue  # a closed offer is no original: a re-post is fresh
            return offer
        return None

    def is_closed_discarded(self, offer: OfferRow) -> bool:
        """Discarded because the posting was closed and never sent.

        ``descartada`` + the ``CLOSED_NOTE`` entry + no send/outcome history
        (``fecha_envio`` empty and no timeline event in a post-send estado).
        Offers discarded for any other reason are not closed-discarded.
        """
        if offer.estado != "descartada" or offer.fecha_envio is not None:
            return False
        if not _has_note_entry(offer.notas, CLOSED_NOTE):
            return False
        later = ("enviada", *OUTCOME_ESTADOS)
        marks = ",".join("?" for _ in later)
        row = self._conn.execute(
            f"SELECT 1 FROM offer_events WHERE offer_id = ? AND estado IN ({marks}) "
            "LIMIT 1",
            (offer.id, *later),
        ).fetchone()
        return row is None

    def closed_at(self, offer_id: int) -> str | None:
        """ISO date the offer was last closed: its newest closed event.

        Falls back to ``fecha_update`` for rows closed before the event existed
        (the closing wrote the note last, so it is the best available date).
        """
        row = self._conn.execute(
            "SELECT at FROM offer_events WHERE offer_id = ? AND kind = 'note' "
            "AND note = ? ORDER BY id DESC LIMIT 1",
            (offer_id, CLOSED_NOTE),
        ).fetchone()
        if row is not None:
            return str(row[0])[:10]
        update = self.get_offer(offer_id).fecha_update
        return update[:10] if update else None

    def days_since_closed(self, offer_id: int) -> int | None:
        """Whole days between :meth:`closed_at` and the repository clock."""
        closed = self.closed_at(offer_id)
        if closed is None:
            return None
        today = date.fromisoformat(self._clock()[:10])
        return (today - date.fromisoformat(closed)).days

    def reopen_closed(self, offer_id: int, portal: str) -> None:
        """Back to the state of a brand-new bridged job and log why.

        The ``CLOSED_NOTE`` entry is dropped (so a later discard for another
        reason is not mistaken for a closure) and the retry latch is cleared.
        """
        entries = [
            e.strip()
            for e in (self.get_offer(offer_id).notas or "").split(NOTE_SEPARATOR)
            if e.strip() and e.strip() != CLOSED_NOTE
        ]
        self._conn.execute(
            "UPDATE offers SET estado = 'pendiente_analisis', notas = ?, "
            "analyze_attempts = 0, next_analyze_at = NULL WHERE id = ?",
            (NOTE_SEPARATOR.join(entries) or None, offer_id),
        )
        self._conn.commit()
        self.add_event(
            offer_id, "note", note=f"reabierta: volvió a aparecer en {portal}"
        )

    def duplicate_portal_labels(self, offer_id: int) -> list[str]:
        """Portal labels where ``offer_id`` was also seen, oldest first.

        Read from the stored cross-source duplicates ("duplicada de #<id>"
        entries); the original's own portal is excluded and each label appears
        once.
        """
        own = self._conn.execute(
            "SELECT origen FROM offers WHERE id = ?", (offer_id,)
        ).fetchone()
        marker = duplicate_note(offer_id)
        labels: list[str] = []
        for origen, notas in self._conn.execute(
            "SELECT origen, notas FROM offers WHERE notas LIKE ? ORDER BY id",
            (f"%{marker}%",),
        ):
            entries = (e.strip() for e in (notas or "").split(NOTE_SEPARATOR))
            if marker not in entries or not origen:
                continue
            if own is not None and origen == own[0]:
                continue
            label = source_label(origen)
            if label not in labels:
                labels.append(label)
        return labels

    def save_card(
        self,
        offer_id: int,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode: str | None,
        keyboard: str | None,
    ) -> None:
        """Remember the card just sent for the offer (the latest one wins)."""
        self._conn.execute(
            "INSERT OR REPLACE INTO offer_cards "
            "(offer_id, chat_id, message_id, text, parse_mode, keyboard) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (offer_id, chat_id, message_id, text, parse_mode, keyboard),
        )
        self._conn.commit()

    def get_card(self, offer_id: int) -> StoredCard | None:
        row = self._conn.execute(
            "SELECT offer_id, chat_id, message_id, text, parse_mode, keyboard "
            "FROM offer_cards WHERE offer_id = ?",
            (offer_id,),
        ).fetchone()
        return None if row is None else StoredCard(**dict(zip(_CARD_COLS, row)))

    def update_card_text(self, offer_id: int, text: str) -> None:
        self._conn.execute(
            "UPDATE offer_cards SET text = ? WHERE offer_id = ?", (text, offer_id)
        )
        self._conn.commit()

    def move_card(
        self,
        offer_id: int,
        old_chat_id: int,
        old_message_id: int,
        chat_id: int,
        message_id: int,
    ) -> None:
        """Re-point the stored card of an offer to a re-sent message, only when
        the stored card IS the old message (no-op otherwise, e.g. a separate
        reply for the same offer must not hijack the tracked card)."""
        self._conn.execute(
            "UPDATE offer_cards SET chat_id = ?, message_id = ? "
            "WHERE offer_id = ? AND chat_id = ? AND message_id = ?",
            (chat_id, message_id, offer_id, old_chat_id, old_message_id),
        )
        self._conn.commit()

    def forget_card(self, chat_id: int, message_id: int) -> None:
        """Drop the stored card of a message the owner just interacted with
        (button handlers rewrite it, so an in-place edit would clobber that)."""
        self._conn.execute(
            "DELETE FROM offer_cards WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        )
        self._conn.commit()

    def find_offer_by_url(self, url: str) -> OfferRow | None:
        """The offer whose ``url_oferta`` matches exactly, or ``None``.

        Bridge dedupe seam (beacon-bridge D3/D4): the bridge checks this
        before upserting so re-POSTed jobs skip silently.
        """
        row = self._conn.execute(
            "SELECT * FROM offers WHERE url_oferta = ?", (url,)
        ).fetchone()
        return None if row is None else _row_to_offer(row)

    def set_estado(self, offer_id: int, estado: str) -> None:
        """Transition the estado; the 13-value CHECK rejects invalid values."""
        self._conn.execute(
            "UPDATE offers SET estado = ?, fecha_update = ? WHERE id = ?",
            (estado, self._clock(), offer_id),
        )
        self._conn.commit()

    def append_note(self, offer_id: int, note: str) -> None:
        """Append ``note`` as one ``" · "``-separated entry; idempotent per entry.

        Idempotency compares whole entries (split on the separator), never
        substrings, so a hand-written note that merely contains the phrase
        (or ``duplicada de #1`` vs ``#12``) does not suppress the marker.
        """
        existing = (self.get_offer(offer_id).notas or "").strip()
        if note in (e.strip() for e in existing.split(NOTE_SEPARATOR)):
            return
        self._conn.execute(
            "UPDATE offers SET notas = ?, fecha_update = ? WHERE id = ?",
            (
                f"{existing}{NOTE_SEPARATOR}{note}" if existing else note,
                self._clock(),
                offer_id,
            ),
        )
        self._conn.commit()

    def mark_sent(
        self,
        offer_id: int,
        *,
        cv_path: str | None,
        cambios: str | None,
        match: int,
        judgment: SentJudgment | None = None,
    ) -> bool:
        """Record a sent offer: estado=enviada + fecha_envio + CV fields.

        ``cv_path=None`` records a send without a generated CV (no CV file).

        Also stores the ``sent_snapshots`` row in the same transaction; the
        first one is kept. Returns True when this send created the snapshot,
        False on a re-send. ``judgment`` (v21) is the judge's view at send time,
        stored in the same transaction; the first one is kept too.
        """
        today = self._clock()
        self._conn.execute(
            "UPDATE offers SET estado = 'enviada', fecha_envio = ?, cv_adaptado = ?, "
            "cambios = ?, match = ?, fecha_update = ? WHERE id = ?",
            (today, cv_path, cambios, match, today, offer_id),
        )
        created = self._conn.execute(
            "INSERT OR IGNORE INTO sent_snapshots (offer_id, sent_at, posting_text, "
            "cover_letter, cv_file, cv_variant, url) "
            "SELECT id, ?, source_data, carta_presentacion, ?, cv_base, url_oferta "
            "FROM offers WHERE id = ?",
            (today, None if cv_path is None else Path(cv_path).name, offer_id),
        ).rowcount
        if judgment is not None:
            self._conn.execute(
                "INSERT OR IGNORE INTO sent_judgments (offer_id, score, verdict, "
                "core_role, hard_walls, perfil, snapshot_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    offer_id,
                    judgment.score,
                    judgment.verdict,
                    judgment.core_role,
                    judgment.hard_walls,
                    judgment.perfil,
                    today,
                ),
            )
        self._conn.commit()
        return created == 1

    def get_sent_judgment(self, offer_id: int) -> SentJudgment | None:
        """The judge's view at the first send (v21), or ``None`` (legacy sends)."""
        row = self._conn.execute(
            "SELECT score, verdict, core_role, hard_walls, perfil "
            "FROM sent_judgments WHERE offer_id = ?",
            (offer_id,),
        ).fetchone()
        return None if row is None else SentJudgment.model_validate(dict(row))

    def add_calibration(
        self,
        offer_id: int,
        *,
        outcome: str,
        polarity: str,
        reason: str | None,
        judgment: SentJudgment,
        status: str,
    ) -> int | None:
        """Insert one example; ``None`` when the offer+polarity already has one."""
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO calibration_examples (offer_id, outcome, polarity, "
            "reason, score_at_send, verdict, profile, core_role, hard_walls, status, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                offer_id,
                outcome,
                polarity,
                reason,
                judgment.score,
                judgment.verdict,
                judgment.perfil,
                judgment.core_role,
                judgment.hard_walls,
                status,
                self._clock(),
            ),
        )
        self._conn.commit()
        return cur.lastrowid if cur.rowcount == 1 else None

    def get_calibration(self, example_id: int) -> CalibrationExample | None:
        row = self._conn.execute(
            "SELECT * FROM calibration_examples WHERE id = ?", (example_id,)
        ).fetchone()
        return None if row is None else CalibrationExample.model_validate(dict(row))

    def calibration_for(
        self, offer_id: int, polarity: str
    ) -> CalibrationExample | None:
        row = self._conn.execute(
            "SELECT * FROM calibration_examples WHERE offer_id = ? AND polarity = ?",
            (offer_id, polarity),
        ).fetchone()
        return None if row is None else CalibrationExample.model_validate(dict(row))

    def transition_calibration(self, example_id: int, source: str, target: str) -> bool:
        """Move one example ``source`` -> ``target``; False when not in ``source``."""
        cur = self._conn.execute(
            "UPDATE calibration_examples SET status = ?, "
            "approved_at = CASE WHEN ? = 'active' THEN ? ELSE approved_at END "
            "WHERE id = ? AND status = ?",
            (target, target, self._clock(), example_id, source),
        )
        if cur.rowcount == 1 and target == "active":
            # Latest outcome wins: the same offer cannot hold an active example
            # of each polarity (they would give contradictory advice).
            self._conn.execute(
                "UPDATE calibration_examples SET status = 'inactive' "
                "WHERE status = 'active' AND id != ? AND offer_id = "
                "(SELECT offer_id FROM calibration_examples WHERE id = ?)",
                (example_id, example_id),
            )
        self._conn.commit()
        return cur.rowcount == 1

    def list_calibrations(
        self, status: str = "active", *, limit: int | None = None
    ) -> list[CalibrationExample]:
        """Examples in ``status``, oldest approval first (stable: ties by id)."""
        sql = (
            "SELECT * FROM calibration_examples WHERE status = ? "
            "ORDER BY approved_at, id"
        )
        if limit is not None:
            # Most recent N, still returned oldest-first.
            sql = (
                "SELECT * FROM (SELECT * FROM calibration_examples WHERE status = ? "
                "ORDER BY approved_at DESC, id DESC LIMIT ?) ORDER BY approved_at, id"
            )
            params: tuple[object, ...] = (status, limit)
        else:
            params = (status,)
        return [
            CalibrationExample.model_validate(dict(r))
            for r in self._conn.execute(sql, params).fetchall()
        ]

    def get_sent_snapshot(self, offer_id: int) -> SentSnapshot | None:
        row = self._conn.execute(
            "SELECT * FROM sent_snapshots WHERE offer_id = ?", (offer_id,)
        ).fetchone()
        return None if row is None else SentSnapshot.model_validate(dict(row))

    def add_event(
        self,
        offer_id: int,
        kind: str,
        *,
        estado: str | None = None,
        note: str | None = None,
    ) -> None:
        """Append one timeline event and touch the offer's ``fecha_update``."""
        at = self._clock()
        self._conn.execute(
            "INSERT INTO offer_events (offer_id, at, kind, estado, note) "
            "VALUES (?, ?, ?, ?, ?)",
            (offer_id, at, kind, estado, note),
        )
        self._conn.execute(
            "UPDATE offers SET fecha_update = ? WHERE id = ?", (at, offer_id)
        )
        self._conn.commit()

    def record_estado_event(self, offer_id: int, estado: str) -> None:
        """Change the estado and log it on the timeline in ONE transaction.

        Either both writes land or neither does (a failing insert rolls the
        estado change back).
        """
        at = self._clock()
        try:
            self._conn.execute(
                "UPDATE offers SET estado = ?, fecha_update = ? WHERE id = ?",
                (estado, at, offer_id),
            )
            self._conn.execute(
                "INSERT INTO offer_events (offer_id, at, kind, estado, note) "
                "VALUES (?, ?, 'estado', ?, NULL)",
                (offer_id, at, estado),
            )
        except sqlite3.Error:
            self._conn.rollback()
            raise
        self._conn.commit()

    def list_events(self, offer_id: int) -> list[OfferEvent]:
        """The offer's timeline, oldest first."""
        rows = self._conn.execute(
            "SELECT * FROM offer_events WHERE offer_id = ? ORDER BY id", (offer_id,)
        ).fetchall()
        return [OfferEvent.model_validate(dict(r)) for r in rows]

    # --- interview prep packs ------------------------------------------------

    def add_prep_pack(
        self, offer_id: int, stage: str, *, html: str, json_text: str
    ) -> int:
        """Store a pack and log the ``preparación <stage>`` note, atomically."""
        at = self._clock()
        try:
            cursor = self._conn.execute(
                "INSERT INTO prep_packs (offer_id, stage, created_at, html, json) "
                "VALUES (?, ?, ?, ?, ?)",
                (offer_id, stage, at, html, json_text),
            )
            self._conn.execute(
                "INSERT INTO offer_events (offer_id, at, kind, estado, note) "
                "VALUES (?, ?, 'note', NULL, ?)",
                (offer_id, at, f"preparación {stage}"),
            )
            self._conn.execute(
                "UPDATE offers SET fecha_update = ? WHERE id = ?", (at, offer_id)
            )
        except sqlite3.Error:
            self._conn.rollback()
            raise
        self._conn.commit()
        return int(cursor.lastrowid or 0)

    def latest_prep_pack(self, offer_id: int, stage: str) -> PrepPackRow | None:
        row = self._conn.execute(
            "SELECT * FROM prep_packs WHERE offer_id = ? AND stage = ? "
            "ORDER BY id DESC LIMIT 1",
            (offer_id, stage),
        ).fetchone()
        if row is None:
            return None
        return PrepPackRow(
            id=row["id"],
            offer_id=row["offer_id"],
            stage=row["stage"],
            created_at=row["created_at"],
            html=row["html"],
            json_text=row["json"],
        )

    # --- STAR story bank -----------------------------------------------------

    def add_star(self, title: str, source_claim_ids: list[str]) -> int:
        """New story with every text field still missing; return its id."""
        cursor = self._conn.execute(
            "INSERT INTO star_stories (title, source_claim_ids, situation, task, "
            "action, result, use_for, complete, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (
                title,
                json.dumps(source_claim_ids),
                *([STAR_PLACEHOLDER] * 5),
                self._clock(),
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid or 0)

    def get_star(self, star_id: int) -> StarStory:
        """The story row; fail fast when it does not exist."""
        row = self._conn.execute(
            "SELECT * FROM star_stories WHERE id = ?", (star_id,)
        ).fetchone()
        if row is None:
            raise RepositoryError(f"star story {star_id} not found")
        return _row_to_star(row)

    def list_stars(self) -> list[StarStory]:
        """Every story, oldest first."""
        rows = self._conn.execute("SELECT * FROM star_stories ORDER BY id").fetchall()
        return [_row_to_star(r) for r in rows]

    def update_star(self, star_id: int, **fields: str | None) -> None:
        """Store the given fields as provided and recompute completeness."""
        unknown = set(fields) - _STAR_EDITABLE
        if unknown:
            raise RepositoryError(f"unknown star fields: {sorted(unknown)}")
        story = self.get_star(star_id)
        merged = {name: getattr(story, name) for name in STAR_TEXT_FIELDS}
        merged.update({k: v for k, v in fields.items() if k in STAR_TEXT_FIELDS})
        complete = all(
            v is not None and v != STAR_PLACEHOLDER and v.strip()
            for v in merged.values()
        )
        columns = [*fields, "complete", "updated_at"]
        assignments = ", ".join(f"{c} = ?" for c in columns)
        self._conn.execute(
            f"UPDATE star_stories SET {assignments} WHERE id = ?",
            (*fields.values(), int(complete), self._clock(), star_id),
        )
        self._conn.commit()

    def delete_star(self, star_id: int) -> None:
        """Remove a story (no error when it is already gone)."""
        self._conn.execute("DELETE FROM star_stories WHERE id = ?", (star_id,))
        self._conn.commit()

    def diff_seen(self, beacon_id: int, job_ids: list[str]) -> list[str]:
        """Return the job ids never seen for this beacon and record them."""
        known = {
            row[0]
            for row in self._conn.execute(
                "SELECT job_id FROM seen_job_ids WHERE beacon_id = ?", (beacon_id,)
            )
        }
        new_ids = [job_id for job_id in dict.fromkeys(job_ids) if job_id not in known]
        first_seen = self._clock()
        self._conn.executemany(
            "INSERT INTO seen_job_ids (beacon_id, job_id, first_seen_at) VALUES (?, ?, ?)",
            [(beacon_id, job_id, first_seen) for job_id in new_ids],
        )
        self._conn.commit()
        return new_ids

    def bump_keyword(self, profile: str, keyword: str) -> None:
        """Upsert the demand log: frequency +1, last_seen refreshed."""
        self._conn.execute(
            "INSERT INTO keyword_registry (profile, keyword, frequency, last_seen) "
            "VALUES (?, ?, 1, ?) ON CONFLICT(profile, keyword) DO UPDATE SET "
            "frequency = frequency + 1, last_seen = excluded.last_seen",
            (profile, keyword, self._clock()),
        )
        self._conn.commit()

    def sent_offers(self) -> list[OfferRow]:
        """All rows with fecha_envio set, in id order (xlsx export source)."""
        rows = self._conn.execute(
            "SELECT * FROM offers WHERE fecha_envio IS NOT NULL ORDER BY id"
        ).fetchall()
        return [_row_to_offer(row) for row in rows]

    # --- Slice 8a additions: service-layer use-cases (analysis, keywords, CV) ---

    def save_match_result(
        self, offer_id: int, *, score: int, threshold: int, breakdown_json: str
    ) -> None:
        """Persist the latest analysis; one row per offer (PK offer_id)."""
        self._conn.execute(
            "INSERT OR REPLACE INTO match_results "
            "(offer_id, score, threshold, breakdown_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (offer_id, score, threshold, breakdown_json, self._clock()),
        )
        self._conn.commit()

    def latest_match(self, offer_id: int) -> MatchRow | None:
        """The stored analysis for the offer, or ``None`` when unanalyzed."""
        row = self._conn.execute(
            "SELECT * FROM match_results WHERE offer_id = ?", (offer_id,)
        ).fetchone()
        if row is None:
            return None
        return MatchRow.model_validate(dict(zip(row.keys(), row, strict=True)))

    def save_judge_result(
        self, offer_id: int, *, score: int, verdict: str, judgment_json: str
    ) -> None:
        """Persist the latest LLM judgment; one row per offer (PK offer_id)."""
        self._conn.execute(
            "INSERT OR REPLACE INTO judge_results "
            "(offer_id, score, verdict, judgment_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (offer_id, score, verdict, judgment_json, self._clock()),
        )
        self._conn.commit()

    def clear_judge_result(self, offer_id: int) -> None:
        """Delete the stored judgment (llm-offer-judge robustness follow-up).

        A re-analysis whose judge call fails/falls back must never leave a
        STALE judgment from an earlier successful attempt looking current —
        :meth:`latest_judge_result` reads this table directly, so clearing
        it here (called from ``JobService.analyze``'s judge-failure branch)
        is the single source of truth fix. A no-op when there is no row.
        """
        self._conn.execute("DELETE FROM judge_results WHERE offer_id = ?", (offer_id,))
        self._conn.commit()

    def latest_judge_result(self, offer_id: int) -> JudgeRow | None:
        """The stored judgment for the offer, or ``None`` when never judged."""
        row = self._conn.execute(
            "SELECT * FROM judge_results WHERE offer_id = ?", (offer_id,)
        ).fetchone()
        if row is None:
            return None
        return JudgeRow.model_validate(dict(zip(row.keys(), row, strict=True)))

    def analysed_offer_ids_since(self, day: str) -> list[int]:
        """Ids of ``analizada`` offers whose latest analysis is dated ``day`` or later.

        The analysis date is ``match_results.created_at`` (written by every
        successful ``analyze``; the judge row is written in the same call).
        ``day`` is an ISO date; a coarse day-level filter, callers refine it.
        """
        rows = self._conn.execute(
            "SELECT DISTINCT o.id FROM offers o JOIN match_results m ON m.offer_id = o.id "
            "WHERE o.estado = 'analizada' AND substr(m.created_at, 1, 10) >= ? "
            "ORDER BY o.id",
            (day,),
        ).fetchall()
        return [int(row[0]) for row in rows]

    def record_offer_keywords(
        self, offer_id: int, profile: str, keywords: list[tuple[str, str]]
    ) -> int:
        """§5 demand log: bump each keyword at most once per offer.

        ``keywords`` are (keyword, status) pairs; the registry CHECK validates
        the status. Keywords already linked to this offer via ``offer_keywords``
        are skipped, so re-analysis never double-counts. Returns the number of
        newly recorded keywords.
        """
        recorded = 0
        for keyword, status in keywords:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO offer_keywords (offer_id, profile, keyword) "
                "VALUES (?, ?, ?)",
                (offer_id, profile, keyword),
            )
            if cursor.rowcount == 1:
                self._conn.execute(
                    "INSERT INTO keyword_registry (profile, keyword, frequency, "
                    "last_seen, status) VALUES (?, ?, 1, ?, ?) "
                    "ON CONFLICT(profile, keyword) DO UPDATE SET "
                    "frequency = frequency + 1, last_seen = excluded.last_seen, "
                    "status = excluded.status",
                    (profile, keyword, self._clock(), status),
                )
                recorded += 1
        self._conn.commit()
        return recorded

    def record_cv(
        self, offer_id: int, *, cv_base: str, cv_path: str, cambios: str
    ) -> None:
        """Record a generated CV: cv_base + cv_adaptado + cambios, estado→preparada."""
        self._conn.execute(
            "UPDATE offers SET cv_base = ?, cv_adaptado = ?, cambios = ?, "
            "estado = 'preparada', fecha_update = ? WHERE id = ?",
            (cv_base, cv_path, cambios, self._clock(), offer_id),
        )
        self._conn.commit()

    def recent_offers(self, limit: int = 10) -> list[OfferRow]:
        """The most recent offers, newest first (bot /status listing source)."""
        rows = self._conn.execute(
            "SELECT * FROM offers ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_offer(row) for row in rows]

    def all_offers(self) -> list[OfferRow]:
        """Every offer row, oldest first (rejudge-backlog selection source).

        Unlike :meth:`recent_offers`, this is unbounded and ascending — the
        backlog re-judge use-case needs the FULL set to apply its own
        selection rules (state/text/date/already-judged) before any
        ``--limit`` truncation, not just the most recent N.
        """
        rows = self._conn.execute("SELECT * FROM offers ORDER BY id").fetchall()
        return [_row_to_offer(row) for row in rows]

    def tracker_offers(self) -> list[OfferRow]:
        """Every offer, newest first, ``match`` falling back to the analysis score.

        Source of the synced tracker xlsx: ``offers.match`` is only stamped on
        mark_sent, so analyzed rows would otherwise show an empty match. One
        LEFT JOIN over ``match_results`` (PK offer_id) — no N+1.
        """
        rows = self._conn.execute(
            "SELECT o.*, m.score AS fallback_score FROM offers o "
            "LEFT JOIN match_results m ON m.offer_id = o.id ORDER BY o.id DESC"
        ).fetchall()
        offers: list[OfferRow] = []
        for row in rows:
            offer = _row_to_offer(row)
            if offer.match is None and row["fallback_score"] is not None:
                offer = offer.model_copy(update={"match": int(row["fallback_score"])})
            offers.append(offer)
        return offers

    # --- Manual history import (import-history) -------------------------------

    def commit(self) -> None:
        """Commit pending writes (import-history applies a whole run at once)."""
        self._conn.commit()

    def rollback(self) -> None:
        """Discard pending writes (``--dry-run`` plans on the real rows)."""
        self._conn.rollback()

    def _find_by_linkedin_job_id(self, job_id: str) -> OfferRow | None:
        # Stored URLs differ by host prefix / trailing slash, so match on the
        # numeric id (LIKE narrows, job_id_from_url confirms).
        rows = self._conn.execute(
            "SELECT * FROM offers WHERE url_oferta LIKE ? ORDER BY id",
            (f"%{job_id}%",),
        ).fetchall()
        for row in rows:
            offer = _row_to_offer(row)
            if (
                offer.url_oferta is not None
                and source_from_url(offer.url_oferta) == "linkedin"
                and job_id_from_url(offer.url_oferta) == job_id
            ):
                return offer
        return None

    def _find_by_marker(self, marker: str) -> OfferRow | None:
        row = self._conn.execute(
            "SELECT * FROM offers WHERE instr(COALESCE(notas, ''), ?) > 0 "
            "ORDER BY id LIMIT 1",
            (marker,),
        ).fetchone()
        return None if row is None else _row_to_offer(row)

    def import_history_record(
        self, rec: HistoryRecord
    ) -> Literal["inserted", "merged", "skipped"]:
        """Apply one manual-log record WITHOUT committing; see ``commit``.

        1. LinkedIn job id already stored -> merge (fill only empty fields,
           advance a pre-send estado, append the ``[log #N]`` marker), or skip
           when that marker is already there.
        2. Else a row carrying the marker -> skip (re-runs, URL-less records).
        3. Else insert a new offer.
        """
        marker = log_marker(rec.log_id)
        job_id = (
            job_id_from_url(rec.url_oferta)
            if rec.url_oferta and source_from_url(rec.url_oferta) == "linkedin"
            else None
        )
        existing = self._find_by_linkedin_job_id(job_id) if job_id else None
        if existing is not None:
            if marker in (existing.notas or ""):
                return "skipped"
            self._merge_history(existing, rec, marker)
            return "merged"
        if self._find_by_marker(marker) is not None:
            return "skipped"
        self._insert_history(rec, marker, job_id)
        return "inserted"

    def _merge_history(
        self, existing: OfferRow, rec: HistoryRecord, marker: str
    ) -> None:
        updates: dict[str, object] = {}
        for column in (
            "fecha_envio",
            "match",
            "cv_base",
            "cv_adaptado",
            "sueldo_rango",
            "contacto",
            "fecha_update",
        ):
            incoming = getattr(rec, column)
            current = getattr(existing, column)
            if incoming is not None and (current is None or current == ""):
                updates[column] = incoming
        if existing.estado in PRE_SEND_ESTADOS and rec.estado not in PRE_SEND_ESTADOS:
            updates["estado"] = rec.estado
        base = (existing.notas or "").strip() or rec.notas
        updates["notas"] = f"{base} {marker}" if base else marker
        assignments = ", ".join(f"{column} = ?" for column in updates)
        self._conn.execute(
            f"UPDATE offers SET {assignments} WHERE id = ?",
            (*updates.values(), existing.id),
        )

    def _insert_history(
        self, rec: HistoryRecord, marker: str, job_id: str | None
    ) -> None:
        url = rec.url_oferta
        if job_id is not None:
            url = canonical_url("linkedin", job_id)
        is_linkedin = url is not None and source_from_url(url) == "linkedin"
        notas = f"{rec.notas} {marker}" if rec.notas else marker
        self._conn.execute(
            "INSERT INTO offers (fecha_envio, empresa, posicion, origen, "
            "url_oferta, cv_base, cv_adaptado, match, sueldo_rango, estado, "
            "fecha_update, contacto, notas, source_data) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rec.fecha_envio,
                rec.empresa,
                rec.posicion,
                "linkedin" if is_linkedin else "otro",
                url,
                rec.cv_base,
                rec.cv_adaptado,
                rec.match,
                rec.sueldo_rango,
                rec.estado,
                rec.fecha_update or rec.fecha_envio or self._clock(),
                rec.contacto,
                notas,
                f"Importado de ofertas_log.md #{rec.log_id}",
            ),
        )

    def keyword_rows(self, profile: str | None = None) -> list[KeywordRow]:
        """Demand-log rows (optionally one profile), highest frequency first."""
        query = (
            "SELECT profile, keyword, frequency, last_seen, status "
            "FROM keyword_registry"
        )
        params: tuple[str, ...] = ()
        if profile is not None:
            query += " WHERE profile = ?"
            params = (profile,)
        query += " ORDER BY frequency DESC, keyword"
        rows = self._conn.execute(query, params).fetchall()
        return [
            KeywordRow(
                profile=row[0],
                keyword=row[1],
                frequency=row[2],
                last_seen=row[3],
                status=row[4],
            )
            for row in rows
        ]

    # --- Slice 8b additions: beacons (job-discovery spec, design D3/D11) ---

    def create_beacon(self, name: str, url: str) -> int:
        """Persist a new beacon: enabled, active, never checked; return its id."""
        cursor = self._conn.execute(
            "INSERT INTO beacons (name, url) VALUES (?, ?)", (name, url)
        )
        if cursor.lastrowid is None:
            raise RepositoryError("insert into beacons did not return an id")
        self._conn.commit()
        return cursor.lastrowid

    def _row_to_beacon(self, row: sqlite3.Row) -> BeaconRow:
        return BeaconRow.model_validate(dict(zip(row.keys(), row, strict=True)))

    def get_beacon(self, beacon_id: int) -> BeaconRow:
        """Return the beacon row; fail fast when it does not exist."""
        row = self._conn.execute(
            "SELECT * FROM beacons WHERE id = ?", (beacon_id,)
        ).fetchone()
        if row is None:
            raise RepositoryError(f"beacon {beacon_id} not found")
        return self._row_to_beacon(row)

    def list_beacons(self) -> list[BeaconRow]:
        """All beacons in id order (heartbeat scan source)."""
        rows = self._conn.execute("SELECT * FROM beacons ORDER BY id").fetchall()
        return [self._row_to_beacon(row) for row in rows]

    def resume_beacon(self, beacon_id: int) -> None:
        """Resume a beacon: enabled + active, walls reset, due immediately.

        Also the way back from ``disable`` — resume is the single reactivation
        path, so ``enabled`` returns to 1 here.
        """
        self._conn.execute(
            "UPDATE beacons SET enabled = 1, status = 'active', "
            "consecutive_walls = 0, next_check_after = NULL WHERE id = ?",
            (beacon_id,),
        )
        self._conn.commit()

    def disable_beacon(self, beacon_id: int) -> None:
        """Disable a beacon: it stays in the list but is never checked again."""
        self._conn.execute("UPDATE beacons SET enabled = 0 WHERE id = ?", (beacon_id,))
        self._conn.commit()

    def record_beacon_check(
        self,
        beacon_id: int,
        *,
        checked_at: str,
        next_check_after: str,
        status: str,
        consecutive_walls: int,
        last_wall_kind: str | None,
        checks_today: int,
        checks_date: str,
    ) -> None:
        """Persist the outcome of one beacon check (state + due time + daily caps)."""
        self._conn.execute(
            "UPDATE beacons SET last_checked_at = ?, next_check_after = ?, "
            "status = ?, consecutive_walls = ?, last_wall_kind = ?, "
            "checks_today = ?, checks_date = ? WHERE id = ?",
            (
                checked_at,
                next_check_after,
                status,
                consecutive_walls,
                last_wall_kind,
                checks_today,
                checks_date,
                beacon_id,
            ),
        )
        self._conn.commit()

    # --- Notification pacing (notification-pacing D3): retry bookkeeping -------

    def record_analyze_failure(self, offer_id: int, *, next_analyze_at: str) -> int:
        """Bump ``analyze_attempts`` and set the next due time; return attempts."""
        cursor = self._conn.execute(
            "UPDATE offers SET analyze_attempts = analyze_attempts + 1, "
            "next_analyze_at = ?, fecha_update = ? WHERE id = ?",
            (next_analyze_at, self._clock(), offer_id),
        )
        if cursor.rowcount != 1:
            raise RepositoryError(f"offer {offer_id} not found")
        self._conn.commit()
        row = self._conn.execute(
            "SELECT analyze_attempts FROM offers WHERE id = ?", (offer_id,)
        ).fetchone()
        return int(row[0])

    def clear_analyze_retry(self, offer_id: int) -> None:
        """Reset the retry state (attempts=0, next=NULL) — the exhaustion latch."""
        self._conn.execute(
            "UPDATE offers SET analyze_attempts = 0, next_analyze_at = NULL "
            "WHERE id = ?",
            (offer_id,),
        )
        self._conn.commit()

    def defer_analyze_retry(self, offer_id: int, *, next_analyze_at: str) -> None:
        """Mark a row retry-pending WITHOUT touching the attempt counter.

        Shutdown deferral (notification-pacing D9): rows the worker never got
        to (queued or mid-flight) are handed back to the heartbeat sweep with
        ``next_analyze_at = now``; a shutdown is not a failure, so the counter
        stays as-is.
        """
        self._conn.execute(
            "UPDATE offers SET next_analyze_at = ? WHERE id = ?",
            (next_analyze_at, offer_id),
        )
        self._conn.commit()

    def list_retry_pending(self, now_iso: str, max_attempts: int) -> list[OfferRow]:
        """Retry-pending rows due now: not sent, ≤ max attempts, next <= now.

        Exact sweep filter (notification-pacing D4): ``next_analyze_at`` set
        and due, ``analyze_attempts <= max_attempts`` (0 included — shutdown
        deferral parks rows with the counter untouched), and only ``enviada``
        rows excluded so a sent offer is never re-driven. Rows cleared on
        success (attempts=0, next=NULL) never match.
        """
        rows = self._conn.execute(
            "SELECT * FROM offers WHERE estado != 'enviada' "
            "AND analyze_attempts <= ? "
            "AND next_analyze_at IS NOT NULL AND next_analyze_at <= ? "
            "ORDER BY id",
            (max_attempts, now_iso),
        ).fetchall()
        return [_row_to_offer(row) for row in rows]
