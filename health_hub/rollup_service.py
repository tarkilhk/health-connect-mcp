"""I/O wrapper for daily rollup recomputation.

Pure aggregate logic lives in :mod:`health_hub.aggregate`. This module
adds the DB layer: it loads events + sleep stages for a local date's UTC
window, calls :func:`compute_rollup`, and UPSERTs the result into
``daily_rollups``.

Does NOT commit — the caller (route handler) manages the transaction so
that events + stages + rollups land atomically.

Sleep attribution rule (SPEC §7.3): a sleep session belongs to the
local date of its ``end_utc``, not its ``start_utc``. A session that
starts at 22:00 on Aug 8 and ends at 06:00 on Aug 9 must roll up under
Aug 9. We therefore query sleep events by ``end_utc`` in the date's UTC
window, while all other types are queried by ``start_utc``.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import aiosqlite

from health_hub.aggregate.rollup import compute_rollup
from health_hub.aggregate.timewindow import day_bounds_utc
from health_hub.contract.payload import Event

log = logging.getLogger(__name__)

# Non-sleep events: attributed by start_utc.
_EVENTS_NON_SLEEP_SQL = (
    "SELECT event_id, data_type, start_utc, end_utc, value_num, value_json "
    "FROM events WHERE data_type != 'sleep' AND start_utc >= ? AND start_utc < ?"
)

# Sleep events: attributed by end_utc per SPEC §7.3.
_EVENTS_SLEEP_SQL = (
    "SELECT event_id, data_type, start_utc, end_utc, value_num, value_json "
    "FROM events WHERE data_type = 'sleep' AND end_utc >= ? AND end_utc < ?"
)

# Sleep stages for sessions whose end_utc falls in the window.
_STAGES_SQL = (
    "SELECT session_event_id, stage, start_utc, end_utc, seconds "
    "FROM sleep_stages WHERE session_event_id IN ("
    "  SELECT event_id FROM events"
    "  WHERE data_type='sleep' AND end_utc >= ? AND end_utc < ?"
    ")"
)

_UPSERT_SQL = (
    "INSERT INTO daily_rollups"
    " (local_date, steps, distance_m, active_kcal, total_kcal,"
    "  sleep_in_bed_s, sleep_asleep_s, hr_avg, hr_min, hr_max,"
    "  hr_resting, weight_kg, coverage_json, computed_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    " ON CONFLICT(local_date) DO UPDATE SET"
    "  steps=excluded.steps,"
    "  distance_m=excluded.distance_m,"
    "  active_kcal=excluded.active_kcal,"
    "  total_kcal=excluded.total_kcal,"
    "  sleep_in_bed_s=excluded.sleep_in_bed_s,"
    "  sleep_asleep_s=excluded.sleep_asleep_s,"
    "  hr_avg=excluded.hr_avg,"
    "  hr_min=excluded.hr_min,"
    "  hr_max=excluded.hr_max,"
    "  hr_resting=excluded.hr_resting,"
    "  weight_kg=excluded.weight_kg,"
    "  coverage_json=excluded.coverage_json,"
    "  computed_at=excluded.computed_at"
)


async def recalc_rollups_for_dates(
    db: aiosqlite.Connection,
    local_dates: list[str],
    tz: str,
    day_boundary_hour: int,
) -> list[str]:
    """Recalculate ``daily_rollups`` for the given local dates.

    For each date:
    1. Resolve its [start_utc, end_utc) window via :func:`day_bounds_utc`.
    2. Load non-sleep events whose ``start_utc`` falls in that window.
    3. Load sleep events whose ``end_utc`` falls in that window (SPEC §7.3).
    4. Load sleep stages for those sleep sessions.
    5. Build :class:`Event` objects and call :func:`compute_rollup`.
    6. UPSERT the result into ``daily_rollups``.

    Returns the list of dates recalculated (all of them on success). On
    error, re-raises so the caller's transaction can roll back. The
    ingest route treats a rollup failure as a 500 — events + stages +
    rollups must commit atomically; a partial commit would leave the
    daily_rollups table out of sync with the events table.

    Does NOT commit — the caller manages the transaction.
    """
    done: list[str] = []
    now = datetime.now(timezone.utc).isoformat()

    for local_date in local_dates:
        done.append(await _recalc_one(db, local_date, tz, day_boundary_hour, now))

    return done


async def _recalc_one(
    db: aiosqlite.Connection,
    local_date: str,
    tz: str,
    day_boundary_hour: int,
    now: str,
) -> str:
    """Recompute and upsert the rollup for a single local date.

    Raises on any error so the caller's transaction rolls back.
    """
    start_utc, end_utc = day_bounds_utc(local_date, tz, day_boundary_hour)

    events: list[Event] = []

    # Non-sleep events: attributed by start_utc.
    cursor = await db.execute(_EVENTS_NON_SLEEP_SQL, (start_utc, end_utc))
    rows = await cursor.fetchall()
    for row in rows:
        events.append(
            Event(
                data_type=row[1],
                start_utc=row[2],
                end_utc=row[3],
                value_num=row[4],
                value_json=row[5],
            )
        )

    # Sleep events: attributed by end_utc (SPEC §7.3).
    cursor = await db.execute(_EVENTS_SLEEP_SQL, (start_utc, end_utc))
    rows = await cursor.fetchall()
    for row in rows:
        events.append(
            Event(
                data_type=row[1],
                start_utc=row[2],
                end_utc=row[3],
                value_num=row[4],
                value_json=row[5],
            )
        )

    stages: list[dict] = []
    cursor = await db.execute(_STAGES_SQL, (start_utc, end_utc))
    stage_rows = await cursor.fetchall()
    for row in stage_rows:
        stages.append(
            {
                "session_event_id": row[0],
                "stage": row[1],
                "start_utc": row[2],
                "end_utc": row[3],
                "seconds": row[4],
            }
        )

    rollup = compute_rollup(local_date, events, stages)

    coverage = dict(rollup.coverage_json)
    coverage["overlap_detected"] = rollup.has_overlap

    await db.execute(
        _UPSERT_SQL,
        (
            rollup.local_date,
            rollup.steps,
            rollup.distance_m,
            rollup.active_kcal,
            rollup.total_kcal,
            rollup.sleep_in_bed_s,
            rollup.sleep_asleep_s,
            rollup.hr_avg,
            rollup.hr_min,
            rollup.hr_max,
            rollup.hr_resting,
            rollup.weight_kg,
            json.dumps(coverage),
            now,
        ),
    )

    return local_date
