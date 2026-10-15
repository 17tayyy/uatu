"""Pure ASGI middleware: request id on every log line and response, one log line per request."""

import re
import time
from uuid import uuid4

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = structlog.get_logger(component="web")

REQUEST_ID = re.compile(r"^[A-Za-z0-9\-]{1,64}$")
SILENT_PATHS = {"/health/live", "/health/ready"}


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get("x-request-id", "")
        request_id = incoming if REQUEST_ID.match(incoming) else uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        status_code = 500
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message).append("X-Request-ID", request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if scope["path"].startswith("/api/") or status_code >= 400:  # not every static file
                log.info(
                    "http.request",
                    method=scope["method"],
                    path=scope["path"],  # never the query string
                    status_code=status_code,
                    duration_ms=round((time.perf_counter() - start) * 1000, 1),
                )


# The page is one bundle from here (frontend/, built with Vite): MapLibre's worker included, as a module
# script of its own. The optional satellite layer's tiles come from MapTiler or EOX. The API docs (Swagger UI,
# ReDoc) load from jsdelivr and need inline scripts, so they go without the policy.
IMAGERY = "https://api.maptiler.com https://tiles.maps.eox.at"
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    f"img-src 'self' data: blob: {IMAGERY}; worker-src 'self' blob:; child-src 'self' blob:; "
    f"connect-src 'self' {IMAGERY}; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)
NO_POLICY_PATHS = {"/docs", "/docs/oauth2-redirect", "/redoc"}


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "no-referrer"
                if scope["path"] not in NO_POLICY_PATHS:
                    headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
            await send(message)

        await self.app(scope, receive, send_wrapper)
