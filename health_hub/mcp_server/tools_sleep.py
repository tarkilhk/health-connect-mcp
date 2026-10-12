"""Sleep-session detail with shared derived totals and unchanged stage values."""
from health_hub.aggregate.sleep import sleep_totals
from health_hub.aggregate.timewindow import date_window, local_date_of
from health_hub.config import load_settings
from health_hub.db.sleep import load_sleep_stages
from health_hub.mcp_server.server import get_db, mcp


@mcp.tool()
async def sleep_detail(date_from: str, date_to: str) -> dict:
    """Derived sleep detail for inclusive local dates, attributed by session end.

    Time asleep is null when recorded stages do not completely establish it.
    At most 400 sessions are returned; use health_records for paginated access
    to original sleep records and stages.
    """
    settings = load_settings()
    begin, finish = date_window(date_from, date_to, settings.timezone, settings.day_boundary_hour)
    db = get_db()
    async with db.execute(
        "SELECT event_id, start_utc, end_utc FROM events WHERE data_type = 'sleep' "
        "AND utc_instant(end_utc) >= utc_instant(?) AND utc_instant(end_utc) < utc_instant(?) "
        "ORDER BY utc_instant(end_utc), event_id LIMIT 401",
        (begin, finish),
    ) as cursor:
        rows = await cursor.fetchall()
    stages = await load_sleep_stages(db, [row[0] for row in rows[:400]])
    sessions, caveats = [], []
    for event_id, start, end in rows[:400]:
        in_bed, asleep = sleep_totals(start, end, stages[event_id])
        if asleep is None:
            caveats.append(f"Sleep session ending {end} has incomplete or invalid stages; time asleep is unknown.")
        sessions.append({"date": local_date_of(end, settings.timezone, settings.day_boundary_hour),
                         "start_utc": start, "end_utc": end, "time_in_bed_s": in_bed,
                         "time_asleep_s": asleep, "stages": stages[event_id]})
    if len(rows) > 400:
        caveats.append("More than 400 sleep sessions match; use health_records for paginated record access.")
    if not rows:
        caveats.append(f"No stored sleep sessions for {date_from} to {date_to}.")
    return {"sessions": sessions, "caveats": caveats or ["Sleep totals derived from complete recorded stages."]}
