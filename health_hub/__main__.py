"""Entry point: run Health Hub with uvicorn (single worker, per SPEC 9.3.3)."""
from __future__ import annotations

import uvicorn

from health_hub.config import load_settings


def main() -> None:
    settings = load_settings()
    uvicorn.run(
        "health_hub.app:app",
        host="0.0.0.0",
        port=settings.port,
        workers=1,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
