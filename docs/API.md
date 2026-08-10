# API Reference

Complete reference for the HTTP ingest API, debug endpoints, and MCP tools.

## Authentication model

| Endpoint group | Auth mechanism | Token |
|---|---|---|
| `GET /health` | None | — |
| `POST /ingest/health`, `POST /ingest/screentime` | `Authorization: Bearer <DEVICE_TOKEN>` header + optional `X-Signature` HMAC | `DEVICE_TOKEN` |
| `POST /ingest/echo` | None | — |
| `POST /mcp` | `Authorization: Bearer <MCP_TOKEN>` header | `MCP_TOKEN` |
| `GET /debug*` | `?token=<MCP_TOKEN>` query parameter | `MCP_TOKEN` |

All token comparisons use `hmac.compare_digest` (timing-safe). A wrong or missing token returns `401` with no body.

---

## Health ingest contract

### `POST /ingest/health`

Receives a Health Connect payload from the companion app.

**Headers:**
- `Content-Type: application/json; charset=utf-8`
- `Authorization: Bearer <DEVICE_TOKEN>`
- `X-Signature: sha256=<hex>` — HMAC-SHA256 of the raw body, if `WEBHOOK_HMAC_SECRET` is set

**Body** (top-level object — no `device` field here):
```json
{
  "timestamp": "2026-08-09T12:00:00Z",
  "app_version": "1.4.0",
  "source": "health_connect",
  "steps": [{"count": 1234, "start_time": "...", "end_time": "..."}],
  "heart_rate": [{"bpm": 72, "time": "..."}],
  "sleep": [{"session_end_time": "...", "duration_seconds": 28800,
             "stages": [{"stage": "deep", "start_time": "...", "end_time": "...",
                         "duration_seconds": 7200}]}],
  "_diagnostics": {
    "steps": {"permission_granted": true, "page_count": 3,
              "raw_record_count": 2841, "filtered_record_count": 1000,
              "min_time": "...", "max_time": "...", "last_sync": "...", "error": null}
  }
}
```

**Processing order:** read raw body → token check → signature check → store raw payload → parse JSON → validate envelope → normalize → upsert events/stages/diagnostics → recalc rollups → respond.

**Key rules:**
- Arrays with zero records are **absent** from the JSON entirely (not empty). The server reads every type via `payload.get(key) or []`.
- `heart_rate` is a flat list of samples, not records. One element = one measurement.
- A sleep session has no `start_time`. The server computes `start = session_end_time − duration_seconds`.
- No record has an ID. Deduplication is content-hash based.

**Response codes:**

| Situation | Code | Cursor behavior |
|---|---|---|
| All records stored | `200` | App advances cursor |
| Some records invalid, rest stored | `200` + `errors[]` | App advances cursor |
| Bad token or signature | `401` | App does not retry (permanent) |
| Body is not JSON | `400` | App does not retry (permanent) |
| Database write error | `500` | App retries; cursor does **not** move |

**Success body:**
```json
{"payload_id": 42, "received": 1247, "stored": 1203, "duplicates": 44,
 "errors": [], "rollups_updated": ["2026-08-08", "2026-08-09"]}
```

### The 25 health types

| JSON key | Kind | Value field | Cumulative |
|---|---|---|---|
| `steps` | INTERVAL | `count` | yes |
| `distance` | INTERVAL | `meters` | yes |
| `active_calories` | INTERVAL | `calories` | yes |
| `total_calories` | INTERVAL | `calories` | yes |
| `exercise` | INTERVAL | `type`, `duration_seconds` | no |
| `hydration` | INTERVAL | `liters` | yes |
| `nutrition` | INTERVAL | `calories`, `protein_grams`, `carbs_grams`, `fat_grams` (all optional) | yes |
| `mindfulness` | INTERVAL | `title` (nullable), `duration_seconds` | no |
| `menstruation_period` | INTERVAL | — | no |
| `sleep` | SESSION | `session_end_time`, `duration_seconds`, `stages[]` | no |
| `heart_rate` | INSTANT | `bpm` | no |
| `resting_heart_rate` | INSTANT | `bpm` | no |
| `heart_rate_variability` | INSTANT | `heart_rate_variability_millis` | no |
| `blood_pressure` | INSTANT | `systolic`, `diastolic` | no |
| `blood_glucose` | INSTANT | `mmol_per_liter` | no |
| `oxygen_saturation` | INSTANT | `percentage` | no |
| `respiratory_rate` | INSTANT | `rate` | no |
| `body_temperature` | INSTANT | `celsius` | no |
| `weight` | INSTANT | `kilograms` | no |
| `height` | INSTANT | `meters` | no |
| `body_fat` | INSTANT | `percentage` | no |
| `lean_body_mass` | INSTANT | `kilograms` | no |
| `bone_mass` | INSTANT | `kilograms` | no |
| `body_water_mass` | INSTANT | `kilograms` | no |
| `menstruation_flow` | INSTANT | `flow` (`light`/`medium`/`heavy`/`unknown`) | no |

Sleep `stage` values: `unknown`, `awake`, `sleeping`, `out_of_bed`, `light`, `deep`, `rem`, `awake_in_bed`.

---

## Screen time contract

### `POST /ingest/screentime`

Receives a 7-day screen-time payload. Unlike health, this one **has** a `device` field.

**Body:**
```json
{
  "timestamp": "...", "app_version": "1.4.0",
  "device": "Google Pixel 8",
  "source": "screen_time",
  "screen_time": [{"date": "2026-08-09", "total_screen_time_minutes": 180,
                   "apps": [{"package": "...", "name": "...", "minutes": 45, "last_used": "..."}]}]
}
```

Written as an upsert by `date`; the app list for each day is fully replaced. Auth is the same as `/ingest/health` (token + signature).

---

## Echo endpoint

### `POST /ingest/echo`

Accepts anything, validates nothing, stores the raw body with `source='echo'`, always returns `200`:

```json
{"ok": true, "payload_id": 7, "byte_size": 2048}
```

Use this to capture the app's raw format without risking the ingest pipeline. Point the app at `/ingest/echo` temporarily, sync once, then inspect the body in the `/debug` echo panel.

---

## Debug endpoints

All require `?token=<MCP_TOKEN>`. Missing or wrong token → `401` with no body.

| Path | Returns |
|---|---|
| `GET /debug` | The inspector HTML page (vanilla JS, no CDN, mobile-responsive) |
| `GET /debug/api/payloads?limit=20` | Recent payloads: id, time, source, app_version, size, `signature_valid`, event count, parse error |
| `GET /debug/api/payload/{id}` | Full raw body + parsed events + sleep stages + result counts |
| `GET /debug/api/diagnostics?payload_id=N` | The `_diagnostics` block for one payload, per type |
| `GET /debug/api/coverage?days=30` | Day × type matrix of event counts |
| `GET /debug/api/rollup/{local_date}` | Daily rollup + **naive sums** (pre-dedup) side by side |
| `GET /debug/api/stats` | Total events, type counts, date range, last ingest time |

The `/debug/api/rollup/{date}` endpoint is the key tool for diagnosing double-counting: it shows the naive sum of all records next to the deduplicated rollup. Their ratio measures source overlap.

---

## MCP tools

All tools are invoked over the Streamable HTTP transport at `POST /mcp` with `Authorization: Bearer <MCP_TOKEN>`. Every response is an `object` with a `caveats: string[]` field. Responses are capped at 400 objects.

### `daily_summary`

Daily rollups for a date range.

**Params:**
| Param | Type | Required | Notes |
|---|---|---|---|
| `date_from` | string | yes | `YYYY-MM-DD` |
| `date_to` | string | yes | `YYYY-MM-DD` (inclusive) |
| `metrics` | string[] | no | Filter to specific metrics (e.g. `["steps", "sleep_in_bed_s"]`) |

**Example request:**
```json
{"jsonrpc":"2.0","id":2,"method":"tools/call",
 "params":{"name":"daily_summary","arguments":{"date_from":"2026-08-01","date_to":"2026-08-09"}}}
```

**Example response (abridged):**
```json
{"days":[{"local_date":"2026-08-01","steps":8421,"sleep_in_bed_s":28800,"sleep_asleep_s":21600,"hr_avg":72}],
 "coverage":{"2026-08-01":{"steps":"ok","sleep_in_bed_s":"ok","hr_avg":"ok","distance_m":"absent"}},
 "caveats":["partial coverage for 2026-08-01: missing distance_m, active_kcal, ..."]}
```

Available metrics: `steps`, `distance_m`, `active_kcal`, `total_kcal`, `sleep_in_bed_s`, `sleep_asleep_s`, `hr_avg`, `hr_min`, `hr_max`, `hr_resting`, `weight_kg`. Days with no data return `null` fields (never `0`).

### `metric_series`

Time series for one metric with day/week bucketing.

**Params:**
| Param | Type | Required | Notes |
|---|---|---|---|
| `metric` | string | yes | One of the metric names above |
| `date_from` | string | yes | `YYYY-MM-DD` |
| `date_to` | string | yes | `YYYY-MM-DD` (inclusive) |
| `bucket` | string | no | `"day"` (default) or `"week"` |

**Example response:**
```json
{"metric":"steps","unit":"count",
 "points":[{"date":"2026-08-01","value":8421},{"date":"2026-08-02","value":9102}],
 "caveats":["data present for all days in range"]}
```

If a range exceeds 400 days with `bucket="day"`, the tool switches to weekly buckets and notes it in `caveats`. Weekly buckets start on Monday; cumulative metrics are summed, non-cumulative metrics are averaged.

### `sleep_detail`

Detailed sleep sessions with stages. Sessions are attributed by `end_utc` (a session ending at 06:00 belongs to that local date, not the previous one).

**Params:**
| Param | Type | Required | Notes |
|---|---|---|---|
| `date_from` | string | yes | `YYYY-MM-DD` |
| `date_to` | string | yes | `YYYY-MM-DD` (inclusive) |

**Example response (abridged):**
```json
{"sessions":[{"date":"2026-08-01","start_utc":"...","end_utc":"...",
              "time_in_bed_s":28800,"time_asleep_s":21600,
              "stages":[{"stage":"deep","start_utc":"...","end_utc":"...","seconds":7200}]}],
 "caveats":["all sessions have stage data"]}
```

`time_in_bed_s` includes `awake` and `awake_in_bed` stages; `time_asleep_s` excludes them.

### `get_sync_health`

Per-type sync status for the last N days.

**Params:**
| Param | Type | Required | Notes |
|---|---|---|---|
| `days` | int | no | Default 30 |

**Example response (abridged):**
```json
{"metrics":[{"data_type":"steps","last_event_utc":"2026-08-09T...","coverage_days":9,"missing_days":0,"last_error":null}],
 "period_days":30,
 "caveats":["steps: missing 21 of 30 days"]}
```

Reports per type: last event timestamp, coverage days, missing days, and the last error from `_diagnostics`.

### `inspect_last_payload`

Metadata and diagnostics of the most recent raw payload. No parameters.

**Example response (abridged):**
```json
{"payload_id":42,"received_at":"...","app_version":"1.4.0","source":"health_connect",
 "byte_size":48213,"signature_valid":true,
 "diagnostics":[{"data_type":"steps","permission_granted":true,"page_count":3,"raw_record_count":2841,"filtered_record_count":1000,"error":null}],
 "caveats":["last payload is healthy"]}
```
