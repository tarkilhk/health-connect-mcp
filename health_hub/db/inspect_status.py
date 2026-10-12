"""Read coverage, cached rollups and database counts for the debug inspector."""
import json
from datetime import UTC, datetime, timedelta

import aiosqlite

from health_hub.aggregate.timewindow import date_window, local_date_of, utc_sort_key
from health_hub.config import Settings
from health_hub.contract.registry import TYPE_REGISTRY
from health_hub.db.events import load_attributed_events


async def get_coverage(db: aiosqlite.Connection, days: int, settings: Settings) -> dict:
    today = local_date_of(datetime.now(UTC).isoformat(), settings.timezone, settings.day_boundary_hour)
    end = datetime.fromisoformat(today).date()
    day_list = [(end - timedelta(days=i)).isoformat() for i in range(days)]
    types = list(TYPE_REGISTRY)
    window = date_window(day_list[-1], today, settings.timezone, settings.day_boundary_hour)
    matrix = {day: dict.fromkeys(types, 0) for day in day_list}
    for row in await load_attributed_events(db, window):
        instant = row["end_utc"] if row["data_type"] == "sleep" else row["start_utc"]
        day = local_date_of(instant, settings.timezone, settings.day_boundary_hour)
        if row["data_type"] in matrix[day]:
            matrix[day][row["data_type"]] += 1
    return {"days": day_list, "types": types, "matrix": matrix}


async def get_rollup(db: aiosqlite.Connection, local_date: str, settings: Settings) -> dict:
    window = date_window(local_date, local_date, settings.timezone, settings.day_boundary_hour)
    counts: dict[str, int] = {}
    sums: dict[str, float | None] = {}
    for row in await load_attributed_events(db, window):
        data_type, value = row["data_type"], row["value_num"]
        counts[data_type] = counts.get(data_type, 0) + 1
        sums.setdefault(data_type, None)
        if value is not None:
            sums[data_type] = (sums[data_type] or 0) + value
    async with db.execute(
        """SELECT steps, distance_m, active_kcal, total_kcal, sleep_in_bed_s,
                  sleep_asleep_s, hr_avg, hr_min, hr_max, hr_resting, weight_kg, coverage_json
           FROM daily_rollups WHERE local_date = ?""", (local_date,),
    ) as cursor:
        row = await cursor.fetchone()
    rollup = dict(row) if row is not None else None
    if rollup is not None:
        rollup["coverage"] = json.loads(rollup.pop("coverage_json"))
    return {"local_date": local_date, "rollup": rollup,
            "naive_sums": sums if rollup is not None else None, "event_counts": counts}


async def get_stats(db: aiosqlite.Connection) -> dict:
    async with db.execute("SELECT count(*) FROM events") as cursor:
        total_events = (await cursor.fetchone())[0]
    async with db.execute(
        "SELECT data_type, count(*) FROM events GROUP BY data_type ORDER BY data_type"
    ) as cursor:
        type_counts = {row[0]: row[1] for row in await cursor.fetchall()}
    async with db.execute(
        "SELECT min(utc_instant(start_utc)), max(utc_instant(start_utc)) FROM events"
    ) as cursor:
        first, last = await cursor.fetchone()
    async with db.execute(
        "SELECT received_at, app_version FROM raw_payloads ORDER BY id DESC LIMIT 1"
    ) as cursor:
        row = await cursor.fetchone()
    async with db.execute("SELECT count(*) FROM raw_payloads") as cursor:
        total_payloads = (await cursor.fetchone())[0]
    return {"total_events": total_events, "type_counts": type_counts,
            "date_from": utc_sort_key(first)[:10] if first else None,
            "date_to": utc_sort_key(last)[:10] if last else None,
            "last_ingest_at": row[0] if row else None,
            "last_app_version": row[1] if row else None, "total_payloads": total_payloads}
