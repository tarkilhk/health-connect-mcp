"""Pydantic models for MCP tool output schemas (SPEC §9.2).

Every output root is an ``object`` (never a bare array) and every response
includes ``caveats: list[str]`` so the LLM is warned about missing data,
overlaps, or aggregation changes rather than silently fabricating values.

These models document the shape of each tool's return value. The tools
themselves return plain ``dict`` instances (serialized to JSON text by the
MCP SDK); the models are kept here as the single source of truth for field
names and types.
"""
from __future__ import annotations

from pydantic import BaseModel


class DailySummaryDay(BaseModel):
    """One day's rolled-up metrics. ``None`` fields mean no data (not zero)."""

    local_date: str
    steps: int | None = None
    distance_m: float | None = None
    active_kcal: float | None = None
    total_kcal: float | None = None
    sleep_in_bed_s: int | None = None
    sleep_asleep_s: int | None = None
    hr_avg: float | None = None
    hr_min: int | None = None
    hr_max: int | None = None
    hr_resting: int | None = None
    weight_kg: float | None = None


class DailySummaryOutput(BaseModel):
    """Output of ``daily_summary``: days + per-date coverage map + caveats."""

    days: list[DailySummaryDay]
    coverage: dict[str, dict[str, str]]
    caveats: list[str]


class MetricPoint(BaseModel):
    """One point in a metric time series."""

    date: str
    value: float | None


class MetricSeriesOutput(BaseModel):
    """Output of ``metric_series``: metric name, unit, points, caveats."""

    metric: str
    unit: str
    points: list[MetricPoint]
    caveats: list[str]


class SleepSession(BaseModel):
    """One sleep session with its stages."""

    date: str
    start_utc: str | None
    end_utc: str
    time_in_bed_s: int
    time_asleep_s: int
    stages: list[dict]


class SleepDetailOutput(BaseModel):
    """Output of ``sleep_detail``: sessions + caveats."""

    sessions: list[SleepSession]
    caveats: list[str]


class SyncHealthMetric(BaseModel):
    """Sync health for one data type."""

    data_type: str
    last_event_utc: str | None
    coverage_days: int
    missing_days: int
    last_error: str | None


class SyncHealthOutput(BaseModel):
    """Output of ``get_sync_health``: per-type metrics + caveats."""

    metrics: list[SyncHealthMetric]
    period_days: int
    caveats: list[str]


class InspectPayloadOutput(BaseModel):
    """Output of ``inspect_last_payload``: metadata + diagnostics + caveats."""

    payload_id: int | None
    received_at: str | None
    app_version: str | None
    source: str | None
    byte_size: int | None
    signature_valid: bool | None
    diagnostics: list[dict]
    caveats: list[str]
