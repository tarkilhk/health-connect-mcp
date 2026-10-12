# health-connect-mcp

[![CI](https://github.com/tarkilhk/health-connect-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/tarkilhk/health-connect-mcp/actions/workflows/ci.yml)

Personal MCP server that ingests Health Connect data from an Android companion app and exposes aggregated health metrics to LLM clients over the Model Context Protocol.

```
Android 14+ ──POST /ingest/health──────▶ ┌──────────────────┐
  (companion app)                         │                  │
Android 14+ ──POST /ingest/hc-webhook──▶ │   Health Hub     │──MCP──▶ LLM
  (HC Webhook client)                     │   SQLite         │
                   browser ──GET /debug──▶ └──────────────────┘
```

The companion app reads records from Health Connect and pushes them to this server. The server normalizes, deduplicates, and rolls them up into daily aggregates for LLM clients (Claude, Perplexity, OpenCode). Seven MCP tools provide summaries, diagnostics, and explicit access to all 34 stored data types. Use summaries for totals and `health_records` when individual measurements or nested records are needed. Every response includes `caveats` about missing data and interpretation.

## Requirements
- Android 14+ with [life-dashboard-companion-app](https://github.com/owen282000/life-dashboard-companion-app) v1.4.0+
- Any host with Docker and Docker Compose
- Domain with TLS (Caddy handles certificates automatically)

## Quick Start
```bash
cp .env.example .env
# Edit .env: set DEVICE_TOKEN and MCP_TOKEN (run: openssl rand -hex 32)
docker compose up -d --build
curl -s http://localhost:8000/health
```

## Container images

After lint and tests pass, each push to `main` builds the Docker image and publishes it to `ghcr.io/tarkilhk/health-connect-mcp` with a semantic version tag and `latest`.

`VERSION` contains only the major and minor version, currently `0.2`. CI reads the published tags and allocates the next patch: `0.2.0`, `0.2.1`, and so on. Change `VERSION` to `0.3` to start publishing `0.3.0`. Keep published version tags in GHCR so CI can continue the patch sequence.

Publishing uses GitHub's built-in `GITHUB_TOKEN`; no extra secret or registry registration is needed. On a fork, enable workflows from the repository's Actions tab before pushing. New GHCR packages are private by default; change the package's visibility to public if you want unauthenticated pulls. See [GitHub's Container registry documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

## Companion Apps

The server accepts health data from **two** Android apps via separate endpoints. Both write to the same database and use content-hash deduplication — sending the same data from both apps will not double-count.

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

## Connect Your Phone
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

## MCP Tools

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

## Known Limitations
- No record IDs from Health Connect — deduplication is content-hash based
- Multiple data sources (phone + watch) can cause double-counting for cumulative metrics. The server suppresses overlapping intervals, but this is a heuristic. Check the comparison panel on `/debug`.
- App sync cursor only moves forward — records added to Health Connect retroactively may not arrive
- Health Connect does not provide data older than 30 days without re-authorization — the server IS your archive. Back up `data/`.

## License
MIT — see [LICENSE](LICENSE).

## Companion App
[life-dashboard-companion-app](https://github.com/owen282000/life-dashboard-companion-app) v1.4.0
