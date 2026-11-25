"""The guard: pure ASGI middleware that checks a request BEFORE anything parses it.

Why middleware and not a FastAPI dependency: FastAPI reads the body
(``await request.body()`` / ``.json()``) BEFORE it resolves dependencies, so a
dependency runs after the parse it was meant to prevent. Only middleware sees
the raw ``receive`` first.

The order (SPEC section 3.2):

1. ``Content-Length``: malformed is a 400; over the route's cap is a 413, and
   ``receive`` is never called.
2. A bounded read: at most ``maxBodyBytes + 1`` bytes are pulled, which catches
   a missing or lying length.
3. JSON bodies (and bodies with no Content-Type, which FastAPI parses as JSON)
   are scanned: depth, members, finite numbers. Then the identical bytes are
   replayed to the application.

Non-JSON bodies (uploads) are not buffered: ``receive`` is wrapped to count,
and crossing the cap answers 413 even if the framework swallowed the signal and
tried to answer something else.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from .envelope import Rendered, envelope, render, validation
from .scan import scan
from .sentences import MALFORMED, body_too_large, status_defaults
from .settings import PROFILES, settings, workbook_limits

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

RID = re.compile(r"^[A-Za-z0-9._:-]{1,128}\Z")
_DIGITS = re.compile(r"^[0-9]+\Z")
MARKER = "__fancy_guard__"


class GuardState:
    """What a handler sees at ``request.state.fancy_guard``."""

    def __init__(self, request_id: str, limits: dict[str, Any]) -> None:
        self.request_id = request_id
        self.limits = limits
        self.workbook_limits = workbook_limits(limits)

    def read_workbook(self, data: bytes, field: str = "file") -> dict[str, Any]:
        from .workbook import read_workbook

        return read_workbook(data, self.limits, field=field)


def guard(profile: str | None = None, **limits: Any) -> Callable[[Any], Any]:
    """Per-route limits on an endpoint: ``@guard(profile="upload")`` or
    ``@guard(maxBodyBytes=8_192)``. Limits are validated here, at import time."""
    settings(limits)  # refuse a bad key now, not on the first request

    def mark(fn: Any) -> Any:
        setattr(fn, MARKER, {"profile": profile, "limits": dict(limits)})
        return fn

    return mark


class _TooLargeError(Exception):
    pass


def _media_type(value: str | None) -> str | None:
    if value is None:
        return None
    return value.split(";", 1)[0].strip().lower()


def _is_json(media: str | None) -> bool:
    return media is None or media == "application/json" or media.endswith("+json")


def _match(router: Any, scope: Scope) -> tuple[Any, str | None]:
    """(endpoint, name) of the route that will serve this request, following
    Mounts. Done once per request; nothing downstream repeats it."""
    for _ in range(16):  # mounts nest; a cycle cannot loop forever
        routes = getattr(router, "routes", None)
        if not routes:
            return None, None
        for route in routes:
            try:
                match, child = route.matches(scope)
            except Exception:  # a route that cannot match this scope is not this one
                continue
            if getattr(match, "name", "") != "FULL":
                continue
            endpoint = getattr(route, "endpoint", None)
            if endpoint is not None:
                return endpoint, getattr(route, "name", None)
            sub = getattr(route, "app", None)
            scope = {**scope, **child}
            router = getattr(sub, "router", None)
            break
        else:
            return None, None
    return None, None


class GuardMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        limits: Mapping[str, Any] | None = None,
        profiles: Mapping[str, Mapping[str, Any]] | None = None,
        routes: Mapping[str, Mapping[str, Any] | str] | None = None,
        request_id_header: str = "x-request-id",
        trust_incoming_request_id: bool = False,
    ) -> None:
        self.app = app
        self.base = settings(limits)
        self.profiles = {name: settings(p, self.base) for name, p in {**PROFILES, **dict(profiles or {})}.items()}
        self.routes: dict[str, dict[str, Any]] = {}
        for name, value in (routes or {}).items():
            self.routes[name] = self._profile(value) if isinstance(value, str) else settings(value, self.base)
        self.rh = request_id_header.lower()
        self.trust = bool(trust_incoming_request_id)

    def _profile(self, name: str) -> dict[str, Any]:
        if name not in self.profiles:
            raise ValueError(f"fancy-http-guard: unknown profile {name!r}; known: {', '.join(self.profiles)}")
        return self.profiles[name]

    def limits_for(self, scope: Scope) -> dict[str, Any]:
        endpoint, name = _match(getattr(scope.get("app"), "router", None), scope)
        marker = getattr(endpoint, MARKER, None) if endpoint is not None else None
        if isinstance(marker, dict):
            base = self._profile(marker["profile"]) if marker["profile"] else self.base
            return settings(marker["limits"], base) if marker["limits"] else base
        if name is not None and name in self.routes:
            return self.routes[name]
        return self.base

    def request_id(self, headers: list[tuple[str, str]]) -> str:
        if self.trust:
            inbound = next((v for k, v in headers if k == self.rh), None)
            if inbound is not None and RID.match(inbound):
                return inbound
        return str(uuid.uuid4())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = [(k.decode("latin-1").lower(), v.decode("latin-1")) for k, v in scope.get("headers", [])]
        rid = self.request_id(headers)
        lim = self.limits_for(scope)
        state = scope.setdefault("state", {})
        state["request_id"] = rid
        state["fancy_guard"] = GuardState(rid, lim)
        cap: int = lim["maxBodyBytes"]

        async def answer(r: Rendered) -> None:
            await send({"type": "http.response.start", "status": r.status, "headers": r.asgi_headers()})
            await send({"type": "http.response.body", "body": r.payload()})

        def too_large(actual: int | None) -> Rendered:
            limit: dict[str, Any] = {"name": "maxBodyBytes", "max": cap}
            if actual is not None:
                limit["actual"] = actual
            return envelope(413, "body_too_large", body_too_large(cap, actual), rid, self.rh, limit=limit)

        # 1. Content-Length, before one body byte is read.
        # An empty value is absent (SPEC 3.2): PHP-FPM sends one on every GET.
        lengths = [v for k, v in headers if k == "content-length" and v != ""]
        if lengths:
            if len(set(lengths)) > 1 or not _DIGITS.match(lengths[0]):
                await answer(envelope(400, "bad_request", status_defaults(400)[1], rid, self.rh))
                return
            if len(lengths[0]) > 15:
                await answer(too_large(None))
                return
            if int(lengths[0]) > cap:
                await answer(too_large(int(lengths[0])))
                return

        media = _media_type(next((v for k, v in headers if k == "content-type"), None))
        downstream: Receive = receive
        overflow = {"hit": False}

        if _is_json(media):
            # 2 + 3. Bounded read, then the scan, then a replay of the same bytes.
            buf = bytearray()
            while True:
                message = await receive()
                if message["type"] != "http.request":
                    return  # the client went away; nobody is listening
                buf += message.get("body", b"")
                if len(buf) > cap:
                    await answer(too_large(None))
                    return
                if not message.get("more_body", False):
                    break
            data = bytes(buf)
            if data:
                refused = self._scan(data, lim, rid)
                if refused is not None:
                    await answer(refused)
                    return
            replayed = {"done": False}

            async def replay() -> Message:
                if not replayed["done"]:
                    replayed["done"] = True
                    return {"type": "http.request", "body": data, "more_body": False}
                return await receive()

            downstream = replay
        else:
            counted = {"n": 0}

            async def counting() -> Message:
                message = await receive()
                if message["type"] == "http.request":
                    counted["n"] += len(message.get("body", b""))
                    if counted["n"] > cap:
                        overflow["hit"] = True
                        raise _TooLargeError
                return message

            downstream = counting

        started = {"app": False, "replaced": False}

        async def guarded_send(message: Message) -> None:
            if started["replaced"]:
                return  # the app's own answer to an overflow is discarded
            if message["type"] == "http.response.start":
                if overflow["hit"]:
                    started["replaced"] = True
                    await answer(too_large(None))
                    return
                started["app"] = True
                kept = [(k, v) for k, v in message.get("headers", []) if k.lower() != self.rh.encode("latin-1")]
                kept.append((self.rh.encode("latin-1"), rid.encode("latin-1")))
                message = {**message, "headers": kept}
            await send(message)

        try:
            await self.app(scope, downstream, guarded_send)
        except _TooLargeError:
            if started["app"]:
                raise
            if not started["replaced"]:
                await answer(too_large(None))
        except Exception as exc:
            if started["app"]:
                raise  # the response has begun; the server closes the connection
            if started["replaced"]:
                return
            if overflow["hit"]:
                await answer(too_large(None))
                return
            await answer(
                render(exc, request_id=rid, request_id_header=self.rh, max_message_length=lim["maxMessageLength"])
            )

    def _scan(self, data: bytes, lim: dict[str, Any], rid: str) -> Rendered | None:
        malformed = envelope(400, "malformed_body", MALFORMED, rid, self.rh)
        if data.startswith(b"\xef\xbb\xbf"):
            return malformed
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return malformed
        result = scan(text, lim)
        if result.fault:
            return malformed
        if result.items:
            return validation(result.items, rid, self.rh)
        return None
