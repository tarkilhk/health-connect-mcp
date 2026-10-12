"""Concurrent HTTP requests cannot commit or expose another request's work."""
import asyncio
import json

import pytest
from starlette.requests import Request

from health_hub.ingest import persist
from tests.mcp_client import _auth_headers, _sign


async def test_echo_cannot_commit_failed_health_write(client, db, monkeypatch):
    entered, release, echo_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_upsert, original_body = persist.upsert_events, Request.body

    async def paused_upsert(*args, **kwargs):
        result = await original_upsert(*args, **kwargs)
        entered.set()
        await release.wait()
        return result

    async def observed_body(request):
        result = await original_body(request)
        if request.url.path == "/ingest/echo":
            echo_entered.set()
        return result

    async def failed_rollup(*args, **kwargs):
        raise ValueError("synthetic rollup failure")

    monkeypatch.setattr(persist, "upsert_events", paused_upsert)
    monkeypatch.setattr(persist, "recalc_rollups_for_dates", failed_rollup)
    monkeypatch.setattr(Request, "body", observed_body)
    body = json.dumps({"timestamp": "2026-08-09T12:00:00Z", "app_version": "synthetic",
                       "source": "health_connect", "heart_rate": [
                           {"time": "2026-08-09T12:00:00Z", "bpm": 60},
                       ]}).encode()
    health = asyncio.create_task(client.post("/ingest/health", content=body,
                                             headers=_auth_headers({"X-Signature": _sign(body)})))
    echo = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        echo = asyncio.create_task(client.post("/ingest/echo", content=b"synthetic echo"))
        await asyncio.wait_for(echo_entered.wait(), timeout=5)
        # Echo has received its body while the other request owns the writer.
        assert not echo.done()
    finally:
        release.set()
        health_response = await health
        if echo is not None:
            echo_response = await echo
    assert health_response.status_code == 500
    assert echo_response.status_code == 200
    assert (await (await db.execute("SELECT COUNT(*) FROM events")).fetchone())[0] == 0
    assert (await (await db.execute("SELECT COUNT(*) FROM daily_rollups")).fetchone())[0] == 0
    assert (await (await db.execute("SELECT COUNT(*) FROM raw_payloads")).fetchone())[0] == 2


async def test_cancelled_ingestion_rolls_back_and_releases_writer(client, db, monkeypatch):
    entered = asyncio.Event()
    original = persist.upsert_events

    async def paused_upsert(*args, **kwargs):
        await original(*args, **kwargs)
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(persist, "upsert_events", paused_upsert)
    body = json.dumps({"timestamp": "2026-08-09T12:00:00Z", "app_version": "synthetic",
                       "source": "health_connect", "heart_rate": [
                           {"time": "2026-08-09T12:00:00Z", "bpm": 60},
                       ]}).encode()
    task = asyncio.create_task(client.post("/ingest/health", content=body,
                                          headers=_auth_headers({"X-Signature": _sign(body)})))
    await asyncio.wait_for(entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    response = await asyncio.wait_for(client.post("/ingest/echo", content=b"synthetic echo"), timeout=5)
    assert response.status_code == 200
    assert (await (await db.execute("SELECT COUNT(*) FROM events")).fetchone())[0] == 0
    assert (await (await db.execute("SELECT COUNT(*) FROM raw_payloads")).fetchone())[0] == 2


async def test_repeated_cancellation_cannot_release_writer_before_rollback(client, db, monkeypatch):
    writing, rolling_back, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_upsert, original_rollback = persist.upsert_events, db.rollback

    async def paused_upsert(*args, **kwargs):
        await original_upsert(*args, **kwargs)
        writing.set()
        await asyncio.Event().wait()

    async def paused_rollback():
        rolling_back.set()
        await release.wait()
        await original_rollback()

    monkeypatch.setattr(persist, "upsert_events", paused_upsert)
    monkeypatch.setattr(db, "rollback", paused_rollback)
    body = json.dumps({"timestamp": "2026-08-09T12:00:00Z", "app_version": "synthetic",
                       "source": "health_connect", "weight": [
                           {"time": "2026-08-09T12:00:00Z", "kilograms": 71},
                       ]}).encode()
    task = asyncio.create_task(client.post("/ingest/health", content=body,
                                          headers=_auth_headers({"X-Signature": _sign(body)})))
    echo = None
    try:
        await asyncio.wait_for(writing.wait(), timeout=5)
        task.cancel()
        await asyncio.wait_for(rolling_back.wait(), timeout=5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        echo = asyncio.create_task(client.post("/ingest/echo", content=b"synthetic echo"))
        await asyncio.sleep(0)
        assert not echo.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        if echo is not None:
            assert (await asyncio.wait_for(echo, timeout=5)).status_code == 200
    assert (await (await db.execute("SELECT COUNT(*) FROM events")).fetchone())[0] == 0
