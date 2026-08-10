# Security

This server stores personal health data. Treat it accordingly.

## Threat Model

The server receives health data over HTTPS from an Android companion app, stores it in a local SQLite database, and exposes aggregated summaries to LLM clients over MCP. It is a **single-user personal server** — there is no built-in user isolation or multi-tenancy.

- **Ingest auth:** `Authorization: Bearer <DEVICE_TOKEN>` header, verified with `hmac.compare_digest`. Optional `X-Signature` HMAC-SHA256 over the raw body (activated by `WEBHOOK_HMAC_SECRET`) prevents request forgery by anyone who can reach the endpoint but does not have the shared secret.
- **MCP auth:** `Authorization: Bearer <MCP_TOKEN>` header on `/mcp`.
- **Debug auth:** `?token=<MCP_TOKEN>` query parameter on `/debug*`. The debug inspector exposes raw payloads and parsed events — it is protected by the same `MCP_TOKEN`.
- **No auth on `/health`** (liveness probe only, returns no data) and **no auth on `/ingest/echo`** (stores raw bytes only, returns no data).

The `/debug` inspector exposes raw data and is protected by the same `MCP_TOKEN`.

## Requirements

- Always use HTTPS. Caddy provides automatic TLS via Let's Encrypt; do not expose the container's HTTP port directly to the internet.
- Generate strong random tokens: `openssl rand -hex 32`. Never reuse `DEVICE_TOKEN` and `MCP_TOKEN`.
- Enable HMAC verification (`WEBHOOK_HMAC_SECRET`) in production. Set the same secret in the companion app.
- Keep `.env` out of version control (already in `.gitignore`).
- Back up the `data/` directory — it is your only health data archive. Health Connect does not provide data older than 30 days without re-authorization.
- Run uvicorn with `--workers 1`. This is enforced by the entry point; do not override it.

## Reporting Vulnerabilities

Open a GitHub issue or email the maintainer.
