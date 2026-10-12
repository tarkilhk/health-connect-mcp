"""Authenticated MCP discovery and round trips for all 34 stored types."""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from health_hub.contract.registry import TYPE_REGISTRY
from tests.mcp_client import (
    _call_tool,
    _ingest,
    _mcp_call,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _all_types_payload():
    payload = json.loads((FIXTURES / "payload_health_full.json").read_text())
    payload.update({
        "skin_temperature": [{"time": "2026-08-09T12:00:00Z", "delta_celsius": 0.5,
                              "deltas": [{"time": "2026-08-09T12:00:00Z", "delta_celsius": 0.5}]}],
        "basal_metabolic_rate": [{"time": "2026-08-09T12:00:00Z", "watts": 80.5}],
        "vo2_max": [{"time": "2026-08-09T12:00:00Z", "ml_per_kg_per_min": 45.2}],
        # The remaining types have no repository field contract. These are
        # synthetic opaque values to verify preservation, not asserted HC fields.
        "intermenstrual_bleeding": [{"time": "2026-08-09T12:00:00Z"}],
        "basal_body_temperature": [{"time": "2026-08-09T12:00:00Z", "sample": 36.5}],
        "cervical_mucus": [{"time": "2026-08-09T12:00:00Z", "sample": "category-a"}],
        "ovulation_test": [{"time": "2026-08-09T12:00:00Z", "sample": "category-b"}],
        "sexual_activity": [{"time": "2026-08-09T12:00:00Z", "sample": False}],
        "test": [{"time": "2026-08-09T12:00:00Z", "sample": {"nested": [0, False, None]}}],
    })
    assert set(TYPE_REGISTRY) <= payload.keys()
    return payload


async def test_catalog_and_protocol_schemas_cover_all_34(client):
    tools = (await _mcp_call(client, "tools/list", {}))["result"]["tools"]
    query_tool = next(t for t in tools if t["name"] == "health_records")
    assert set(query_tool["inputSchema"]["properties"]["data_type"]["enum"]) == set(TYPE_REGISTRY)
    record_schema = query_tool["outputSchema"]["properties"]["records"]["items"]
    assert set(record_schema["discriminator"]["mapping"]) == set(TYPE_REGISTRY)
    catalog = await _call_tool(client, "list_data_types", {})
    info = {t["data_type"]: t for t in catalog["data_types"]}
    assert set(info) == set(TYPE_REGISTRY)
    assert info["blood_pressure"]["value_schema"]["properties"]["diastolic"]["unit"] == "mmHg"
    assert info["nutrition"]["value_schema"]["properties"]["protein_grams"]["unit"] == "g"
    assert info["exercise"]["value_schema"]["properties"]["duration_seconds"]["unit"] == "s"
    assert info["test"]["caveats"]


async def test_all_types_round_trip_through_authenticated_mcp(client, db):
    payload = _all_types_payload()
    first = await _ingest(client, payload)
    duplicate = await _ingest(client, payload)
    assert duplicate["stored"] == 0
    assert duplicate["duplicates"] == first["stored"]
    tools = (await _mcp_call(client, "tools/list", {}))["result"]["tools"]
    schema = next(t for t in tools if t["name"] == "health_records")["outputSchema"]
    validator = Draft202012Validator(schema)
    for key, spec in TYPE_REGISTRY.items():
        result = await _call_tool(client, "health_records", {
            "data_type": key, "date_from": "2026-01-01", "date_to": "2026-12-31",
        })
        validator.validate(result)
        assert len(result["records"]) == len(payload[key]), key
        assert result["next_cursor"] is None
        assert result["caveats"]
        for record in result["records"]:
            assert record["data_type"] == key
            assert record["kind"] == spec.kind.value
            # Nullable modeled fields may be emitted, but every stored field
            # (including paired/category/nested values) must survive unchanged.
            assert any(all(record["values"][k] == v for k, v in original.items())
                       for original in payload[key]), key


@pytest.mark.parametrize("authorization", [None, "Bearer wrong", "test-mcp-token"])
@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
async def test_mcp_transport_requires_bearer_token(client, authorization, method):
    headers = {} if authorization is None else {"Authorization": authorization}
    response = await client.request(method, "/mcp", headers=headers)
    assert response.status_code == 401


async def test_new_tools_reject_wrong_token(client):
    for name, args in [("list_data_types", {}), ("health_records", {
        "data_type": "blood_pressure", "date_from": "2026-08-09", "date_to": "2026-08-09",
    })]:
        response = await client.post("/mcp", headers={
            "Authorization": "Bearer wrong",
            "Accept": "application/json, text/event-stream",
        }, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": name, "arguments": args}})
        assert response.status_code == 401
