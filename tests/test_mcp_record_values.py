"""Stored-data caveats and structured MCP output."""
import json

from tests.mcp_client import _call_tool, _ingest, _mcp_call, _tool_result


async def test_invalid_stored_value_is_reported_and_pagination_advances(client, db):
    await _ingest(client, {
        "timestamp": "2026-08-09T12:00:00Z", "app_version": "synthetic", "source": "health_connect",
        "heart_rate": [{"time": "2026-08-09T12:00:00Z", "bpm": 60},
                       {"time": "2026-08-09T12:01:00Z", "bpm": 61}],
    })
    # Simulate corrupt stored input; the typed reader must not coerce a string
    # into a measurement or hide the problem behind an empty-page message.
    await db.execute("UPDATE events SET value_json = ? WHERE start_utc = ?", (
        json.dumps({"time": "2026-08-09T12:00:00Z", "bpm": "unknown"}), "2026-08-09T12:00:00Z",
    ))
    await db.commit()
    args = {"data_type": "heart_rate", "date_from": "2026-08-09", "date_to": "2026-08-09", "limit": 1}
    page = await _call_tool(client, "health_records", args)
    assert page["records"] == []
    assert any("invalid values" in c for c in page["caveats"])
    assert page["next_cursor"]
    final = await _call_tool(client, "health_records", {**args, "cursor": page["next_cursor"]})
    assert final["records"][0]["values"]["bpm"] == 61
    assert final["next_cursor"] is None


async def test_new_tools_return_matching_text_and_structured_content(client):
    for name, args in [("list_data_types", {}), ("health_records", {
        "data_type": "test", "date_from": "2026-08-09", "date_to": "2026-08-09",
    })]:
        response = await _mcp_call(client, "tools/call", {"name": name, "arguments": args})
        assert response["result"]["structuredContent"] == _tool_result(response)
