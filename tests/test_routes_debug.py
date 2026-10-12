"""Integration tests for the debug inspector endpoints.

Uses httpx.ASGITransport against a Starlette app backed by an temporary-file
SQLite database. Ingests a health payload first, then queries debug
endpoints to verify data appears.
"""
from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette

from health_hub.api.routes_debug import routes as debug_routes
from health_hub.api.routes_health import routes as health_routes
from health_hub.api.routes_ingest import routes as ingest_routes
from health_hub.config import Settings
from health_hub.db.store import Database

FIXTURES = Path(__file__).parent / "fixtures"
DEVICE_TOKEN = "test-device-token"
MCP_TOKEN = "test-mcp-token"
SIGNING_SECRET = "test-secret"


def _sign(body: bytes, secret: str = SIGNING_SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _auth_headers(extra: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {DEVICE_TOKEN}"}
    if extra:
        headers.update(extra)
    return headers


def _load_full_payload() -> bytes:
    return (FIXTURES / "payload_health_full.json").read_bytes()


def _token_qs() -> dict:
    return {"token": MCP_TOKEN}


@pytest.fixture
async def test_app(tmp_path):
    """Starlette app with temporary-file DB and all routes mounted."""
    settings = Settings(
        device_token=DEVICE_TOKEN,
        mcp_token=MCP_TOKEN,
        allowed_hosts=["localhost", "localhost:*"],
        signing_key=SIGNING_SECRET,
        database_path=str(tmp_path / "synthetic.sqlite3"),
        timezone="UTC",
        day_boundary_hour=0,
        log_level="ERROR",
        port=8000,
    )
    database = await Database.open(settings)
    db = database.reader
    app = Starlette(routes=health_routes + ingest_routes + debug_routes)
    app.state.settings = settings
    app.state.db = db
    app.state.database = database
    try:
        yield app
    finally:
        await database.close()


@pytest.fixture
async def client(test_app):
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def ingested_client(client):
    """Client with one health payload already ingested (payload id=1)."""
    body = _load_full_payload()
    response = await client.post(
        "/ingest/health",
        content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )
    assert response.status_code == 200
    return client


# --- GET /debug — static HTML ------------------------------------------------


async def test_debug_html_no_token_returns_401(client):
    response = await client.get("/debug")
    assert response.status_code == 401
    assert response.content == b""


async def test_debug_html_bad_token_returns_401(client):
    response = await client.get("/debug", params={"token": "wrong"})
    assert response.status_code == 401
    assert response.content == b""


async def test_debug_html_valid_token_returns_200(client):
    response = await client.get("/debug", params=_token_qs())
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")


# --- GET /debug/api/payloads -------------------------------------------------


async def test_payloads_no_token_returns_401(client):
    response = await client.get("/debug/api/payloads")
    assert response.status_code == 401


async def test_payloads_bad_token_returns_401(client):
    response = await client.get("/debug/api/payloads", params={"token": "nope"})
    assert response.status_code == 401


async def test_payloads_returns_array(ingested_client):
    response = await ingested_client.get("/debug/api/payloads", params=_token_qs())
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    p = data[0]
    assert "id" in p
    assert "received_at" in p
    assert "source" in p
    assert "app_version" in p
    assert "byte_size" in p
    assert "signature_valid" in p
    assert "event_count" in p
    assert "parse_error" in p
    assert p["source"] == "health_connect"
    assert p["app_version"] == "1.4.0"
    assert p["signature_valid"] == 1
    assert p["event_count"] > 0
    assert p["parse_error"] is None


async def test_payloads_limit_param(ingested_client):
    response = await ingested_client.get(
        "/debug/api/payloads", params={**_token_qs(), "limit": "1"}
    )
    assert response.status_code == 200
    assert len(response.json()) == 1


# --- GET /debug/api/payload/{id} ---------------------------------------------


async def test_payload_detail_returns_body_and_events(ingested_client):
    r = await ingested_client.get("/debug/api/payloads", params=_token_qs())
    payload_id = r.json()[0]["id"]

    response = await ingested_client.get(
        f"/debug/api/payload/{payload_id}", params=_token_qs()
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == payload_id
    assert data["source"] == "health_connect"
    assert data["app_version"] == "1.4.0"
    assert "body" in data
    assert isinstance(data["events"], list)
    assert len(data["events"]) > 0
    evt = data["events"][0]
    assert "event_id" in evt
    assert "data_type" in evt
    assert "start_utc" in evt
    assert "end_utc" in evt
    assert "value_num" in evt
    assert isinstance(data["stages"], list)
    # Fixture has 7 sleep stages across 2 sessions.
    assert len(data["stages"]) == 7
    assert "result" in data
    assert data["result"]["received"] > 0
    assert data["result"]["stored"] > 0
    assert data["result"]["duplicates"] == 0


async def test_payload_detail_not_found_returns_404(ingested_client):
    response = await ingested_client.get(
        "/debug/api/payload/99999", params=_token_qs()
    )
    assert response.status_code == 404


async def test_payload_detail_no_token_returns_401(ingested_client):
    response = await ingested_client.get("/debug/api/payload/1")
    assert response.status_code == 401


# --- GET /debug/api/diagnostics ----------------------------------------------


async def test_diagnostics_returns_rows(ingested_client):
    r = await ingested_client.get("/debug/api/payloads", params=_token_qs())
    payload_id = r.json()[0]["id"]

    response = await ingested_client.get(
        "/debug/api/diagnostics",
        params={**_token_qs(), "payload_id": str(payload_id)},
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1  # fixture has one diagnostics entry (steps)
    d = data[0]
    assert d["data_type"] == "steps"
    assert d["permission_granted"] == 1
    assert d["page_count"] == 3
    assert d["raw_record_count"] == 2841
    assert d["filtered_record_count"] == 1000
    assert d["min_time"] == "2026-07-10T00:00:00Z"
    assert d["max_time"] == "2026-08-09T23:59:59Z"
    assert d["last_sync"] == "2026-08-09T12:00:00Z"
    assert d["error"] is None


async def test_diagnostics_no_token_returns_401(ingested_client):
    response = await ingested_client.get(
        "/debug/api/diagnostics", params={"payload_id": "1"}
    )
    assert response.status_code == 401


# --- GET /debug/api/coverage -------------------------------------------------


async def test_coverage_returns_matrix(ingested_client):
    response = await ingested_client.get(
        "/debug/api/coverage", params={**_token_qs(), "days": "365"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "days" in data
    assert "types" in data
    assert "matrix" in data
    assert isinstance(data["days"], list)
    assert len(data["days"]) == 365
    assert isinstance(data["types"], list)
    assert "steps" in data["types"]
    assert isinstance(data["matrix"], dict)

    # At least one day has steps data.
    steps_days = [
        day for day, counts in data["matrix"].items() if counts.get("steps", 0) > 0
    ]
    assert len(steps_days) > 0, "Expected at least one day with steps data"

    # If the fixture date is in range, verify exact count.
    if "2026-08-09" in data["matrix"]:
        assert data["matrix"]["2026-08-09"]["steps"] == 2


async def test_coverage_no_token_returns_401(ingested_client):
    response = await ingested_client.get("/debug/api/coverage")
    assert response.status_code == 401


# --- GET /debug/api/rollup/{local_date} --------------------------------------


async def test_rollup_no_data_returns_null(client):
    response = await client.get(
        "/debug/api/rollup/2026-08-09", params=_token_qs()
    )
    assert response.status_code == 200
    data = response.json()
    assert data["local_date"] == "2026-08-09"
    assert data["rollup"] is None
    assert data["naive_sums"] is None


async def test_rollup_with_data_returns_rollup(ingested_client, test_app):
    # Insert a rollup row manually (INSERT OR REPLACE — ingest may have
    # already created a row for this date via recalc).
    db = test_app.state.database._writer
    await db.execute(
        """INSERT OR REPLACE INTO daily_rollups
           (local_date, steps, distance_m, active_kcal, total_kcal,
            sleep_in_bed_s, sleep_asleep_s, hr_avg, hr_min, hr_max, hr_resting,
            weight_kg, coverage_json, computed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "2026-08-09",
            10000,
            8.5,
            75.2,
            655.9,
            28800,
            23400,
            72,
            58,
            80,
            58,
            72.4,
            '{"steps": true}',
            "2026-08-10T00:00:00Z",
        ),
    )
    await db.commit()

    response = await ingested_client.get(
        "/debug/api/rollup/2026-08-09", params=_token_qs()
    )
    assert response.status_code == 200
    data = response.json()
    assert data["local_date"] == "2026-08-09"
    assert data["rollup"] is not None
    assert data["rollup"]["steps"] == 10000
    assert data["rollup"]["distance_m"] == 8.5
    assert data["rollup"]["weight_kg"] == 72.4
    assert data["rollup"]["coverage"] == {"steps": True}
    assert data["naive_sums"] is not None
    assert "steps" in data["naive_sums"]
    # Fixture has 2 step records with counts 1234 and 567 = 1801.
    assert data["naive_sums"]["steps"] == 1801
    assert data["event_counts"]["steps"] == 2


async def test_rollup_no_token_returns_401(client):
    response = await client.get("/debug/api/rollup/2026-08-09")
    assert response.status_code == 401


# --- GET /debug/api/stats ----------------------------------------------------


async def test_stats_returns_global_stats(ingested_client):
    response = await ingested_client.get("/debug/api/stats", params=_token_qs())
    assert response.status_code == 200
    data = response.json()
    assert "total_events" in data
    assert data["total_events"] > 0
    assert "type_counts" in data
    assert isinstance(data["type_counts"], dict)
    assert "steps" in data["type_counts"]
    assert "date_from" in data
    assert "date_to" in data
    assert "last_ingest_at" in data
    assert "last_app_version" in data
    assert data["last_app_version"] == "1.4.0"
    assert "total_payloads" in data
    assert data["total_payloads"] == 1


async def test_stats_no_token_returns_401(client):
    response = await client.get("/debug/api/stats")
    assert response.status_code == 401


# --- All-endpoints auth sweep ------------------------------------------------


async def test_all_debug_endpoints_require_token(ingested_client):
    endpoints = [
        "/debug",
        "/debug/api/payloads",
        "/debug/api/payload/1",
        "/debug/api/diagnostics?payload_id=1",
        "/debug/api/coverage",
        "/debug/api/rollup/2026-08-09",
        "/debug/api/stats",
    ]
    for ep in endpoints:
        response = await ingested_client.get(ep)
        assert response.status_code == 401, f"{ep} should return 401 without token"


async def test_all_debug_endpoints_with_valid_token(ingested_client):
    endpoints = [
        "/debug",
        "/debug/api/payloads",
        "/debug/api/payload/1",
        "/debug/api/diagnostics?payload_id=1",
        "/debug/api/coverage?days=30",
        "/debug/api/rollup/2026-08-09",
        "/debug/api/stats",
    ]
    for ep in endpoints:
        sep = "&" if "?" in ep else "?"
        response = await ingested_client.get(f"{ep}{sep}token={MCP_TOKEN}")
        assert response.status_code == 200, f"{ep} should return 200 with valid token"
