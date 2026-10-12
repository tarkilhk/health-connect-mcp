"""Tests for health_hub.aggregate.rollup — daily aggregation per SPEC §7.3."""
from __future__ import annotations

from health_hub.aggregate.rollup import DailyRollup, compute_rollup
from health_hub.contract.payload import Event
from health_hub.ingest.identity import event_id


def _evt(data_type: str, start: str, end: str, value_num: float | None) -> Event:
    return Event(
        data_type=data_type,
        start_utc=start,
        end_utc=end,
        value_num=value_num,
        value_json="{}",
    )


DATE = "2026-08-09"


def test_steps_overlapping_takes_max():
    a = _evt("steps", "2026-08-09T08:00:00Z", "2026-08-09T09:00:00Z", 5000.0)
    b = _evt("steps", "2026-08-09T08:30:00Z", "2026-08-09T09:30:00Z", 5000.0)
    r = compute_rollup(DATE, [a, b], [])
    assert r.steps == 5000
    assert r.has_overlap is True


def test_active_kcal_and_total_kcal_kept_separate():
    active = [
        _evt("active_calories", "2026-08-09T08:00:00Z", "2026-08-09T09:00:00Z", 200.0),
        _evt("active_calories", "2026-08-09T10:00:00Z", "2026-08-09T11:00:00Z", 300.0),
    ]
    total = [
        _evt("total_calories", "2026-08-09T08:00:00Z", "2026-08-09T09:00:00Z", 1800.0),
        _evt("total_calories", "2026-08-09T10:00:00Z", "2026-08-09T11:00:00Z", 1900.0),
    ]
    r = compute_rollup(DATE, active + total, [])
    assert r.active_kcal == 500.0
    assert r.total_kcal == 3700.0
    assert r.active_kcal != r.total_kcal


def test_sleep_8h_with_2h_awake():
    # Session 22:00 -> 06:00 next day = 8h = 28800s. End on 2026-08-09.
    sleep = _evt(
        "sleep",
        "2026-08-08T22:00:00Z",
        "2026-08-09T06:00:00Z",
        None,
    )
    stages = [
        {"session_event_id": event_id(sleep.data_type, sleep.start_utc, sleep.end_utc, sleep.value_json), "stage": "sleeping", "start_utc": "2026-08-08T22:00:00Z",
         "end_utc": "2026-08-09T00:00:00Z", "seconds": 7200},
        {"session_event_id": event_id(sleep.data_type, sleep.start_utc, sleep.end_utc, sleep.value_json), "stage": "awake", "start_utc": "2026-08-09T00:00:00Z",
         "end_utc": "2026-08-09T02:00:00Z", "seconds": 7200},
        {"session_event_id": event_id(sleep.data_type, sleep.start_utc, sleep.end_utc, sleep.value_json), "stage": "sleeping", "start_utc": "2026-08-09T02:00:00Z",
         "end_utc": "2026-08-09T06:00:00Z", "seconds": 14400},
    ]
    r = compute_rollup(DATE, [sleep], stages)
    assert r.sleep_in_bed_s == 28800
    assert r.sleep_asleep_s == 21600


def test_heart_rate_avg_min_max():
    hr = [
        _evt("heart_rate", "2026-08-09T08:00:00Z", "2026-08-09T08:00:00Z", 60.0),
        _evt("heart_rate", "2026-08-09T09:00:00Z", "2026-08-09T09:00:00Z", 80.0),
        _evt("heart_rate", "2026-08-09T10:00:00Z", "2026-08-09T10:00:00Z", 100.0),
    ]
    r = compute_rollup(DATE, hr, [])
    assert r.hr_avg == 80.0
    assert r.hr_min == 60
    assert r.hr_max == 100


def test_resting_heart_rate_takes_last():
    rhr = [
        _evt("resting_heart_rate", "2026-08-09T08:00:00Z", "2026-08-09T08:00:00Z", 65.0),
        _evt("resting_heart_rate", "2026-08-09T20:00:00Z", "2026-08-09T20:00:00Z", 62.0),
    ]
    r = compute_rollup(DATE, rhr, [])
    assert r.hr_resting == 62


def test_weight_takes_last_measurement():
    w = [
        _evt("weight", "2026-08-09T08:00:00Z", "2026-08-09T08:00:00Z", 70.5),
        _evt("weight", "2026-08-09T20:00:00Z", "2026-08-09T20:00:00Z", 71.0),
    ]
    r = compute_rollup(DATE, w, [])
    assert r.weight_kg == 71.0


def test_coverage_absent_for_missing_metric():
    # Only steps provided; weight should be absent.
    steps = _evt("steps", "2026-08-09T08:00:00Z", "2026-08-09T09:00:00Z", 1000.0)
    r = compute_rollup(DATE, [steps], [])
    assert r.coverage_json["weight"] == "absent"
    assert r.coverage_json["steps"] == "ok"


def test_coverage_partial_when_gap_exceeds_six_hours():
    # Two HR samples 8h apart -> gap > 6h -> partial.
    hr = [
        _evt("heart_rate", "2026-08-09T08:00:00Z", "2026-08-09T08:00:00Z", 60.0),
        _evt("heart_rate", "2026-08-09T16:00:00Z", "2026-08-09T16:00:00Z", 80.0),
    ]
    r = compute_rollup(DATE, hr, [])
    assert r.coverage_json["heart_rate"] == "partial"


def test_coverage_ok_when_samples_dense():
    # HR samples 1h apart -> ok.
    hr = [
        _evt("heart_rate", "2026-08-09T08:00:00Z", "2026-08-09T08:00:00Z", 60.0),
        _evt("heart_rate", "2026-08-09T09:00:00Z", "2026-08-09T09:00:00Z", 62.0),
        _evt("heart_rate", "2026-08-09T10:00:00Z", "2026-08-09T10:00:00Z", 64.0),
    ]
    r = compute_rollup(DATE, hr, [])
    assert r.coverage_json["heart_rate"] == "ok"


def test_empty_events_returns_empty_rollup_with_all_absent():
    r = compute_rollup(DATE, [], [])
    assert isinstance(r, DailyRollup)
    assert r.local_date == DATE
    assert r.steps is None
    assert r.sleep_in_bed_s is None
    assert all(v == "absent" for v in r.coverage_json.values())
    assert r.has_overlap is False


def test_distance_cumulative_dedup():
    a = _evt("distance", "2026-08-09T08:00:00Z", "2026-08-09T09:00:00Z", 1000.0)
    b = _evt("distance", "2026-08-09T08:30:00Z", "2026-08-09T09:30:00Z", 1200.0)
    c = _evt("distance", "2026-08-09T11:00:00Z", "2026-08-09T12:00:00Z", 500.0)
    r = compute_rollup(DATE, [a, b, c], [])
    # Group 1: max(1000, 1200)=1200. Group 2: 500. Total 1700.
    assert r.distance_m == 1700.0
    assert r.has_overlap is True


def test_coverage_no_false_positive_for_nested_intervals():
    """M1: nested intervals inside a wider span must not trigger 'partial'.

    Without max_end_so_far tracking, [00:00-12:00] then [01:00-02:00] then
    [10:00-11:00] would compare 10:00 against 02:00 and wrongly flag a gap.
    With max_end tracking, max_end stays at 12:00 throughout -> no gap -> "ok".
    """
    hr = [
        _evt("heart_rate", "2026-08-09T00:00:00Z", "2026-08-09T12:00:00Z", 70.0),
        _evt("heart_rate", "2026-08-09T01:00:00Z", "2026-08-09T02:00:00Z", 72.0),
        _evt("heart_rate", "2026-08-09T10:00:00Z", "2026-08-09T11:00:00Z", 74.0),
    ]
    r = compute_rollup(DATE, hr, [])
    assert r.coverage_json["heart_rate"] == "ok"


def test_coverage_detects_real_gap_after_nested_span_ends():
    """M1: gap after the wider span ends (>6h) is still detected as 'partial'."""
    hr = [
        _evt("heart_rate", "2026-08-09T00:00:00Z", "2026-08-09T06:00:00Z", 70.0),
        _evt("heart_rate", "2026-08-09T01:00:00Z", "2026-08-09T02:00:00Z", 72.0),
        # 7h gap from 06:00 to 13:00 -> partial.
        _evt("heart_rate", "2026-08-09T13:00:00Z", "2026-08-09T14:00:00Z", 74.0),
    ]
    r = compute_rollup(DATE, hr, [])
    assert r.coverage_json["heart_rate"] == "partial"
