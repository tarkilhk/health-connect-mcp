"""Tests for health_hub.contract.registry."""
from health_hub.contract.kinds import RecordKind
from health_hub.contract.registry import TYPE_REGISTRY


def test_registry_has_25_entries():
    assert len(TYPE_REGISTRY) == 25


def test_no_duplicate_json_keys():
    keys = [spec.json_key for spec in TYPE_REGISTRY.values()]
    assert len(keys) == len(set(keys))


def test_json_key_matches_dict_key():
    for key, spec in TYPE_REGISTRY.items():
        assert spec.json_key == key


def test_registry_matches_full_contract():
    """M4: comprehensive check of (kind, value_field, cumulative) for all 25 types.

    Replaces scattered shallow checks with one authoritative mapping. Any
    missing-field or wrong-value bug fails here.
    """
    expected = {
        "steps": (RecordKind.INTERVAL, "count", True),
        "distance": (RecordKind.INTERVAL, "meters", True),
        "active_calories": (RecordKind.INTERVAL, "calories", True),
        "total_calories": (RecordKind.INTERVAL, "calories", True),
        "exercise": (RecordKind.INTERVAL, "duration_seconds", False),
        "hydration": (RecordKind.INTERVAL, "liters", True),
        "nutrition": (RecordKind.INTERVAL, "calories", True),
        "mindfulness": (RecordKind.INTERVAL, "duration_seconds", False),
        "menstruation_period": (RecordKind.INTERVAL, None, False),
        "sleep": (RecordKind.SESSION, None, False),
        "heart_rate": (RecordKind.INSTANT, "bpm", False),
        "resting_heart_rate": (RecordKind.INSTANT, "bpm", False),
        "heart_rate_variability": (RecordKind.INSTANT, "heart_rate_variability_millis", False),
        "blood_pressure": (RecordKind.INSTANT, "systolic", False),
        "blood_glucose": (RecordKind.INSTANT, "mmol_per_liter", False),
        "oxygen_saturation": (RecordKind.INSTANT, "percentage", False),
        "respiratory_rate": (RecordKind.INSTANT, "rate", False),
        "body_temperature": (RecordKind.INSTANT, "celsius", False),
        "weight": (RecordKind.INSTANT, "kilograms", False),
        "height": (RecordKind.INSTANT, "meters", False),
        "body_fat": (RecordKind.INSTANT, "percentage", False),
        "lean_body_mass": (RecordKind.INSTANT, "kilograms", False),
        "bone_mass": (RecordKind.INSTANT, "kilograms", False),
        "body_water_mass": (RecordKind.INSTANT, "kilograms", False),
        "menstruation_flow": (RecordKind.INSTANT, "flow", False),
    }
    assert set(TYPE_REGISTRY.keys()) == set(expected.keys())
    for key, (kind, value_field, cumulative) in expected.items():
        spec = TYPE_REGISTRY[key]
        assert spec.kind == kind, f"{key}: kind {spec.kind} != {kind}"
        assert spec.value_field == value_field, (
            f"{key}: value_field {spec.value_field!r} != {value_field!r}"
        )
        assert spec.cumulative == cumulative, (
            f"{key}: cumulative {spec.cumulative} != {cumulative}"
        )
