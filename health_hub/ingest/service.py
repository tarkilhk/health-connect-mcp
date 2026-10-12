"""Shared ingestion lifecycle: preserve raw input, validate, then persist."""
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import aiosqlite
from pydantic import BaseModel, ValidationError

from health_hub.config import Settings
from health_hub.db.store import Database
from health_hub.ingest.raw_store import (
    enrich_raw_payload,
    mark_parse_error,
    store_raw_payload,
)

log = logging.getLogger(__name__)
Persist = Callable[[aiosqlite.Connection, dict, int, Settings], Awaitable[dict]]


@dataclass(frozen=True)
class PayloadFormat:
    source: str
    envelope: type[BaseModel]
    persist: Persist


@dataclass(frozen=True)
class IngestResult:
    status: int
    body: dict


class IngestService:
    def __init__(self, database: Database, settings: Settings):
        self._database = database
        self._settings = settings

    async def receive(self, format: PayloadFormat, body: bytes, signature_valid: bool) -> IngestResult:
        async with self._database.write() as db:
            payload_id = await store_raw_payload(db, format.source, body, signature_valid)
            if not signature_valid:
                return IngestResult(401, {"error": "invalid_signature", "payload_id": payload_id})
            payload = await _decode_payload(db, payload_id, body, format.envelope)
            if isinstance(payload, IngestResult):
                return payload
            await enrich_raw_payload(db, payload_id, payload)
            try:
                result = await format.persist(db, payload, payload_id, self._settings)
                await db.commit()
            except Exception as exc:
                log.exception("Payload persistence failed (payload_id=%d)", payload_id)
                error = "db_error" if isinstance(exc, aiosqlite.Error) else "internal_error"
                return IngestResult(500, {"error": error, "detail": str(exc), "payload_id": payload_id})
            return IngestResult(200, {"payload_id": payload_id, **result})

    async def echo(self, body: bytes) -> IngestResult:
        try:
            async with self._database.write() as db:
                payload_id = await store_raw_payload(db, "echo", body, signature_valid=True)
        except aiosqlite.Error:
            log.exception("Echo raw payload storage failed")
            return IngestResult(200, {"ok": True, "payload_id": None, "byte_size": len(body),
                                      "warning": "raw payload storage failed"})
        return IngestResult(200, {"ok": True, "payload_id": payload_id, "byte_size": len(body)})


async def _decode_payload(db, payload_id: int, body: bytes, envelope: type[BaseModel]):
    try:
        payload = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return await _reject_payload(db, payload_id, "invalid_json", str(exc))
    if not isinstance(payload, dict):
        return await _reject_payload(db, payload_id, "invalid_json", "expected a JSON object")
    try:
        envelope.model_validate(payload)
    except ValidationError as exc:
        return await _reject_payload(db, payload_id, "invalid_envelope", str(exc))
    return payload


async def _reject_payload(db, payload_id: int, error: str, detail: str) -> IngestResult:
    await mark_parse_error(db, payload_id, detail)
    result = {"error": error, "payload_id": payload_id}
    if error == "invalid_envelope":
        result["detail"] = detail
    return IngestResult(400, result)
