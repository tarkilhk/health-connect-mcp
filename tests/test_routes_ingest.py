"""Integration tests for the ingestion endpoints.

Uses httpx.ASGITransport against a Starlette app backed by an in-memory
SQLite database. No network, no real server.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import aiosqlite
import httpx
import pytest
from starlette.applications import Starlette

from health_hub.api.routes_health import routes as health_routes
from health_hub.api.routes_ingest import routes as ingest_routes
from health_hub.config import Settings
from health_hub.db.migrations import apply_migrations

FIXTURES = Path(__file__).parent / "fixtures"
DEVICE_TOKEN = "test-device-token"
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


@pytest.fixture
async def test_app():
    """Starlette app with in-memory DB and signature verification active."""
    settings = Settings(
        device_token=DEVICE_TOKEN,
        mcp_token="test-mcp-token",
        allowed_hosts=["localhost", "localhost:*"],
        signing_key=SIGNING_SECRET,
        database_path=":memory:",
        timezone="UTC",
        day_boundary_hour=0,
        log_level="ERROR",
        port=8000,
    )
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await apply_migrations(db)
    app = Starlette(routes=health_routes + ingest_routes)
    app.state.settings = settings
    app.state.db = db
    try:
        yield app
    finally:
        await db.close()


@pytest.fixture
async def db(test_app):
    """Direct access to the in-memory DB owned by test_app."""
    return test_app.state.db


@pytest.fixture
async def client(test_app):
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _count(db, sql: str, *params) -> int:
    cursor = await db.execute(sql, params)
    row = await cursor.fetchone()
    return row[0]


# --- POST /ingest/health ----------------------------------------------------


async def test_health_valid_token_stores_events(client, db):
    body = _load_full_payload()
    response = await client.post(
        "/ingest/health",
        content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["received"] >= 29
    assert data["stored"] == data["received"]
    assert data["duplicates"] == 0
    assert data["errors"] == []
    assert isinstance(data["rollups_updated"], list)
    assert len(data["rollups_updated"]) > 0
    assert all(isinstance(d, str) for d in data["rollups_updated"])
    assert data["payload_id"] > 0

    # Events landed in DB.
    assert await _count(db, "SELECT count(*) FROM events") == data["received"]
    # Sleep stages landed in DB (fixture has 7 stages across 2 sessions).
    assert await _count(db, "SELECT count(*) FROM sleep_stages") == 7
    # Diagnostics: fixture has one entry (steps).
    assert await _count(db, "SELECT count(*) FROM sync_diagnostics") == 1
    # raw_payloads has one row with signature_valid=1.
    assert await _count(db, "SELECT count(*) FROM raw_payloads") == 1
    cursor = await db.execute("SELECT signature_valid, app_version FROM raw_payloads")
    row = await cursor.fetchone()
    assert row[0] == 1
    assert row[1] == "1.4.0"


async def test_health_bad_token_returns_401(client, db):
    body = _load_full_payload()
    response = await client.post(
        "/ingest/health",
        content=body,
        headers={
            "Authorization": "Bearer wrong-token",
            "X-Signature": _sign(body),
        },
    )
    assert response.status_code == 401
    # No raw_payload written on token failure.
    assert await _count(db, "SELECT count(*) FROM raw_payloads") == 0


async def test_health_bad_signature_returns_401_but_raw_stored(client, db):
    body = _load_full_payload()
    response = await client.post(
        "/ingest/health",
        content=body,
        headers=_auth_headers({"X-Signature": "sha256=deadbeef"}),
    )
    assert response.status_code == 401
    # raw_payloads entry exists with signature_valid=0.
    assert await _count(db, "SELECT count(*) FROM raw_payloads") == 1
    cursor = await db.execute("SELECT signature_valid FROM raw_payloads")
    row = await cursor.fetchone()
    assert row[0] == 0
    # No events stored.
    assert await _count(db, "SELECT count(*) FROM events") == 0


async def test_health_non_json_returns_400_with_parse_error(client, db):
    body = b"this is not json"
    response = await client.post(
        "/ingest/health",
        content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )
    assert response.status_code == 400
    cursor = await db.execute(
        "SELECT parse_error, signature_valid FROM raw_payloads"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] is not None  # parse_error set
    assert row[1] == 1  # signature was valid


async def test_health_idempotent_same_payload_twice(client, db):
    body = _load_full_payload()
    headers = _auth_headers({"X-Signature": _sign(body)})

    r1 = await client.post("/ingest/health", content=body, headers=headers)
    assert r1.status_code == 200
    count_after_first = await _count(db, "SELECT count(*) FROM events")
    assert count_after_first > 0

    r2 = await client.post("/ingest/health", content=body, headers=headers)
    assert r2.status_code == 200
    assert r2.json()["stored"] == 0
    assert r2.json()["duplicates"] == r2.json()["received"]

    count_after_second = await _count(db, "SELECT count(*) FROM events")
    assert count_after_second == count_after_first
    # Two raw_payloads rows (append-only), but events unchanged.
    assert await _count(db, "SELECT count(*) FROM raw_payloads") == 2


async def test_health_missing_auth_returns_401(client):
    body = _load_full_payload()
    response = await client.post(
        "/ingest/health",
        content=body,
        headers={"X-Signature": _sign(body)},  # no Authorization
    )
    assert response.status_code == 401


async def test_health_envelope_fields_extracted(client, db):
    """app_version and payload_timestamp are extracted into raw_payloads."""
    body = _load_full_payload()
    response = await client.post(
        "/ingest/health",
        content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )
    assert response.status_code == 200
    cursor = await db.execute(
        "SELECT app_version, payload_timestamp, source FROM raw_payloads"
    )
    row = await cursor.fetchone()
    assert row[0] == "1.4.0"
    assert row[1] == "2026-08-09T12:00:00Z"
    assert row[2] == "health_connect"


# --- POST /ingest/screentime ------------------------------------------------


async def test_screentime_writes_days_and_apps(client, db):
    payload = {
        "timestamp": "2026-08-09T12:00:00Z",
        "app_version": "1.4.0",
        "device": "Pixel 8",
        "source": "screen_time",
        "screen_time": [
            {
                "date": "2026-08-09",
                "total_screen_time_minutes": 120,
                "apps": [
                    {
                        "package": "com.android.chrome",
                        "name": "Chrome",
                        "minutes": 45,
                        "last_used": "2026-08-09T22:00:00Z",
                    },
                    {
                        "package": "com.twitter.android",
                        "name": "X",
                        "minutes": 30,
                        "last_used": "2026-08-09T21:00:00Z",
                    },
                ],
            },
        ],
    }
    body = json.dumps(payload).encode("utf-8")
    response = await client.post(
        "/ingest/screentime",
        content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["days_written"] == 1
    assert data["payload_id"] > 0
    assert data["rollups_updated"] == []

    cursor = await db.execute(
        "SELECT local_date, total_minutes FROM screen_time_days"
    )
    row = await cursor.fetchone()
    assert row[0] == "2026-08-09"
    assert row[1] == 120

    assert (
        await _count(
            db,
            "SELECT count(*) FROM screen_time_apps WHERE local_date = ?",
            "2026-08-09",
        )
        == 2
    )

    # device extracted from payload into raw_payloads.
    cursor = await db.execute(
        "SELECT device, source FROM raw_payloads WHERE source = ?",
        ("screen_time",),
    )
    row = await cursor.fetchone()
    assert row[0] == "Pixel 8"


async def test_screentime_missing_auth_returns_401(client):
    body = json.dumps({"screen_time": []}).encode()
    response = await client.post("/ingest/screentime", content=body)
    assert response.status_code == 401


async def test_screentime_idempotent_replace_apps(client, db):
    """Re-posting the same date replaces apps (DELETE + INSERT)."""
    payload = {
        "timestamp": "t",
        "app_version": "v",
        "device": "d",
        "source": "screen_time",
        "screen_time": [
            {
                "date": "2026-08-09",
                "total_screen_time_minutes": 60,
                "apps": [
                    {"package": "com.a", "name": "A", "minutes": 60, "last_used": "t"},
                ],
            },
        ],
    }
    body = json.dumps(payload).encode()
    r1 = await client.post(
        "/ingest/screentime", content=body, headers=_auth_headers({"X-Signature": _sign(body)})
    )
    assert r1.status_code == 200
    assert await _count(db, "SELECT count(*) FROM screen_time_apps") == 1

    # Second post: different apps for same date.
    payload["screen_time"][0]["apps"] = [
        {"package": "com.b", "name": "B", "minutes": 30, "last_used": "t"},
        {"package": "com.c", "name": "C", "minutes": 30, "last_used": "t"},
    ]
    body2 = json.dumps(payload).encode()
    r2 = await client.post(
        "/ingest/screentime", content=body2, headers=_auth_headers({"X-Signature": _sign(body2)})
    )
    assert r2.status_code == 200
    # Old app replaced, two new apps.
    assert await _count(db, "SELECT count(*) FROM screen_time_apps") == 2
    assert (
        await _count(
            db,
            "SELECT count(*) FROM screen_time_apps WHERE package = ?",
            "com.a",
        )
        == 0
    )


# --- POST /ingest/echo ------------------------------------------------------


async def test_echo_always_200(client, db):
    body = b"anything at all"
    response = await client.post("/ingest/echo", content=body)
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["byte_size"] == len(body)
    assert data["payload_id"] > 0

    cursor = await db.execute(
        "SELECT source, byte_size, signature_valid FROM raw_payloads"
    )
    row = await cursor.fetchone()
    assert row[0] == "echo"
    assert row[1] == len(body)
    assert row[2] == 1


async def test_echo_no_auth_required(client):
    """Echo has no token check — always 200."""
    response = await client.post("/ingest/echo", content=b"ping")
    assert response.status_code == 200


async def test_echo_binary_body(client, db):
    """Echo preserves binary bytes via latin-1 fallback in body_json."""
    body = bytes(range(256))
    response = await client.post("/ingest/echo", content=body)
    assert response.status_code == 200
    assert response.json()["byte_size"] == 256


# --- H1: Screentime signature verification ----------------------------------


async def test_screentime_bad_signature_returns_401_but_raw_stored(client, db):
    """H1: screentime now verifies X-Signature. Bad sig -> 401, raw stored."""
    payload = {
        "timestamp": "t", "app_version": "v", "device": "d", "source": "screen_time",
        "screen_time": [],
    }
    body = json.dumps(payload).encode()
    response = await client.post(
        "/ingest/screentime",
        content=body,
        headers=_auth_headers({"X-Signature": "sha256=deadbeef"}),
    )
    assert response.status_code == 401
    assert await _count(db, "SELECT count(*) FROM raw_payloads") == 1
    cursor = await db.execute("SELECT signature_valid FROM raw_payloads")
    assert (await cursor.fetchone())[0] == 0


async def test_screentime_missing_signature_returns_401(client, db):
    """H1: missing X-Signature on screentime -> 401."""
    payload = {
        "timestamp": "t", "app_version": "v", "device": "d", "source": "screen_time",
        "screen_time": [],
    }
    body = json.dumps(payload).encode()
    response = await client.post(
        "/ingest/screentime",
        content=body,
        headers=_auth_headers(),  # no X-Signature
    )
    assert response.status_code == 401


async def test_screentime_empty_secret_skips_signature_check(test_app, client):
    """H1: when WEBHOOK_HMAC_SECRET is empty, signature check is skipped."""
    import dataclasses

    test_app.state.settings = dataclasses.replace(
        test_app.state.settings, signing_key=""
    )
    payload = {
        "timestamp": "t", "app_version": "v", "device": "d", "source": "screen_time",
        "screen_time": [],
    }
    body = json.dumps(payload).encode()
    # No X-Signature header, but secret is empty -> should pass.
    response = await client.post(
        "/ingest/screentime",
        content=body,
        headers=_auth_headers(),
    )
    assert response.status_code == 200


# --- H2: JSON / envelope validation -----------------------------------------


async def test_health_json_array_body_returns_400(client, db):
    """H2: top-level JSON array -> 400, not 500."""
    body = b"[1, 2, 3]"
    response = await client.post(
        "/ingest/health",
        content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_json"
    cursor = await db.execute("SELECT parse_error FROM raw_payloads")
    assert (await cursor.fetchone())[0] is not None


async def test_health_json_scalar_body_returns_400(client, db):
    """H2: top-level JSON scalar -> 400, not 500."""
    body = b'"hello"'
    response = await client.post(
        "/ingest/health",
        content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_json"


async def test_health_missing_timestamp_returns_400(client, db):
    """H2: missing required envelope field 'timestamp' -> 400 via HealthPayload."""
    payload = {"app_version": "v", "source": "s", "steps": []}
    body = json.dumps(payload).encode()
    response = await client.post(
        "/ingest/health",
        content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_envelope"
    cursor = await db.execute("SELECT parse_error FROM raw_payloads")
    assert (await cursor.fetchone())[0] is not None


async def test_health_malformed_record_beside_valid_returns_200_with_errors(client, db):
    """H2: individual record-level errors don't block 200; errors[] populated."""
    payload = {
        "timestamp": "t", "app_version": "v", "source": "s",
        "steps": [
            {"count": 100, "start_time": "2026-08-09T08:00:00Z", "end_time": "2026-08-09T09:00:00Z"},
            {"count": 200},  # missing start_time/end_time
        ],
    }
    body = json.dumps(payload).encode()
    response = await client.post(
        "/ingest/health",
        content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["stored"] == 1  # only the valid record
    assert len(data["errors"]) == 1
    assert data["errors"][0]["type"] == "steps"
    assert data["errors"][0]["index"] == 1


async def test_screentime_missing_device_returns_400(client, db):
    """H2: ScreenTimePayload requires 'device' -> 400 if missing."""
    payload = {"timestamp": "t", "app_version": "v", "source": "screen_time", "screen_time": []}
    body = json.dumps(payload).encode()
    response = await client.post(
        "/ingest/screentime",
        content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_envelope"


# --- M4: Echo DB failure returns 200 with warning ---------------------------


async def test_echo_db_failure_returns_200_with_warning(client, db, monkeypatch):
    """M4: echo always returns 200 even if raw storage fails."""

    async def fail_store(*args, **kwargs):
        raise aiosqlite.OperationalError("simulated DB failure")

    monkeypatch.setattr("health_hub.api.routes_ingest.store_raw_payload", fail_store)
    body = b"echo-data"
    response = await client.post("/ingest/echo", content=body)
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["payload_id"] is None
    assert data["byte_size"] == len(body)
    assert data["warning"] == "raw payload storage failed"


# --- M5: DB-failure safety invariant ----------------------------------------


async def test_health_db_error_returns_500(client, db, monkeypatch):
    """M5: DB write failure during events upsert -> 500, not 200."""

    async def fail_upsert(*args, **kwargs):
        raise aiosqlite.OperationalError("simulated DB failure")

    monkeypatch.setattr("health_hub.api.routes_ingest.upsert_events", fail_upsert)
    body = _load_full_payload()
    response = await client.post(
        "/ingest/health",
        content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )
    assert response.status_code == 500
    assert response.json()["error"] == "db_error"
    # L0 raw_payloads survived (committed before L1).
    assert await _count(db, "SELECT count(*) FROM raw_payloads") == 1
    # L1 events rolled back.
    assert await _count(db, "SELECT count(*) FROM events") == 0


async def test_health_retry_after_500_is_idempotent(client, db, monkeypatch):
    """M5: retry after 500 doesn't duplicate events on successful retry."""
    body = _load_full_payload()
    headers = _auth_headers({"X-Signature": _sign(body)})

    from health_hub.ingest.diagnostics import store_diagnostics as _real_store_diag

    should_fail = True

    async def maybe_fail(*args, **kwargs):
        nonlocal should_fail
        if should_fail:
            raise aiosqlite.OperationalError("simulated diagnostics failure")
        return await _real_store_diag(*args, **kwargs)

    monkeypatch.setattr("health_hub.api.routes_ingest.store_diagnostics", maybe_fail)

    # First attempt: fails during diagnostics.
    r1 = await client.post("/ingest/health", content=body, headers=headers)
    assert r1.status_code == 500
    # M6: events rolled back (transaction).
    assert await _count(db, "SELECT count(*) FROM events") == 0
    assert await _count(db, "SELECT count(*) FROM sync_diagnostics") == 0

    # Retry: succeeds.
    should_fail = False
    r2 = await client.post("/ingest/health", content=body, headers=headers)
    assert r2.status_code == 200
    assert r2.json()["stored"] == r2.json()["received"]  # all new, no dupes
    assert r2.json()["duplicates"] == 0


# --- M6: L1 transaction atomicity -------------------------------------------


async def test_health_diagnostics_failure_rolls_back_events(client, db, monkeypatch):
    """M6: if diagnostics fails after events+stages succeed, all L1 rolled back."""

    async def fail_diagnostics(*args, **kwargs):
        raise aiosqlite.OperationalError("simulated diagnostics failure")

    monkeypatch.setattr("health_hub.api.routes_ingest.store_diagnostics", fail_diagnostics)

    body = _load_full_payload()
    response = await client.post(
        "/ingest/health",
        content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )
    assert response.status_code == 500
    # M6: all L1 writes rolled back.
    assert await _count(db, "SELECT count(*) FROM events") == 0
    assert await _count(db, "SELECT count(*) FROM sleep_stages") == 0
    assert await _count(db, "SELECT count(*) FROM sync_diagnostics") == 0
    # L0 raw_payloads survived (committed before L1 transaction).
    assert await _count(db, "SELECT count(*) FROM raw_payloads") == 1


# --- Phase 5b: rollup persistence + transaction behavior ---------------------


async def test_ingest_creates_daily_rollup_with_expected_values(client, db):
    """After ingest, daily_rollups row exists for 2026-08-09 with computed values."""
    body = _load_full_payload()
    response = await client.post(
        "/ingest/health",
        content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )
    assert response.status_code == 200

    cursor = await db.execute(
        "SELECT steps, distance_m, active_kcal, total_kcal, hr_avg, hr_min, "
        "hr_max, hr_resting, weight_kg, sleep_in_bed_s, sleep_asleep_s "
        "FROM daily_rollups WHERE local_date = ?",
        ("2026-08-09",),
    )
    row = await cursor.fetchone()
    assert row is not None, "rollup row for 2026-08-09 must exist"

    steps, distance_m, active_kcal, total_kcal, hr_avg, hr_min, hr_max, \
        hr_resting, weight_kg, sleep_in_bed_s, sleep_asleep_s = row

    # steps: 1234 + 567 = 1801 (non-overlapping, touching at 09:00).
    assert steps == 1801
    # distance: 820.5 + 410.0 = 1230.5.
    assert distance_m == 1230.5
    # active_kcal: 45.2 + 30.0 = 75.2.
    assert active_kcal == 75.2
    # total_kcal: 245.1 + 410.8 = 655.9.
    assert total_kcal == 655.9
    # hr: avg of (72,75,80,78,72) = 377/5 = 75.4.
    assert hr_avg == 75.4
    assert hr_min == 72
    assert hr_max == 80
    # resting_hr: single value 58 on 2026-08-09.
    assert hr_resting == 58
    # weight: last value on 2026-08-09 = 72.4.
    assert weight_kg == 72.4
    # sleep: session ending 2026-08-09T07:00, 8h = 28800s, no awake stages.
    assert sleep_in_bed_s == 28800
    assert sleep_asleep_s == 28800


async def test_ingest_rollup_coverage_json_is_valid_with_expected_keys(client, db):
    """daily_rollups.coverage_json parses as JSON with all expected metric keys."""
    body = _load_full_payload()
    response = await client.post(
        "/ingest/health",
        content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )
    assert response.status_code == 200

    cursor = await db.execute(
        "SELECT coverage_json FROM daily_rollups WHERE local_date = ?",
        ("2026-08-09",),
    )
    row = await cursor.fetchone()
    assert row is not None
    coverage = json.loads(row[0])

    expected_keys = {
        "steps", "distance", "active_calories", "total_calories",
        "sleep", "heart_rate", "resting_heart_rate", "weight",
        "overlap_detected",
    }
    assert set(coverage.keys()) == expected_keys
    # 2026-08-09 has data for all 8 metrics.
    assert coverage["steps"] == "ok"
    assert coverage["sleep"] == "ok"
    assert coverage["heart_rate"] == "ok"
    assert coverage["weight"] == "ok"
    # No overlapping intervals in the fixture.
    assert coverage["overlap_detected"] is False


async def test_sleep_session_spanning_dates_rolls_up_on_end_date(client, db):
    """Sleep session starting prev day, ending target day -> counts on end date."""
    payload = {
        "timestamp": "2026-08-10T12:00:00Z",
        "app_version": "1.4.0",
        "source": "health_connect",
        "sleep": [
            {
                "session_end_time": "2026-08-10T06:00:00Z",
                "duration_seconds": 28800,  # 8h, started 2026-08-09 22:00 UTC
                "stages": [
                    {"stage": "deep", "start_time": "2026-08-09T22:00:00Z",
                     "end_time": "2026-08-10T00:00:00Z", "duration_seconds": 7200},
                    {"stage": "light", "start_time": "2026-08-10T00:00:00Z",
                     "end_time": "2026-08-10T04:00:00Z", "duration_seconds": 14400},
                    {"stage": "awake", "start_time": "2026-08-10T04:00:00Z",
                     "end_time": "2026-08-10T05:00:00Z", "duration_seconds": 3600},
                    {"stage": "light", "start_time": "2026-08-10T05:00:00Z",
                     "end_time": "2026-08-10T06:00:00Z", "duration_seconds": 3600},
                ],
            },
        ],
    }
    body = json.dumps(payload).encode()
    response = await client.post(
        "/ingest/health",
        content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )
    assert response.status_code == 200
    data = response.json()
    # Sleep end_utc is 2026-08-10 -> recalc must touch 2026-08-10, NOT 2026-08-09.
    assert "2026-08-10" in data["rollups_updated"]
    assert "2026-08-09" not in data["rollups_updated"]

    # Rollup row for 2026-08-10 has sleep_in_bed_s = 28800 (8h).
    cursor = await db.execute(
        "SELECT sleep_in_bed_s, sleep_asleep_s FROM daily_rollups WHERE local_date = ?",
        ("2026-08-10",),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 28800  # 8h in bed
    # awake stage = 3600s -> asleep = 28800 - 3600 = 25200.
    assert row[1] == 25200

    # And 2026-08-09 has NO sleep rollup (session not attributed there).
    cursor = await db.execute(
        "SELECT sleep_in_bed_s FROM daily_rollups WHERE local_date = ?",
        ("2026-08-09",),
    )
    row = await cursor.fetchone()
    assert row is None or row[0] is None


async def test_recalc_failure_rolls_back_events_and_returns_500(client, db, monkeypatch):
    """If recalc raises, the whole L1 transaction rolls back -> 500, 0 events."""

    call_count = {"n": 0}

    async def fail_recalc(*args, **kwargs):
        call_count["n"] += 1
        # Raise on the first call; route's except block must catch it.
        raise ValueError("simulated recalc failure")

    monkeypatch.setattr(
        "health_hub.api.routes_ingest.recalc_rollups_for_dates", fail_recalc
    )

    body = _load_full_payload()
    response = await client.post(
        "/ingest/health",
        content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )
    assert response.status_code == 500
    assert response.json()["error"] == "internal_error"
    assert call_count["n"] == 1

    # L1 events + stages rolled back (transaction atomicity).
    assert await _count(db, "SELECT count(*) FROM events") == 0
    assert await _count(db, "SELECT count(*) FROM sleep_stages") == 0
    assert await _count(db, "SELECT count(*) FROM daily_rollups") == 0
    # L0 raw_payloads survived (committed separately).
    assert await _count(db, "SELECT count(*) FROM raw_payloads") == 1
