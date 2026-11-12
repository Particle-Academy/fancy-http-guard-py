"""The one error envelope, and the renderer that turns what an app raised into
it (fancy-conformance suites/http/SPEC.md sections 1 and 4).

**Exception text is never echoed.** A crash is a fixed 500 in every
environment, debug included; its detail goes to the log under the request id.
A deliberate 4xx keeps the app's own sentence; a 5xx never does, unless the
app raised ``HttpError(..., public=True)``.

Starlette, FastAPI and holy-sheet are recognised through SOFT imports: none is
a dependency, and each is only looked for once one of its exceptions could
exist.
"""

from __future__ import annotations

import http
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from . import sentences as s
from .errors import HttpError, ValidationFailed

CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}\Z")
_DELTA = re.compile(r"^[0-9]{1,10}\Z")
log = logging.getLogger("fancy_http_guard")

_READ_LIMITS = ("maxRows", "maxColumns", "maxCells", "maxSharedStrings")


@dataclass
class Rendered:
    status: int
    body: dict[str, Any]
    headers: list[tuple[str, str]]

    def payload(self) -> bytes:
        return json.dumps(self.body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    def asgi_headers(self) -> list[tuple[bytes, bytes]]:
        payload = self.payload()
        out = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in self.headers]
        out.append((b"content-length", str(len(payload)).encode()))
        return out


def envelope(
    status: int,
    error: str,
    message: str,
    request_id: str,
    request_id_header: str = "x-request-id",
    *,
    retry_after: int | None = None,
    **extra: Any,
) -> Rendered:
    body: dict[str, Any] = {"error": error, "message": message, **extra}
    headers = [("content-type", "application/json"), (request_id_header, request_id)]
    if retry_after is not None:
        body["retryAfter"] = retry_after
        headers.append(("retry-after", str(retry_after)))
    body["requestId"] = request_id
    return Rendered(status, body, headers)


def validation(items: list[dict[str, Any]], request_id: str, request_id_header: str = "x-request-id") -> Rendered:
    message = items[0]["message"] if len(items) == 1 else f"{len(items)} fields need attention."
    return envelope(422, "validation_failed", message, request_id, request_id_header, errors=items)


def _retry(headers: Mapping[str, str] | None) -> int | None:
    for k, v in (headers or {}).items():
        if k.lower() == "retry-after" and _DELTA.match(v.strip()):
            return int(v.strip())
    return None


def _http(status: int, message: str | None, headers: Mapping[str, str] | None, rid: str, rh: str) -> Rendered:
    code, fallback = s.status_defaults(status)
    retry = _retry(headers)
    if status >= 500:
        return envelope(status, code, s.server_sentence(status, retry, rid), rid, rh, retry_after=retry)
    if status == 429:
        return envelope(429, code, s.rate_limited(retry), rid, rh, retry_after=retry)
    text = s.sanitise(message) if isinstance(message, str) else ""
    if status == 422:
        return validation([{"error": "invalid", "message": text or fallback}], rid, rh)
    return envelope(status, code, text or fallback, rid, rh, retry_after=retry)


def crash(rid: str, rh: str = "x-request-id") -> Rendered:
    return envelope(500, "server_error", s.server_sentence(500, None, rid), rid, rh)


def _soft(module: str, name: str) -> Any:
    try:
        mod = __import__(module, fromlist=[name])
    except ImportError:
        return None
    return getattr(mod, name, None)


def holy_sheet_limit(exc: BaseException) -> dict[str, Any] | None:
    """holy-sheet's limit refusal as ``{name, max, actual}``, or None."""
    refused = _soft("holy_sheet.exceptions", "ArchiveRefused")
    if refused is None or not isinstance(exc, refused):
        return None
    if getattr(exc, "reason", None) not in ("archive_limit", "read_limit"):
        return None
    name, mx, actual = getattr(exc, "limit", None), getattr(exc, "max", None), getattr(exc, "actual", None)
    if not isinstance(name, str) or not isinstance(mx, int) or not isinstance(actual, int):
        return None
    return {"name": name, "max": mx, "actual": actual}


def render(exc: BaseException, *, request_id: str, request_id_header: str = "x-request-id") -> Rendered:
    """What an exception becomes. Anything unrecognised is a crash."""
    rid, rh = request_id, request_id_header

    if isinstance(exc, HttpError):
        if exc.public:
            if not isinstance(exc.error, str) or not CODE.match(exc.error):
                log.error("fancy-http-guard: HttpError(public=True) with a malformed code %r [%s]", exc.error, rid)
                return crash(rid, rh)
            ok = isinstance(exc.retry_after, int) and exc.retry_after >= 0
            retry = exc.retry_after if ok else _retry(exc.headers)
            text = s.sanitise(exc.message or "") or s.status_defaults(exc.status)[1]
            return envelope(exc.status, exc.error, text, rid, rh, retry_after=retry)
        r = _http(exc.status, exc.message, exc.headers, rid, rh)
        if exc.error is not None and CODE.match(exc.error) and exc.status < 500 and r.status != 422:
            r.body["error"] = exc.error
        return r

    if isinstance(exc, ValidationFailed):
        items = []
        for e in exc.errors:
            item: dict[str, Any] = {"error": e.get("error") if CODE.match(str(e.get("error", ""))) else "invalid"}
            item["message"] = s.sanitise(str(e.get("message", ""))) or s.status_defaults(422)[1]
            if isinstance(e.get("field"), str) and e["field"]:
                item["field"] = e["field"]
            items.append(item)
        return validation(items or [{"error": "invalid", "message": s.status_defaults(422)[1]}], rid, rh)

    starlette_http = _soft("starlette.exceptions", "HTTPException")
    if starlette_http is not None and isinstance(exc, starlette_http):
        status = int(exc.status_code)
        detail = exc.detail if isinstance(exc.detail, str) else None
        try:
            if detail == http.HTTPStatus(status).phrase:
                detail = None  # Starlette's default detail: the reason phrase is no message
        except ValueError:
            pass
        return _http(status, detail, getattr(exc, "headers", None), rid, rh)

    request_validation = _soft("fastapi.exceptions", "RequestValidationError")
    if request_validation is not None and isinstance(exc, request_validation):
        items = []
        for e in exc.errors():
            loc = list(e.get("loc", ()))
            if loc and loc[0] in ("body", "query", "path", "header", "cookie"):
                loc = loc[1:]
            item = {"error": "invalid", "message": s.sanitise(str(e.get("msg", ""))) or s.status_defaults(422)[1]}
            path = ".".join(str(p) for p in loc)
            if path:
                item["field"] = path
            items.append(item)
        return validation(items or [{"error": "invalid", "message": s.status_defaults(422)[1]}], rid, rh)

    limit = holy_sheet_limit(exc)
    if limit is not None:
        return envelope(413, "workbook_too_large", s.workbook_sentence(limit), rid, rh, limit=limit)

    log.error("fancy-http-guard: unhandled %s [request %s]", type(exc).__name__, rid, exc_info=exc)
    return crash(rid, rh)
