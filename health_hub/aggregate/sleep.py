"""Shared, pure sleep totals derived only from complete recorded stages."""
from datetime import datetime

ASLEEP_STAGES = frozenset({"sleeping", "light", "deep", "rem"})
AWAKE_STAGES = frozenset({"awake", "awake_in_bed", "out_of_bed"})


def sleep_totals(start_utc: str, end_utc: str, stages: list[dict]) -> tuple[int, int | None]:
    """Return time in bed and observed time asleep, in seconds.

    Missing, unknown, overlapping, or incomplete stages leave time asleep
    unknown. Stage boundaries and declared durations must agree and cover
    the session exactly. Stored records are never modified.
    """
    start, end = datetime.fromisoformat(start_utc), datetime.fromisoformat(end_utc)
    in_bed = int((end - start).total_seconds())
    if not stages:
        return in_bed, None
    try:
        return in_bed, _observed_asleep(start, end, stages)
    except (KeyError, TypeError, ValueError):
        return in_bed, None


def _observed_asleep(start: datetime, end: datetime, stages: list[dict]) -> int | None:
    position, asleep = start, 0
    ordered = sorted(stages, key=lambda stage: datetime.fromisoformat(stage["start_utc"]))
    for stage in ordered:
        begin = datetime.fromisoformat(stage["start_utc"])
        finish = datetime.fromisoformat(stage["end_utc"])
        seconds = (finish - begin).total_seconds()
        if begin != position or finish <= begin or finish > end or seconds != stage["seconds"]:
            return None
        if stage["stage"] in ASLEEP_STAGES:
            asleep += int(seconds)
        elif stage["stage"] not in AWAKE_STAGES:
            return None
        position = finish
    return asleep if position == end else None
