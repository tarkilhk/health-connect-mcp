"""Pure normalization: dict payload -> (events, sleep_stages, errors).

No I/O. Drives off TYPE_REGISTRY; the only type-specific branch is sleep
(kind=SESSION). INTERVAL records carry start_time/end_time; INSTANT records
carry a single time. Sleep carries session_end_time + duration_seconds.

Partial acceptance (SPEC §8.2): malformed records are skipped and reported
in the errors list; valid records survive. Each error dict has keys:
type (str), index (int), error (str).
"""
import json
from datetime import datetime, timedelta, timezone

from health_hub.contract.kinds import RecordKind
from health_hub.contract.payload import Event
from health_hub.contract.registry import TYPE_REGISTRY, TypeSpec
from health_hub.ingest.identity import event_id


def normalize_health_payload(
    payload: dict,
) -> tuple[list[Event], list[dict], list[dict]]:
    """Convert a raw health payload dict into (events, sleep_stages, errors)."""
    events: list[Event] = []
    stages: list[dict] = []
    errors: list[dict] = []

    for spec in TYPE_REGISTRY.values():
        records = _get_records(payload, spec.json_key)
        for idx, rec in enumerate(records):
            try:
                if not isinstance(rec, dict):
                    raise TypeError(f"record is {type(rec).__name__}, expected dict")
                if spec.kind == RecordKind.INTERVAL:
                    events.append(_normalize_interval(spec, rec))
                elif spec.kind == RecordKind.INSTANT:
                    events.append(_normalize_instant(spec, rec))
                else:  # SESSION
                    evt, sess_stages, stage_errors = _normalize_sleep(spec, rec, idx)
                    events.append(evt)
                    stages.extend(sess_stages)
                    errors.extend(stage_errors)
            except Exception as exc:  # noqa: BLE001 - collect per-record errors, do not abort the batch
                errors.append(
                    {"type": spec.json_key, "index": idx, "error": str(exc)}
                )

    return events, stages, errors


def normalize_hc_webhook_payload(payload: dict) -> tuple[list[Event], list[dict], list[dict]]:
    """Convert HC Webhook format to internal format, then normalize.

    Transforms:
    - heart_rate_variability: rmssd_millis → heart_rate_variability_millis
    - menstruation_flow: int flow → string (0=unknown, 1=light, 2=medium, 3=heavy)
    """
    from copy import deepcopy
    normalized = deepcopy(payload)

    # Map HRV field name
    hrv = normalized.get("heart_rate_variability")
    if hrv:
        for rec in hrv:
            if not isinstance(rec, dict):
                continue  # malformed — delegate handles it
            if "rmssd_millis" in rec:
                rec["heart_rate_variability_millis"] = rec.pop("rmssd_millis")

    # Map menstruation_flow int → string
    flow_map = {0: "unknown", 1: "light", 2: "medium", 3: "heavy"}
    mf = normalized.get("menstruation_flow")
    if mf:
        for rec in mf:
            if not isinstance(rec, dict):
                continue
            flow_val = rec.get("flow")
            if isinstance(flow_val, int) and not isinstance(flow_val, bool):
                rec["flow"] = flow_map.get(flow_val, "unknown")

    return normalize_health_payload(normalized)


def _get_records(payload: dict, key: str) -> list:
    """Return payload[key] if it is a list, else []."""
    raw = payload.get(key)
    if not isinstance(raw, list):
        return []
    return raw


def _canonical_json(rec: dict) -> str:
    """Canonical JSON for value_json: sorted keys, compact separators, UTF-8."""
    return json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _value_num(spec: TypeSpec, rec: dict) -> float | None:
    """Pull the numeric value if spec declares a value_field present on rec."""
    if spec.value_field and spec.value_field in rec:
        return rec[spec.value_field]
    return None


def _normalize_interval(spec: TypeSpec, rec: dict) -> Event:
    """INTERVAL: bounded by start_time / end_time."""
    return Event(
        data_type=spec.data_type,
        start_utc=rec["start_time"],
        end_utc=rec["end_time"],
        value_num=_value_num(spec, rec),
        value_json=_canonical_json(rec),
        stages=None,
    )


def _normalize_instant(spec: TypeSpec, rec: dict) -> Event:
    """INSTANT: single time, start == end."""
    instant = rec["time"]
    return Event(
        data_type=spec.data_type,
        start_utc=instant,
        end_utc=instant,
        value_num=_value_num(spec, rec),
        value_json=_canonical_json(rec),
        stages=None,
    )


def _normalize_sleep(
    spec: TypeSpec, rec: dict, session_index: int
) -> tuple[Event, list[dict], list[dict]]:
    """SESSION: end - duration = start. Build child stage dicts linked by id.

    Returns ``(event, stages, stage_errors)``. Malformed stages are reported
    in ``stage_errors`` rather than silently skipped; the session event is
    still created and surviving stages are returned. Each stage error dict
    has keys: type, index, stage_index, error.
    """
    end_utc = rec["session_end_time"]
    duration = rec["duration_seconds"]
    end_dt = _parse_iso(end_utc)
    start_dt = end_dt - timedelta(seconds=duration)
    start_utc = _format_iso(start_dt)
    value_json = _canonical_json(rec)

    evt = Event(
        data_type=spec.data_type,
        start_utc=start_utc,
        end_utc=end_utc,
        value_num=None,
        value_json=value_json,
        stages=None,
    )

    sess_id = event_id(spec.data_type, start_utc, end_utc, value_json)
    sess_stages: list[dict] = []
    stage_errors: list[dict] = []
    for stage_idx, stage in enumerate(rec.get("stages") or []):
        try:
            if not isinstance(stage, dict):
                raise TypeError(
                    f"stage is {type(stage).__name__}, expected dict"
                )
            sess_stages.append(
                {
                    "session_event_id": sess_id,
                    "stage": stage["stage"],
                    "start_utc": stage["start_time"],
                    "end_utc": stage["end_time"],
                    "seconds": stage["duration_seconds"],
                }
            )
        except (KeyError, TypeError) as exc:
            stage_errors.append(
                {
                    "type": spec.json_key,
                    "index": session_index,
                    "stage_index": stage_idx,
                    "error": str(exc),
                }
            )
    return evt, sess_stages, stage_errors


def _parse_iso(s: str) -> datetime:
    """Parse ISO8601 with optional trailing Z into a timezone-aware datetime."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _format_iso(dt: datetime) -> str:
    """Format a datetime as ISO8601 UTC with a trailing Z.

    Preserves microseconds when present.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    if dt.microsecond:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
