"""Additional derived daily summaries and explicitly bucketed metric series."""
import json

from health_hub.aggregate.series import Bucket, bounded_dates, weekly_points
from health_hub.mcp_server.server import get_db, mcp

_METRIC_UNITS = {
    "steps": "count", "distance_m": "m", "active_kcal": "kcal", "total_kcal": "kcal",
    "sleep_in_bed_s": "s", "sleep_asleep_s": "s", "hr_avg": "bpm", "hr_min": "bpm",
    "hr_max": "bpm", "hr_resting": "bpm", "weight_kg": "kg",
}
_CUMULATIVE = {"steps", "distance_m", "active_kcal", "total_kcal", "sleep_in_bed_s", "sleep_asleep_s"}
_COVERAGE_TYPES = {
    "steps": "steps", "distance_m": "distance", "active_kcal": "active_calories",
    "total_kcal": "total_calories", "sleep_in_bed_s": "sleep", "sleep_asleep_s": "sleep",
    "hr_avg": "heart_rate", "hr_min": "heart_rate", "hr_max": "heart_rate",
    "hr_resting": "resting_heart_rate", "weight_kg": "weight",
}
_ROLLUPS_SQL = (
    "SELECT local_date, steps, distance_m, active_kcal, total_kcal, sleep_in_bed_s, "
    "sleep_asleep_s, hr_avg, hr_min, hr_max, hr_resting, weight_kg, coverage_json "
    "FROM daily_rollups WHERE local_date >= ? AND local_date <= ? ORDER BY local_date"
)


def _coverage(row: dict, stored: dict) -> dict[str, str]:
    return {metric: "absent" if row.get(metric) is None else stored[_COVERAGE_TYPES[metric]]
            for metric in _METRIC_UNITS}


async def _load_rollups(dates: list[str]) -> tuple[dict[str, dict], dict[str, dict]]:
    rows, coverage = {}, {}
    async with get_db().execute(_ROLLUPS_SQL, (dates[0], dates[-1])) as cursor:
        for row in await cursor.fetchall():
            values = dict(row)
            local_date = values.pop("local_date")
            coverage[local_date] = json.loads(values.pop("coverage_json"))
            rows[local_date] = values
    return rows, coverage


def _caveats(dates: list[str], rows: dict[str, dict], coverage: dict[str, dict]) -> list[str]:
    caveats = []
    for day in dates:
        if day not in rows:
            caveats.append(f"no data for {day}")
            continue
        states = _coverage(rows[day], coverage[day])
        missing = [metric for metric, state in states.items() if state == "absent"]
        if missing:
            caveats.append(f"partial coverage for {day}: missing {', '.join(missing)}")
        partial = [metric for metric, state in states.items() if state == "partial"]
        if partial:
            caveats.append(f"partial recorded coverage for {day}: {', '.join(partial)}")
        if coverage.get(day, {}).get("overlap_detected"):
            caveats.append(f"overlapping records detected for {day}; totals use heuristic overlap suppression")
        if coverage.get(day, {}).get("sleep") == "partial" and rows[day].get("sleep_asleep_s") is None:
            caveats.append(f"time asleep is unknown for {day}: incomplete or invalid stage coverage")
    return caveats


@mcp.tool()
async def daily_summary(date_from: str, date_to: str, metrics: list[str] | None = None) -> dict:
    """Get additional derived daily rollups for up to 400 inclusive local dates.

    Original measurements are available separately through health_records.
    Missing values remain unknown. Cumulative totals suppress source overlaps
    heuristically; sleep totals require complete recorded stages.
    """
    dates = bounded_dates(date_from, date_to, "day")
    rows, stored_coverage = await _load_rollups(dates)
    selected = set(_METRIC_UNITS if metrics is None else metrics)
    unknown = selected - _METRIC_UNITS.keys()
    if unknown:
        raise ValueError(f"unknown metrics: {', '.join(sorted(unknown))}")
    days = [{"local_date": day, **{metric: value for metric, value in rows.get(day, {}).items()
                                 if metric in selected and value is not None}} for day in dates]
    caveats = _caveats(dates, rows, stored_coverage)
    if metrics is not None:
        caveats.append(f"showing only requested metrics: {', '.join(metrics)}")
    return {"days": days, "coverage": {day: _coverage(rows.get(day, {}), stored_coverage.get(day, {})) for day in dates},
            "caveats": caveats or ["Derived rollup values are present for all days in range."]}


@mcp.tool()
async def metric_series(metric: str, date_from: str, date_to: str, bucket: Bucket = "day") -> dict:
    """Get a derived rollup series with up to 400 explicitly chosen day/week points.

    Weekly buckets start Monday. Cumulative daily metrics are summed;
    other daily metrics are averaged. No automatic bucket switching occurs.
    """
    if metric not in _METRIC_UNITS:
        raise ValueError(f"unknown metric '{metric}'")
    dates = bounded_dates(date_from, date_to, bucket)
    rows, coverage = await _load_rollups(dates)
    daily = {day: rows.get(day, {}).get(metric) for day in dates}
    caveats = _caveats(dates, rows, coverage)
    if bucket == "week":
        points, missing = weekly_points(daily, metric in _CUMULATIVE)
        caveats.append("aggregated to weekly buckets (ISO week start = Monday)")
    else:
        points = [{"date": day, "value": value} for day, value in daily.items()]
        missing = sum(value is None for value in daily.values())
    if missing:
        caveats.append(f"no data for {missing} {bucket} bucket(s)")
    return {"metric": metric, "unit": _METRIC_UNITS[metric], "points": points,
            "caveats": caveats or ["Derived metric values are present for all buckets."]}
