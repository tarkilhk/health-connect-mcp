"""Summary cache rebuilds preserve unitary records and are atomic on failure."""
import pytest

from health_hub.rebuild_rollups import rebuild_rollups
from tests.mcp_client import _call_tool, _ingest


async def test_rebuild_corrects_cache_without_modifying_stored_records(client, test_app, db):
    window = {"date_from": "2026-08-09", "date_to": "2026-08-09"}
    await _ingest(client, {"timestamp": "2026-08-09T12:00:00Z", "app_version": "synthetic",
                           "source": "health_connect", "weight": [
                               {"time": "2026-08-09T12:00:00+08:00", "kilograms": 70},
                               {"time": "2026-08-09T05:00:00Z", "kilograms": 71},
                           ], "sleep": [{"session_end_time": "2026-08-09T06:00:00Z",
                                         "duration_seconds": 28800}]})
    before = await _call_tool(client, "health_records", {"data_type": "sleep", **window})
    await db.execute("UPDATE daily_rollups SET weight_kg = 70, sleep_asleep_s = 28800")
    await db.commit()
    assert await rebuild_rollups(test_app.state.settings) == 1
    result = await _call_tool(client, "daily_summary", window)
    assert result["days"][0]["weight_kg"] == 71
    assert "sleep_asleep_s" not in result["days"][0]
    assert await _call_tool(client, "health_records", {"data_type": "sleep", **window}) == before


async def test_failed_rebuild_preserves_existing_cache(client, test_app, db, monkeypatch):
    from importlib import import_module
    module = import_module("health_hub.rebuild_rollups")
    await _ingest(client, {"timestamp": "2026-08-09T12:00:00Z", "app_version": "synthetic",
                           "source": "health_connect", "weight": [
                               {"time": "2026-08-09T05:00:00Z", "kilograms": 71},
                           ]})

    async def fail(*args):
        raise RuntimeError("synthetic rebuild failure")

    monkeypatch.setattr(module, "recalc_rollups_for_dates", fail)
    with pytest.raises(RuntimeError, match="synthetic rebuild failure"):
        await rebuild_rollups(test_app.state.settings)
    async with db.execute("SELECT weight_kg FROM daily_rollups") as cursor:
        assert (await cursor.fetchone())[0] == 71
