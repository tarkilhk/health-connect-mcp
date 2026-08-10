"""Tests for health_hub.aggregate.dedup — overlap suppression for cumulative types."""
import pytest

from health_hub.aggregate.dedup import sum_non_overlapping
from health_hub.contract.payload import Event


def _evt(start: str, end: str, value: float) -> Event:
    return Event(
        data_type="steps",
        start_utc=start,
        end_utc=end,
        value_num=value,
        value_json="{}",
    )


def test_empty_returns_zero_no_overlap():
    assert sum_non_overlapping([]) == (0.0, False)


def test_single_event_returns_value_no_overlap():
    e = _evt("2026-08-09T08:00:00Z", "2026-08-09T09:00:00Z", 5000.0)
    assert sum_non_overlapping([e]) == (5000.0, False)


def test_two_completely_overlapping_takes_max():
    a = _evt("2026-08-09T08:00:00Z", "2026-08-09T09:00:00Z", 5000.0)
    b = _evt("2026-08-09T08:00:00Z", "2026-08-09T09:00:00Z", 5000.0)
    total, overlap = sum_non_overlapping([a, b])
    assert total == 5000.0
    assert overlap is True


def test_two_non_overlapping_sums():
    a = _evt("2026-08-09T08:00:00Z", "2026-08-09T09:00:00Z", 5000.0)
    b = _evt("2026-08-09T10:00:00Z", "2026-08-09T11:00:00Z", 5000.0)
    total, overlap = sum_non_overlapping([a, b])
    assert total == 10000.0
    assert overlap is False


def test_complex_mixed_overlap_and_separate():
    # Group A: [0-10] val 5000 overlaps [5-15] val 3000 -> max 5000.
    # Group B: [20-30] val 2000 standalone -> 2000.
    # Total = 7000, overlap True.
    a = _evt("2026-08-09T00:00:00Z", "2026-08-09T00:10:00Z", 5000.0)
    b = _evt("2026-08-09T00:05:00Z", "2026-08-09T00:15:00Z", 3000.0)
    c = _evt("2026-08-09T00:20:00Z", "2026-08-09T00:30:00Z", 2000.0)
    total, overlap = sum_non_overlapping([a, b, c])
    assert total == 7000.0
    assert overlap is True


def test_none_value_num_raises_value_error():
    e = Event(
        data_type="steps",
        start_utc="2026-08-09T08:00:00Z",
        end_utc="2026-08-09T09:00:00Z",
        value_num=None,
        value_json="{}",
    )
    with pytest.raises(ValueError):
        sum_non_overlapping([e])


def test_touching_intervals_not_treated_as_overlap():
    # [0-10] and [10-20] touch at 10 but do not overlap (strict <).
    a = _evt("2026-08-09T08:00:00Z", "2026-08-09T09:00:00Z", 5000.0)
    b = _evt("2026-08-09T09:00:00Z", "2026-08-09T10:00:00Z", 5000.0)
    total, overlap = sum_non_overlapping([a, b])
    assert total == 10000.0
    assert overlap is False


def test_three_way_overlap_takes_single_max():
    a = _evt("2026-08-09T08:00:00Z", "2026-08-09T09:00:00Z", 4000.0)
    b = _evt("2026-08-09T08:30:00Z", "2026-08-09T09:30:00Z", 6000.0)
    c = _evt("2026-08-09T08:45:00Z", "2026-08-09T09:15:00Z", 5000.0)
    total, overlap = sum_non_overlapping([a, b, c])
    assert total == 6000.0
    assert overlap is True
