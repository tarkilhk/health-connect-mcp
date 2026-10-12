"""Persist normalized health and screen records in the caller's transaction."""
from collections.abc import Callable

import aiosqlite

from health_hub.aggregate.timewindow import local_date_of
from health_hub.config import Settings
from health_hub.contract.kinds import RecordKind
from health_hub.contract.payload import Event
from health_hub.contract.registry import TYPE_REGISTRY
from health_hub.ingest.diagnostics import store_diagnostics
from health_hub.ingest.writer import (
    upsert_events,
    upsert_screen_time,
    upsert_sleep_stages,
)
from health_hub.rollup_service import recalc_rollups_for_dates

Normalizer = Callable[[dict], tuple[list[Event], list[dict], list[dict]]]


async def persist_health(
    db: aiosqlite.Connection, payload: dict, payload_id: int, settings: Settings,
    *, normalizer: Normalizer,
) -> dict:
    events, stages, errors = normalizer(payload)
    stored, duplicates = await upsert_events(db, events, payload_id)
    await upsert_sleep_stages(db, stages)
    if isinstance(payload.get("_diagnostics"), dict):
        await store_diagnostics(db, payload_id, payload["_diagnostics"])
    dates = {_event_date(event, settings) for event in events}
    rollups = await recalc_rollups_for_dates(
        db, sorted(dates), settings.timezone, settings.day_boundary_hour,
    )
    return {"received": len(events), "stored": stored, "duplicates": duplicates,
            "errors": errors, "rollups_updated": rollups}


def _event_date(event: Event, settings: Settings) -> str:
    spec = TYPE_REGISTRY[event.data_type]
    instant = event.end_utc if spec.kind == RecordKind.SESSION else event.start_utc
    return local_date_of(instant, settings.timezone, settings.day_boundary_hour)


async def persist_screen_time(
    db: aiosqlite.Connection, payload: dict, payload_id: int, settings: Settings,
) -> dict:
    records = payload.get("screen_time")
    if not isinstance(records, list):
        records = []
    days_written = await upsert_screen_time(db, records, payload_id)
    return {"days_written": days_written, "rollups_updated": []}
