-- Health Hub database schema. Idempotent DDL.
-- See SPEC section 5.1.

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

-- L0: raw payloads, append-only, never deleted.
CREATE TABLE IF NOT EXISTS raw_payloads (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  received_at       TEXT NOT NULL,
  source            TEXT NOT NULL,
  app_version       TEXT,
  device            TEXT,
  payload_timestamp TEXT,
  signature_valid   INTEGER NOT NULL,
  byte_size         INTEGER NOT NULL,
  body_json         TEXT NOT NULL,
  parse_error       TEXT
);
DROP INDEX IF EXISTS idx_raw_received;

-- L0b: parsed _diagnostics block.
CREATE TABLE IF NOT EXISTS sync_diagnostics (
  payload_id            INTEGER NOT NULL REFERENCES raw_payloads(id),
  data_type             TEXT NOT NULL,
  permission_granted    INTEGER,
  page_count            INTEGER,
  raw_record_count      INTEGER,
  filtered_record_count INTEGER,
  min_time              TEXT,
  max_time              TEXT,
  last_sync             TEXT,
  error                 TEXT,
  PRIMARY KEY (payload_id, data_type)
);

-- L1: normalized events.
CREATE TABLE IF NOT EXISTS events (
  event_id      TEXT PRIMARY KEY,
  data_type     TEXT NOT NULL,
  start_utc     TEXT NOT NULL,
  end_utc       TEXT NOT NULL,
  value_num     REAL,
  value_json    TEXT NOT NULL,
  origin        TEXT,
  payload_id    INTEGER NOT NULL REFERENCES raw_payloads(id),
  first_seen_at TEXT NOT NULL
);
DROP INDEX IF EXISTS idx_events_type_start;
DROP INDEX IF EXISTS idx_events_start;
CREATE INDEX IF NOT EXISTS idx_events_payload ON events(payload_id);
CREATE INDEX IF NOT EXISTS idx_events_type_instant_start
  ON events(data_type, utc_instant(start_utc), event_id);
CREATE INDEX IF NOT EXISTS idx_events_type_instant_end
  ON events(data_type, utc_instant(end_utc), event_id);
CREATE INDEX IF NOT EXISTS idx_events_instant_start
  ON events(utc_instant(start_utc), data_type, event_id);

CREATE TABLE IF NOT EXISTS sleep_stages (
  session_event_id TEXT NOT NULL REFERENCES events(event_id),
  stage            TEXT NOT NULL,
  start_utc        TEXT NOT NULL,
  end_utc          TEXT NOT NULL,
  seconds          INTEGER NOT NULL,
  PRIMARY KEY (session_event_id, stage, start_utc)
);

CREATE TABLE IF NOT EXISTS screen_time_days (
  local_date    TEXT PRIMARY KEY,
  total_minutes INTEGER NOT NULL,
  payload_id    INTEGER NOT NULL REFERENCES raw_payloads(id),
  updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS screen_time_apps (
  local_date TEXT NOT NULL,
  package    TEXT NOT NULL,
  name       TEXT,
  minutes    INTEGER NOT NULL,
  last_used  TEXT,
  PRIMARY KEY (local_date, package)
);

-- L2: daily rollups by LOCAL date.
CREATE TABLE IF NOT EXISTS daily_rollups (
  local_date      TEXT PRIMARY KEY,
  steps           INTEGER,
  distance_m      REAL,
  active_kcal     REAL,
  total_kcal      REAL,
  sleep_in_bed_s  INTEGER,
  sleep_asleep_s  INTEGER,
  hr_avg          REAL,
  hr_min          INTEGER,
  hr_max          INTEGER,
  hr_resting      INTEGER,
  weight_kg       REAL,
  coverage_json   TEXT NOT NULL,
  computed_at     TEXT NOT NULL
);
