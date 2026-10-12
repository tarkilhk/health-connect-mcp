"""Verify production MCP routing and fork registry metadata."""
import json
from pathlib import Path

import httpx
import pytest

from health_hub import __version__
from health_hub.app import create_app


def test_production_app_exposes_one_mcp_route():
    app = create_app()
    assert [r.path for r in app.routes if r.path.startswith("/mcp")] == ["/mcp"]


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
async def test_production_mcp_route_requires_authentication(method):
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, "/mcp")
    assert response.status_code == 401


@pytest.mark.parametrize("authenticated", [False, True])
async def test_removed_alias_is_not_a_route(authenticated):
    app = create_app()
    headers = {"Authorization": f"Bearer {app.state.settings.mcp_token}"} if authenticated else {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/mcp-health", headers=headers)
    assert response.status_code == 404


def test_registry_metadata_matches_fork_and_endpoint():
    registry = json.loads((Path(__file__).parents[1] / ".mcp/server.json").read_text())
    assert registry["name"] == "io.github.tarkilhk/health-connect-mcp"
    assert registry["version"] == __version__
    assert registry["repository"]["url"] == "https://github.com/tarkilhk/health-connect-mcp"
    assert registry["remotes"][0]["url"] == "https://{host}/mcp"
