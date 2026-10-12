"""Enforce the current MCP HTTP protocol without legacy negotiation."""
import pytest

from health_hub.mcp_server.transport import PROTOCOL_VERSION
from tests.mcp_client import _MCP_HEADERS, _mcp_call


def _request(method="tools/list", version=PROTOCOL_VERSION):
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": {"_meta": {
        "io.modelcontextprotocol/protocolVersion": version,
        "io.modelcontextprotocol/clientCapabilities": {},
    }}}


async def test_modern_discovery_and_tools_need_no_session(client):
    discovery = await _mcp_call(client, "server/discover")
    assert discovery["result"]["supportedVersions"] == [PROTOCOL_VERSION]
    tools = await _mcp_call(client, "tools/list")
    assert len(tools["result"]["tools"]) == 7


@pytest.mark.parametrize("version", [None, "2024-11-05", "2025-06-18", "2025-11-25", "2030-01-01"])
async def test_only_current_protocol_header_is_accepted(client, version):
    headers = {k: v for k, v in _MCP_HEADERS.items() if k != "MCP-Protocol-Version"}
    headers["Mcp-Method"] = "tools/list"
    if version is not None:
        headers["MCP-Protocol-Version"] = version
    response = await client.post("/mcp", headers=headers, json=_request())
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == (-32020 if version is None else -32022)
    assert error["data"]["supportedVersions"] == [PROTOCOL_VERSION]


async def test_initialization_handshake_is_rejected_on_current_protocol(client):
    response = await client.post("/mcp", headers={**_MCP_HEADERS, "Mcp-Method": "initialize"},
                                 json=_request("initialize"))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == -32601


@pytest.mark.parametrize("method", ["GET", "DELETE"])
async def test_authenticated_transport_accepts_post_only(client, method):
    response = await client.request(method, "/mcp", headers=_MCP_HEADERS)
    assert response.status_code == 405
    assert response.headers["allow"] == "POST"


@pytest.mark.parametrize("changes", [{"Mcp-Method": "tools/call"}, {"Mcp-Method": None}])
async def test_method_header_must_match_message(client, changes):
    headers = {**_MCP_HEADERS, "Mcp-Method": "tools/list", **changes}
    headers = {k: v for k, v in headers.items() if v is not None}
    response = await client.post("/mcp", headers=headers, json=_request())
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32020


async def test_protocol_header_must_match_message_metadata(client):
    response = await client.post("/mcp", headers={**_MCP_HEADERS, "Mcp-Method": "tools/list"},
                                 json=_request(version="2025-11-25"))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32020
