"""Pure overlap suppression for cumulative records.

Intersecting intervals form a group, which contributes its maximum value.
Adjacent intervals remain separate. Original stored records are unchanged.
"""
from datetime import datetime

from health_hub.contract.payload import Event


def sum_non_overlapping(events: list[Event]) -> tuple[float, bool]:
    """Return the sum of group maxima and whether any overlap was found.

    Missing numeric values raise ValueError rather than inventing zeros.
    """
    total, group_max, group_end, overlap = 0.0, 0.0, None, False
    for event in sorted(events, key=lambda item: datetime.fromisoformat(item.start_utc)):
        if event.value_num is None:
            raise ValueError(f"Event data_type={event.data_type} start={event.start_utc} has value_num=None")
        start = datetime.fromisoformat(event.start_utc)
        end = datetime.fromisoformat(event.end_utc)
        value = float(event.value_num)
        if group_end is not None and start < group_end:
            group_max = max(group_max, value)
            group_end = max(group_end, end)
            overlap = True
        else:
            total += group_max
            group_max, group_end = value, end
    return total + group_max, overlap
