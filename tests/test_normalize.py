"""Tests for health_hub.ingest.normalize."""
import json
from datetime import datetime, timedelta
from pathlib import Path

from health_hub.contract.kinds import RecordKind
from health_hub.contract.registry import TYPE_REGISTRY
from health_hub.ingest.identity import event_id
from health_hub.ingest.normalize import normalize_health_payload

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _count_input_records(payload):
    return sum(len(payload[k]) for k in TYPE_REGISTRY if isinstance(payload.get(k), list))


def _parse_iso(s):
    return datetime.fromisoformat(s)


def test_full_payload_event_count_matches_input():
    payload = _load("payload_health_full.json")
    events, _, errors = normalize_health_payload(payload)
    assert errors == []
    assert len(events) == _count_input_records(payload)
    assert len(events) >= 29
    # Fixture covers the 25 original types; the 4 HC-Webhook-only types
    # (skin_temperature, basal_metabolic_rate, vo2_max, intermenstrual_bleeding)
    # are absent. Every event type must still be a valid registry key.
    assert {e.data_type for e in events} <= set(TYPE_REGISTRY.keys())
    assert len([e for e in events if e.data_type == "heart_rate"]) == 5


def test_sparse_payload_only_steps_sleep():
    payload = _load("payload_health_sparse.json")
    events, stages, errors = normalize_health_payload(payload)
    assert errors == []
    assert len(events) == 3
    assert {e.data_type for e in events} == {"steps", "sleep"}
    assert len(stages) == 4


def test_heart_rate_100_samples():
    payload = {
        "timestamp": "2026-08-09T12:00:00Z", "app_version": "1.4.0", "source": "health_connect",
        "heart_rate": [{"bpm": 60 + (i % 20), "time": f"2026-08-09T08:{i:02d}:00Z"} for i in range(100)],
    }
    events, stages, _ = normalize_health_payload(payload)
    assert len(events) == 100 and stages == []


def test_sleep_start_utc_equals_end_minus_duration():
    payload = _load("payload_health_full.json")
    events, _, _ = normalize_health_payload(payload)
    sleep = [e for e in events if e.data_type == "sleep"]
    assert len(sleep) == 2
    for rec in payload["sleep"]:
        end_dt = _parse_iso(rec["session_end_time"])
        start_dt = end_dt - timedelta(seconds=rec["duration_seconds"])
        assert (start_dt, end_dt) in [(_parse_iso(e.start_utc), _parse_iso(e.end_utc)) for e in sleep]


def test_sleep_stages_link():
    payload = _load("payload_health_full.json")
    events, stages, _ = normalize_health_payload(payload)
    sleep = [e for e in events if e.data_type == "sleep"]
    assert len(stages) == sum(len(r.get("stages") or []) for r in payload["sleep"])
    valid_ids = {event_id(e.data_type, e.start_utc, e.end_utc, e.value_json) for e in sleep}
    for s in stages:
        assert s["session_event_id"] in valid_ids
        assert set(s.keys()) == {"session_event_id", "stage", "start_utc", "end_utc", "seconds"}


def test_empty_and_diagnostics_only_payloads():
    base = {"timestamp": "t", "app_version": "v", "source": "s"}
    assert normalize_health_payload(dict(base)) == ([], [], [])
    p = dict(base, _diagnostics={"steps": {}})
    assert normalize_health_payload(p) == ([], [], [])


def test_absent_keys_in_sparse():
    payload = _load("payload_health_sparse.json")
    events, _, _ = normalize_health_payload(payload)
    present = {e.data_type for e in events}
    for key in TYPE_REGISTRY:
        assert (key in present) == (key in ("steps", "sleep"))


def test_instant_events_equal_start_end():
    payload = _load("payload_health_full.json")
    events, _, _ = normalize_health_payload(payload)
    instant_types = {s.data_type for s in TYPE_REGISTRY.values() if s.kind == RecordKind.INSTANT}
    for e in events:
        if e.data_type in instant_types:
            assert e.start_utc == e.end_utc


def test_interval_events_and_value_json():
    payload = _load("payload_health_full.json")
    events, _, _ = normalize_health_payload(payload)
    steps = [e for e in events if e.data_type == "steps"]
    assert {(e.start_utc, e.end_utc) for e in steps} == {(r["start_time"], r["end_time"]) for r in payload["steps"]}
    for e in events:
        parsed = json.loads(e.value_json)
        assert e.value_json == json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def test_value_num_for_primary_fields():
    """H1: exercise/mindfulness/blood_pressure have primary value_field; menstruation_period is None."""
    payload = _load("payload_health_full.json")
    events, _, _ = normalize_health_payload(payload)
    by_type = {}
    for e in events:
        by_type.setdefault(e.data_type, []).append(e)
    for dt, field in [("exercise", "duration_seconds"), ("mindfulness", "duration_seconds"),
                      ("blood_pressure", "systolic"), ("steps", "count")]:
        for e in by_type.get(dt, []):
            assert e.value_num == json.loads(e.value_json)[field], f"{dt} value_num mismatch"
    for e in by_type.get("menstruation_period", []):
        assert e.value_num is None


def test_nutrition_with_and_without_calories():
    """H1: nutrition value_field is 'calories'; missing -> None, present -> value."""
    payload = {
        "timestamp": "t", "app_version": "v", "source": "s",
        "nutrition": [
            {"calories": 320, "protein_grams": 18, "start_time": "2026-08-09T08:00:00Z", "end_time": "2026-08-09T09:00:00Z"},
            {"protein_grams": 5, "start_time": "2026-08-09T09:00:00Z", "end_time": "2026-08-09T10:00:00Z"},
        ],
    }
    events, _, _ = normalize_health_payload(payload)
    nutr = [e for e in events if e.data_type == "nutrition"]
    assert len(nutr) == 2 and nutr[0].value_num == 320 and nutr[1].value_num is None


def test_bad_interval_beside_valid():
    payload = {
        "timestamp": "t", "app_version": "v", "source": "s",
        "steps": [
            {"count": 100, "start_time": "2026-08-09T08:00:00Z", "end_time": "2026-08-09T09:00:00Z"},
            {"count": 200},
        ],
    }
    events, _, errors = normalize_health_payload(payload)
    assert len(events) == 1 and events[0].data_type == "steps"
    assert len(errors) == 1 and errors[0]["type"] == "steps" and errors[0]["index"] == 1


def test_malformed_sleep_beside_valid():
    payload = {
        "timestamp": "t", "app_version": "v", "source": "s",
        "sleep": [
            {"duration_seconds": 28800},
            {"session_end_time": "2026-08-09T07:00:00Z", "duration_seconds": 28800, "stages": []},
        ],
    }
    events, _, errors = normalize_health_payload(payload)
    assert len([e for e in events if e.data_type == "sleep"]) == 1
    assert len(errors) == 1 and errors[0]["type"] == "sleep"


def test_bad_stage_skipped_session_kept():
    payload = {
        "timestamp": "t", "app_version": "v", "source": "s",
        "sleep": [{
            "session_end_time": "2026-08-09T07:00:00Z", "duration_seconds": 28800,
            "stages": [
                {"stage": "deep", "start_time": "2026-08-08T23:00:00Z", "end_time": "2026-08-09T01:00:00Z", "duration_seconds": 7200},
                {"stage": "light"},
            ],
        }],
    }
    events, stages, errors = normalize_health_payload(payload)
    assert len(events) == 1 and len(stages) == 1
    # M3: malformed stage is now reported in errors, not silently dropped.
    assert len(errors) == 1
    assert errors[0]["type"] == "sleep"
    assert errors[0]["index"] == 0
    assert errors[0]["stage_index"] == 1
    assert "error" in errors[0]


def test_bad_stage_reports_error_with_correct_indices():
    """M3: multiple bad stages in different sessions get correct indices."""
    payload = {
        "timestamp": "t", "app_version": "v", "source": "s",
        "sleep": [
            {
                "session_end_time": "2026-08-09T07:00:00Z", "duration_seconds": 28800,
                "stages": [
                    {"stage": "deep", "start_time": "2026-08-08T23:00:00Z", "end_time": "2026-08-09T01:00:00Z", "duration_seconds": 7200},
                    {"stage": "light"},  # bad: missing keys
                ],
            },
            {
                "session_end_time": "2026-08-10T07:00:00Z", "duration_seconds": 28800,
                "stages": [
                    "not-a-dict",  # bad: not a dict
                    {"stage": "rem", "start_time": "2026-08-10T04:00:00Z", "end_time": "2026-08-10T05:00:00Z", "duration_seconds": 3600},
                ],
            },
        ],
    }
    events, stages, errors = normalize_health_payload(payload)
    assert len(events) == 2  # both sessions survive
    assert len(stages) == 2  # one valid stage per session
    assert len(errors) == 2  # one bad stage per session
    # First error: session 0, stage 1
    assert errors[0]["index"] == 0 and errors[0]["stage_index"] == 1
    # Second error: session 1, stage 0
    assert errors[1]["index"] == 1 and errors[1]["stage_index"] == 0


def test_non_dict_item_skipped_with_error():
    payload = {
        "timestamp": "t", "app_version": "v", "source": "s",
        "steps": [
            {"count": 100, "start_time": "2026-08-09T08:00:00Z", "end_time": "2026-08-09T09:00:00Z"},
            "not-a-dict", 42,
        ],
    }
    events, _, errors = normalize_health_payload(payload)
    assert len(events) == 1 and len(errors) == 2 and {e["index"] for e in errors} == {1, 2}


def test_sleep_start_preserves_fractional_seconds():
    payload = {
        "timestamp": "t", "app_version": "v", "source": "s",
        "sleep": [{"session_end_time": "2026-08-09T07:00:00.500000Z", "duration_seconds": 3600, "stages": []}],
    }
    events, _, _ = normalize_health_payload(payload)
    assert events[0].start_utc == "2026-08-09T06:00:00.500000Z"
    assert events[0].end_utc == "2026-08-09T07:00:00.500000Z"
