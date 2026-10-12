"""Pure, explicitly bounded daily and ISO-week summary windows."""
from datetime import date, timedelta
from typing import Literal

Bucket = Literal["day", "week"]
MAX_OBJECTS = 400


def bounded_dates(date_from: str, date_to: str, bucket: Bucket) -> list[str]:
    start, end = date.fromisoformat(date_from), date.fromisoformat(date_to)
    if start.isoformat() != date_from or end.isoformat() != date_to:
        raise ValueError("dates must use YYYY-MM-DD")
    if end < start:
        raise ValueError("date_to must be on or after date_from")
    count = (end - start).days + 1
    if bucket == "week":
        first = start - timedelta(days=start.weekday())
        last = end - timedelta(days=end.weekday())
        count = (last - first).days // 7 + 1
    if count > MAX_OBJECTS:
        raise ValueError("requested range exceeds the 400-object limit; narrow the range or explicitly request weekly buckets")
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def weekly_points(daily: dict[str, float | None], cumulative: bool) -> tuple[list[dict], int]:
    weeks: dict[str, list[float]] = {}
    for local_date, value in daily.items():
        day = date.fromisoformat(local_date)
        key = (day - timedelta(days=day.weekday())).isoformat()
        values = weeks.setdefault(key, [])
        if value is not None:
            values.append(value)
    points, missing = [], 0
    for key, values in sorted(weeks.items()):
        value = sum(values) if values else None
        if value is None:
            missing += 1
        elif not cumulative:
            value /= len(values)
        points.append({"date": key, "value": value})
    return points, missing
