"""POST /ingest/health, /ingest/screentime, /ingest/echo — ingestion endpoints.

Pipeline order for /ingest/health follows SPEC section 8.2:
read body -> token check -> signature check -> store raw -> parse JSON ->
validate envelope -> normalize -> upsert events + stages + diagnostics
in one transaction.

CRITICAL: a 200 response is only sent after all DB writes succeed. If any
L1 DB write fails, the transaction is rolled back and 500 is returned so
the app cursor does not advance and the payload is retried. L0
(``raw_payloads``) is committed separately and survives L1 failure.
"""
from __future__ import annotations

import json
import logging

import aiosqlite
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from health_hub.aggregate.timewindow import local_date_of
from health_hub.api.auth import verify_signature, verify_token
from health_hub.contract.payload import HealthPayload, ScreenTimePayload
from health_hub.ingest.diagnostics import store_diagnostics
from health_hub.ingest.normalize import normalize_health_payload
from health_hub.ingest.raw_store import store_raw_payload
from health_hub.ingest.writer import (
    upsert_events,
    upsert_screen_time,
    upsert_sleep_stages,
)
from health_hub.rollup_service import recalc_rollups_for_dates

log = logging.getLogger(__name__)


async def ingest_health(request: Request) -> JSONResponse:
    """Receive a Health Connect payload, normalize, and store events."""
    settings = request.app.state.settings
    db: aiosqlite.Connection = request.app.state.db

    # 1. Read raw body.
    body = await request.body()

    # 2. Token check — 401 before any DB write.
    if not verify_token(request.headers.get("Authorization"), settings.device_token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    # 3. Signature check — set flag; raw_payload is stored regardless.
    sig_header = request.headers.get("X-Signature")
    sig_valid = verify_signature(body, sig_header, settings.signing_key)

    # 4. Store raw payload BEFORE any validation (L0, committed separately).
    payload_id = await store_raw_payload(db, "health_connect", body, sig_valid)

    # If signature verification is active and failed, stop here. The raw
    # payload is preserved with signature_valid=0 for forensics.
    if not sig_valid:
        return JSONResponse(
            {"error": "invalid_signature", "payload_id": payload_id},
            status_code=401,
        )

    # 5. Parse JSON. On failure, mark parse_error and return 400.
    try:
        payload = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        await _mark_parse_error(db, payload_id, str(exc))
        return JSONResponse(
            {"error": "invalid_json", "payload_id": payload_id},
            status_code=400,
        )

    # 6a. Top-level must be a dict (not array/scalar).
    if not isinstance(payload, dict):
        await _mark_parse_error(
            db, payload_id, f"top-level JSON is {type(payload).__name__}, expected object"
        )
        return JSONResponse(
            {"error": "invalid_json", "payload_id": payload_id},
            status_code=400,
        )

    # 6b. Validate envelope via HealthPayload (timestamp, app_version, source,
    # and no forbidden keys like "device").
    try:
        HealthPayload.model_validate(payload)
    except ValidationError as exc:
        await _mark_parse_error(db, payload_id, str(exc))
        return JSONResponse(
            {"error": "invalid_envelope", "payload_id": payload_id, "detail": str(exc)},
            status_code=400,
        )

    # 6c. Extract envelope fields and enrich the raw_payloads row (L0 update).
    await _enrich_raw_payload(
        db,
        payload_id,
        app_version=payload.get("app_version"),
        payload_timestamp=payload.get("timestamp"),
    )

    # 7. Normalize (pure, no DB).
    events, stages, errors = normalize_health_payload(payload)

    # 8-10. L1 writes in a single transaction. If any write fails, rollback
    # everything (events + stages + diagnostics + rollups) and return 500.
    # L0 raw_payloads survives because it was committed separately.
    try:
        stored, duplicates = await upsert_events(db, events, payload_id)
        await upsert_sleep_stages(db, stages)
        if isinstance(payload.get("_diagnostics"), dict):
            await store_diagnostics(db, payload_id, payload["_diagnostics"])

        # Recalculate daily rollups for every local date touched by this
        # payload's events. Runs inside the same transaction so events +
        # stages + rollups commit atomically (SPEC §7.3, §8.2 step 7).
        # Sleep sessions are attributed by end_utc per SPEC §7.3 (a session
        # ending at 06:00 belongs to that day, not the previous one).
        affected: set[str] = set()
        for evt in events:
            instant = evt.end_utc if evt.data_type == "sleep" else evt.start_utc
            affected.add(
                local_date_of(
                    instant, settings.timezone, settings.day_boundary_hour
                )
            )
        rollups_updated = await recalc_rollups_for_dates(
            db,
            sorted(affected),
            settings.timezone,
            settings.day_boundary_hour,
        )

        await db.commit()
    except aiosqlite.Error as exc:
        await db.rollback()
        log.exception("DB error during /ingest/health")
        return JSONResponse(
            {"error": "db_error", "detail": str(exc), "payload_id": payload_id},
            status_code=500,
        )
    except Exception as exc:
        # Non-DB error during normalize/recalc (e.g. ValueError from
        # dedup on a None value_num). Roll back the L1 transaction so
        # events are NOT committed without their rollups.
        await db.rollback()
        log.exception("Unexpected error during /ingest/health")
        return JSONResponse(
            {"error": "internal_error", "detail": str(exc), "payload_id": payload_id},
            status_code=500,
        )

    return JSONResponse(
        {
            "payload_id": payload_id,
            "received": len(events),
            "stored": stored,
            "duplicates": duplicates,
            "errors": errors,
            "rollups_updated": rollups_updated,
        },
        status_code=200,
    )


async def ingest_screentime(request: Request) -> JSONResponse:
    """Receive a screen-time payload and upsert per-day totals + apps.

    Token AND signature are both verified per SPEC section 8.2.
    """
    settings = request.app.state.settings
    db: aiosqlite.Connection = request.app.state.db

    body = await request.body()

    # Token check — 401 before any DB write.
    if not verify_token(request.headers.get("Authorization"), settings.device_token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    # Signature check — set flag; raw_payload is stored regardless.
    sig_header = request.headers.get("X-Signature")
    sig_valid = verify_signature(body, sig_header, settings.signing_key)

    # Store raw payload BEFORE any validation (L0, committed separately).
    payload_id = await store_raw_payload(db, "screen_time", body, sig_valid)

    if not sig_valid:
        return JSONResponse(
            {"error": "invalid_signature", "payload_id": payload_id},
            status_code=401,
        )

    # Parse JSON.
    try:
        payload = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        await _mark_parse_error(db, payload_id, str(exc))
        return JSONResponse(
            {"error": "invalid_json", "payload_id": payload_id},
            status_code=400,
        )

    # Top-level must be a dict.
    if not isinstance(payload, dict):
        await _mark_parse_error(
            db, payload_id, f"top-level JSON is {type(payload).__name__}, expected object"
        )
        return JSONResponse(
            {"error": "invalid_json", "payload_id": payload_id},
            status_code=400,
        )

    # Validate envelope via ScreenTimePayload (timestamp, app_version, device, source).
    try:
        ScreenTimePayload.model_validate(payload)
    except ValidationError as exc:
        await _mark_parse_error(db, payload_id, str(exc))
        return JSONResponse(
            {"error": "invalid_envelope", "payload_id": payload_id, "detail": str(exc)},
            status_code=400,
        )

    # Enrich raw_payloads with envelope metadata (L0 update).
    await _enrich_raw_payload(
        db,
        payload_id,
        app_version=payload.get("app_version"),
        device=payload.get("device"),
    )

    # L1 writes in a single transaction.
    try:
        screen_time_data = payload.get("screen_time")
        if not isinstance(screen_time_data, list):
            screen_time_data = []
        days_written = await upsert_screen_time(db, screen_time_data, payload_id)
        await db.commit()
    except aiosqlite.Error as exc:
        await db.rollback()
        log.exception("DB error during /ingest/screentime")
        return JSONResponse(
            {"error": "db_error", "detail": str(exc), "payload_id": payload_id},
            status_code=500,
        )

    return JSONResponse(
        {
            "payload_id": payload_id,
            "days_written": days_written,
            "rollups_updated": [],
        },
        status_code=200,
    )


async def ingest_echo(request: Request) -> JSONResponse:
    """Minimal echo: store raw bytes, always 200. No auth, no parsing.

    Per SPEC section 8.3, /ingest/echo ALWAYS returns 200 — even if the raw
    payload storage fails. In that case, a ``warning`` field is included.
    """
    db: aiosqlite.Connection = request.app.state.db
    body = await request.body()
    try:
        payload_id = await store_raw_payload(
            db, "echo", body, signature_valid=True
        )
    except aiosqlite.Error as exc:
        log.warning("Echo raw payload storage failed: %s", exc)
        return JSONResponse(
            {
                "ok": True,
                "payload_id": None,
                "byte_size": len(body),
                "warning": "raw payload storage failed",
            },
            status_code=200,
        )
    return JSONResponse(
        {"ok": True, "payload_id": payload_id, "byte_size": len(body)},
        status_code=200,
    )


async def _mark_parse_error(
    db: aiosqlite.Connection, payload_id: int, error_msg: str
) -> None:
    """Record a parse error on an existing raw_payloads row."""
    await db.execute(
        "UPDATE raw_payloads SET parse_error = ? WHERE id = ?",
        (error_msg, payload_id),
    )
    await db.commit()


async def _enrich_raw_payload(
    db: aiosqlite.Connection,
    payload_id: int,
    app_version: str | None = None,
    device: str | None = None,
    payload_timestamp: str | None = None,
) -> None:
    """Fill in envelope metadata on a raw_payloads row after parsing.

    Uses COALESCE so NULL arguments do not clobber previously set values.
    """
    await db.execute(
        """UPDATE raw_payloads SET
             app_version = COALESCE(?, app_version),
             device = COALESCE(?, device),
             payload_timestamp = COALESCE(?, payload_timestamp)
           WHERE id = ?""",
        (app_version, device, payload_timestamp, payload_id),
    )
    await db.commit()


routes: list[Route] = [
    Route("/ingest/health", ingest_health, methods=["POST"]),
    Route("/ingest/screentime", ingest_screentime, methods=["POST"]),
    Route("/ingest/echo", ingest_echo, methods=["POST"]),
]
