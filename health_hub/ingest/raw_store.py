"""L0 raw payload storage: append-only write to ``raw_payloads``.

Called before any validation so the raw bytes are always preserved. The
caller may later UPDATE the row with ``parse_error`` or envelope metadata
(``app_version``, ``device``, ``payload_timestamp``).
"""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite


async def store_raw_payload(
    db: aiosqlite.Connection,
    source: str,
    raw_body: bytes,
    signature_valid: bool,
    app_version: str | None = None,
    device: str | None = None,
    payload_timestamp: str | None = None,
    parse_error: str | None = None,
) -> int:
    """Insert a row into ``raw_payloads``. Returns the new ``payload_id``.

    ``received_at`` is UTC now. ``byte_size`` is ``len(raw_body)``.
    ``body_json`` decodes the body as UTF-8 with a latin-1 fallback so even
    binary payloads are preserved losslessly.
    """
    received_at = datetime.now(timezone.utc).isoformat()
    byte_size = len(raw_body)
    body_json = _decode_body(raw_body)

    cursor = await db.execute(
        """INSERT INTO raw_payloads
           (received_at, source, app_version, device, payload_timestamp,
            signature_valid, byte_size, body_json, parse_error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            received_at,
            source,
            app_version,
            device,
            payload_timestamp,
            1 if signature_valid else 0,
            byte_size,
            body_json,
            parse_error,
        ),
    )
    await db.commit()
    return cursor.lastrowid


def _decode_body(raw_body: bytes) -> str:
    """Decode bytes as UTF-8, falling back to latin-1 (never raises)."""
    try:
        return raw_body.decode("utf-8")
    except UnicodeDecodeError:
        return raw_body.decode("latin-1")
