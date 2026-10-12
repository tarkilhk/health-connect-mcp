"""Integration tests for MCP operations and caveats."""
from datetime import UTC, datetime

from health_hub.mcp_server import tools_ops
from tests.mcp_client import (
    _auth_headers,
    _call_tool,
    _load_full_payload,
    _sign,
)

# --- get_sync_health --------------------------------------------------------


async def test_get_sync_health_no_data(client):
    result = await _call_tool(client, "get_sync_health", {"days": 7})
    assert result["period_days"] == 7
    assert len(result["metrics"]) == 34  # all data types
    assert all(m["coverage_days"] == 0 for m in result["metrics"])
    assert len(result["caveats"]) > 0


async def test_get_sync_health_after_ingest(client, db, monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 10, tzinfo=UTC)

    monkeypatch.setattr(tools_ops, "datetime", FixedDatetime)
    body = _load_full_payload()
    await client.post(
        "/ingest/health", content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )
    result = await _call_tool(client, "get_sync_health", {"days": 30})
    steps_metric = next(m for m in result["metrics"] if m["data_type"] == "steps")
    assert steps_metric["coverage_days"] >= 1
    assert steps_metric["last_event_utc"] is not None
    assert len(result["caveats"]) > 0


# --- inspect_last_payload ---------------------------------------------------


async def test_inspect_last_payload_no_payloads(client):
    result = await _call_tool(client, "inspect_last_payload", {})
    assert result["payload_id"] is None
    assert any("no payloads" in c for c in result["caveats"])


async def test_inspect_last_payload_after_ingest(client, db):
    body = _load_full_payload()
    await client.post(
        "/ingest/health", content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )
    result = await _call_tool(client, "inspect_last_payload", {})
    assert result["payload_id"] == 1
    assert result["source"] == "health_connect"
    assert result["app_version"] == "1.4.0"
    assert result["signature_valid"] is True
    assert len(result["diagnostics"]) >= 1
    assert result["diagnostics"][0]["data_type"] == "steps"
    assert len(result["caveats"]) > 0


# --- caveats always present -------------------------------------------------


async def test_caveats_always_present_in_every_tool(client, db):
    """Every tool response must include a non-empty caveats list (SPEC §9.2)."""
    body = _load_full_payload()
    await client.post(
        "/ingest/health", content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )

    calls = [
        ("daily_summary", {"date_from": "2026-08-09", "date_to": "2026-08-09"}),
        ("metric_series", {"metric": "steps", "date_from": "2026-08-09", "date_to": "2026-08-09"}),
        ("sleep_detail", {"date_from": "2026-08-08", "date_to": "2026-08-09"}),
        ("get_sync_health", {"days": 7}),
        ("inspect_last_payload", {}),
    ]
    for name, args in calls:
        result = await _call_tool(client, name, args)
        assert "caveats" in result, f"{name} missing caveats key"
        assert isinstance(result["caveats"], list), f"{name} caveats not a list"
        assert len(result["caveats"]) > 0, f"{name} has empty caveats"
