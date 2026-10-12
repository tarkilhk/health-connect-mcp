"""MCP tools: get_sync_health and inspect_last_payload.

``get_sync_health`` reports per-data-type sync status: last event, coverage
days, missing days, and the last error from ``sync_diagnostics``.
``inspect_last_payload`` returns metadata + diagnostics of the most recent
raw payload.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from pydantic import Field

from health_hub.contract.registry import TYPE_REGISTRY
from health_hub.db.inspect_payloads import load_diagnostics
from health_hub.mcp_server.server import get_db, mcp

_COVERAGE_SQL = {
    False: "SELECT count(DISTINCT substr(utc_instant(start_utc), 1, 10)) FROM events "
           "WHERE data_type = ? AND utc_instant(start_utc) >= utc_instant(?) "
           "AND utc_instant(start_utc) < utc_instant(?)",
    True: "SELECT count(DISTINCT substr(utc_instant(end_utc), 1, 10)) FROM events "
          "WHERE data_type = ? AND utc_instant(end_utc) >= utc_instant(?) "
          "AND utc_instant(end_utc) < utc_instant(?)",
}

@mcp.tool()
async def get_sync_health(days: Annotated[int, Field(strict=True, ge=1, le=365)] = 30) -> dict:
    """Get health check per data type: last event, coverage, gaps, errors.

    Args:
        days: UTC calendar days including today, from 1 to 365 (default 30)
    """
    db = get_db()
    now = datetime.now(UTC)
    period_start_iso = (now.date() - timedelta(days=days - 1)).isoformat() + "T00:00:00Z"
    period_end_iso = (now.date() + timedelta(days=1)).isoformat() + "T00:00:00Z"

    metrics: list[dict] = []
    caveats: list[str] = []
    for data_type in sorted(TYPE_REGISTRY.keys()):
        metric = await _check_one_type(db, data_type, period_start_iso, period_end_iso, days)
        metrics.append(metric)
        if metric["missing_days"] > 0:
            caveats.append(
                f"{data_type}: missing {metric['missing_days']} of {days} days"
            )
        if metric["last_error"]:
            caveats.append(f"{data_type}: last error: {metric['last_error']}")

    if not any(m["last_event_utc"] for m in metrics):
        caveats.append("no stored events found for any data type")
    if not caveats:
        caveats.append("all data types have full coverage")
    return {"metrics": metrics, "period_days": days, "caveats": caveats}


async def _check_one_type(
    db, data_type: str, period_start_iso: str, period_end_iso: str, days: int
) -> dict:
    """Check sync health for one data type."""
    cursor = await db.execute(
        "SELECT end_utc FROM events WHERE data_type = ? "
        "ORDER BY utc_instant(end_utc) DESC LIMIT 1",
        (data_type,),
    )
    last_row = await cursor.fetchone()
    last_event = last_row[0] if last_row else None

    cursor = await db.execute(
        _COVERAGE_SQL[data_type == "sleep"],
        (data_type, period_start_iso, period_end_iso),
    )
    coverage_days = (await cursor.fetchone())[0] or 0

    cursor = await db.execute(
        "SELECT error FROM sync_diagnostics WHERE data_type = ? "
        "AND error IS NOT NULL ORDER BY payload_id DESC LIMIT 1",
        (data_type,),
    )
    error_row = await cursor.fetchone()
    last_error = error_row[0] if error_row else None

    return {
        "data_type": data_type,
        "last_event_utc": last_event,
        "coverage_days": coverage_days,
        "missing_days": max(0, days - coverage_days),
        "last_error": last_error,
    }


@mcp.tool()
async def inspect_last_payload() -> dict:
    """Return metadata and diagnostics of the last received payload."""
    db = get_db()
    row = await _last_payload_row(db)
    if row is None:
        return {
            "payload_id": None, "received_at": None, "app_version": None,
            "source": None, "byte_size": None, "signature_valid": None,
            "diagnostics": [], "caveats": ["no payloads received yet"],
        }
    diagnostics = await load_diagnostics(db, row[0])
    caveats = _payload_caveats(row[5], diagnostics)
    return {
        "payload_id": row[0], "received_at": row[1], "app_version": row[3],
        "source": row[2], "byte_size": row[4],
        "signature_valid": bool(row[5]) if row[5] is not None else None,
        "diagnostics": diagnostics, "caveats": caveats,
    }


async def _last_payload_row(db):
    """Return the most recent raw_payloads row, or None."""
    cursor = await db.execute(
        "SELECT id, received_at, source, app_version, byte_size, "
        "signature_valid FROM raw_payloads ORDER BY id DESC LIMIT 1"
    )
    return await cursor.fetchone()


def _payload_caveats(sig_valid, diagnostics: list[dict]) -> list[str]:
    """Build caveats for the last payload based on signature + diagnostics."""
    caveats: list[str] = []
    if not sig_valid:
        caveats.append("last payload has invalid signature")
    for diag in diagnostics:
        if diag["error"]:
            caveats.append(f"{diag['data_type']}: error in diagnostics")
        if diag["permission_granted"] is False:
            caveats.append(f"{diag['data_type']}: permission not granted")
    if not diagnostics:
        caveats.append("no diagnostics block in last payload")
    if not caveats:
        caveats.append("last payload is healthy")
    return caveats
