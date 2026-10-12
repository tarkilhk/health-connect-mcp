"""Load stored events by the attribution shared by daily rollups and inspectors."""
import aiosqlite

_DAY_EVENTS_SQL = """
    SELECT event_id, data_type, start_utc, end_utc, value_num, value_json
    FROM events WHERE data_type != 'sleep'
      AND utc_instant(start_utc) >= utc_instant(?)
      AND utc_instant(start_utc) < utc_instant(?)
    UNION ALL
    SELECT event_id, data_type, start_utc, end_utc, value_num, value_json
    FROM events WHERE data_type = 'sleep'
      AND utc_instant(end_utc) >= utc_instant(?)
      AND utc_instant(end_utc) < utc_instant(?)
"""


async def load_attributed_events(db: aiosqlite.Connection, window: tuple[str, str]) -> list[aiosqlite.Row]:
    async with db.execute(_DAY_EVENTS_SQL, (*window, *window)) as cursor:
        return list(await cursor.fetchall())
