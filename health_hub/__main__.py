"""Entry point: run Health Hub with uvicorn (single worker, per SPEC 9.3.3)."""
from __future__ import annotations

import argparse
import asyncio

import uvicorn

from health_hub.config import load_settings
from health_hub.rebuild_rollups import rebuild_rollups


def main() -> None:
    parser = argparse.ArgumentParser(description="Health Connect MCP server")
    parser.add_argument("--rebuild-rollups", action="store_true",
                        help="Rebuild the derived summary cache from stored events, then exit")
    args = parser.parse_args()
    settings = load_settings()
    if args.rebuild_rollups:
        count = asyncio.run(rebuild_rollups(settings))
        print(f"Rebuilt daily rollups for {count} local dates.")
        return
    uvicorn.run(
        "health_hub.app:app",
        host="0.0.0.0",
        port=settings.port,
        workers=1,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
