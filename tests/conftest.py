"""Pytest configuration: asyncio mode (see pytest.ini) and in-memory DB fixture."""
from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator

import pytest

# Provide dummy required env vars so importing config during tests does not fail.
os.environ.setdefault("DEVICE_TOKEN", "test-device-token")
os.environ.setdefault("MCP_TOKEN", "test-mcp-token")
os.environ.setdefault("ALLOWED_HOSTS", "localhost,localhost:*")

pytest_plugins = ["tests.mcp_client"]


@pytest.fixture
def memory_db() -> Iterator[sqlite3.Connection]:
    """A fresh in-memory SQLite connection for each test."""
    conn = sqlite3.connect(":memory:")
    try:
        yield conn
    finally:
        conn.close()
