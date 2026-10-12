"""Reproduce the audited defects through actual MCP and ingestion calls."""
import asyncio

from health_hub.aggregate.timewindow import utc_sort_key
from health_hub.ingest import persist
from tests.mcp_client import _call_tool, _ingest


def _payload(**records):
    return {"timestamp": "2026-08-09T12:00:00Z", "app_version": "synthetic",
            "source": "health_connect", **records}


async def test_sleep_detail_and_summary_agree_without_changing_records(client):
    session = {"session_end_time": "2026-08-09T06:00:00Z", "duration_seconds": 28800,
               "stages": [
                   {"stage": "awake_in_bed", "start_time": "2026-08-08T22:00:00Z",
                    "end_time": "2026-08-09T00:00:00Z", "duration_seconds": 7200},
                   {"stage": "light", "start_time": "2026-08-09T00:00:00Z",
                    "end_time": "2026-08-09T06:00:00Z", "duration_seconds": 21600},
               ]}
    await _ingest(client, _payload(sleep=[session]))
    window = {"date_from": "2026-08-09", "date_to": "2026-08-09"}
    records = await _call_tool(client, "health_records", {"data_type": "sleep", **window})
    assert records["records"][0]["values"] == session
    detail = await _call_tool(client, "sleep_detail", window)
    summary = await _call_tool(client, "daily_summary", window)
    assert detail["sessions"][0]["time_asleep_s"] == 21600
    assert summary["days"][0]["sleep_asleep_s"] == 21600


async def test_summary_and_detail_use_actual_offset_instants(client):
    await _ingest(client, _payload(sleep=[{
        "session_end_time": "2026-08-09T00:30:00+08:00", "duration_seconds": 3600,
    }], weight=[
        {"time": "2026-08-09T12:00:00+08:00", "kilograms": 70},
        {"time": "2026-08-09T05:00:00Z", "kilograms": 71},
    ]))
    detail = await _call_tool(client, "sleep_detail", {
        "date_from": "2026-08-09", "date_to": "2026-08-09",
    })
    assert detail["sessions"] == []
    summary = await _call_tool(client, "daily_summary", {
        "date_from": "2026-08-09", "date_to": "2026-08-09",
    })
    assert summary["days"][0]["weight_kg"] == 71
    assert "sleep_in_bed_s" not in summary["days"][0]


async def test_mcp_cannot_read_uncommitted_ingestion(client, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    original = persist.upsert_events

    async def pause_after_write(*args, **kwargs):
        result = await original(*args, **kwargs)
        entered.set()
        await release.wait()
        return result

    monkeypatch.setattr(persist, "upsert_events", pause_after_write)
    task = asyncio.create_task(_ingest(client, _payload(heart_rate=[{
        "time": "2026-08-09T12:00:00Z", "bpm": 60,
    }])))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        result = await _call_tool(client, "health_records", {
            "data_type": "heart_rate", "date_from": "2026-08-09", "date_to": "2026-08-09",
        })
        assert result["records"] == []
    finally:
        release.set()
        await task


async def test_record_page_does_not_convert_the_entire_type(client, test_app):
    await _ingest(client, _payload(heart_rate=[
        {"time": "2026-08-09T12:00:00Z", "bpm": 60, "sample_index": i}
        for i in range(5000)
    ]))
    calls = 0

    def counted_key(value):
        nonlocal calls
        calls += 1
        return utc_sort_key(value)

    await test_app.state.db.create_function("utc_instant", 1, counted_key, deterministic=True)
    result = await _call_tool(client, "health_records", {
        "data_type": "heart_rate", "date_from": "2026-08-09", "date_to": "2026-08-09", "limit": 1,
    })
    assert len(result["records"]) == 1
    assert result["next_cursor"]
    assert calls < 20
