"""Type registry — single source of truth for the 34 stored data types.

Each TypeSpec describes one health data type: its JSON key, kind
(interval / instant / session), value field, unit, and whether it is
cumulative. No other module should hardcode type names; everything is
driven by iterating TYPE_REGISTRY.
"""
from dataclasses import dataclass

from pydantic import BaseModel

from health_hub.contract.kinds import RecordKind
from health_hub.contract.values import (
    BloodPressureValues,
    ExerciseValues,
    JsonValues,
    MindfulnessValues,
    NutritionValues,
    SleepValues,
    scalar_values,
)


@dataclass(frozen=True)
class TypeSpec:
    """Specification of a single Health Connect data type."""

    json_key: str
    data_type: str
    kind: RecordKind
    value_field: str | None
    unit: str
    cumulative: bool
    values_model: type[BaseModel]


TYPE_REGISTRY: dict[str, TypeSpec] = {
    "steps": TypeSpec("steps", "steps", RecordKind.INTERVAL, "count", "count", True, scalar_values("count", "count", int)),
    "distance": TypeSpec("distance", "distance", RecordKind.INTERVAL, "meters", "m", True, scalar_values("meters", "m")),
    "active_calories": TypeSpec("active_calories", "active_calories", RecordKind.INTERVAL, "calories", "kcal", True, scalar_values("calories", "kcal")),
    "total_calories": TypeSpec("total_calories", "total_calories", RecordKind.INTERVAL, "calories", "kcal", True, scalar_values("calories", "kcal")),
    "exercise": TypeSpec("exercise", "exercise", RecordKind.INTERVAL, "duration_seconds", "session", False, ExerciseValues),
    "hydration": TypeSpec("hydration", "hydration", RecordKind.INTERVAL, "liters", "l", True, scalar_values("liters", "l")),
    "nutrition": TypeSpec("nutrition", "nutrition", RecordKind.INTERVAL, "calories", "kcal", True, NutritionValues),
    "mindfulness": TypeSpec("mindfulness", "mindfulness", RecordKind.INTERVAL, "duration_seconds", "session", False, MindfulnessValues),
    "menstruation_period": TypeSpec("menstruation_period", "menstruation_period", RecordKind.INTERVAL, None, "session", False, JsonValues),
    "sleep": TypeSpec("sleep", "sleep", RecordKind.SESSION, None, "session", False, SleepValues),
    "heart_rate": TypeSpec("heart_rate", "heart_rate", RecordKind.INSTANT, "bpm", "bpm", False, scalar_values("bpm", "bpm", int)),
    "resting_heart_rate": TypeSpec("resting_heart_rate", "resting_heart_rate", RecordKind.INSTANT, "bpm", "bpm", False, scalar_values("bpm", "bpm", int)),
    "heart_rate_variability": TypeSpec("heart_rate_variability", "heart_rate_variability", RecordKind.INSTANT, "heart_rate_variability_millis", "ms", False, scalar_values("heart_rate_variability_millis", "ms")),
    "blood_pressure": TypeSpec("blood_pressure", "blood_pressure", RecordKind.INSTANT, "systolic", "mmHg", False, BloodPressureValues),
    "blood_glucose": TypeSpec("blood_glucose", "blood_glucose", RecordKind.INSTANT, "mmol_per_liter", "mmol/L", False, scalar_values("mmol_per_liter", "mmol/L")),
    "oxygen_saturation": TypeSpec("oxygen_saturation", "oxygen_saturation", RecordKind.INSTANT, "percentage", "%", False, scalar_values("percentage", "%")),
    "respiratory_rate": TypeSpec("respiratory_rate", "respiratory_rate", RecordKind.INSTANT, "rate", "breaths/min", False, scalar_values("rate", "breaths/min")),
    "body_temperature": TypeSpec("body_temperature", "body_temperature", RecordKind.INSTANT, "celsius", "°C", False, scalar_values("celsius", "°C")),
    "weight": TypeSpec("weight", "weight", RecordKind.INSTANT, "kilograms", "kg", False, scalar_values("kilograms", "kg")),
    "height": TypeSpec("height", "height", RecordKind.INSTANT, "meters", "m", False, scalar_values("meters", "m")),
    "body_fat": TypeSpec("body_fat", "body_fat", RecordKind.INSTANT, "percentage", "%", False, scalar_values("percentage", "%")),
    "lean_body_mass": TypeSpec("lean_body_mass", "lean_body_mass", RecordKind.INSTANT, "kilograms", "kg", False, scalar_values("kilograms", "kg")),
    "bone_mass": TypeSpec("bone_mass", "bone_mass", RecordKind.INSTANT, "kilograms", "kg", False, scalar_values("kilograms", "kg")),
    "menstruation_flow": TypeSpec("menstruation_flow", "menstruation_flow", RecordKind.INSTANT, "flow", "category", False, scalar_values("flow", "category", str)),
    "skin_temperature": TypeSpec("skin_temperature", "skin_temperature", RecordKind.INSTANT, "delta_celsius", "°C", False, scalar_values("delta_celsius", "°C")),
    "basal_metabolic_rate": TypeSpec("basal_metabolic_rate", "basal_metabolic_rate", RecordKind.INSTANT, "watts", "W", False, scalar_values("watts", "W")),
    "vo2_max": TypeSpec("vo2_max", "vo2_max", RecordKind.INSTANT, "ml_per_kg_per_min", "mL/(kg·min)", False, scalar_values("ml_per_kg_per_min", "mL/(kg·min)")),
    "intermenstrual_bleeding": TypeSpec("intermenstrual_bleeding", "intermenstrual_bleeding", RecordKind.INSTANT, None, "bool", False, JsonValues),
    "body_water_mass": TypeSpec("body_water_mass", "body_water_mass", RecordKind.INSTANT, "kilograms", "kg", False, scalar_values("kilograms", "kg")),
    "basal_body_temperature": TypeSpec("basal_body_temperature", "basal_body_temperature", RecordKind.INSTANT, None, "°C", False, JsonValues),
    "cervical_mucus": TypeSpec("cervical_mucus", "cervical_mucus", RecordKind.INSTANT, None, "category", False, JsonValues),
    "ovulation_test": TypeSpec("ovulation_test", "ovulation_test", RecordKind.INSTANT, None, "category", False, JsonValues),
    "sexual_activity": TypeSpec("sexual_activity", "sexual_activity", RecordKind.INSTANT, None, "bool", False, JsonValues),
    "test": TypeSpec("test", "test", RecordKind.INSTANT, None, "unknown", False, JsonValues),
}
