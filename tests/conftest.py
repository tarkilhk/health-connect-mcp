"""Required test configuration and shared MCP fixtures."""
from __future__ import annotations

import os

# Provide dummy required env vars so importing config during tests does not fail.
os.environ.setdefault("DEVICE_TOKEN", "test-device-token")
os.environ.setdefault("MCP_TOKEN", "test-mcp-token")
os.environ.setdefault("ALLOWED_HOSTS", "localhost,localhost:*")

pytest_plugins = ["tests.mcp_client"]
