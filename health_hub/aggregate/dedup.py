"""Pure deduplication for cumulative interval types.

When two devices (phone + watch) record the same walk, the intervals
overlap and a naive sum double-counts. We group overlapping intervals
and take the MAX value per group, then sum group maxes. This drops the
typical 1.5-3x inflation while preserving genuinely separate activity
blocks (e.g. morning run + evening walk).
"""
from __future__ import annotations

from datetime import datetime

from health_hub.contract.payload import Event


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def sum_non_overlapping(events: list[Event]) -> tuple[float, bool]:
    """Sum cumulative values suppressing overlapping intervals.

    Algorithm:
    1. Sort by start_utc.
    2. Walk the list, growing a group while the next interval's start
       is strictly less than the current group's max end.
    3. From each group take the MAX value_num (not sum).
    4. Sum group maxes.

    Returns ``(total, overlap_detected)``. ``overlap_detected`` is True
    if at least one group contained more than one interval.

    Raises ``ValueError`` if any event has ``value_num is None``.
    """
    if not events:
        return (0.0, False)

    for e in events:
        if e.value_num is None:
            raise ValueError(
                f"Event data_type={e.data_type} start={e.start_utc} has value_num=None"
            )

    sorted_events = sorted(events, key=lambda e: e.start_utc)

    total = 0.0
    overlap_detected = False

    group_max: float = 0.0
    group_end: datetime | None = None
    group_count = 0

    for e in sorted_events:
        s = _parse_iso(e.start_utc)
        en = _parse_iso(e.end_utc)
        # value_num is validated non-None above; narrow for the type checker.
        if e.value_num is None:  # pragma: no cover - validated above
            raise ValueError(
                f"Event data_type={e.data_type} start={e.start_utc} has value_num=None"
            )
        v = float(e.value_num)
        if group_end is not None and s < group_end:
            # Overlaps current group: extend, take max.
            group_max = max(group_max, v)
            group_end = max(group_end, en)
            group_count += 1
        else:
            # Close previous group.
            if group_count > 0:
                total += group_max
                if group_count > 1:
                    overlap_detected = True
            # Start new group.
            group_max = v
            group_end = en
            group_count = 1

    # Close trailing group.
    if group_count > 0:
        total += group_max
        if group_count > 1:
            overlap_detected = True

    return (total, overlap_detected)
