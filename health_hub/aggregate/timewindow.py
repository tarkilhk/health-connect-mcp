"""Pure time-window helpers — UTC<->local date conversions.

No I/O. All functions are deterministic given the same inputs.

day_boundary_hour shifts the local "day start" forward: with boundary=4,
02:30 local time belongs to the PREVIOUS date (because the day starts at
04:00 local). Used so a sleep session ending at 03:00 local is attributed
to the previous calendar date.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo


def _parse_iso(s: str) -> datetime:
    """Parse ISO8601 with optional trailing Z into a timezone-aware datetime."""
    return datetime.fromisoformat(s)


def utc_sort_key(instant: str) -> str | None:
    """Canonical UTC microsecond key for comparisons without SQLite rounding.

    Invalid or timezone-free timestamps have no absolute instant and are
    excluded from time-window queries.
    """
    try:
        dt = datetime.fromisoformat(instant)
        if dt.utcoffset() is None:
            return None
        return dt.astimezone(UTC).isoformat(timespec="microseconds")
    except (ValueError, TypeError, OverflowError):
        return None


def local_date_of(instant_utc: str, tz: str = "UTC", day_boundary_hour: int = 0) -> str:
    """Convert UTC ISO8601 instant to local date string 'YYYY-MM-DD'.

    The instant is converted to ``tz``, then shifted back by
    ``day_boundary_hour`` hours before taking the calendar date. With
    boundary=0 this is plain local-date conversion.
    """
    dt = _parse_iso(instant_utc).astimezone(ZoneInfo(tz))
    if day_boundary_hour:
        dt = dt - timedelta(hours=day_boundary_hour)
    return dt.date().isoformat()


def day_bounds_utc(local_date: str, tz: str = "UTC", day_boundary_hour: int = 0) -> tuple[str, str]:
    """Return [start_utc, end_utc) ISO8601 strings for a local date.

    The local day starts at ``day_boundary_hour`` (00:00 by default) in
    ``tz`` and spans 24h. Both endpoints are returned in UTC.
    """
    d = datetime.fromisoformat(local_date).date()
    local_start = datetime(
        d.year, d.month, d.day, day_boundary_hour, 0, 0, tzinfo=ZoneInfo(tz)
    )
    local_end = local_start + timedelta(days=1)
    start_utc = local_start.astimezone(UTC)
    end_utc = local_end.astimezone(UTC)
    return (start_utc.isoformat(), end_utc.isoformat())


def date_window(date_from: str, date_to: str, tz: str, boundary: int) -> tuple[str, str]:
    """Inclusive canonical local dates mapped to a half-open UTC window."""
    start, end = date.fromisoformat(date_from), date.fromisoformat(date_to)
    if start.isoformat() != date_from or end.isoformat() != date_to:
        raise ValueError("dates must use YYYY-MM-DD")
    if end < start:
        raise ValueError("date_to must be on or after date_from")
    begin, _ = day_bounds_utc(date_from, tz, boundary)
    _, finish = day_bounds_utc(date_to, tz, boundary)
    return begin, finish
