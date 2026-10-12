"""Ingestion HTTP transport: authentication and format selection."""
from functools import partial

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from health_hub.api.auth import verify_signature, verify_token
from health_hub.contract.payload import (
    HCWebhookPayload,
    HealthPayload,
    ScreenTimePayload,
)
from health_hub.ingest.normalize import (
    normalize_hc_webhook_payload,
    normalize_health_payload,
)
from health_hub.ingest.persist import persist_health, persist_screen_time
from health_hub.ingest.service import IngestService, PayloadFormat

HEALTH = PayloadFormat("health_connect", HealthPayload,
                       partial(persist_health, normalizer=normalize_health_payload))
HC_WEBHOOK = PayloadFormat("health_connect", HCWebhookPayload,
                           partial(persist_health, normalizer=normalize_hc_webhook_payload))
SCREEN_TIME = PayloadFormat("screen_time", ScreenTimePayload, persist_screen_time)


async def ingest_health(request: Request) -> JSONResponse:
    return await _receive(request, HEALTH)


async def ingest_hc_webhook(request: Request) -> JSONResponse:
    return await _receive(request, HC_WEBHOOK)


async def ingest_screentime(request: Request) -> JSONResponse:
    return await _receive(request, SCREEN_TIME)


async def _receive(request: Request, format: PayloadFormat) -> JSONResponse:
    settings = request.app.state.settings
    body = await request.body()
    if not verify_token(request.headers.get("Authorization"), settings.device_token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    signature_valid = verify_signature(body, request.headers.get("X-Signature"), settings.signing_key)
    service = IngestService(request.app.state.database, settings)
    result = await service.receive(format, body, signature_valid)
    return JSONResponse(result.body, status_code=result.status)


async def ingest_echo(request: Request) -> JSONResponse:
    service = IngestService(request.app.state.database, request.app.state.settings)
    result = await service.echo(await request.body())
    return JSONResponse(result.body, status_code=result.status)


routes: list[Route] = [
    Route("/ingest/health", ingest_health, methods=["POST"]),
    Route("/ingest/hc-webhook", ingest_hc_webhook, methods=["POST"]),
    Route("/ingest/screentime", ingest_screentime, methods=["POST"]),
    Route("/ingest/echo", ingest_echo, methods=["POST"]),
]
