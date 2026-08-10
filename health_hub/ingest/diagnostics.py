"""L0b sync diagnostics storage: one row per data_type into ``sync_diagnostics``.

The ``_diagnostics`` block in a health payload maps data_type -> info dict.
Each info dict may carry: permission_granted, page_count, raw_record_count,
filtered_record_count, min_time, max_time, last_sync, error.
"""
from __future__ import annotations

from typing import Any

import aiosqlite


async def store_diagnostics(
    db: aiosqlite.Connection,
    payload_id: int,
    diagnostics: dict,
) -> None:
    """Parse ``_diagnostics`` dict and insert one row per data_type.

    Non-dict values are skipped. Uses ``INSERT OR REPLACE`` keyed on
    ``(payload_id, data_type)`` so re-ingest of the same payload updates
    rather than conflicts.

    Does NOT commit — caller manages the transaction.
    """
    if not isinstance(diagnostics, dict):
        return

    for data_type, info in diagnostics.items():
        if not isinstance(info, dict):
            continue
        await db.execute(
            """INSERT OR REPLACE INTO sync_diagnostics
               (payload_id, data_type, permission_granted, page_count,
                raw_record_count, filtered_record_count, min_time, max_time,
                last_sync, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                payload_id,
                data_type,
                _bool_int(info.get("permission_granted")),
                info.get("page_count"),
                info.get("raw_record_count"),
                info.get("filtered_record_count"),
                info.get("min_time"),
                info.get("max_time"),
                info.get("last_sync"),
                info.get("error"),
            ),
        )


def _bool_int(value: Any) -> int | None:
    """Coerce a bool/tristate to sqlite INTEGER: True=1, False=0, None=None."""
    if value is None:
        return None
    return 1 if value else 0
