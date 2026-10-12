# health-connect-mcp

[![CI](https://github.com/tarkilhk/health-connect-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/tarkilhk/health-connect-mcp/actions/workflows/ci.yml)

Self-hosted Health Connect server with typed access to all 34 stored data types through the Model Context Protocol (MCP). It accepts Android uploads, keeps a SQLite archive, and gives MCP clients individual stored records, with optional daily summaries.

This is a fork of [naymEvg/health-conect-mcp](https://github.com/naymEvg/health-conect-mcp). The original project provides the ingestion, storage, aggregation, and debugging foundation. This fork extends the data available through MCP and adopts the current MCP transport used by this project.

## Why this fork exists

The original server's five MCP tools focus on summaries, sleep, and sync diagnostics. Its database can hold more types than those summary tools expose. Measurements such as paired blood pressure, detailed nutrition, and categorical records need their own query surface so an LLM can inspect the actual stored values.

This fork adds a discoverable catalog and typed, paginated record queries for every registered type, alongside the existing aggregate tools. It also moves the server to MCP Python SDK 2.x and protocol revision `2026-07-28`, with one authenticated `/mcp` endpoint. The aim is broader data access with explicit units, time windows, and caveats about what the archive can tell you.

The server is for a single person's archive. You run it on your own host and choose which LLM clients receive its MCP token. See [SECURITY.md](SECURITY.md) for the access model.

## Changes and fixes in this fork

| Area | Change |
|---|---|
| Data discovery | `list_data_types` exposes all 34 stored types, record kinds, units, and value schemas. Registration does not imply measurements exist. |
| Record access | `health_records` returns one selected type over an inclusive local date range, with pages of 100 records by default and a maximum of 400. |
| Value preservation | Blood pressure retains both readings; nutrition retains optional nutrients; sleep retains nested stages. Fields with no defined schema remain stored JSON with a caveat. |
| Date queries | Queries compare UTC instants across timezone offsets, preserve microsecond ordering, and respect `TIMEZONE`, `DAY_BOUNDARY_HOUR`, and daylight-saving boundaries. |
| Pagination | Cursors are bound to the original type and date window. Tied timestamps use event IDs for deterministic ordering. Invalid limits, dates, types, and cursors return MCP errors. |
| Ingestion isolation | One shared validation/persistence pipeline serializes writes; MCP and debug use a separate query-only connection and see committed data. |
| Sleep calculations | Detail and daily summaries use one calculation. Missing, unknown, overlapping or incomplete stages leave time asleep unknown. |
| Aggregate limits | Daily summaries and metric series accept at most 400 output buckets; clients choose weekly buckets explicitly. |
| Inspector | Coverage and rollup comparisons use the configured local timezone and day boundary, with sleep attributed by session end. |
| MCP transport | SDK 2.x, protocol `2026-07-28`, and `/mcp` replace the previous transport and `/mcp-health` alias. Requests declare their version and capabilities; discovery uses `server/discover`. |
| Authentication and metadata | Production-route tests cover authentication and protocol validation. MCP registry metadata points to this fork and its `/mcp` endpoint. |
| Image releases | Successful pushes to `main` publish semantically versioned GHCR images. `VERSION` controls major/minor; CI allocates patches from existing image tags. |

Tests exercise all registered types with synthetic data, compound values, sparse records, local date boundaries, pagination, and the production MCP route. The fork retains the upstream ingestion and aggregate tools; individual records carry their own interpretation caveats.

## What's inside

```
Android 14+ ──POST /ingest/health──────▶ ┌──────────────────┐
  (companion app)                         │                  │
Android 14+ ──POST /ingest/hc-webhook──▶ │   Health Hub     │──MCP──▶ LLM
  (HC Webhook client)                     │   SQLite         │
                   browser ──GET /debug──▶ └──────────────────┘
```

The companion app reads records from Health Connect and pushes them to this server. The server normalizes, deduplicates, and rolls them up into daily aggregates for LLM clients (Claude, Perplexity, OpenCode). Seven MCP tools provide summaries, diagnostics, and explicit access to all 34 stored data types. Use `health_records` for individual measurements and nested records. The aggregate tools provide additional derived totals. Every response includes `caveats` about missing data and interpretation.

The archive keeps raw uploads for inspection, normalized events for record queries, and daily rollups for summaries. A token-protected `/debug` inspector helps trace payloads, coverage, and source overlap. The companion app can also upload screen-time data to `/ingest/screentime`.

| Directory | Responsibility |
|---|---|
| `health_hub/contract/` | Payload contracts, the type registry, and typed record values |
| `health_hub/ingest/` | Normalization, content-hash identity, persistence, and sync diagnostics |
| `health_hub/aggregate/` | Local date windows, overlap handling, and rollup calculations |
| `health_hub/db/` | SQLite connections and schema |
| `health_hub/mcp_server/` | MCP transport, tool definitions, record queries, and output schemas |
| `health_hub/api/` | Ingestion, health checks, authentication, and the debug inspector |
| `tests/` | Synthetic ingestion, aggregation, MCP, and image-versioning checks |
| `.github/` and `scripts/` | CI, image publishing, and deployment verification |

For setup and development, see [the setup guide](docs/SETUP.md), [Android configuration](docs/ANDROID.md), [the API reference](docs/API.md), [troubleshooting](docs/TROUBLESHOOTING.md), and [contributing](CONTRIBUTING.md).

## Requirements

- Android 14+ with [life-dashboard-companion-app](https://github.com/owen282000/life-dashboard-companion-app) v1.4.0+
- Any host with Docker and Docker Compose
- Domain with TLS (Caddy handles certificates automatically)

## Quick start

```bash
cp .env.example .env
# Edit .env: set DEVICE_TOKEN and MCP_TOKEN (run: openssl rand -hex 32)
docker compose up -d --build
curl -s http://localhost:8001/health
```

## Container images

After lint and tests pass, each push to `main` builds the Docker image and publishes it to `ghcr.io/tarkilhk/health-connect-mcp` with a semantic version tag and `latest`.

`VERSION` contains only the major and minor version, currently `1.0`. CI reads the published tags and allocates the next patch: `1.0.0`, `1.0.1`, and so on. Change `VERSION` to `1.1` to start publishing `1.1.0`. Keep published version tags in GHCR so CI can continue the patch sequence.

Publishing uses GitHub's built-in `GITHUB_TOKEN`; no extra secret or registry registration is needed. On a fork, enable workflows from the repository's Actions tab before pushing. New GHCR packages are private by default; change the package's visibility to public if you want unauthenticated pulls. See [GitHub's Container registry documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

## Refreshing derived summaries

Individual records and raw uploads remain unchanged by this cleanup. Existing cached summaries need recomputation to use the corrected sleep and timestamp calculations. Stop the service, rebuild only the derived cache, then start it again:

```bash
docker compose stop health-hub
docker compose run --rm health-hub python -m health_hub --rebuild-rollups
docker compose up -d health-hub
```

For a local installation, run `python -m health_hub --rebuild-rollups` with the same environment and `DATABASE_PATH` as the stopped server. The rebuild uses `TIMEZONE` and `DAY_BOUNDARY_HOUR` and commits atomically. `DATABASE_PATH` must name a file; separate read and write connections require shared storage.

## Companion apps

The server accepts health data from **two** Android apps via separate endpoints. Both write to the same database. Content-hash deduplication removes identical normalized records; overlapping measurements that differ can still double-count.

| App | Endpoint | Format | Best for |
|-----|----------|--------|----------|
| [life-dashboard-companion-app](https://github.com/owen282000/life-dashboard-companion-app) v1.4.0 | `POST /ingest/health` | `source: "health_connect"` required | Incremental sync, 200 records per type |
| [HC Webhook](https://github.com/mcnaveen/health-connect-webhook) | `POST /ingest/hc-webhook` | `source` optional, `rmssd_millis` HRV, extra types | Full sync, all types in one POST |

Both endpoints require:
```
Authorization: Bearer <DEVICE_TOKEN>
Content-Type: application/json; charset=utf-8
```

The HC Webhook endpoint also accepts 5 additional Health Connect types not present in the companion app: `skin_temperature`, `basal_metabolic_rate`, `vo2_max`, `intermenstrual_bleeding`, `body_water_mass`. Unknown type keys are silently tolerated — only types in the server's registry are processed.

## Connect your phone

See [docs/ANDROID.md](docs/ANDROID.md) for step-by-step instructions.

## Connect an LLM

Add to Claude Desktop config (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "health-connect-mcp": {
      "url": "https://your-domain.example.com/mcp",
      "headers": {"Authorization": "Bearer <YOUR_MCP_TOKEN>"}
    }
  }
}
```
Use a client that supports MCP protocol revision `2026-07-28`. The server uses MCP Python SDK 2.x and accepts only that revision, at `/mcp`. Each request carries its protocol version and client capabilities; there is no `initialize` handshake or `Mcp-Session-Id`. The `Authorization` header must include the `Bearer ` prefix.

Older protocol revisions and the former `/mcp-health` route are not supported. Client compatibility depends on the installed client version and runtime. For Hermes, select `protocol: stateless` in the server configuration. See the client's documentation for current protocol support.

## MCP tools

| Tool | Description |
|---|---|
| `daily_summary` | Daily health rollups: steps, distance, calories, sleep, HR, weight |
| `metric_series` | Time series for the 11 documented rollup metrics with day/week bucketing |
| `sleep_detail` | Detailed sleep sessions with stages (deep, REM, light, awake) |
| `get_sync_health` | Per-type sync status: last event, coverage, gaps, errors |
| `inspect_last_payload` | Raw payload metadata and app diagnostics |
| `list_data_types` | Discover all 34 stored types, units, record kinds and value schemas |
| `health_records` | Typed records for one selected type and local date window, with pagination |

Each tool returns an `object` (never a bare array) with a `caveats: string[]` field. `health_records` returns 100 records by default and accepts a limit from 1 to 400. Continue with `next_cursor` using the same type and dates. Paired blood pressure values, optional nutrition fields, categories, sleep stages and other nested JSON are preserved. Records have content-hash deduplication; overlapping source records have no suppression in this tool, so summing them may overcount.

Start with `list_data_types`, then call, for example:

```json
{"name":"health_records","arguments":{"data_type":"blood_pressure","date_from":"2026-08-01","date_to":"2026-08-09","limit":100}}
```

Dates use `TIMEZONE` and `DAY_BOUNDARY_HOUR`. Sleep is selected by session end, instants by measurement time, and intervals by intersection with the window. Missing measurements mean unknown. Some registry entries have no defined value-field contract, including the custom `test` type; their stored JSON is returned with a caveat. See [docs/API.md](docs/API.md) for all 34 types and the query contract.

## Known limitations

- No record IDs from Health Connect — deduplication is content-hash based
- Multiple data sources (phone + watch) can cause double-counting for cumulative metrics. The server suppresses overlapping intervals, but this is a heuristic. Check the comparison panel on `/debug`.
- App sync cursor only moves forward — records added to Health Connect retroactively may not arrive
- Health Connect does not provide data older than 30 days without re-authorization — the server IS your archive. Back up `data/`.

## License
MIT — see [LICENSE](LICENSE).
