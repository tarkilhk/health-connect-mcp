# Health Connect MCP architecture

The server archives Android Health Connect uploads and exposes individual stored records for all 34 registered types. Daily summaries, metric series and derived sleep detail are additional views. See [API.md](API.md) for field contracts and examples; [SPEC.md](../SPEC.md) records the implementation target.

## Data flow

```text
Android upload → authenticated ingestion → raw payload archive
                                        → normalized events and stages
                                        → optional daily summary cache

MCP /mcp → query-only connection → individual records or derived views
Browser /debug → query-only connection → payload and storage inspection
```

`contract/registry.py` is the type catalog. It defines record kinds, primary value fields, units, cumulative behavior and value schemas. Normalization, MCP discovery and the record query surface derive from it. Types with no established field contract expose their stored JSON with an interpretation caveat.

## Ingestion

`api/routes_ingest.py` handles HTTP authentication and selects the payload format. `ingest/service.py` owns the shared lifecycle; format adapters in `ingest/persist.py` normalize and persist data.

1. Read the body and verify the device bearer token. An invalid token returns 401 before storage.
2. Verify the optional HMAC signature and commit the raw payload, including its signature status.
3. Reject an invalid signature with 401. Parse JSON and validate the selected envelope; invalid input returns 400 with a stored parse error.
4. Normalize records using pure functions. Malformed individual records are reported while valid records proceed.
5. Write events, stages, diagnostics and affected daily rollups in one transaction.
6. Return 200 only after commit. A persistence failure returns 500 and rolls back normalized writes. The raw input remains archived.

Both health endpoints use this pipeline. `/ingest/hc-webhook` has its own active input contract and normalizer, including HRV field and menstrual category conversion. `/ingest/screentime` upserts days and replaces their app lists. The unauthenticated `/ingest/echo` endpoint archives diagnostic uploads.

## Database ownership

`db/store.py` owns a SQLite file and two WAL connections. A query-only reader serves MCP and debug requests. A private writer serves ingestion under a lock held for the complete request lifecycle, including raw-input commits. One request cannot commit or roll back another request's writes.

Failed or cancelled writes finish rollback before releasing the lock, including repeated cancellation. Readers see committed data. `DATABASE_PATH` must name a file; independent in-memory connections cannot provide this shared archive. The process runs one uvicorn worker.

The schema has four layers:

| Layer | Tables | Purpose |
|---|---|---|
| Raw input | `raw_payloads` | Append-only upload archive and parse/signature status |
| Diagnostics | `sync_diagnostics` | Permissions, record counts, sync times and source errors |
| Stored records | `events`, `sleep_stages`, `screen_time_days`, `screen_time_apps` | Normalized records and screen-time state |
| Derived cache | `daily_rollups` | Optional daily metrics and recorded coverage |

Events use a hash of type, timestamps and canonical value JSON for identity. Identical normalized records are deduplicated. Distinct overlapping source records remain available through `health_records`.

Every application connection registers the deterministic SQLite function `utc_instant`. Expression indexes use its canonical UTC keys for precise ordering and filtering across offsets. Original timestamp strings and value JSON remain stored unchanged.

## Record queries

`list_data_types` advertises every registered type, its units and value schema. `health_records` requires one type and inclusive local dates. It uses `TIMEZONE` and `DAY_BOUNDARY_HOUR` to construct a half-open UTC window:

- Instant records use measurement time.
- Sleep sessions use session end.
- Intervals intersect the window and are returned whole, without clipping or prorating values.

Pages default to 100 records and allow 1–400. Chronological start time and event ID provide deterministic continuation; cursors are bound to the original type and window. Unknown fields and nested data are preserved. Values failing the declared output contract are skipped with an event-specific caveat; pagination still advances. Missing nullable fields can appear as null and remain distinct from zero or false.

## Derived views

Pure calculations live in `aggregate/`. `rollup_service.py` adds database loading and cache writes. The debug inspector shares the same event attribution and local day windows.

Cumulative totals use a documented heuristic: each connected group of overlapping intervals contributes its largest value. Heart-rate metrics use observed samples; resting heart rate and weight use the last actual measurement instant. These transformations do not change individual records.

`aggregate/sleep.py` supplies both sleep detail and daily totals. Time in bed is the full session duration. Time asleep sums sleeping, light, deep and REM stages and excludes awake, awake-in-bed and out-of-bed stages. Stages must cover the session completely without gaps, overlaps, unknown classifications or inconsistent durations. Otherwise time asleep remains unknown.

Daily summaries and metric series enforce at most 400 output buckets. Clients choose daily or weekly buckets explicitly. Weekly cumulative metrics sum available daily values; other metrics average available values. Missing weeks remain null and missing daily coverage appears in caveats. Partial recorded coverage stays partial even when a value is present. Sleep detail returns at most 400 sessions; paginated original sessions remain available through `health_records`.

Cached summaries must be refreshed after changing calculation rules or timezone settings. With the service stopped, `python -m health_hub --rebuild-rollups` replaces the cache atomically from stored events. It never rewrites raw uploads or individual records. See [the README](../README.md#refreshing-derived-summaries) for container commands.

## MCP and authentication

The only MCP route is `/mcp`, using Python SDK 2.x and protocol revision `2026-07-28` only. Requests declare protocol metadata and matching headers. Discovery uses `server/discover`; there is no initialization handshake or protocol session. Authentication precedes protocol validation.

Seven tools provide the catalog, individual record queries, daily summaries, metric series, sleep detail, sync health and last-payload diagnostics. Every response is an object with caveats. Summary tools cover their documented metric subset; the record tool covers the entire registry.

`MCP_TOKEN` protects MCP requests and debug inspection. `DEVICE_TOKEN` protects production ingestion. Environment variables supply tokens, allowed hosts, HMAC settings, the database path and timezone. See [SECURITY.md](../SECURITY.md) and [SETUP.md](SETUP.md) for configuration.
