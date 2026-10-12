"""L1 event, sleep-stage, and screen-time writers.

All upserts are idempotent:
- events: ``INSERT ... ON CONFLICT(event_id) DO NOTHING`` (content-hash dedup)
- sleep_stages: ``INSERT OR IGNORE`` keyed on (session_event_id, stage, start_utc)
- screen_time_days: upsert by local_date; apps are DELETE + INSERT per date

These functions do NOT commit. The caller is responsible for calling
``db.commit()`` (or ``db.rollback()``) so that events + stages + diagnostics
can be wrapped in a single atomic transaction by the route handler.
"""
from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite

from health_hub.contract.payload import Event
from health_hub.ingest.identity import event_id


async def upsert_events(
    db: aiosqlite.Connection,
    events: list[Event],
    payload_id: int,
) -> tuple[int, int]:
    """Insert events with ``ON CONFLICT(event_id) DO NOTHING``.

    Returns ``(stored, duplicates)`` where ``stored`` is the count of newly
    inserted rows and ``duplicates`` is the count of skipped conflicts.
    Does NOT commit — caller manages the transaction.
    """
    stored = 0
    duplicates = 0
    now = datetime.now(UTC).isoformat()

    for evt in events:
        eid = event_id(evt.data_type, evt.start_utc, evt.end_utc, evt.value_json)
        cursor = await db.execute(
            """INSERT INTO events
               (event_id, data_type, start_utc, end_utc, value_num, value_json,
                origin, payload_id, first_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(event_id) DO NOTHING""",
            (
                eid,
                evt.data_type,
                evt.start_utc,
                evt.end_utc,
                evt.value_num,
                evt.value_json,
                None,
                payload_id,
                now,
            ),
        )
        if cursor.rowcount == 1:
            stored += 1
        else:
            duplicates += 1

    return stored, duplicates


async def upsert_sleep_stages(
    db: aiosqlite.Connection,
    stages: list[dict],
) -> int:
    """Insert sleep stage rows with ``INSERT OR IGNORE``. Returns count inserted.

    Does NOT commit — caller manages the transaction.
    """
    inserted = 0
    for s in stages:
        cursor = await db.execute(
            """INSERT OR IGNORE INTO sleep_stages
               (session_event_id, stage, start_utc, end_utc, seconds)
               VALUES (?, ?, ?, ?, ?)""",
            (
                s["session_event_id"],
                s["stage"],
                s["start_utc"],
                s["end_utc"],
                s["seconds"],
            ),
        )
        if cursor.rowcount == 1:
            inserted += 1
    return inserted


async def upsert_screen_time(
    db: aiosqlite.Connection,
    screen_time_data: list[dict],
    payload_id: int,
) -> int:
    """Upsert ``screen_time_days`` and replace apps per date.

    Each item in ``screen_time_data`` has:
    ``date`` (local_date), ``total_screen_time_minutes``, ``apps`` (list of
    ``{package, name, minutes, last_used}``).

    Returns the count of days written.

    Does NOT commit — caller manages the transaction.
    """
    now = datetime.now(UTC).isoformat()
    days_written = 0

    for day in screen_time_data:
        local_date = day["date"]
        total_minutes = day["total_screen_time_minutes"]

        await db.execute(
            """INSERT INTO screen_time_days
               (local_date, total_minutes, payload_id, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(local_date) DO UPDATE SET
                 total_minutes = excluded.total_minutes,
                 payload_id = excluded.payload_id,
                 updated_at = excluded.updated_at""",
            (local_date, total_minutes, payload_id, now),
        )

        await _replace_screen_apps(db, local_date, day.get("apps") or [])

        days_written += 1

    return days_written


async def _replace_screen_apps(db: aiosqlite.Connection, local_date: str, apps: list[dict]) -> None:
    await db.execute("DELETE FROM screen_time_apps WHERE local_date = ?", (local_date,))
    await db.executemany(
        """INSERT OR REPLACE INTO screen_time_apps
           (local_date, package, name, minutes, last_used) VALUES (?, ?, ?, ?, ?)""",
        [(local_date, app.get("package"), app.get("name"), app.get("minutes"), app.get("last_used"))
         for app in apps],
    )
