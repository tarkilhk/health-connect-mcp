"""Bounded, parameterized stored-event queries and query-bound cursors."""
import base64
import binascii
import json
from datetime import date, datetime

from health_hub.aggregate.timewindow import day_bounds_utc
from health_hub.config import Settings
from health_hub.contract.registry import TypeSpec
from health_hub.mcp_server.server import get_db


def record_window(date_from: str, date_to: str, settings: Settings) -> tuple[str, str]:
    """Validate inclusive local dates and produce a half-open UTC window."""
    start, end = date.fromisoformat(date_from), date.fromisoformat(date_to)
    if start.isoformat() != date_from or end.isoformat() != date_to:
        raise ValueError("dates must use YYYY-MM-DD")
    if end < start:
        raise ValueError("date_to must be on or after date_from")
    begin, _ = day_bounds_utc(date_from, settings.timezone, settings.day_boundary_hour)
    _, finish = day_bounds_utc(date_to, settings.timezone, settings.day_boundary_hour)
    return begin, finish


def decode_cursor(cursor: str | None, query: list[str]) -> tuple[str | None, str]:
    """A cursor is valid only for its original type and UTC window."""
    if cursor is None:
        return None, ""
    try:
        if len(cursor) > 4096:
            raise ValueError("oversized cursor")
        raw = base64.b64decode(cursor, altchars=b"-_", validate=True)
        scope, start, event_id = json.loads(raw)
        if scope != query or not isinstance(start, str) or not isinstance(event_id, str):
            raise ValueError("cursor does not match query")
        if datetime.fromisoformat(start).utcoffset() is None:
            raise ValueError("cursor timestamp must include timezone")
        return start, event_id
    except (ValueError, TypeError, binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError("invalid cursor for this type and date window") from exc


def encode_cursor(query: list[str], row) -> str:
    raw = json.dumps([query, row[1], row[0]], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode()


async def load_record_page(spec: TypeSpec, window: tuple[str, str], limit: int, cursor):
    """Read at most limit+1 events, comparing actual instants across offsets.

    Instants and sessions use timestamp/end attribution. Intervals intersect
    the requested window; zero-duration intervals use instant attribution.
    """
    begin, finish = window
    after_start, after_id = cursor
    cursor = await get_db().execute(
        "SELECT event_id, start_utc, end_utc, value_json FROM events "
        "WHERE data_type = ? AND ("
        "(? = 'session' AND utc_instant(end_utc) >= utc_instant(?) "
        "AND utc_instant(end_utc) < utc_instant(?)) OR "
        "(? = 'instant' AND utc_instant(start_utc) >= utc_instant(?) "
        "AND utc_instant(start_utc) < utc_instant(?)) OR "
        "(? = 'interval' AND utc_instant(start_utc) < utc_instant(?) AND "
        "(utc_instant(end_utc) > utc_instant(?) OR "
        "(utc_instant(end_utc) = utc_instant(start_utc) "
        "AND utc_instant(start_utc) >= utc_instant(?))))) "
        "AND (? IS NULL OR utc_instant(start_utc) > utc_instant(?) OR "
        "(utc_instant(start_utc) = utc_instant(?) AND event_id > ?)) "
        "ORDER BY utc_instant(start_utc), event_id LIMIT ?",
        (spec.data_type, spec.kind.value, begin, finish,
         spec.kind.value, begin, finish, spec.kind.value, finish, begin, begin,
         after_start, after_start, after_start, after_id, limit + 1),
    )
    return await cursor.fetchall()
