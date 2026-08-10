"""Tests for health_hub.contract.payload — M2: forbidden key rejection."""
import pytest
from pydantic import ValidationError

from health_hub.contract.payload import HealthPayload, ScreenTimePayload


def test_health_payload_accepts_known_type_keys():
    p = HealthPayload(timestamp="t", app_version="v", source="s", steps=[{"count": 1}])
    assert p.steps == [{"count": 1}]


def test_health_payload_accepts_diagnostics():
    p = HealthPayload(timestamp="t", app_version="v", source="s", _diagnostics={"steps": {}})
    assert p.model_dump()["_diagnostics"] == {"steps": {}}


def test_health_payload_rejects_device():
    with pytest.raises(ValidationError):
        HealthPayload(timestamp="t", app_version="v", source="s", device="phone")


def test_health_payload_rejects_unknown_key():
    with pytest.raises(ValidationError):
        HealthPayload(timestamp="t", app_version="v", source="s", unknown_key="x")


def test_screentime_payload_requires_device():
    p = ScreenTimePayload(timestamp="t", app_version="v", device="phone", source="s")
    assert p.device == "phone"
