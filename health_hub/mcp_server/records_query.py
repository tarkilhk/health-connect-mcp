"""Indexed stored-event queries and query-bound continuation cursors."""
import base64
import binascii
import json
from datetime import datetime
from typing import NamedTuple

from health_hub.aggregate.timewindow import date_window, utc_sort_key
from health_hub.config import Settings
from health_hub.contract.kinds import RecordKind
from health_hub.contract.registry import TypeSpec
from health_hub.mcp_server.server import get_db


class RecordRow(NamedTuple):
    event_id: str
    start_utc: str
    end_utc: str
    value_json: str


Window = tuple[str, str]
Position = tuple[str | None, str]
_SELECT = "SELECT event_id, start_utc, end_utc, value_json FROM events WHERE data_type = ? AND "
_AFTER = ("AND (utc_instant(start_utc), event_id) > (?, ?) "
          "ORDER BY utc_instant(start_utc), event_id LIMIT ?")
_QUERIES = {
    RecordKind.INSTANT: _SELECT + "utc_instant(start_utc) >= ? AND utc_instant(start_utc) < ? " + _AFTER,
    RecordKind.SESSION: _SELECT + "utc_instant(end_utc) >= ? AND utc_instant(end_utc) < ? " + _AFTER,
    RecordKind.INTERVAL: _SELECT + "utc_instant(start_utc) < ? AND "
        "(utc_instant(end_utc) > ? OR (utc_instant(end_utc) = utc_instant(start_utc) "
        "AND utc_instant(start_utc) >= ?)) " + _AFTER,
}


def record_window(date_from: str, date_to: str, settings: Settings) -> Window:
    return date_window(date_from, date_to, settings.timezone, settings.day_boundary_hour)


def decode_cursor(cursor: str | None, query: list[str]) -> Position:
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


def encode_cursor(query: list[str], row: RecordRow) -> str:
    raw = json.dumps([query, row.start_utc, row.event_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode()


async def load_record_page(spec: TypeSpec, window: Window, limit: int, position: Position) -> list[RecordRow]:
    begin, finish = map(utc_sort_key, window)
    after_start, after_id = position
    after = utc_sort_key(after_start) if after_start is not None else ""
    bounds = (finish, begin, begin) if spec.kind == RecordKind.INTERVAL else (begin, finish)
    async with get_db().execute(
        _QUERIES[spec.kind], (spec.data_type, *bounds, after, after_id, limit + 1),
    ) as cursor:
        return [RecordRow(*row) for row in await cursor.fetchall()]
