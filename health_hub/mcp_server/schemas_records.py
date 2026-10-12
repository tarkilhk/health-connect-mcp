"""Registry-derived MCP input enum and discriminated record output schemas."""
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, JsonValue, create_model

from health_hub.contract.registry import TYPE_REGISTRY

DataType = Literal[tuple(TYPE_REGISTRY)]


class StoredRecord(BaseModel):
    """Normalized boundaries and lossless stored values, with no aggregation."""

    event_id: str
    start_utc: str
    end_utc: str


RECORD_MODELS = {
    key: create_model(
        f"{key.title().replace('_', '')}Record",
        __base__=StoredRecord,
        data_type=(Literal[key], ...),
        kind=(Literal[spec.kind.value], ...),
        values=(spec.values_model, ...),
    )
    for key, spec in TYPE_REGISTRY.items()
}
# The union is built at runtime from the registry, so PEP 604 syntax cannot
# express its variable number of members.
HealthRecord = Annotated[Union[tuple(RECORD_MODELS.values())], Field(discriminator="data_type")]  # noqa: UP007


class RecordsOutput(BaseModel):
    """One bounded page; next_cursor continues the same type and date window."""

    data_type: DataType
    date_from: str
    date_to: str
    timezone: str
    day_boundary_hour: int
    window_start_utc: str
    window_end_utc: str
    records: list[HealthRecord]
    next_cursor: str | None
    caveats: list[str]


class DataTypeInfo(BaseModel):
    data_type: DataType
    kind: Literal["instant", "interval", "session"]
    unit: str
    value_field: str | None
    cumulative: bool
    value_schema: dict[str, JsonValue]
    caveats: list[str]


class DataTypesOutput(BaseModel):
    data_types: list[DataTypeInfo]
    caveats: list[str]
