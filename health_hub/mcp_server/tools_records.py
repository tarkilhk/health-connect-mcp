"""Explicit typed MCP access to every registered stored data type."""
import json
from typing import Annotated

from pydantic import Field, ValidationError

from health_hub.config import load_settings
from health_hub.contract.registry import TYPE_REGISTRY
from health_hub.contract.values import JsonValues
from health_hub.mcp_server.records_query import (
    decode_cursor,
    encode_cursor,
    load_record_page,
    record_window,
)
from health_hub.mcp_server.schemas_records import (
    RECORD_MODELS,
    DataType,
    DataTypeInfo,
    DataTypesOutput,
    RecordsOutput,
)
from health_hub.mcp_server.server import mcp


def _type_caveats(spec) -> list[str]:
    caveats = ["Stored records are not daily aggregates; overlapping sources can double count."]
    if spec.values_model is JsonValues:
        caveats.append("No value-field contract is defined; interpret only the stored JSON fields.")
    if spec.kind.value == "interval":
        caveats.append("Intersecting intervals are returned whole, without clipping or prorating values.")
    if spec.kind.value == "session":
        caveats.append("Sleep uses session end attribution; duration_seconds is time in bed, including awake stages.")
        caveats.append("Stages are stored input JSON; malformed entries can exist and must not be inferred.")
    return caveats


@mcp.tool()
async def list_data_types() -> DataTypesOutput:
    """Discover all stored types, their kinds, units and typed value schemas.

    Use health_records for explicit record access. daily_summary and
    metric_series provide the documented subset of aggregated metrics.
    No measurements or raw payloads are returned by this catalog.
    """
    return DataTypesOutput(
        data_types=[DataTypeInfo(
            data_type=key, kind=spec.kind.value, unit=spec.unit,
            value_field=spec.value_field, cumulative=spec.cumulative,
            value_schema=spec.values_model.model_json_schema(),
            caveats=_type_caveats(spec),
        ) for key, spec in TYPE_REGISTRY.items()],
        caveats=["Registration does not imply data exists or permission was granted.",
                 "Value schemas annotate per-field units; category and session units are labels, not quantities."],
    )


@mcp.tool()
async def health_records(
    data_type: DataType, date_from: str, date_to: str,
    limit: Annotated[int, Field(strict=True, ge=1, le=400)] = 100,
    cursor: str | None = None,
) -> RecordsOutput:
    """Read one explicitly selected type with lossless stored JSON values.

    Dates are inclusive YYYY-MM-DD in TIMEZONE with DAY_BOUNDARY_HOUR.
    Instants use time, sleep uses session end, intervals intersect the window.
    Returns at most 400 records; pass next_cursor with the same type/dates
    for the next page. Units and value contracts are in list_data_types.
    Records are content-hash deduplicated, without overlap suppression.
    Missing values mean unknown, never zero or a negative health finding.
    """
    settings = load_settings()
    window = record_window(date_from, date_to, settings)
    query = [data_type, *window]
    position = decode_cursor(cursor, query)
    spec = TYPE_REGISTRY[data_type]
    rows = await load_record_page(spec, window, limit, position)
    caveats = _type_caveats(spec)
    records = _parse_records(rows[:limit], spec, caveats)
    next_cursor = encode_cursor(query, rows[limit - 1]) if len(rows) > limit else None
    if next_cursor:
        caveats.append("More records exist; continue with next_cursor. New ingestion can change subsequent pages.")
    if not rows:
        caveats.append("No stored records in this window; absence does not imply zero or a negative finding.")
    return RecordsOutput(
        data_type=data_type, date_from=date_from, date_to=date_to,
        timezone=settings.timezone, day_boundary_hour=settings.day_boundary_hour,
        window_start_utc=window[0], window_end_utc=window[1],
        records=records, next_cursor=next_cursor, caveats=caveats,
    )


def _parse_records(rows, spec, caveats):
    records = []
    for event_id, start, end, stored_json in rows:
        try:
            records.append(RECORD_MODELS[spec.data_type](
                event_id=event_id, start_utc=start, end_utc=end,
                data_type=spec.data_type, kind=spec.kind.value,
                values=json.loads(stored_json),
            ))
        except (ValueError, TypeError, ValidationError):
            caveats.append(f"Stored event {event_id} has invalid values for its declared schema and was skipped.")
    return records
