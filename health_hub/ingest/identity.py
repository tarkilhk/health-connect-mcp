"""Content-hash identity for normalized events.

Two events with the same (data_type, start_utc, end_utc, value_json) produce
the same id — this is what makes ingest idempotent and enables dedup on
re-delivery. The value_json input is canonicalized internally via
json.loads + sorted re-dump, so key order in the caller's string does not
affect the hash.
"""
import hashlib
import json


def event_id(data_type: str, start_utc: str, end_utc: str, value_json: str) -> str:
    """SHA-256 content hash. Same record = same hash = idempotent dedup."""
    canonical_value = _canonicalize(value_json)
    canonical = f"{data_type}|{start_utc}|{end_utc}|{canonical_value}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonicalize(value_json: str) -> str:
    """Re-dump value_json with sorted keys and compact separators.

    If the input is not valid JSON, return it unchanged (defensive fallback).
    """
    try:
        parsed = json.loads(value_json)
    except (json.JSONDecodeError, TypeError):
        return value_json
    return json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
