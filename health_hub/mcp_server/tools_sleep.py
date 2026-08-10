"""MCP tool: sleep_detail.

Returns sleep sessions with stages for a date range. Sessions are attributed
by ``end_utc`` per SPEC §7.3 (a session ending at 06:00 belongs to that
local date, not the previous one).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from health_hub.aggregate.timewindow import day_bounds_utc, local_date_of
from health_hub.config import load_settings
from health_hub.mcp_server.server import get_db, mcp

_settings = load_settings()


def _parse_date(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _end_window(date_from: str, date_to: str) -> tuple[str, str]:
    """Return [start_utc, end_utc) for the inclusive date range.

    Sleep sessions are queried by ``end_utc`` (SPEC §7.3), so the window
    covers from the start of date_from to the end of date_to.
    """
    start = _parse_date(date_from)
    end = _parse_date(date_to) + timedelta(days=1)
    return start.strftime("%Y-%m-%dT00:00:00Z"), end.strftime("%Y-%m-%dT00:00:00Z")


@mcp.tool()
async def sleep_detail(
    date_from: str,
    date_to: str,
) -> dict:
    """Get detailed sleep data with stages for a date range.

    Args:
        date_from: Start date YYYY-MM-DD
        date_to: End date YYYY-MM-DD (inclusive)
    """
    db = get_db()
    try:
        start_date = _parse_date(date_from).date()
        end_date = _parse_date(date_to).date()
    except ValueError:
        return {"sessions": [], "caveats": ["invalid date format, expected YYYY-MM-DD"]}

    # Build per-date UTC windows using configured timezone + day boundary,
    # then merge into one contiguous range.
    tz = _settings.timezone
    boundary = _settings.day_boundary_hour
    begin_utc, _ = day_bounds_utc(start_date.isoformat(), tz, boundary)
    _, finish_utc = day_bounds_utc(end_date.isoformat(), tz, boundary)

    cursor = await db.execute(
        "SELECT event_id, start_utc, end_utc, value_json "
        "FROM events WHERE data_type = 'sleep' AND end_utc >= ? AND end_utc < ? "
        "ORDER BY end_utc",
        (begin_utc, finish_utc),
    )
    sleep_rows = await cursor.fetchall()

    if not sleep_rows:
        return {"sessions": [], "caveats": [f"no sleep sessions for {date_from} to {date_to}"]}

    sessions = []
    caveats: list[str] = []
    for row in sleep_rows:
        event_id, start_utc_val, end_utc_val, value_json = row
        stages = await _load_stages(db, event_id)
        time_in_bed, time_asleep = _parse_durations(value_json, stages)
        date_label = local_date_of(end_utc_val, tz, boundary)

        if not stages:
            caveats.append(f"sleep session ending {end_utc_val} has no stage data")
        session = {
            "date": date_label,
            "start_utc": start_utc_val,
            "end_utc": end_utc_val,
            "time_in_bed_s": time_in_bed,
            "time_asleep_s": time_asleep,
            "stages": stages,
        }
        sessions.append(session)

    return {"sessions": sessions, "caveats": caveats or ["all sessions have stage data"]}


async def _load_stages(db, session_event_id: str) -> list[dict]:
    """Load sleep stages for one session, ordered by start time."""
    cursor = await db.execute(
        "SELECT stage, start_utc, end_utc, seconds "
        "FROM sleep_stages WHERE session_event_id = ? ORDER BY start_utc",
        (session_event_id,),
    )
    return [
        {"stage": r[0], "start_utc": r[1], "end_utc": r[2], "seconds": r[3]}
        for r in await cursor.fetchall()
    ]


def _parse_durations(value_json: str, stages: list[dict]) -> tuple[int, int]:
    """Extract (time_in_bed_s, time_asleep_s) from the event value_json.

    Falls back to summing stage seconds if the JSON does not carry explicit
    durations. ``time_asleep`` excludes 'awake' stages.
    """
    try:
        data = json.loads(value_json)
    except (json.JSONDecodeError, TypeError):
        data = {}

    in_bed = data.get("duration_seconds")
    if in_bed is None and stages:
        in_bed = sum(s["seconds"] for s in stages)

    asleep = data.get("asleep_seconds")
    if asleep is None and stages:
        asleep = sum(s["seconds"] for s in stages if s["stage"] != "awake")

    return int(in_bed or 0), int(asleep or 0)
