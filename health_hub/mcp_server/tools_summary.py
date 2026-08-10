"""MCP tools: daily_summary and metric_series. Read from daily_rollups (SPEC §9.2)."""
from __future__ import annotations

import json
from datetime import date, timedelta

from health_hub.mcp_server.server import get_db, mcp

# Metric name -> (rollup column, unit). Source of truth for both tools.
_METRIC_COLUMNS: dict[str, tuple[str, str]] = {
    "steps": ("steps", "count"), "distance_m": ("distance_m", "m"),
    "active_kcal": ("active_kcal", "kcal"), "total_kcal": ("total_kcal", "kcal"),
    "sleep_in_bed_s": ("sleep_in_bed_s", "s"), "sleep_asleep_s": ("sleep_asleep_s", "s"),
    "hr_avg": ("hr_avg", "bpm"), "hr_min": ("hr_min", "bpm"),
    "hr_max": ("hr_max", "bpm"), "hr_resting": ("hr_resting", "bpm"),
    "weight_kg": ("weight_kg", "kg"),
}
_ALL_METRICS = list(_METRIC_COLUMNS.keys())
_COLUMNS = [_METRIC_COLUMNS[m][0] for m in _ALL_METRICS]
_CUMULATIVE = {"steps", "distance_m", "active_kcal", "total_kcal", "sleep_in_bed_s", "sleep_asleep_s"}
_MAX_OBJECTS = 400  # SPEC §9.2.


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def _date_range(date_from: str, date_to: str) -> list[str]:
    """Inclusive list of YYYY-MM-DD strings."""
    start, end = _parse_date(date_from), _parse_date(date_to)
    if end < start:
        return []
    days, cur = [], start
    while cur <= end:
        days.append(cur.isoformat())
        cur += timedelta(days=1)
    return days


def _coverage(row: tuple | None) -> dict[str, str]:
    """{metric: 'ok'|'absent'} for a rollup row (None = all absent)."""
    if row is None:
        return {m: "absent" for m in _ALL_METRICS}
    return {m: ("ok" if row[i] is not None else "absent") for i, m in enumerate(_ALL_METRICS)}


def _build_caveats(
    dates: list[str], rows: dict[str, tuple], cov_jsons: dict[str, dict],
    metrics_filter: list[str] | None, overflow: bool,
) -> list[str]:
    caveats: list[str] = []
    for d in dates:
        row = rows.get(d)
        if row is None:
            caveats.append(f"no data for {d}")
            continue
        absent = [m for m, s in _coverage(row).items() if s == "absent"]
        if absent:
            caveats.append(f"partial coverage for {d}: missing {', '.join(absent)}")
        if cov_jsons.get(d, {}).get("overlap_detected"):
            caveats.append(f"overlapping records detected for {d}, possible double counting")
    if metrics_filter:
        caveats.append(f"showing only requested metrics: {', '.join(metrics_filter)}")
    if overflow:
        caveats.append(f"range exceeds {_MAX_OBJECTS} object limit; consider metric_series with bucket='week'")
    return caveats


async def _load_rollups(dates: list[str]) -> tuple[dict[str, tuple], dict[str, dict]]:
    """Query daily_rollups for the given dates. Returns (rows, coverage_jsons)."""
    db = get_db()
    ph = ",".join("?" * len(dates))
    cursor = await db.execute(
        f"SELECT local_date, {', '.join(_COLUMNS)}, coverage_json "
        f"FROM daily_rollups WHERE local_date IN ({ph})",
        dates,
    )
    rows: dict[str, tuple] = {}
    cov_jsons: dict[str, dict] = {}
    for row in await cursor.fetchall():
        ld = row[0]
        rows[ld] = tuple(row[1 : 1 + len(_COLUMNS)])
        try:
            cov_jsons[ld] = json.loads(row[1 + len(_COLUMNS)])
        except (json.JSONDecodeError, TypeError):
            cov_jsons[ld] = {}
    return rows, cov_jsons


@mcp.tool()
async def daily_summary(
    date_from: str, date_to: str, metrics: list[str] | None = None,
) -> dict:
    """Get daily health rollups for a date range.

    Args:
        date_from: Start date YYYY-MM-DD
        date_to: End date YYYY-MM-DD (inclusive)
        metrics: Optional list of metric names to include (e.g. ["steps", "sleep_in_bed_s"])
    """
    try:
        dates = _date_range(date_from, date_to)
    except ValueError:
        return {"days": [], "coverage": {}, "caveats": ["invalid date format, expected YYYY-MM-DD"]}
    if not dates:
        return {"days": [], "coverage": {}, "caveats": ["empty date range"]}
    rows, cov_jsons = await _load_rollups(dates)
    days: list[dict] = []
    coverage: dict[str, dict[str, str]] = {}
    for d in dates:
        row = rows.get(d)
        day: dict = {"local_date": d}
        if row:
            for i, m in enumerate(_ALL_METRICS):
                if row[i] is not None:
                    day[m] = row[i]
        days.append(day)
        coverage[d] = _coverage(row)
    unknown: set[str] = set()
    if metrics:
        allowed = set(metrics)
        unknown = allowed - set(_ALL_METRICS)
        allowed -= unknown
        days = [{k: v for k, v in d.items() if k == "local_date" or k in allowed} for d in days]
        metrics = list(allowed) if allowed else metrics  # preserve for caveats
    caveats = _build_caveats(dates, rows, cov_jsons, metrics, len(dates) > _MAX_OBJECTS)
    if unknown:
        caveats.insert(0, f"unknown metrics ignored: {', '.join(sorted(unknown))}")
    if not caveats:
        caveats.append("all days in range have full coverage")
    return {"days": days, "coverage": coverage, "caveats": caveats}


@mcp.tool()
async def metric_series(
    metric: str, date_from: str, date_to: str, bucket: str = "day",
) -> dict:
    """Get individual metric time series with configurable bucketing.

    Args:
        metric: Metric name (steps, distance_m, hr_avg, sleep_in_bed_s, etc.)
        date_from: Start date YYYY-MM-DD
        date_to: End date YYYY-MM-DD (inclusive)
        bucket: Aggregation bucket — "day" or "week"
    """
    if metric not in _METRIC_COLUMNS:
        return {"metric": metric, "unit": "", "points": [], "caveats": [f"unknown metric '{metric}'"]}
    col, unit = _METRIC_COLUMNS[metric]
    try:
        dates = _date_range(date_from, date_to)
    except ValueError:
        return {"metric": metric, "unit": unit, "points": [], "caveats": ["invalid date format, expected YYYY-MM-DD"]}
    if not dates:
        return {"metric": metric, "unit": unit, "points": [], "caveats": ["empty date range"]}
    force_weekly = len(dates) > _MAX_OBJECTS and bucket == "day"
    caveats: list[str] = []
    if force_weekly:
        caveats.append(f"range has {len(dates)} days exceeding {_MAX_OBJECTS} limit; switched to weekly bucket")
    daily = await _load_metric_values(col, dates)
    use_weekly = bucket == "week" or force_weekly
    if not use_weekly:
        points = [{"date": d, "value": daily[d]} for d in dates]
        missing = [d for d in dates if daily[d] is None]
        if missing:
            caveats.append(f"no data for {len(missing)} day(s): {missing[:10]}")
    else:
        points, missing = _aggregate_weekly(dates, daily, col)
        if not force_weekly:
            caveats.append("aggregated to weekly buckets (ISO week start = Monday)")
        if missing:
            caveats.append(f"no data for {len(missing)} week(s)")
    if not caveats:
        caveats.append("data present for all days in range")
    return {"metric": metric, "unit": unit, "points": points, "caveats": caveats}


async def _load_metric_values(col: str, dates: list[str]) -> dict[str, float | None]:
    """Load one metric column for the given dates from daily_rollups."""
    db = get_db()
    ph = ",".join("?" * len(dates))
    cursor = await db.execute(
        f"SELECT local_date, {col} FROM daily_rollups WHERE local_date IN ({ph}) ORDER BY local_date",
        dates,
    )
    daily: dict[str, float | None] = {d: None for d in dates}
    for row in await cursor.fetchall():
        daily[row[0]] = row[1]
    return daily


def _aggregate_weekly(
    dates: list[str], daily: dict[str, float | None], col: str
) -> tuple[list[dict], list[str]]:
    """Aggregate daily values into ISO-week buckets (Monday start)."""
    is_cumulative = col in _CUMULATIVE
    buckets: dict[str, list[float]] = {}
    all_weeks: set[str] = set()
    for d in dates:
        dt = _parse_date(d)
        key = (dt - timedelta(days=dt.weekday())).isoformat()
        all_weeks.add(key)
        val = daily.get(d)
        if val is not None:
            buckets.setdefault(key, []).append(val)
    points, missing = [], []
    for key in sorted(all_weeks):
        vals = buckets.get(key)
        if vals:
            agg = sum(vals) if is_cumulative else sum(vals) / len(vals)
            points.append({"date": key, "value": agg})
        else:
            missing.append(key)
    return points, missing