"""Pure daily rollup computation per SPEC §7.3.

Takes a local_date + the events already attributed to that date (caller
is responsible for filtering by :func:`~health_hub.aggregate.timewindow.local_date_of`)
plus the sleep stages for sessions ending on that date, and produces a
single :class:`DailyRollup`.

No I/O. No DB. Deterministic.

Contract: the caller MUST pre-filter events to those belonging to
``local_date`` (per ``local_date_of`` for non-sleep types and per
``local_date_of(end_utc)`` for sleep sessions, per SPEC §7.3).
``compute_rollup`` does not re-check attribution — it trusts the caller
and uses every event it is given.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from health_hub.aggregate.dedup import sum_non_overlapping
from health_hub.aggregate.sleep import sleep_totals
from health_hub.contract.payload import Event
from health_hub.ingest.identity import event_id

# Cumulative types that map to a DailyRollup field.
# (hydration / nutrition are cumulative but have no rollup field today.)
_CUMULATIVE_FIELD: dict[str, str] = {
    "steps": "steps",
    "distance": "distance_m",
    "active_calories": "active_kcal",
    "total_calories": "total_kcal",
}

# Metrics tracked in coverage_json. Mirrors DailyRollup value fields.
_COVERAGE_METRICS: tuple[str, ...] = (
    "steps",
    "distance",
    "active_calories",
    "total_calories",
    "sleep",
    "heart_rate",
    "resting_heart_rate",
    "weight",
)

_GAP_THRESHOLD = timedelta(hours=6)


@dataclass
class DailyRollup:
    """One day of aggregated health metrics."""

    local_date: str
    steps: int | None = None
    distance_m: float | None = None
    active_kcal: float | None = None
    total_kcal: float | None = None
    sleep_in_bed_s: int | None = None
    sleep_asleep_s: int | None = None
    hr_avg: float | None = None
    hr_min: int | None = None
    hr_max: int | None = None
    hr_resting: int | None = None
    weight_kg: float | None = None
    coverage_json: dict = field(default_factory=dict)
    has_overlap: bool = False


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _has_gap_gt_6h(events: list[Event]) -> bool:
    """True if any gap between consecutive coverage spans exceeds 6h.

    Walks events sorted by start_utc and tracks ``max_end_so_far``. A gap
    is detected when the next event's start is more than 6h after the
    furthest-reached end. This is correct for nested intervals: e.g.
    ``[00:00-12:00]`` followed by ``[01:00-02:00]`` and ``[10:00-11:00]``
    has no gap because ``max_end_so_far`` stays at 12:00 throughout.
    """
    if len(events) < 2:
        return False
    sorted_ev = sorted(events, key=lambda e: _parse_iso(e.start_utc))
    max_end = _parse_iso(sorted_ev[0].end_utc)
    for i in range(1, len(sorted_ev)):
        next_start = _parse_iso(sorted_ev[i].start_utc)
        if next_start - max_end > _GAP_THRESHOLD:
            return True
        cur_end = _parse_iso(sorted_ev[i].end_utc)
        max_end = max(max_end, cur_end)
    return False


def _coverage(events: list[Event]) -> str:
    if not events:
        return "absent"
    return "partial" if _has_gap_gt_6h(events) else "ok"


def compute_rollup(
    local_date: str, events: list[Event], stages: list[dict]
) -> DailyRollup:
    """Compute daily rollup per SPEC §7.3.

    The caller MUST pre-filter ``events`` to those attributed to
    ``local_date``: for non-sleep types by ``local_date_of(start_utc)``
    and for sleep sessions by ``local_date_of(end_utc)``. This function
    does not re-check attribution and uses every event it receives.
    ``stages`` must be filtered to sessions whose ``end_utc`` falls on
    ``local_date``.
    """
    rollup = DailyRollup(local_date=local_date)

    # Group by data_type.
    by_type: dict[str, list[Event]] = {}
    for e in events:
        by_type.setdefault(e.data_type, []).append(e)

    # Cumulative metrics: dedup + sum, set field.
    for data_type, field_name in _CUMULATIVE_FIELD.items():
        evs = by_type.get(data_type)
        if not evs:
            continue
        total, overlap = sum_non_overlapping(evs)
        if data_type == "steps":
            rollup.steps = int(total)
        else:
            setattr(rollup, field_name, total)
        if overlap:
            rollup.has_overlap = True

    _apply_instant_metrics(rollup, by_type)

    _apply_sleep(rollup, by_type.get("sleep", []), stages)

    # coverage_json per metric.
    for metric in _COVERAGE_METRICS:
        rollup.coverage_json[metric] = _coverage(by_type.get(metric, []))
    if rollup.sleep_in_bed_s is not None and rollup.sleep_asleep_s is None:
        rollup.coverage_json["sleep"] = "partial"

    return rollup


def _apply_sleep(rollup: DailyRollup, events: list[Event], stages: list[dict]) -> None:
    if not events:
        return
    by_session: dict[str, list[dict]] = {}
    for stage in stages:
        by_session.setdefault(stage["session_event_id"], []).append(stage)
    totals = []
    for event in events:
        session_id = event_id(event.data_type, event.start_utc, event.end_utc, event.value_json)
        totals.append(sleep_totals(event.start_utc, event.end_utc, by_session.get(session_id, [])))
    rollup.sleep_in_bed_s = sum(in_bed for in_bed, _ in totals)
    if all(asleep is not None for _, asleep in totals):
        rollup.sleep_asleep_s = sum(asleep for _, asleep in totals if asleep is not None)


def _apply_instant_metrics(rollup: DailyRollup, by_type: dict[str, list[Event]]) -> None:
    heart_values = [
        event.value_num for event in by_type.get("heart_rate", []) if event.value_num is not None
    ]
    if heart_values:
        rollup.hr_avg = sum(heart_values) / len(heart_values)
        rollup.hr_min, rollup.hr_max = int(min(heart_values)), int(max(heart_values))
    for data_type, field_name, convert in [
        ("resting_heart_rate", "hr_resting", int), ("weight", "weight_kg", float),
    ]:
        events = by_type.get(data_type, [])
        if events:
            last = max(events, key=lambda event: _parse_iso(event.start_utc))
            if last.value_num is not None:
                setattr(rollup, field_name, convert(last.value_num))
