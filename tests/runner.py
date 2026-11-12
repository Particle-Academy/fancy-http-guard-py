"""Turns a fancy-conformance http row into a call, and the result back into the
row's shape. Shared by the conformance suite and the hostile tests.

The rules a runner owes (suites/http/request-guards manifest):

- "$generated" in an expected value stands for a generated request id: a
  lowercase UUID v4, identical in the header and the body. It is checked HERE,
  then replaced, so the comparison stays exact.
- bodyRead none: not one body byte was pulled. bounded: at most
  maxBodyBytes + 1 bytes plus one transport chunk.
- A refusal never reaches the application, and a pass hands it the identical
  bytes.
"""

# No `from __future__ import annotations` here: the FastAPI endpoints below are
# defined inside functions, and FastAPI resolves a STRING annotation against
# module globals, where a locally imported Request does not exist. It then
# treats the parameter as a required query field, and every row reads 422.
import json
import re
from typing import Any

import fancy_http_guard as g
from asgi import CHUNK, Response, request

GEN = "$generated"
UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

try:  # the Starlette branch is exercised whenever Starlette is installed
    import starlette  # noqa: F401

    HAVE_STARLETTE = True
except ImportError:  # pragma: no cover - the no-dependencies CI job
    HAVE_STARLETTE = False


def body_bytes(body: Any, resolve: Any) -> bytes:
    if isinstance(body, str):
        return body.encode("utf-8")
    if isinstance(body, dict) and "hex" in body:
        return bytes.fromhex(body["hex"])
    return bytes(resolve(body))


def _swap(value: Any, rid: str) -> Any:
    """Replace the generated id with the placeholder, everywhere."""
    text = json.dumps(value)
    return json.loads(text.replace(rid, GEN)) if rid else value


def envelope_result(resp: Response, rid_header: str, inbound: str | None) -> dict[str, Any]:
    rid = resp.header(rid_header)
    assert rid is not None, "every response carries the request id"
    generated = rid != inbound
    if generated:
        assert UUID4.match(rid), f"a generated id is a lowercase UUID v4, got {rid!r}"
    assert resp.header("content-type") == "application/json"
    body = resp.json()
    assert body.get("requestId") == rid, "the body carries the header's id"
    headers = {"content-type": "application/json", rid_header: rid}
    retry = resp.header("retry-after")
    if retry is not None:
        headers["retry-after"] = retry
    out: dict[str, Any] = {"status": resp.status, "headers": headers, "body": body}
    return _swap(out, rid) if generated else out


def guard_app(inp: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    seen: dict[str, Any] = {}

    async def app(scope: Any, receive: Any, send: Any) -> None:
        body = b""
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        seen["body"] = body
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"ok"})

    config = inp.get("config", {})
    mw = g.GuardMiddleware(
        app,
        limits=inp.get("limits", {}),
        request_id_header=config.get("requestIdHeader", "x-request-id"),
        trust_incoming_request_id=config.get("trustIncomingRequestId", False),
    )
    return mw, seen


def run_guard(inp: dict[str, Any], resolve: Any) -> dict[str, Any]:
    data = body_bytes(inp["body"], resolve)
    mw, seen = guard_app(inp)
    headers = dict(inp["headers"])
    rid_header = inp.get("config", {}).get("requestIdHeader", "x-request-id").lower()
    inbound = headers.get(rid_header)
    resp = request(mw, headers=headers, body=data)
    if resp.raised is not None:
        raise resp.raised
    if resp.status == 200 and "body" in seen:
        assert seen["body"] == data, "a pass hands the application the identical bytes"
        rid = resp.header(rid_header)
        assert rid is not None
        if rid != inbound:
            assert UUID4.match(rid)
            return {"pass": True, "requestId": GEN}
        return {"pass": True, "requestId": rid}
    assert "body" not in seen, "a refusal never reaches the application"
    out = envelope_result(resp, rid_header, inbound)
    if out["body"]["error"] not in ("body_too_large", "bad_request"):
        return out  # bodyRead is reported for the size and length refusals only
    limit = g.settings(inp.get("limits", {}))["maxBodyBytes"]
    if resp.pulled == 0:
        out["bodyRead"] = "none"
    elif resp.pulled <= limit + 1 + CHUNK:
        out["bodyRead"] = "bounded"
    else:
        out["bodyRead"] = "full"
    return out


# ---------------------------------------------------------------------------
# render rows
# ---------------------------------------------------------------------------


def build_exception(t: dict[str, Any], *, starlette_branch: bool) -> BaseException:
    kind = t["kind"]
    if kind == "http":
        if starlette_branch:
            from starlette.exceptions import HTTPException

            return HTTPException(t["status"], detail=t.get("message"), headers=t.get("headers"))
        return g.HttpError(t["status"], t.get("message"), headers=t.get("headers"))
    if kind == "crash":
        return RuntimeError(t["message"])
    if kind == "validation":
        if starlette_branch:
            from fastapi.exceptions import RequestValidationError

            errors = []
            for e in t["errors"]:
                loc: list[Any] = ["body"] + [int(s) if s.isdigit() else s for s in e["field"].split(".")]
                errors.append({"loc": tuple(loc), "msg": e["message"], "type": "value_error"})
            return RequestValidationError(errors)
        return g.ValidationFailed([{"field": e["field"], "message": e["message"]} for e in t["errors"]])
    if kind == "public":
        return g.HttpError(t["status"], t["message"], error=t["error"], public=True, retry_after=t.get("retryAfter"))
    if kind == "workbook":
        from holy_sheet.exceptions import ArchiveRefused, ReadLimitExceeded

        if t["limit"] in ("maxRows", "maxColumns", "maxCells", "maxSharedStrings"):
            return ReadLimitExceeded(t["limit"], t["max"], t["actual"])
        return ArchiveRefused.over_limit(t["limit"], t["max"], t["actual"])
    raise AssertionError(f"unknown kind {kind}")


def render_app(inp: dict[str, Any], *, starlette_branch: bool) -> Any:
    t = inp["throw"]
    config = inp.get("config", {})
    kw = {
        "request_id_header": config.get("requestIdHeader", "x-request-id"),
        "trust_incoming_request_id": config.get("trustIncomingRequestId", False),
    }

    if t["kind"] == "response":
        raw = [(k.lower().encode(), v.encode()) for k, v in t.get("headers", {}).items()]

        async def responder(scope: Any, receive: Any, send: Any) -> None:
            await send({"type": "http.response.start", "status": t["status"], "headers": raw})
            await send({"type": "http.response.body", "body": b"untouched"})

        inner: Any = responder
    else:

        async def raiser(scope: Any, receive: Any, send: Any) -> None:
            raise build_exception(t, starlette_branch=starlette_branch)

        inner = raiser

    if not starlette_branch:
        return g.GuardMiddleware(inner, **kw)

    from fastapi import FastAPI
    from starlette.requests import Request
    from starlette.responses import Response as StarletteResponse

    api = FastAPI()

    if t["kind"] == "response":

        @api.post("/")
        async def returns(request: Request) -> StarletteResponse:
            return StarletteResponse(b"untouched", status_code=t["status"], headers=t.get("headers", {}))

    else:

        @api.post("/")
        async def throws(request: Request) -> None:
            raise build_exception(t, starlette_branch=True)

    g.install(api, **kw)
    return api


def run_render(inp: dict[str, Any], *, starlette_branch: bool) -> dict[str, Any]:
    app = render_app(inp, starlette_branch=starlette_branch)
    headers = dict(inp.get("headers", {}))
    rid_header = inp.get("config", {}).get("requestIdHeader", "x-request-id").lower()
    resp = request(app, headers=headers, body=b"{}" if starlette_branch else b"")
    if resp.raised is not None:
        raise resp.raised
    t = inp["throw"]
    if t["kind"] == "crash":
        everything = resp.body + b"".join(k + v for k, v in resp.headers)
        assert t["message"].encode() not in everything, "a crash's text reached the response"
    if resp.body == b"untouched":
        assert resp.header(rid_header) is not None, "even a passthrough carries the request id"
        return {"passthrough": True, "status": resp.status}
    return envelope_result(resp, rid_header, headers.get(rid_header))


# ---------------------------------------------------------------------------
# workbook rows
# ---------------------------------------------------------------------------


def run_workbook(inp: dict[str, Any], resolve: Any) -> dict[str, Any]:
    data = bytes(resolve(inp["file"]))
    try:
        g.read_workbook(data, inp.get("limits", {}), field=inp["field"])
    except Exception as e:  # the renderer decides
        r = g.render(e, request_id="4f8c2a9e-1b7d-4c3e-9a5f-2d6b8e0c1a73")
        assert r is not None
        body = _swap(r.body, "4f8c2a9e-1b7d-4c3e-9a5f-2d6b8e0c1a73")
        headers = {k.lower(): v for k, v in r.headers}
        return {
            "status": r.status,
            "headers": {"content-type": headers["content-type"], "x-request-id": GEN},
            "body": body,
        }
    return {"pass": True}
