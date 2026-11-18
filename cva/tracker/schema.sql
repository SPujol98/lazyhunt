-- Tracker schema (design D4/D5). Applied once when PRAGMA user_version < 1;
-- future schema changes are ordered user_version migrations, no framework.
CREATE TABLE offers (                 -- §1 verbatim, 17 fields; ALL offers stored
  id INTEGER PRIMARY KEY,
  fecha_envio TEXT, empresa TEXT, posicion TEXT,
  origen TEXT CHECK(origen IN ('linkedin','infojobs','referido','web_empresa','otro')),
  url_oferta TEXT,
  cv_base TEXT CHECK(cv_base IN ('AI_ES','AI_EN','DS_ES','DS_EN')),
  cv_adaptado TEXT, cambios TEXT,
  match INTEGER CHECK(match BETWEEN 0 AND 100),
  sueldo_rango TEXT, sueldo_ofrecido TEXT,
  estado TEXT NOT NULL DEFAULT 'pendiente_analisis' CHECK(estado IN (
    'pendiente_analisis','analizada','preparada','descartada',        -- pre-send (D5)
    'enviada','screening','entrevista_tecnica','entrevista_final',
    'oferta','aceptada','rechazada','retirada','sin_respuesta')),     -- §2 verbatim
  fecha_update TEXT, contacto TEXT, notas TEXT,
  source_data TEXT NOT NULL);
CREATE TABLE beacons (
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, url TEXT NOT NULL,
  enabled INTEGER DEFAULT 1, status TEXT DEFAULT 'active'
    CHECK(status IN ('active','degraded','paused')),
  consecutive_walls INTEGER DEFAULT 0, next_check_after TEXT,
  last_checked_at TEXT, last_wall_kind TEXT);
CREATE TABLE seen_job_ids (
  beacon_id INTEGER REFERENCES beacons(id), job_id TEXT NOT NULL,
  first_seen_at TEXT, notified_at TEXT, PRIMARY KEY(beacon_id, job_id));
CREATE TABLE keyword_registry (       -- §5; auto-append target (D6)
  profile TEXT, keyword TEXT, frequency INTEGER DEFAULT 1, last_seen TEXT,
  status TEXT CHECK(status IN ('✓','~','✗')), PRIMARY KEY(profile, keyword));
CREATE TABLE offer_keywords (
  offer_id INTEGER REFERENCES offers(id), profile TEXT, keyword TEXT,
  PRIMARY KEY(offer_id, profile, keyword));
CREATE TABLE extraction_cache (
  cache_key TEXT PRIMARY KEY, extraction_json TEXT NOT NULL, model TEXT, created_at TEXT);
CREATE TABLE match_results (
  offer_id INTEGER PRIMARY KEY REFERENCES offers(id),
  score INTEGER, threshold INTEGER, breakdown_json TEXT NOT NULL, created_at TEXT);
