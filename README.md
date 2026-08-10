# Health Hub MCP Server

[![CI](https://github.com/USER/REPO/actions/workflows/ci.yml/badge.svg)](https://github.com/USER/REPO/actions/workflows/ci.yml)

Personal MCP server that ingests Health Connect data from an Android companion app and exposes aggregated health metrics to LLM clients over the Model Context Protocol.

```
Android 14+ ──POST /ingest/health──────▶ ┌──────────────────┐
            ──POST /ingest/screentime──▶ │   Health Hub     │──MCP──▶ LLM
                                          │   SQLite         │
                  browser ──GET /debug──▶ └──────────────────┘
```

The companion app reads raw records from Health Connect and pushes them to this server. The server normalizes, deduplicates, and rolls them up into daily aggregates, then exposes them to LLM clients (Claude, Perplexity, OpenCode) through five MCP tools. Raw events are never handed to the model by default — only rolled-up summaries with explicit `caveats` so the model cannot silently fabricate missing data.

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

## Connect Your Phone
See [docs/ANDROID.md](docs/ANDROID.md) for step-by-step instructions.

## Connect an LLM
Add to Claude Desktop config (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "health-hub": {
      "url": "https://your-domain.example.com/mcp",
      "headers": {"Authorization": "Bearer <YOUR_MCP_TOKEN>"}
    }
  }
}
```
For Perplexity, OpenCode, or other MCP clients, use the same URL and token. The `Authorization` header must include the `Bearer ` prefix — the server rejects requests without it.

## MCP Tools

| Tool | Description |
|---|---|
| `daily_summary` | Daily health rollups: steps, distance, calories, sleep, HR, weight |
| `metric_series` | Time series for any metric with day/week bucketing |
| `sleep_detail` | Detailed sleep sessions with stages (deep, REM, light, awake) |
| `get_sync_health` | Per-type sync status: last event, coverage, gaps, errors |
| `inspect_last_payload` | Raw payload metadata and app diagnostics |

Each tool returns an `object` (never a bare array) with a `caveats: string[]` field that warns the model about missing days, overlapping records, or aggregation changes. Responses are capped at 400 objects.

## Known Limitations
- No record IDs from Health Connect — deduplication is content-hash based
- Multiple data sources (phone + watch) can cause double-counting for cumulative metrics. The server suppresses overlapping intervals, but this is a heuristic. Check the comparison panel on `/debug`.
- App sync cursor only moves forward — records added to Health Connect retroactively may not arrive
- Health Connect does not provide data older than 30 days without re-authorization — the server IS your archive. Back up `data/`.

## License
MIT — see [LICENSE](LICENSE).

## Companion App
[life-dashboard-companion-app](https://github.com/owen282000/life-dashboard-companion-app) v1.4.0
