"""Pydantic v2 payload models and Event dataclass.

HealthPayload allows the 25 dynamic type keys plus _diagnostics as extra
fields, but rejects any key outside that set — notably `device`, which only
belongs in ScreenTimePayload.
"""
from dataclasses import dataclass

from pydantic import BaseModel, model_validator

from health_hub.contract.registry import TYPE_REGISTRY

_ALLOWED_HEALTH_KEYS = {"timestamp", "app_version", "source", "_diagnostics"} | set(
    TYPE_REGISTRY.keys()
)


class HealthPayload(BaseModel):
    """Top-level health payload envelope. Type keys (steps, sleep, ...) are extra."""

    timestamp: str
    app_version: str
    source: str

    model_config = {"extra": "allow"}

    @model_validator(mode="after")
    def _reject_forbidden_keys(self) -> "HealthPayload":
        """Raise if any key is outside envelope + type registry + _diagnostics."""
        all_keys = set(self.model_dump().keys())
        forbidden = all_keys - _ALLOWED_HEALTH_KEYS
        if forbidden:
            raise ValueError(f"Forbidden keys in health payload: {sorted(forbidden)}")
        return self


class ScreenTimePayload(BaseModel):
    """Top-level screen-time payload envelope. `device` is required here only."""

    timestamp: str
    app_version: str
    device: str
    source: str


@dataclass
class Event:
    """One normalized record ready for DB insertion."""

    data_type: str
    start_utc: str
    end_utc: str
    value_num: float | None
    value_json: str
    stages: list[dict] | None = None
