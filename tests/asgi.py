"""A tiny ASGI driver, so the suite needs no HTTP client.

ASGI is a protocol of three dicts, so driving it directly is a few dozen lines
and exercises exactly the surface a real server would. Unlike a client library,
it can COUNT what the application pulled: the body is offered in fixed chunks,
and `Response.pulled` is how many body bytes `receive()` handed over. That is
how "refused unread" is a measurement rather than a claim.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

CHUNK = 64


@dataclass
class Response:
    status: int = 0
    headers: list[tuple[bytes, bytes]] = field(default_factory=list)
    body: bytes = b""
    pulled: int = 0
    raised: BaseException | None = None

    def header(self, name: str) -> str | None:
        wanted = name.lower().encode()
        for key, value in self.headers:
            if key.lower() == wanted:
                return value.decode("latin-1")
        return None

    def json(self) -> Any:
        return json.loads(self.body)


def request(
    app: Any,
    method: str = "POST",
    path: str = "/",
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    chunk: int = CHUNK,
) -> Response:
    return asyncio.run(_call(app, method, path, headers or {}, body, chunk))


async def _call(app: Any, method: str, path: str, headers: dict[str, str], body: bytes, chunk: int) -> Response:
    path, _, query = path.partition("?")
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()],
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
    }
    response = Response()
    pieces = [body[i : i + chunk] for i in range(0, len(body), chunk)] or [b""]
    state = {"next": 0}

    async def receive() -> dict[str, Any]:
        i = state["next"]
        if i >= len(pieces):
            return {"type": "http.disconnect"}
        state["next"] = i + 1
        response.pulled += len(pieces[i])
        return {"type": "http.request", "body": pieces[i], "more_body": i < len(pieces) - 1}

    out: list[bytes] = []

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            response.status = message["status"]
            response.headers = list(message.get("headers", []))
        elif message["type"] == "http.response.body":
            out.append(message.get("body", b""))

    try:
        await app(scope, receive, send)
    except BaseException as e:  # a test asserts on it
        response.raised = e
    response.body = b"".join(out)
    return response
