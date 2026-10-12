"""Derived summaries obey bounds and never silently change requested buckets."""
from datetime import date, timedelta

import pytest

from tests.mcp_client import _call_tool, _mcp_call


@pytest.mark.parametrize("tool, args", [
    ("daily_summary", {}), ("metric_series", {"metric": "steps"}),
])
async def test_daily_requests_above_400_objects_are_errors(client, tool, args):
    start = date(2026, 1, 1)
    response = await _mcp_call(client, "tools/call", {"name": tool, "arguments": {
        "date_from": start.isoformat(), "date_to": (start + timedelta(days=400)).isoformat(), **args,
    }})
    assert response["result"]["isError"] is True


async def test_explicit_weekly_series_preserves_missing_buckets(client):
    start = date(2026, 1, 1)
    result = await _call_tool(client, "metric_series", {
        "metric": "steps", "bucket": "week", "date_from": start.isoformat(),
        "date_to": (start + timedelta(days=400)).isoformat(),
    })
    assert 50 < len(result["points"]) < 60
    assert all(point["value"] is None for point in result["points"])


async def test_weekly_requests_above_400_buckets_are_errors(client):
    start = date(2026, 1, 5)
    response = await _mcp_call(client, "tools/call", {"name": "metric_series", "arguments": {
        "metric": "steps", "bucket": "week", "date_from": start.isoformat(),
        "date_to": (start + timedelta(weeks=400)).isoformat(),
    }})
    assert response["result"]["isError"] is True


async def test_summary_keeps_partial_coverage_despite_present_average(client):
    from tests.mcp_client import _ingest
    await _ingest(client, {"timestamp": "2026-08-09T12:00:00Z", "app_version": "synthetic",
                           "source": "health_connect", "heart_rate": [
                               {"time": "2026-08-09T01:00:00Z", "bpm": 60},
                               {"time": "2026-08-09T10:00:00Z", "bpm": 80},
                           ]})
    result = await _call_tool(client, "daily_summary", {"date_from": "2026-08-09", "date_to": "2026-08-09"})
    assert result["days"][0]["hr_avg"] == 70
    assert result["coverage"]["2026-08-09"]["hr_avg"] == "partial"
    assert any("partial recorded coverage" in caveat for caveat in result["caveats"])
