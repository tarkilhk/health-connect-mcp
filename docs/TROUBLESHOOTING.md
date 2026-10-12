# Troubleshooting

Symptom → where to look → what to do.

## No data at all

**Symptoms:** `/debug` shows zero payloads, zero events, or the status panel is red.

**Check, in order:**

1. **Is the server reachable?**
   ```bash
   curl -s https://your-domain.example.com/health
   ```
   Must return `{"status":"ok",...}`. If not, see [SETUP.md](SETUP.md).

2. **Did the app actually send anything?** Open `/debug?token=<MCP_TOKEN>` and look at the "Recent payloads" table. If it is empty, the app never reached the server.

3. **Is the webhook URL correct in the app?** It must be `https://your-domain.example.com/ingest/health` (and `/ingest/screentime` for screen time). Check for trailing slashes, wrong path, or `http://` instead of `https://`.

4. **Is `DEVICE_TOKEN` matching?** The app's `Authorization: Bearer <token>` header must exactly match `DEVICE_TOKEN` in `.env`. A mismatch returns `401` — but the raw payload is still stored with `signature_valid=0`. Check `/debug/api/payloads`: if you see rows with `signature_valid=false` (or `0`), the token or HMAC secret is wrong.

5. **Are Health Connect permissions granted?** In `/debug`, open the diagnostics panel. Any type with `permission_granted: false` produces no data. Re-grant it in the app.

## Data is days behind

**Symptoms:** The last payload is from days ago; new syncs are not arriving.

**Check:**

1. **Last ingest time.** The `/debug` status panel shows "last ingest". If it is more than 3 hours old, the panel turns red. This means the app has not successfully synced.

2. **App sync settings.** Open the companion app and check whether sync is enabled and on a schedule. Confirm the last sync attempt and any error the app shows.

3. **`last_sync` per type.** In the diagnostics panel, each type has a `last_sync` timestamp. If one type lags while others are fresh, that type may be hitting an error (check the `error` column) or have its permission revoked.

4. **Cursor gap.** The app's cursor only moves forward. If the app crashed mid-batch and the cursor advanced, the skipped records will not arrive on their own. Use the `/debug/api/coverage` matrix to spot the gap, then re-trigger a sync from the app.

## Numbers don't match the phone

**Symptoms:** Steps or calories on `/debug` are higher (or lower) than what the phone's Health Connect app shows.

**Check the aggregation comparison panel:**

1. Open `/debug`, pick the day in question, and look at the "Aggregation verification" panel.
2. It shows two columns: **naive sum** (all records summed, no dedup) and **deduplicated** (the rollup value).
3. If the naive sum is roughly double the deduplicated value, you have overlapping records from multiple sources (e.g. phone + watch). The server suppresses overlaps, but this is a heuristic — check the `overlap_detected` flag in that day's coverage JSON.
4. If the naive sum and deduplicated values are equal but both are wrong, the app may be sending partial batches. Check `raw_record_count` vs `filtered_record_count` in the diagnostics panel — if `filtered` is much lower than `raw`, the app's own page filter is dropping records.

**Why this happens:** Health Connect has no record IDs and no source attribution. The app reads raw records, not Health Connect's own aggregates. Two sources writing the same step interval produce two records with the same content hash, which the dedup layer merges. Non-identical overlaps (different timestamps) are not merged and cause double-counting.

## MCP client says "unavailable"

**Symptoms:** An MCP client cannot connect or reports that tools are unavailable.

**Check, in order:**

1. **Protocol and URL.** Use exactly `/mcp` with a client that supports `2026-07-28`. Older revisions return `400`; `/mcp-health` returns `404`. There is no `initialize` handshake or `Mcp-Session-Id`. The current SDK rejects inconsistent protocol metadata or method headers. For Hermes, select `protocol: stateless`.

2. **`ALLOWED_HOSTS` exact match.** The MCP SDK does exact string matching on the `Host` header. If `ALLOWED_HOSTS` does not include the exact hostname (and the `:*` variant for non-default ports), every request gets `421 Misdirected Request`. The client interprets this as unavailable. Confirm `ALLOWED_HOSTS` matches the hostname in the client's URL — no wildcards.

3. **`flush_interval -1` in Caddy.** Without it, the MCP response stream hangs until the connection closes. The client times out and reports unavailable. Confirm the Caddyfile has a separate `handle /mcp*` block with `flush_interval -1`.

4. **`Authorization` header.** The client must send `Authorization: Bearer <MCP_TOKEN>` (with the `Bearer ` prefix). A bare token returns `401`. Check the client's config.

Keep the single-worker setting enforced by `__main__.py`; it matches the application's SQLite connection model. The current MCP transport itself has no session affinity requirement.

## 401 on /debug

**Symptoms:** `GET /debug` returns 401 with no body.

**Cause:** The `?token=<MCP_TOKEN>` query parameter is missing, empty, or does not match `MCP_TOKEN` in `.env`.

**Fix:** Open `https://your-domain.example.com/debug?token=<exact MCP_TOKEN value>`. The comparison is timing-safe and exact — no leading/trailing whitespace.

## 500 on ingest

**Symptoms:** `POST /ingest/health` returns `{"error":"db_error",...}` with status 500. The app retries but never succeeds.

**Check:**

1. **Database path and permissions.** `DATABASE_PATH` points to `/data/healthhub.sqlite3` inside the container by default. The `./data` volume must be writable by the container user. Check `docker compose logs health-hub` for the SQLite error.
2. **Disk space.** SQLite returns errors when the disk is full. Run `df -h` on the host.
3. **Database corruption.** If the SQLite file is corrupt, restore from a `data/` backup. The server runs migrations on startup but cannot repair corruption.

**Why 500 matters:** A `500` response means the cursor does **not** advance and the app retries. This is intentional — returning `200` on a DB failure would advance the cursor and lose the batch permanently. Fix the underlying DB issue and the app's retries will succeed.
