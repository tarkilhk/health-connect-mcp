# Setup Guide

Complete setup from a clean Linux host to a running, TLS-protected Health Hub server.

## Prerequisites
- A VPS or any Linux host with Docker and Docker Compose installed
- A domain name pointed at your host (an A record in your DNS provider)
- Ports 80 and 443 open (Caddy uses these for HTTP-01 TLS issuance)

## Step 1: Clone and configure

```bash
git clone <your-repo-url> health-hub
cd health-hub
cp .env.example .env
```

Generate two strong random tokens and fill them into `.env`:

```bash
openssl rand -hex 32   # -> paste into DEVICE_TOKEN
openssl rand -hex 32   # -> paste into MCP_TOKEN
```

Edit `.env` and set the required values:

| Variable | Value |
|---|---|
| `DEVICE_TOKEN` | Random hex — authenticates the Android app's ingest requests |
| `MCP_TOKEN` | Random hex — authenticates LLM clients and the `/debug` inspector |
| `ALLOWED_HOSTS` | The public hostname(s) the server is reached at, comma-separated. Include the `:*` variant for non-default ports. Example: `health.example.com,health.example.com:*` |

Optional variables (defaults are sane for most setups):

| Variable | Default | Notes |
|---|---|---|
| `WEBHOOK_HMAC_SECRET` | _(empty)_ | Shared secret for `X-Signature` HMAC verification. Set it in the companion app and here. Empty disables verification. |
| `DATABASE_PATH` | `/data/healthhub.sqlite3` | Path inside the container. The compose volume maps `./data` → `/data`. |
| `TIMEZONE` | `UTC` | IANA timezone used for all daily aggregation. |
| `DAY_BOUNDARY_HOUR` | `0` | Hour (0–23) at which the local day starts. Set to `4` to roll post-midnight activity into the previous day. |
| `LOG_LEVEL` | `INFO` | |
| `PORT` | `8000` | Port uvicorn listens on inside the container. |

> The MCP SDK does **exact** string matching on the `Host` header. Wildcards do not work. A wrong `ALLOWED_HOSTS` produces `421 Misdirected Request` on every MCP call.

## Step 2: Start the server

```bash
docker compose up -d --build
curl -s http://localhost:8000/health
```

You should see:
```json
{"status":"ok","version":"0.1.0","db":"ok"}
```

The compose file maps host port `8001` → container port `8000` by default. Adjust `docker-compose.yml` if you want a different host port.

## Step 3: TLS with Caddy

Caddy obtains and renews Let's Encrypt certificates automatically. Install Caddy on the host (or run it as a container alongside Health Hub).

Copy the example config and replace the domain:

```bash
cp Caddyfile.example Caddyfile
# Edit Caddyfile: replace health.example.com with your real domain
```

The example config:
```
health.example.com {
    handle /mcp* {
        reverse_proxy 127.0.0.1:8000 {
            flush_interval -1   # required: streaming hangs without it
        }
    }
    handle {
        reverse_proxy 127.0.0.1:8000
    }
}
```

Two things are critical and must not be changed:

1. **`flush_interval -1` on the `/mcp*` route.** MCP responses are streamed; without immediate flushing, every request hangs until the connection closes.
2. **A separate `handle /mcp*` block.** The default `handle` block does not set `flush_interval`, which is wrong for MCP.

Reload Caddy and verify:

```bash
sudo systemctl reload caddy
curl -s https://your-domain.example.com/health
```

### Reference deployment

The reference deployment runs on `health.lowcostvibers.ru` behind Caddy with the same config shown above (substitute that domain for `health.example.com`). Use that host's `/health` and `/debug` endpoints to confirm a working deployment before pointing your own app at it.

## Step 4: Verify the deployment

Run the verification script against your live URL:

```bash
./scripts/verify_deploy.sh https://your-domain.example.com
```

It checks `/health`, confirms `/debug` returns 401 without a token, and runs an MCP `initialize` handshake. All checks must pass.

## Step 5: Systemd (optional)

If you run Health Hub without Docker, a systemd unit keeps it alive:

```ini
# /etc/systemd/system/health-hub.service
[Unit]
Description=Health Hub MCP Server
After=network.target

[Service]
Type=simple
User=healthhub
WorkingDirectory=/opt/health-hub
EnvironmentFile=/opt/health-hub/.env
ExecStart=/opt/health-hub/.venv/bin/python -m health_hub
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now health-hub
```

Remember: run uvicorn with **one worker** (`workers=1`). The entry point already enforces this. Multiple workers break MCP session state — `initialize` lands in one process, `tools/call` in another, and the client gets `404 session not found`.

## Next steps

- [docs/ANDROID.md](ANDROID.md) — configure the companion app to send data
- [docs/API.md](API.md) — full API and MCP tool reference
- [docs/TROUBLESHOOTING.md](TROUBLESHOOTING.md) — diagnosing common issues
