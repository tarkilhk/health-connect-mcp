"""Sleep derivation never invents missing or conflicting stage coverage."""
import pytest

from health_hub.aggregate.sleep import sleep_totals
from tests.mcp_client import _call_tool, _ingest

START, END = "2026-08-09T00:00:00Z", "2026-08-09T01:00:00Z"


@pytest.mark.parametrize("stage", ["awake", "awake_in_bed", "out_of_bed"])
def test_awake_stages_are_excluded(stage):
    assert sleep_totals(START, END, [{"stage": stage, "start_utc": START,
                                     "end_utc": END, "seconds": 3600}]) == (3600, 0)


@pytest.mark.parametrize("stages", [
    [], [{"stage": "unknown", "start_utc": START, "end_utc": END, "seconds": 3600}],
    [{"stage": "light", "start_utc": "2026-08-09T00:30:00Z", "end_utc": END, "seconds": 1800}],
    [{"stage": "light", "start_utc": START, "end_utc": END, "seconds": 1}],
    [{"stage": "light", "start_utc": START, "end_utc": END, "seconds": 3600}] * 2,
])
def test_incomplete_or_invalid_stages_leave_asleep_unknown(stages):
    assert sleep_totals(START, END, stages) == (3600, None)


async def test_missing_stages_are_unknown_in_both_mcp_aggregates(client):
    await _ingest(client, {"timestamp": "2026-08-09T12:00:00Z", "app_version": "synthetic",
                           "source": "health_connect", "sleep": [
                               {"session_end_time": END, "duration_seconds": 3600},
                           ]})
    window = {"date_from": "2026-08-09", "date_to": "2026-08-09"}
    detail = await _call_tool(client, "sleep_detail", window)
    summary = await _call_tool(client, "daily_summary", window)
    assert detail["sessions"][0]["time_in_bed_s"] == 3600
    assert detail["sessions"][0]["time_asleep_s"] is None
    assert summary["days"][0]["sleep_in_bed_s"] == 3600
    assert summary["days"][0].get("sleep_asleep_s") is None
