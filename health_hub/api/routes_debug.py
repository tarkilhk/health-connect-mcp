"""GET /debug/* — Debug Inspector JSON API + static HTML page.

All endpoints require ``?token=<MCP_TOKEN>`` query parameter. Uses
``hmac.compare_digest`` for timing-safe comparison. Missing/mismatch →
401 with no body.

The designer builds the real debug.html frontend; this module serves it
and provides the backing JSON API at /debug/api/*.
"""
from __future__ import annotations

import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from health_hub.contract.registry import TYPE_REGISTRY
from health_hub.ingest.normalize import normalize_health_payload

log = logging.getLogger(__name__)

_DEBUG_HTML_PATH = Path(__file__).parent / "debug.html"

# Columns from daily_rollups to expose in the rollup dict (excluding
# local_date, coverage_json, computed_at which are handled separately).
_ROLLUP_COLUMNS = [
    "steps",
    "distance_m",
    "active_kcal",
    "total_kcal",
    "sleep_in_bed_s",
    "sleep_asleep_s",
    "hr_avg",
    "hr_min",
    "hr_max",
    "hr_resting",
    "weight_kg",
]


# --- Auth helper -------------------------------------------------------------


def _check_token(request: Request) -> bool:
    """Verify ``?token=`` query param against ``settings.mcp_token``.

    Uses ``hmac.compare_digest`` for timing-safe comparison. Returns False
    if the token is missing, empty, or does not match.
    """
    settings = request.app.state.settings
    provided = request.query_params.get("token", "")
    if not provided or not settings.mcp_token:
        return False
    return hmac.compare_digest(provided, settings.mcp_token)


def _unauthorized() -> Response:
    """Return a bare 401 with no body per SPEC."""
    return Response(status_code=401)


def _get_int_param(
    request: Request, name: str, default: int, min_val: int, max_val: int
) -> int:
    """Parse and clamp an integer query parameter."""
    raw = request.query_params.get(name, str(default))
    try:
        val = int(raw)
    except (ValueError, TypeError):
        val = default
    return min(max(val, min_val), max_val)


# --- GET /debug — static HTML ------------------------------------------------


async def serve_debug_html(request: Request) -> Response:
    """Serve the debug.html inspector page. Token checked first."""
    if not _check_token(request):
        return _unauthorized()
    html = _DEBUG_HTML_PATH.read_text(encoding="utf-8")
    return HTMLResponse(content=html, status_code=200)


# --- GET /debug/api/payloads -------------------------------------------------


async def list_payloads(request: Request) -> JSONResponse:
    """List raw payloads ordered by received_at DESC with event counts."""
    if not _check_token(request):
        return _unauthorized()
    db: aiosqlite.Connection = request.app.state.db
    limit = _get_int_param(request, "limit", default=20, min_val=1, max_val=100)

    cursor = await db.execute(
        """SELECT r.id, r.received_at, r.source, r.app_version, r.byte_size,
                  r.signature_valid,
                  (SELECT count(*) FROM events WHERE payload_id = r.id) AS event_count,
                  r.parse_error
           FROM raw_payloads r
           ORDER BY r.received_at DESC
           LIMIT ?""",
        (limit,),
    )
    rows = await cursor.fetchall()
    result = [
        {
            "id": row[0],
            "received_at": row[1],
            "source": row[2],
            "app_version": row[3],
            "byte_size": row[4],
            "signature_valid": row[5],
            "event_count": row[6],
            "parse_error": row[7],
        }
        for row in rows
    ]
    return JSONResponse(result)


# --- GET /debug/api/payload/{id} ---------------------------------------------


async def get_payload(request: Request) -> JSONResponse:
    """Return full detail for one payload: body, events, stages, result."""
    if not _check_token(request):
        return _unauthorized()
    db: aiosqlite.Connection = request.app.state.db
    try:
        payload_id = int(request.path_params["id"])
    except (ValueError, TypeError):
        return JSONResponse({"error": "invalid_id"}, status_code=400)

    cursor = await db.execute(
        """SELECT id, received_at, source, app_version, device,
                  payload_timestamp, signature_valid, byte_size, parse_error,
                  body_json
           FROM raw_payloads WHERE id = ?""",
        (payload_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return JSONResponse({"error": "not_found"}, status_code=404)

    body_json = row[9]
    parse_error = row[8]

    # Events for this payload.
    cursor = await db.execute(
        """SELECT event_id, data_type, start_utc, end_utc, value_num
           FROM events WHERE payload_id = ?
           ORDER BY start_utc""",
        (payload_id,),
    )
    events = [
        {
            "event_id": e[0],
            "data_type": e[1],
            "start_utc": e[2],
            "end_utc": e[3],
            "value_num": e[4],
        }
        for e in await cursor.fetchall()
    ]

    # Sleep stages linked to this payload's session events.
    cursor = await db.execute(
        """SELECT s.session_event_id, s.stage, s.start_utc, s.end_utc, s.seconds
           FROM sleep_stages s
           WHERE s.session_event_id IN (
               SELECT event_id FROM events WHERE payload_id = ?
           )
           ORDER BY s.start_utc""",
        (payload_id,),
    )
    stages = [
        {
            "session_event_id": s[0],
            "stage": s[1],
            "start_utc": s[2],
            "end_utc": s[3],
            "seconds": s[4],
        }
        for s in await cursor.fetchall()
    ]

    # Re-normalize body to compute result counts (received/stored/duplicates).
    result = {"received": 0, "stored": 0, "duplicates": 0, "errors": []}
    if not parse_error:
        try:
            payload_dict = json.loads(body_json)
            if isinstance(payload_dict, dict):
                norm_events, _, norm_errors = normalize_health_payload(payload_dict)
                result["received"] = len(norm_events)
                result["stored"] = len(events)
                result["duplicates"] = max(0, len(norm_events) - len(events))
                result["errors"] = norm_errors
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            pass

    return JSONResponse(
        {
            "id": row[0],
            "received_at": row[1],
            "source": row[2],
            "app_version": row[3],
            "device": row[4],
            "payload_timestamp": row[5],
            "signature_valid": row[6],
            "byte_size": row[7],
            "parse_error": parse_error,
            "body": body_json,
            "events": events,
            "stages": stages,
            "result": result,
        }
    )


# --- GET /debug/api/diagnostics ----------------------------------------------


async def get_diagnostics(request: Request) -> JSONResponse:
    """Return sync_diagnostics rows for a given payload_id."""
    if not _check_token(request):
        return _unauthorized()
    db: aiosqlite.Connection = request.app.state.db
    raw_pid = request.query_params.get("payload_id", "")
    try:
        payload_id = int(raw_pid)
    except (ValueError, TypeError):
        return JSONResponse({"error": "invalid_payload_id"}, status_code=400)

    cursor = await db.execute(
        """SELECT data_type, permission_granted, page_count, raw_record_count,
                  filtered_record_count, min_time, max_time, last_sync, error
           FROM sync_diagnostics WHERE payload_id = ?
           ORDER BY data_type""",
        (payload_id,),
    )
    rows = await cursor.fetchall()
    result = [
        {
            "data_type": r[0],
            "permission_granted": r[1],
            "page_count": r[2],
            "raw_record_count": r[3],
            "filtered_record_count": r[4],
            "min_time": r[5],
            "max_time": r[6],
            "last_sync": r[7],
            "error": r[8],
        }
        for r in rows
    ]
    return JSONResponse(result)


# --- GET /debug/api/coverage -------------------------------------------------


async def get_coverage(request: Request) -> JSONResponse:
    """Build a day x type coverage matrix for the last N days (UTC)."""
    if not _check_token(request):
        return _unauthorized()
    db: aiosqlite.Connection = request.app.state.db
    days = _get_int_param(request, "days", default=30, min_val=1, max_val=365)

    today = datetime.now(timezone.utc).date()
    day_list = [(today - timedelta(days=i)).isoformat() for i in range(days)]
    types = list(TYPE_REGISTRY.keys())

    range_start = (today - timedelta(days=days - 1)).isoformat() + "T00:00:00Z"
    range_end = (today + timedelta(days=1)).isoformat() + "T00:00:00Z"

    cursor = await db.execute(
        """SELECT data_type, substr(start_utc, 1, 10) as day, count(*) as cnt
           FROM events
           WHERE start_utc >= ? AND start_utc < ?
           GROUP BY data_type, day""",
        (range_start, range_end),
    )
    counts = await cursor.fetchall()

    # Build full matrix with zeros, fill in actual counts.
    matrix: dict[str, dict[str, int]] = {day: {t: 0 for t in types} for day in day_list}
    for data_type, day, cnt in counts:
        if day in matrix and data_type in matrix[day]:
            matrix[day][data_type] = cnt

    return JSONResponse({"days": day_list, "types": types, "matrix": matrix})


# --- GET /debug/api/rollup/{local_date} --------------------------------------


async def get_rollup(request: Request) -> JSONResponse:
    """Return daily_rollup for a date + raw naive_sums + event_counts."""
    if not _check_token(request):
        return _unauthorized()
    db: aiosqlite.Connection = request.app.state.db
    local_date = request.path_params["local_date"]

    try:
        dt = datetime.fromisoformat(local_date)
    except ValueError:
        return JSONResponse({"error": "invalid_date"}, status_code=400)

    day_start = local_date + "T00:00:00Z"
    next_day = (dt + timedelta(days=1)).strftime("%Y-%m-%d")
    day_end = next_day + "T00:00:00Z"

    # Event counts by type for this date.
    cursor = await db.execute(
        """SELECT data_type, count(*) as cnt
           FROM events
           WHERE start_utc >= ? AND start_utc < ?
           GROUP BY data_type""",
        (day_start, day_end),
    )
    event_counts = {r[0]: r[1] for r in await cursor.fetchall()}

    # Rollup row.
    cursor = await db.execute(
        f"""SELECT {', '.join(_ROLLUP_COLUMNS)}, coverage_json
            FROM daily_rollups WHERE local_date = ?""",
        (local_date,),
    )
    rollup_row = await cursor.fetchone()

    if rollup_row is None:
        return JSONResponse(
            {
                "local_date": local_date,
                "rollup": None,
                "naive_sums": None,
                "event_counts": event_counts,
            }
        )

    # Naive sums: raw SUM(value_num) per type (no dedup logic).
    cursor = await db.execute(
        """SELECT data_type, SUM(value_num) as total
           FROM events
           WHERE start_utc >= ? AND start_utc < ?
           GROUP BY data_type""",
        (day_start, day_end),
    )
    naive_sums = {r[0]: r[1] for r in await cursor.fetchall()}

    # Build rollup dict from columns.
    rollup: dict = {}
    for i, col in enumerate(_ROLLUP_COLUMNS):
        rollup[col] = rollup_row[i]
    coverage_raw = rollup_row[len(_ROLLUP_COLUMNS)]
    if coverage_raw:
        try:
            rollup["coverage"] = json.loads(coverage_raw)
        except (json.JSONDecodeError, TypeError):
            rollup["coverage"] = coverage_raw
    else:
        rollup["coverage"] = None

    return JSONResponse(
        {
            "local_date": local_date,
            "rollup": rollup,
            "naive_sums": naive_sums,
            "event_counts": event_counts,
        }
    )


# --- GET /debug/api/stats ----------------------------------------------------


async def get_stats(request: Request) -> JSONResponse:
    """Return global stats across all events and payloads."""
    if not _check_token(request):
        return _unauthorized()
    db: aiosqlite.Connection = request.app.state.db

    cursor = await db.execute("SELECT count(*) FROM events")
    total_events = (await cursor.fetchone())[0]

    cursor = await db.execute(
        "SELECT data_type, count(*) FROM events GROUP BY data_type ORDER BY data_type"
    )
    type_counts = {r[0]: r[1] for r in await cursor.fetchall()}

    cursor = await db.execute(
        "SELECT min(substr(start_utc, 1, 10)), max(substr(start_utc, 1, 10)) FROM events"
    )
    date_row = await cursor.fetchone()
    date_from = date_row[0] if date_row else None
    date_to = date_row[1] if date_row else None

    cursor = await db.execute(
        "SELECT received_at, app_version FROM raw_payloads ORDER BY received_at DESC LIMIT 1"
    )
    last_row = await cursor.fetchone()
    last_ingest_at = last_row[0] if last_row else None
    last_app_version = last_row[1] if last_row else None

    cursor = await db.execute("SELECT count(*) FROM raw_payloads")
    total_payloads = (await cursor.fetchone())[0]

    return JSONResponse(
        {
            "total_events": total_events,
            "type_counts": type_counts,
            "date_from": date_from,
            "date_to": date_to,
            "last_ingest_at": last_ingest_at,
            "last_app_version": last_app_version,
            "total_payloads": total_payloads,
        }
    )


# --- Route table -------------------------------------------------------------


routes: list[Route] = [
    Route("/debug", serve_debug_html, methods=["GET"]),
    Route("/debug/api/payloads", list_payloads, methods=["GET"]),
    Route("/debug/api/payload/{id}", get_payload, methods=["GET"]),
    Route("/debug/api/diagnostics", get_diagnostics, methods=["GET"]),
    Route("/debug/api/coverage", get_coverage, methods=["GET"]),
    Route("/debug/api/rollup/{local_date}", get_rollup, methods=["GET"]),
    Route("/debug/api/stats", get_stats, methods=["GET"]),
]
