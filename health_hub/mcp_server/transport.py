"""HTTP boundary for the server's single, current MCP protocol revision."""
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

PROTOCOL_VERSION = "2026-07-28"


class MCPProtocolMiddleware:
    """Reject old protocol requests before the SDK's handshake-era transport.

    Authentication must wrap this middleware. The SDK validates the current
    protocol's message envelope and mirrored request headers after this gate.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] != "/mcp":
            await self._app(scope, receive, send)
            return
        if scope["method"] != "POST":
            await Response(status_code=405, headers={"Allow": "POST"})(scope, receive, send)
            return
        version = next((v for k, v in scope["headers"] if k == b"mcp-protocol-version"), None)
        if version != PROTOCOL_VERSION.encode("ascii"):
            response = JSONResponse({
                "jsonrpc": "2.0", "id": None,
                "error": {
                    "code": -32020 if version is None else -32022,
                    "message": ("Missing MCP-Protocol-Version header." if version is None else
                                f"This server requires MCP protocol version {PROTOCOL_VERSION}."),
                    "data": {"supportedVersions": [PROTOCOL_VERSION]},
                },
            }, status_code=400)
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)
