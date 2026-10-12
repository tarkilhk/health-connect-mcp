"""Typed stored values for MCP. Unspecified fields remain lossless JSON.

Only fields established by the ingestion contract are named here. Types
without a documented value contract use JsonValues rather than guessed fields.
"""
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, create_model


class JsonValues(BaseModel):
    """A stored JSON object, including any nested vendor-specific fields."""

    model_config = ConfigDict(extra="allow", strict=True, allow_inf_nan=False)
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)


def scalar_values(name: str, unit: str, value_type: type = float) -> type[BaseModel]:
    """Define a nullable measurement without coercing strings or categories."""
    return create_model(
        f"{name.title().replace('_', '')}Values",
        __base__=JsonValues,
        **{name: (value_type | None, Field(default=None, json_schema_extra={"unit": unit}))},
    )


class BloodPressureValues(JsonValues):
    systolic: Annotated[float | None, Field(json_schema_extra={"unit": "mmHg"})] = None
    diastolic: Annotated[float | None, Field(json_schema_extra={"unit": "mmHg"})] = None


class NutritionValues(JsonValues):
    calories: Annotated[float | None, Field(json_schema_extra={"unit": "kcal"})] = None
    protein_grams: Annotated[float | None, Field(json_schema_extra={"unit": "g"})] = None
    carbs_grams: Annotated[float | None, Field(json_schema_extra={"unit": "g"})] = None
    fat_grams: Annotated[float | None, Field(json_schema_extra={"unit": "g"})] = None


class ExerciseValues(JsonValues):
    type: str | None = None
    duration_seconds: Annotated[int | None, Field(json_schema_extra={"unit": "s"})] = None


class MindfulnessValues(JsonValues):
    title: str | None = None
    duration_seconds: Annotated[int | None, Field(json_schema_extra={"unit": "s"})] = None


class SleepStageValues(JsonValues):
    stage: str
    start_time: str
    end_time: str
    duration_seconds: Annotated[int, Field(json_schema_extra={"unit": "s"})]


class SleepValues(JsonValues):
    session_end_time: str
    duration_seconds: Annotated[int, Field(json_schema_extra={"unit": "s"})]
    # Ingestion accepts malformed stage entries; preserve them as JSON so an
    # invalid child cannot hide an otherwise stored sleep session.
    stages: list[SleepStageValues | JsonValue] | None = None
