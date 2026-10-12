"""MCP record query bounds, stable pagination, and validation."""
from datetime import UTC, datetime, timedelta

import pytest

from tests.mcp_client import _call_tool, _ingest, _init_session, _mcp_call


def _payload(**types):
    return {"timestamp": "2026-08-09T12:00:00Z", "app_version": "synthetic",
            "source": "health_connect", **types}


async def _query(client, sid, data_type="heart_rate", **kwargs):
    return await _call_tool(client, sid, "health_records", {
        "data_type": data_type, "date_from": "2026-08-09", "date_to": "2026-08-09", **kwargs,
    })


async def test_default_and_max_page_limits_with_tied_timestamps(client):
    samples = [{"time": "2026-08-09T12:00:00Z", "bpm": 60, "sample_index": i}
               for i in range(405)]
    await _ingest(client, _payload(heart_rate=samples))
    sid = await _init_session(client)
    default = await _query(client, sid)
    assert len(default["records"]) == 100
    page = await _query(client, sid, limit=400)
    assert len(page["records"]) == 400
    assert page["next_cursor"]
    final = await _query(client, sid, limit=400, cursor=page["next_cursor"])
    assert len(final["records"]) == 5
    assert final["next_cursor"] is None
    all_ids = [r["event_id"] for r in page["records"] + final["records"]]
    assert len(set(all_ids)) == 405
    assert all_ids == sorted(all_ids)


async def test_cursor_is_bound_to_original_type_and_window(client):
    await _ingest(client, _payload(heart_rate=[
        {"time": "2026-08-09T12:00:00Z", "bpm": 60},
        {"time": "2026-08-09T12:01:00Z", "bpm": 61},
    ]))
    sid = await _init_session(client)
    page = await _query(client, sid, limit=1)
    for changes in [{"data_type": "weight"}, {"date_to": "2026-08-10"}]:
        response = await _mcp_call(client, "tools/call", {
            "name": "health_records", "arguments": {
                "data_type": "heart_rate", "date_from": "2026-08-09", "date_to": "2026-08-09",
                "cursor": page["next_cursor"], **changes,
            },
        }, sid)
        assert response["result"]["isError"] is True


async def test_local_window_handles_offset_instants_and_interval_intersection(client, monkeypatch):
    # Asia/Singapore at 04:00 begins at 20:00Z on the previous UTC day.
    await _ingest(client, _payload(
        heart_rate=[
            {"time": "2026-08-08T19:59:59Z", "bpm": 50},
            {"time": "2026-08-09T04:00:00+08:00", "bpm": 60},
            {"time": "2026-08-09T19:59:59Z", "bpm": 70},
            {"time": "2026-08-09T20:00:00Z", "bpm": 80},
        ],
        menstruation_period=[
            {"start_time": "2026-08-01T00:00:00Z", "end_time": "2026-08-10T00:00:00Z"},
            {"start_time": "2026-08-08T19:00:00Z", "end_time": "2026-08-08T20:00:00Z"},
            {"start_time": "2026-08-09T20:00:00Z", "end_time": "2026-08-10T00:00:00Z"},
        ],
    ))
    monkeypatch.setenv("TIMEZONE", "Asia/Singapore")
    monkeypatch.setenv("DAY_BOUNDARY_HOUR", "4")
    sid = await _init_session(client)
    instant = await _query(client, sid)
    assert [r["values"]["bpm"] for r in instant["records"]] == [60, 70]
    assert instant["window_start_utc"] == "2026-08-08T20:00:00+00:00"
    assert instant["window_end_utc"] == "2026-08-09T20:00:00+00:00"
    intervals = await _query(client, sid, "menstruation_period")
    assert len(intervals["records"]) == 1
    assert intervals["records"][0]["start_utc"] == "2026-08-01T00:00:00Z"


async def test_sleep_uses_end_and_preserves_nested_stages(client):
    await _ingest(client, _payload(sleep=[
        {"session_end_time": "2026-08-09T06:00:00Z", "duration_seconds": 28800,
         "stages": [{"stage": "awake_in_bed", "start_time": "2026-08-08T22:00:00Z",
                     "end_time": "2026-08-08T23:00:00Z", "duration_seconds": 3600}]},
        {"session_end_time": "2026-08-10T06:00:00Z", "duration_seconds": 28800},
    ]))
    sid = await _init_session(client)
    result = await _query(client, sid, "sleep")
    assert len(result["records"]) == 1
    session = result["records"][0]
    assert session["start_utc"] == "2026-08-08T22:00:00Z"
    assert session["values"]["stages"][0]["stage"] == "awake_in_bed"
    assert any("time in bed" in c for c in result["caveats"])


async def test_empty_and_sparse_values_are_not_zero(client):
    await _ingest(client, _payload(nutrition=[{
        "start_time": "2026-08-09T12:00:00Z", "end_time": "2026-08-09T12:00:00Z",
        "protein_grams": 0, "fat_grams": None,
    }]))
    sid = await _init_session(client)
    result = await _query(client, sid, "nutrition")
    values = result["records"][0]["values"]
    assert values["protein_grams"] == 0
    assert values["calories"] is None
    assert values["fat_grams"] is None
    empty = await _query(client, sid, "sexual_activity")
    assert empty["records"] == []
    assert empty["next_cursor"] is None
    assert any("negative finding" in c for c in empty["caveats"])


@pytest.mark.parametrize("changes", [
    {"data_type": "nonexistent"}, {"data_type": "' OR 1=1 --"},
    {"limit": 0}, {"limit": 401}, {"limit": 1.5}, {"limit": True},
    {"date_from": "2026-02-30"}, {"date_from": "2026-08-10"},
    {"date_from": "20260809"}, {"date_from": "2026-08-09T00:00:00Z"},
    {"cursor": "garbage"}, {"cursor": "W10="},
])
async def test_invalid_arguments_are_mcp_errors(client, changes):
    sid = await _init_session(client)
    response = await _mcp_call(client, "tools/call", {
        "name": "health_records", "arguments": {
            "data_type": "heart_rate", "date_from": "2026-08-09", "date_to": "2026-08-09",
            **changes,
        },
    }, sid)
    assert response["result"]["isError"] is True


async def test_pagination_orders_different_timezone_offsets_by_instant(client):
    begin = datetime(2026, 8, 9, 0, tzinfo=UTC)
    samples = [{"time": (begin + timedelta(hours=h)).isoformat(), "bpm": 60 + h}
               for h in [2, 0, 1]]
    samples[1]["time"] = "2026-08-09T08:00:00+08:00"
    await _ingest(client, _payload(heart_rate=samples))
    sid = await _init_session(client)
    cursor, values = None, []
    for _ in range(3):
        result = await _query(client, sid, limit=1, cursor=cursor)
        values.append(result["records"][0]["values"]["bpm"])
        cursor = result["next_cursor"]
    assert values == [60, 61, 62]
    assert cursor is None


async def test_fractional_timestamps_keep_midnight_bounds_and_chronological_order(client):
    await _ingest(client, _payload(heart_rate=[
        {"time": "2026-08-09T12:00:00.000002Z", "bpm": 62},
        {"time": "2026-08-09T12:00:00.000001Z", "bpm": 61},
        {"time": "2026-08-09T23:59:59.999999Z", "bpm": 63},
        {"time": "2026-08-10T00:00:00Z", "bpm": 64},
    ]))
    sid = await _init_session(client)
    cursor, values = None, []
    for _ in range(3):
        page = await _query(client, sid, limit=1, cursor=cursor)
        values.append(page["records"][0]["values"]["bpm"])
        cursor = page["next_cursor"]
    assert values == [61, 62, 63]
    assert cursor is None


async def test_daylight_saving_window_uses_actual_local_day(client, monkeypatch):
    monkeypatch.setenv("TIMEZONE", "America/New_York")
    sid = await _init_session(client)
    result = await _query(client, sid, date_from="2026-03-08", date_to="2026-03-08")
    start = datetime.fromisoformat(result["window_start_utc"])
    end = datetime.fromisoformat(result["window_end_utc"])
    assert end - start == timedelta(hours=23)
