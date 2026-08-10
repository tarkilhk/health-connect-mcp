"""Application settings loaded from environment variables.

Required variables are read with a clear KeyError so the server refuses to
start without them. No personal values live here — everything comes from env.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

_DEFAULT_DB_PATH = "data/healthhub.sqlite3"
_DEFAULT_TIMEZONE = "UTC"
_DEFAULT_LOG_LEVEL = "INFO"
_DEFAULT_PORT = 8000
_DEFAULT_DAY_BOUNDARY = 0
_DEFAULT_HMAC_SECRET = ""

_TOKEN_HINT = "Generate with: openssl rand -hex 32"
_HOSTS_HINT = (
    "Comma-separated public hostnames this server is reached at, "
    "e.g. health.example.com,health.example.com:*"
)


def _required(name: str, hint: str) -> str:
    """Return a required env var or raise KeyError with a helpful message."""
    try:
        value = os.environ[name]
    except KeyError as exc:
        raise KeyError(
            f"Required environment variable {name} is not set. {hint}"
        ) from exc
    if not value:
        raise KeyError(
            f"Required environment variable {name} is empty. {hint}"
        )
    return value


def _optional(name: str, default: str) -> str:
    """Return an optional env var or its default."""
    return os.environ.get(name, default)


def _parse_hosts(raw: str) -> list[str]:
    return [host.strip() for host in raw.split(",") if host.strip()]


@dataclass(frozen=True)
class Settings:
    """Runtime configuration. Construct via :meth:`from_env`."""

    device_token: str
    mcp_token: str
    allowed_hosts: list[str]
    signing_key: str
    database_path: str
    timezone: str
    day_boundary_hour: int
    log_level: str
    port: int

    @classmethod
    def from_env(cls) -> Settings:
        mac_value = _optional("WEBHOOK_HMAC_SECRET", _DEFAULT_HMAC_SECRET)
        return cls(
            device_token=_required("DEVICE_TOKEN", _TOKEN_HINT),
            mcp_token=_required("MCP_TOKEN", _TOKEN_HINT),
            allowed_hosts=_parse_hosts(_required("ALLOWED_HOSTS", _HOSTS_HINT)),
            signing_key=mac_value,
            database_path=_optional("DATABASE_PATH", _DEFAULT_DB_PATH),
            timezone=_optional("TIMEZONE", _DEFAULT_TIMEZONE),
            day_boundary_hour=int(_optional("DAY_BOUNDARY_HOUR", str(_DEFAULT_DAY_BOUNDARY))),
            log_level=_optional("LOG_LEVEL", _DEFAULT_LOG_LEVEL),
            port=int(_optional("PORT", str(_DEFAULT_PORT))),
        )


def load_settings() -> Settings:
    return Settings.from_env()
