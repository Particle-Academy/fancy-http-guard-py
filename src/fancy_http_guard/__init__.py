"""fancy-http-guard: request guards checked before the body is parsed, and one
error envelope, for Starlette, FastAPI and any ASGI app.

    from fastapi import FastAPI
    import fancy_http_guard

    app = FastAPI()
    fancy_http_guard.install(app)                      # defaults: 1 MiB, depth 32, ...

    @app.post("/imports")
    @fancy_http_guard.guard(profile="upload")          # 10 MiB for this route
    async def imports(request: Request): ...

Contract: fancy-conformance suites/http/SPEC.md (CC-fancy-0031).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .envelope import Rendered, render
from .errors import HttpError, ValidationFailed
from .middleware import GuardMiddleware, GuardState, guard
from .settings import DEFAULTS, MESSAGE_LENGTH_BOUND, PROFILES, settings, workbook_limits
from .workbook import read_workbook

__version__ = "0.2.0"

__all__ = [
    "DEFAULTS",
    "MESSAGE_LENGTH_BOUND",
    "PROFILES",
    "GuardMiddleware",
    "GuardState",
    "HttpError",
    "Rendered",
    "ValidationFailed",
    "__version__",
    "guard",
    "install",
    "read_workbook",
    "render",
    "settings",
    "workbook_limits",
]


def install(
    app: Any,
    *,
    limits: Mapping[str, Any] | None = None,
    profiles: Mapping[str, Mapping[str, Any]] | None = None,
    routes: Mapping[str, Mapping[str, Any] | str] | None = None,
    request_id_header: str = "x-request-id",
    trust_incoming_request_id: bool = False,
) -> None:
    """Install on a Starlette or FastAPI app: the guard middleware, plus
    exception handlers so the framework's own HTTP and validation errors become
    the envelope. Crashes escape the framework's handlers and are rendered by
    the middleware, as a fixed 500, in debug mode too."""
    from starlette.requests import Request
    from starlette.responses import Response

    rh = request_id_header.lower()

    async def handler(request: Request, exc: Exception) -> Response:
        state = request.scope.get("state", {})
        rid = state.get("request_id") or "unknown"
        guarded = state.get("fancy_guard")
        mx = guarded.limits["maxMessageLength"] if isinstance(guarded, GuardState) else DEFAULTS["maxMessageLength"]
        r = render(exc, request_id=rid, request_id_header=rh, max_message_length=mx)
        return Response(r.payload(), status_code=r.status, headers=dict(r.headers))

    from starlette.exceptions import HTTPException

    app.add_exception_handler(HTTPException, handler)
    app.add_exception_handler(HttpError, handler)
    app.add_exception_handler(ValidationFailed, handler)
    try:
        from fastapi.exceptions import RequestValidationError

        app.add_exception_handler(RequestValidationError, handler)
    except ImportError:
        pass
    try:
        from holy_sheet.exceptions import ArchiveRefused

        app.add_exception_handler(ArchiveRefused, handler)
    except ImportError:
        pass
    app.add_middleware(
        GuardMiddleware,
        limits=limits,
        profiles=profiles,
        routes=routes,
        request_id_header=request_id_header,
        trust_incoming_request_id=trust_incoming_request_id,
    )
