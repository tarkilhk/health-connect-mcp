"""Type registry — single source of truth for the 25 Health Connect types.

Each TypeSpec describes one health data type: its JSON key, kind
(interval / instant / session), value field, unit, and whether it is
cumulative. No other module should hardcode type names; everything is
driven by iterating TYPE_REGISTRY.
"""
from dataclasses import dataclass

from health_hub.contract.kinds import RecordKind


@dataclass(frozen=True)
class TypeSpec:
    """Specification of a single Health Connect data type."""

    json_key: str
    data_type: str
    kind: RecordKind
    value_field: str | None
    unit: str
    cumulative: bool


TYPE_REGISTRY: dict[str, TypeSpec] = {
    "steps": TypeSpec("steps", "steps", RecordKind.INTERVAL, "count", "count", True),
    "distance": TypeSpec("distance", "distance", RecordKind.INTERVAL, "meters", "m", True),
    "active_calories": TypeSpec("active_calories", "active_calories", RecordKind.INTERVAL, "calories", "kcal", True),
    "total_calories": TypeSpec("total_calories", "total_calories", RecordKind.INTERVAL, "calories", "kcal", True),
    "exercise": TypeSpec("exercise", "exercise", RecordKind.INTERVAL, "duration_seconds", "session", False),
    "hydration": TypeSpec("hydration", "hydration", RecordKind.INTERVAL, "liters", "l", True),
    "nutrition": TypeSpec("nutrition", "nutrition", RecordKind.INTERVAL, "calories", "kcal", True),
    "mindfulness": TypeSpec("mindfulness", "mindfulness", RecordKind.INTERVAL, "duration_seconds", "session", False),
    "menstruation_period": TypeSpec("menstruation_period", "menstruation_period", RecordKind.INTERVAL, None, "session", False),
    "sleep": TypeSpec("sleep", "sleep", RecordKind.SESSION, None, "session", False),
    "heart_rate": TypeSpec("heart_rate", "heart_rate", RecordKind.INSTANT, "bpm", "bpm", False),
    "resting_heart_rate": TypeSpec("resting_heart_rate", "resting_heart_rate", RecordKind.INSTANT, "bpm", "bpm", False),
    "heart_rate_variability": TypeSpec("heart_rate_variability", "heart_rate_variability", RecordKind.INSTANT, "heart_rate_variability_millis", "ms", False),
    "blood_pressure": TypeSpec("blood_pressure", "blood_pressure", RecordKind.INSTANT, "systolic", "mmHg", False),
    "blood_glucose": TypeSpec("blood_glucose", "blood_glucose", RecordKind.INSTANT, "mmol_per_liter", "mmol/L", False),
    "oxygen_saturation": TypeSpec("oxygen_saturation", "oxygen_saturation", RecordKind.INSTANT, "percentage", "%", False),
    "respiratory_rate": TypeSpec("respiratory_rate", "respiratory_rate", RecordKind.INSTANT, "rate", "breaths/min", False),
    "body_temperature": TypeSpec("body_temperature", "body_temperature", RecordKind.INSTANT, "celsius", "°C", False),
    "weight": TypeSpec("weight", "weight", RecordKind.INSTANT, "kilograms", "kg", False),
    "height": TypeSpec("height", "height", RecordKind.INSTANT, "meters", "m", False),
    "body_fat": TypeSpec("body_fat", "body_fat", RecordKind.INSTANT, "percentage", "%", False),
    "lean_body_mass": TypeSpec("lean_body_mass", "lean_body_mass", RecordKind.INSTANT, "kilograms", "kg", False),
    "bone_mass": TypeSpec("bone_mass", "bone_mass", RecordKind.INSTANT, "kilograms", "kg", False),
    "body_water_mass": TypeSpec("body_water_mass", "body_water_mass", RecordKind.INSTANT, "kilograms", "kg", False),
    "menstruation_flow": TypeSpec("menstruation_flow", "menstruation_flow", RecordKind.INSTANT, "flow", "category", False),
}
