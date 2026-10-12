"""Load stage lists for selected sleep events in one parameterized query."""
import json

import aiosqlite


async def load_sleep_stages(db: aiosqlite.Connection, event_ids: list[str]) -> dict[str, list[dict]]:
    stages = {event_id: [] for event_id in event_ids}
    async with db.execute(
        "SELECT session_event_id, stage, start_utc, end_utc, seconds FROM sleep_stages "
        "WHERE session_event_id IN (SELECT value FROM json_each(?)) "
        "ORDER BY utc_instant(start_utc), stage",
        (json.dumps(event_ids),),
    ) as cursor:
        for row in await cursor.fetchall():
            stages[row[0]].append({"stage": row[1], "start_utc": row[2],
                                   "end_utc": row[3], "seconds": row[4]})
    return stages
