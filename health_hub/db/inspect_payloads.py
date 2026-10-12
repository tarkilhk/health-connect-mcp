"""Read original payloads and their normalized records for the debug inspector."""
import json

import aiosqlite

from health_hub.ingest.normalize import normalize_health_payload


async def list_payloads(db: aiosqlite.Connection, limit: int) -> list[dict]:
    async with db.execute(
        """SELECT r.id, r.received_at, r.source, r.app_version, r.byte_size,
                  r.signature_valid, r.parse_error,
                  (SELECT count(*) FROM events WHERE payload_id = r.id) AS event_count
           FROM raw_payloads r ORDER BY r.id DESC LIMIT ?""", (limit,),
    ) as cursor:
        return [dict(row) for row in await cursor.fetchall()]


async def get_payload(db: aiosqlite.Connection, payload_id: int) -> dict | None:
    async with db.execute(
        """SELECT id, received_at, source, app_version, device, payload_timestamp,
                  signature_valid, byte_size, parse_error, body_json AS body
           FROM raw_payloads WHERE id = ?""", (payload_id,),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    payload = dict(row)
    async with db.execute(
        """SELECT event_id, data_type, start_utc, end_utc, value_num
           FROM events WHERE payload_id = ? ORDER BY utc_instant(start_utc), event_id""",
        (payload_id,),
    ) as cursor:
        payload["events"] = [dict(row) for row in await cursor.fetchall()]
    async with db.execute(
        """SELECT session_event_id, stage, start_utc, end_utc, seconds
           FROM sleep_stages WHERE session_event_id IN
             (SELECT event_id FROM events WHERE payload_id = ?)
           ORDER BY utc_instant(start_utc), stage""", (payload_id,),
    ) as cursor:
        payload["stages"] = [dict(row) for row in await cursor.fetchall()]
    payload["result"] = _normalization_counts(payload)
    return payload


def _normalization_counts(payload: dict) -> dict:
    result = {"received": 0, "stored": len(payload["events"]), "duplicates": 0, "errors": []}
    if payload["parse_error"] or payload["source"] != "health_connect":
        return result
    try:
        body = json.loads(payload["body"])
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return result
    if isinstance(body, dict):
        events, _, errors = normalize_health_payload(body)
        result.update(received=len(events), duplicates=max(0, len(events) - result["stored"]), errors=errors)
    return result


async def load_diagnostics(db: aiosqlite.Connection, payload_id: int) -> list[dict]:
    async with db.execute(
        """SELECT data_type, permission_granted, page_count, raw_record_count,
                  filtered_record_count, min_time, max_time, last_sync, error
           FROM sync_diagnostics WHERE payload_id = ? ORDER BY data_type""", (payload_id,),
    ) as cursor:
        rows = [dict(row) for row in await cursor.fetchall()]
    for row in rows:
        if row["permission_granted"] is not None:
            row["permission_granted"] = bool(row["permission_granted"])
    return rows
