"""Inspector and diagnostics use the same attribution as stored rollups."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from tests.mcp_client import MCP_TOKEN, _call_tool, _ingest


async def test_inspector_rollup_and_coverage_respect_local_boundary(client, test_app, monkeypatch):
    from health_hub.db import inspect_status

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 12, tzinfo=UTC)

    monkeypatch.setattr(inspect_status, "datetime", FixedDatetime)
    test_app.state.settings = replace(test_app.state.settings, timezone="Asia/Singapore", day_boundary_hour=4)
    await _ingest(client, {"timestamp": "2026-08-09T12:00:00Z", "app_version": "synthetic",
                           "source": "health_connect", "weight": [
                               {"time": "2026-08-08T22:00:00Z", "kilograms": 70},
                               {"time": "2026-08-09T03:30:00+08:00", "kilograms": 71},
                           ], "sleep": [{"session_end_time": "2026-08-09T04:30:00+08:00",
                                         "duration_seconds": 28800}]})
    response = await client.get("/debug/api/rollup/2026-08-09", params={"token": MCP_TOKEN})
    result = response.json()
    assert result["event_counts"] == {"weight": 1, "sleep": 1}
    assert result["naive_sums"]["weight"] == result["rollup"]["weight_kg"] == 70
    response = await client.get("/debug/api/coverage", params={"token": MCP_TOKEN, "days": 365})
    matrix = response.json()["matrix"]
    assert matrix["2026-08-09"]["weight"] == matrix["2026-08-09"]["sleep"] == 1
    assert matrix["2026-08-08"]["weight"] == 1


async def test_sync_health_counts_actual_utc_dates_and_sleep_ends(client):
    today = datetime.now(UTC).date()
    yesterday = today - timedelta(days=1)
    # Both weights fall on today in UTC despite different written dates.
    later = today.isoformat() + "T12:00:00Z"
    await _ingest(client, {"timestamp": later, "app_version": "synthetic", "source": "health_connect",
                           "weight": [{"time": yesterday.isoformat() + "T23:30:00-02:00", "kilograms": 70},
                                      {"time": today.isoformat() + "T12:00:00+08:00", "kilograms": 71}],
                           "sleep": [{"session_end_time": today.isoformat() + "T06:00:00Z",
                                      "duration_seconds": 28800}]})
    result = await _call_tool(client, "get_sync_health", {"days": 1})
    metrics = {metric["data_type"]: metric for metric in result["metrics"]}
    assert metrics["weight"]["coverage_days"] == metrics["sleep"]["coverage_days"] == 1
    assert metrics["weight"]["last_event_utc"] == today.isoformat() + "T12:00:00+08:00"


async def test_denied_permission_is_reported_as_boolean_with_caveat(client):
    await _ingest(client, {"timestamp": "2026-08-09T12:00:00Z", "app_version": "synthetic",
                           "source": "health_connect", "_diagnostics": {"steps": {"permission_granted": False}}})
    result = await _call_tool(client, "inspect_last_payload", {})
    assert result["diagnostics"][0]["permission_granted"] is False
    assert "steps: permission not granted" in result["caveats"]


@pytest.mark.parametrize("path, params", [
    ("/debug/api/payloads", {"limit": "101"}),
    ("/debug/api/coverage", {"days": "invalid"}),
    ("/debug/api/rollup/20260809", {}),
])
async def test_debug_rejects_invalid_bounds_and_noncanonical_dates(client, path, params):
    response = await client.get(path, params={"token": MCP_TOKEN, **params})
    assert response.status_code == 400
