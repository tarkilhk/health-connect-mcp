"""Tests for health_hub.aggregate.timewindow — pure UTC<->local date math."""
from health_hub.aggregate.timewindow import day_bounds_utc, local_date_of


def test_utc_boundary_zero_plain_date():
    # 02:00 UTC on 2026-08-09 with UTC tz and boundary 0 -> same date.
    assert local_date_of("2026-08-09T02:00:00Z", "UTC", 0) == "2026-08-09"


def test_moscow_boundary_four_shifts_to_prev_date():
    # 00:30 UTC == 03:30 MSK. With boundary=4 the day starts at 04:00 MSK,
    # so 03:30 belongs to the previous calendar date.
    assert (
        local_date_of("2026-08-09T00:30:00Z", "Europe/Moscow", 4)
        == "2026-08-08"
    )


def test_new_york_boundary_zero_crosses_date():
    # 03:00 UTC on 2026-08-09 == 23:00 EDT on 2026-08-08 (UTC-4 in August).
    assert (
        local_date_of("2026-08-09T03:00:00Z", "America/New_York", 0)
        == "2026-08-08"
    )


def test_new_york_boundary_zero_same_date():
    # 09:00 UTC on 2026-08-09 == 05:00 EDT on 2026-08-09.
    assert (
        local_date_of("2026-08-09T09:00:00Z", "America/New_York", 0)
        == "2026-08-09"
    )


def test_day_bounds_utc_utc_zero_boundary():
    start, end = day_bounds_utc("2026-08-09", "UTC", 0)
    assert start == "2026-08-09T00:00:00+00:00"
    assert end == "2026-08-10T00:00:00+00:00"


def test_day_bounds_utc_moscow_zero_boundary():
    # Moscow is UTC+3; local midnight 2026-08-09 == 2026-08-08T21:00 UTC.
    start, end = day_bounds_utc("2026-08-09", "Europe/Moscow", 0)
    assert start == "2026-08-08T21:00:00+00:00"
    assert end == "2026-08-09T21:00:00+00:00"


def test_day_bounds_utc_moscow_boundary_four():
    # boundary=4: local day starts at 04:00 MSK == 01:00 UTC.
    start, end = day_bounds_utc("2026-08-09", "Europe/Moscow", 4)
    assert start == "2026-08-09T01:00:00+00:00"
    assert end == "2026-08-10T01:00:00+00:00"


def test_iso_offset_input_parsed_correctly():
    # +05:00 offset, UTC tz, boundary 0: 10:00 +05:00 == 05:00 UTC -> same date.
    assert local_date_of("2026-08-09T10:00:00+05:00", "UTC", 0) == "2026-08-09"


def test_iso_offset_input_crosses_date():
    # 02:00 +05:00 == 21:00 previous-day UTC.
    assert local_date_of("2026-08-09T02:00:00+05:00", "UTC", 0) == "2026-08-08"


# --- DST transitions --------------------------------------------------------
# 2026 spring forward: Mar 8 02:00 -> 03:00 EST->EDT (loses 1 hour).
# 2026 fall back:     Nov 1 02:00 -> 01:00 EDT->EST (gains 1 hour).


def test_spring_forward_day_bounds_span_23_hours():
    """America/New_York 2026-03-08: day is 23h absolute (clocks jump 02->03)."""
    start, end = day_bounds_utc("2026-03-08", "America/New_York", 0)
    # Local midnight Mar 8 EST (UTC-5) == 05:00 UTC.
    assert start == "2026-03-08T05:00:00+00:00"
    # Local midnight Mar 9 EDT (UTC-4) == 04:00 UTC.
    assert end == "2026-03-09T04:00:00+00:00"
    # 23h gap.
    from datetime import datetime
    s = datetime.fromisoformat(start)
    e = datetime.fromisoformat(end)
    assert (e - s).total_seconds() == 23 * 3600


def test_fall_back_day_bounds_span_25_hours():
    """America/New_York 2026-11-01: day is 25h absolute (clocks rewind 02->01)."""
    start, end = day_bounds_utc("2026-11-01", "America/New_York", 0)
    # Local midnight Nov 1 EDT (UTC-4) == 04:00 UTC.
    assert start == "2026-11-01T04:00:00+00:00"
    # Local midnight Nov 2 EST (UTC-5) == 05:00 UTC.
    assert end == "2026-11-02T05:00:00+00:00"
    # 25h gap.
    from datetime import datetime
    s = datetime.fromisoformat(start)
    e = datetime.fromisoformat(end)
    assert (e - s).total_seconds() == 25 * 3600


def test_local_date_of_instant_before_spring_forward_transition():
    """01:30 EST on Mar 8 2026 (before 02:00 -> 03:00 jump) -> 2026-03-08."""
    # 01:30 EST == 06:30 UTC.
    assert (
        local_date_of("2026-03-08T06:30:00Z", "America/New_York", 0)
        == "2026-03-08"
    )


def test_local_date_of_instant_after_spring_forward_transition():
    """03:30 EDT on Mar 8 2026 (after 02:00 -> 03:00 jump) -> 2026-03-08."""
    # 03:30 EDT == 07:30 UTC.
    assert (
        local_date_of("2026-03-08T07:30:00Z", "America/New_York", 0)
        == "2026-03-08"
    )


def test_local_date_of_does_not_skip_during_spring_forward():
    """Instants 02:30 EST (06:30 UTC) and 03:30 EDT (07:30 UTC) both on Mar 8.

    The wall-clock hour 02:30 doesn't exist on Mar 8 (jumped from 02:00
    to 03:00), but a UTC instant that WOULD be 02:30 EST still maps to
    Mar 8 — we never attribute it to a different date.
    """
    assert local_date_of("2026-03-08T06:30:00Z", "America/New_York", 0) == "2026-03-08"
    assert local_date_of("2026-03-08T07:30:00Z", "America/New_York", 0) == "2026-03-08"


def test_fall_back_ambiguous_instant_attributed_correctly():
    """01:30 on Nov 1 2026 happens twice (EDT then EST). UTC disambiguates.

    05:30 UTC = 01:30 EDT (first occurrence, UTC-4).
    06:30 UTC = 01:30 EST (second occurrence, UTC-5).
    Both map to the same local date 2026-11-01.
    """
    assert local_date_of("2026-11-01T05:30:00Z", "America/New_York", 0) == "2026-11-01"
    assert local_date_of("2026-11-01T06:30:00Z", "America/New_York", 0) == "2026-11-01"


def test_spring_forward_day_with_boundary_hour():
    """With boundary=4, spring-forward day's UTC bounds shift by 4h."""
    # Local day starts at 04:00 EDT (UTC-4) == 08:00 UTC.
    # Local day ends at 04:00 EDT next day == 08:00 UTC next day (24h absolute
    # because boundary=4 falls inside EDT, no DST jump crossed).
    start, end = day_bounds_utc("2026-03-08", "America/New_York", 4)
    assert start == "2026-03-08T08:00:00+00:00"
    assert end == "2026-03-09T08:00:00+00:00"
