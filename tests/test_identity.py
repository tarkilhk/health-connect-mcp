"""Tests for health_hub.ingest.identity.event_id."""
from health_hub.ingest.identity import event_id


def test_same_record_produces_same_id():
    a = event_id("steps", "2026-08-09T08:00:00Z", "2026-08-09T09:00:00Z", '{"count":1234}')
    b = event_id("steps", "2026-08-09T08:00:00Z", "2026-08-09T09:00:00Z", '{"count":1234}')
    assert a == b
    assert len(a) == 64  # SHA-256 hex


def test_changed_value_produces_different_id():
    a = event_id("steps", "2026-08-09T08:00:00Z", "2026-08-09T09:00:00Z", '{"count":1234}')
    b = event_id("steps", "2026-08-09T08:00:00Z", "2026-08-09T09:00:00Z", '{"count":5678}')
    assert a != b


def test_changed_data_type_produces_different_id():
    a = event_id("steps", "2026-08-09T08:00:00Z", "2026-08-09T09:00:00Z", '{"count":1234}')
    b = event_id("distance", "2026-08-09T08:00:00Z", "2026-08-09T09:00:00Z", '{"count":1234}')
    assert a != b


def test_changed_time_produces_different_id():
    a = event_id("steps", "2026-08-09T08:00:00Z", "2026-08-09T09:00:00Z", '{"count":1234}')
    b = event_id("steps", "2026-08-09T08:00:00Z", "2026-08-09T10:00:00Z", '{"count":1234}')
    assert a != b


def test_event_id_canonicalizes_non_sorted_json():
    """M1: event_id canonicalizes value_json internally; key order irrelevant."""
    rec_a = '{"count":1234,"start_time":"2026-08-09T08:00:00Z","end_time":"2026-08-09T09:00:00Z"}'
    rec_b = '{"end_time":"2026-08-09T09:00:00Z","count":1234,"start_time":"2026-08-09T08:00:00Z"}'
    assert rec_a != rec_b  # raw strings differ
    a = event_id("steps", "2026-08-09T08:00:00Z", "2026-08-09T09:00:00Z", rec_a)
    b = event_id("steps", "2026-08-09T08:00:00Z", "2026-08-09T09:00:00Z", rec_b)
    assert a == b


def test_id_is_hex_string():
    eid = event_id("steps", "2026-08-09T08:00:00Z", "2026-08-09T09:00:00Z", '{"count":1234}')
    int(eid, 16)  # raises if not hex
    assert all(c in "0123456789abcdef" for c in eid)
