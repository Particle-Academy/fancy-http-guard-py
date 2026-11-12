"""Hostile requests, beyond the shared rows. Each test names a mistake.

Written BEFORE the implementation: every one of these failed with
ModuleNotFoundError first, and the sabotage sweep (tests/sabotage.py) proves
they still fail when the guard is weakened.
"""

# No `from __future__ import annotations`: FastAPI must see the real classes of
# the endpoints defined inside the tests below (see tests/runner.py).
import asyncio
import json
from typing import Any

import pytest

import fancy_http_guard as g
from asgi import request
from runner import HAVE_STARLETTE, UUID4

CANARY = "SQLSTATE[42S02] users@10.0.0.4 /srv/app/.env"


async def echo(scope: Any, receive: Any, send: Any) -> None:
    body = b""
    while True:
        m = await receive()
        if m["type"] != "http.request":
            break
        body += m.get("body", b"")
        if not m.get("more_body"):
            break
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": json.dumps({"len": len(body)}).encode()})


# ---------------------------------------------------------------------------
# the body is not read to refuse it
# ---------------------------------------------------------------------------


def test_a_declared_oversize_body_is_refused_without_calling_receive() -> None:
    called = False

    async def app(scope: Any, receive: Any, send: Any) -> None:
        raise AssertionError("the application must not run")

    async def run() -> int:
        nonlocal called
        status = 0

        async def receive() -> dict[str, Any]:
            nonlocal called
            called = True
            return {"type": "http.request", "body": b"x" * 10, "more_body": False}

        async def send(m: dict[str, Any]) -> None:
            nonlocal status
            if m["type"] == "http.response.start":
                status = m["status"]

        scope = {"type": "http", "method": "POST", "path": "/", "headers": [(b"content-length", b"2000000")]}
        await g.GuardMiddleware(app)(scope, receive, send)
        return status

    assert asyncio.run(run()) == 413
    assert called is False, "receive() was called: the body was read in order to refuse it"


def test_a_lying_content_length_is_caught_by_counting() -> None:
    r = request(
        g.GuardMiddleware(echo, limits={"maxBodyBytes": 100}), headers={"content-length": "5"}, body=b"x" * 5000
    )
    assert r.status == 413
    assert r.pulled <= 100 + 1 + 64, f"pulled {r.pulled} bytes to refuse a 100-byte limit"


def test_a_chunked_upload_is_cut_off_mid_stream() -> None:
    r = request(
        g.GuardMiddleware(echo, limits={"maxBodyBytes": 1000}),
        headers={"content-type": "application/octet-stream"},
        body=b"x" * 100_000,
    )
    assert r.status == 413
    assert r.pulled <= 1000 + 1 + 64


def test_two_different_content_lengths_are_refused() -> None:
    r = request(
        g.GuardMiddleware(echo),
        headers={"content-type": "application/json"},
        body=b"{}",
    )
    assert r.status == 200
    scope_headers = [(b"content-length", b"2"), (b"content-length", b"200")]

    async def run() -> int:
        status = 0

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"{}", "more_body": False}

        async def send(m: dict[str, Any]) -> None:
            nonlocal status
            if m["type"] == "http.response.start":
                status = m["status"]

        await g.GuardMiddleware(echo)(
            {"type": "http", "method": "POST", "path": "/", "headers": scope_headers}, receive, send
        )
        return status

    assert asyncio.run(run()) == 400


def test_an_empty_content_length_is_absent_not_malformed() -> None:
    # PHP-FPM sends CONTENT_LENGTH="" on every bodiless request; a proxy in front
    # of either stack can too. Refusing it would refuse every GET.
    r = request(g.GuardMiddleware(echo), method="GET", headers={"content-length": ""}, body=b"")
    assert r.status == 200


def test_a_client_that_disconnects_mid_body_is_not_an_error() -> None:
    async def run() -> list[dict[str, Any]]:
        sent: list[dict[str, Any]] = []
        messages = [{"type": "http.request", "body": b'{"a":', "more_body": True}, {"type": "http.disconnect"}]

        async def receive() -> dict[str, Any]:
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        async def send(m: dict[str, Any]) -> None:
            sent.append(m)

        scope = {"type": "http", "method": "POST", "path": "/", "headers": [(b"content-type", b"application/json")]}
        await g.GuardMiddleware(echo)(scope, receive, send)
        return sent

    assert asyncio.run(run()) == []  # nobody is listening; nothing is sent, nothing raises


# ---------------------------------------------------------------------------
# the scan
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e400", "-1e400", "9" * 400])
def test_non_finite_numbers_are_refused_with_the_field(literal: str) -> None:
    r = request(
        g.GuardMiddleware(echo),
        headers={"content-type": "application/json"},
        body=f'{{"order":{{"lines":[1,{literal}]}}}}'.encode(),
    )
    assert r.status == 422
    assert r.json()["errors"] == [
        {"error": "not_finite", "message": "This must be a finite number.", "field": "order.lines.1"}
    ]


def test_python_json_really_does_accept_nan() -> None:
    # The hazard is real, not theoretical: this is what FastAPI's parse does.
    assert json.loads('{"a": NaN}')["a"] != json.loads('{"a": NaN}')["a"]


def test_a_depth_bomb_stops_at_the_first_level_over() -> None:
    body = b"[" * 1_000_000
    r = request(g.GuardMiddleware(echo), headers={"content-type": "application/json"}, body=body, chunk=65536)
    assert r.status == 422
    item = r.json()["errors"][0]
    assert item["error"] == "too_deep"
    assert item["limit"] == {"name": "maxDepth", "max": 32}


def test_a_list_bomb_stops_at_max_plus_one() -> None:
    body = ("[" + ",".join(["0"] * 300_000) + "]").encode()
    r = request(g.GuardMiddleware(echo), headers={"content-type": "application/json"}, body=body, chunk=65536)
    assert r.status == 422
    assert r.json()["errors"] == [
        {
            "error": "list_too_long",
            "message": "This has more than 10,000 items.",
            "limit": {"name": "maxListLength", "max": 10000},
        }
    ]


def test_an_object_bomb_is_caught_like_a_list() -> None:
    body = ("{" + ",".join(f'"k{i}":0' for i in range(20_001)) + "}").encode()
    r = request(g.GuardMiddleware(echo), headers={"content-type": "application/json"}, body=body, chunk=65536)
    assert r.status == 422
    assert r.json()["errors"][0]["error"] == "list_too_long"


def test_a_pass_hands_the_application_every_byte() -> None:
    body = json.dumps({"rows": [[i, i * 1.5, "x"] for i in range(2000)]}).encode()
    r = request(g.GuardMiddleware(echo), headers={"content-type": "application/json"}, body=body, chunk=777)
    assert r.status == 200
    assert r.json() == {"len": len(body)}


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "limits",
    [
        {"maxBodyBytes": 0},
        {"maxBodyBytes": -1},
        {"maxDepth": "32"},
        {"maxBodyByte": 10},
        {"finiteNumbers": "no"},
        {"workbook": {"maxRow": 1}},
    ],
)
def test_a_bad_setting_is_refused_at_construction(limits: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="fancy-http-guard"):
        g.GuardMiddleware(echo, limits=limits)


def test_every_limit_can_be_raised() -> None:
    s = g.settings(
        {"maxBodyBytes": 500 * 1024 * 1024, "maxDepth": 10_000, "maxListLength": 10**9, "workbook": {"maxCells": 10**8}}
    )
    assert s["maxBodyBytes"] == 500 * 1024 * 1024
    assert g.workbook_limits(s)["maxCells"] == 10**8


# ---------------------------------------------------------------------------
# request ids
# ---------------------------------------------------------------------------


def test_every_response_carries_a_request_id_even_a_pass() -> None:
    r = request(g.GuardMiddleware(echo), headers={"content-type": "application/json"}, body=b"{}")
    rid = r.header("x-request-id")
    assert rid is not None and UUID4.match(rid)


def test_an_app_cannot_set_a_second_request_id_header() -> None:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": [(b"x-request-id", b"forged")]})
        await send({"type": "http.response.body", "body": b""})

    r = request(g.GuardMiddleware(app), body=b"")
    assert [k for k, _ in r.headers if k.lower() == b"x-request-id"] == [b"x-request-id"]
    assert r.header("x-request-id") != "forged"


def test_the_handler_and_the_log_see_the_same_id() -> None:
    seen: dict[str, Any] = {}

    async def app(scope: Any, receive: Any, send: Any) -> None:
        seen["id"] = scope["state"]["request_id"]
        seen["limits"] = scope["state"]["fancy_guard"].workbook_limits
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    r = request(g.GuardMiddleware(app), body=b"")
    assert seen["id"] == r.header("x-request-id")
    assert seen["limits"]["maxCells"] == 100_000


# ---------------------------------------------------------------------------
# exceptions never reach the response
# ---------------------------------------------------------------------------


def test_a_raw_asgi_crash_is_a_fixed_500() -> None:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        raise RuntimeError(CANARY)

    r = request(g.GuardMiddleware(app), body=b"")
    assert r.status == 500
    assert b"SQLSTATE" not in r.body and b"10.0.0.4" not in r.body
    assert r.json()["message"].startswith("Something went wrong on our side. If it keeps happening, quote reference ")


def test_a_crash_after_the_response_started_is_not_answered_twice() -> None:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise RuntimeError(CANARY)

    r = request(g.GuardMiddleware(app), body=b"")
    assert r.status == 200
    assert isinstance(r.raised, RuntimeError), "re-raised for the server to close the connection"
    assert b"SQLSTATE" not in r.body


@pytest.mark.skipif(not HAVE_STARLETTE, reason="the FastAPI branch")
class TestFastAPI:
    def app(self, debug: bool = False, **kw: Any) -> Any:
        from fastapi import FastAPI, HTTPException
        from pydantic import BaseModel

        class Order(BaseModel):
            price: float

        api = FastAPI(debug=debug)

        @api.post("/order")
        async def order(o: Order) -> dict[str, float]:
            return {"price": o.price}

        @api.post("/crash")
        async def crash() -> None:
            raise RuntimeError(CANARY)

        @api.post("/http500")
        async def http500() -> None:
            raise HTTPException(500, detail=CANARY)

        @api.post("/upload")
        @g.guard(profile="upload")
        async def upload() -> dict[str, int]:
            return {"ok": 1}

        @api.post("/tiny")
        @g.guard(maxBodyBytes=8)
        async def tiny() -> dict[str, int]:
            return {"ok": 1}

        g.install(api, **kw)
        return api

    def test_fastapi_alone_takes_nan(self) -> None:
        # The control: without the guard, FastAPI parses NaN into a float.
        from fastapi import FastAPI
        from pydantic import BaseModel

        class Order(BaseModel):
            price: float

        api = FastAPI()

        @api.post("/order")
        async def order(o: Order) -> dict[str, bool]:
            return {"nan": o.price != o.price}

        r = request(api, path="/order", headers={"content-type": "application/json"}, body=b'{"price": NaN}')
        assert r.status == 200 and r.json() == {"nan": True}

    def test_with_the_guard_nan_never_reaches_pydantic(self) -> None:
        r = request(self.app(), path="/order", headers={"content-type": "application/json"}, body=b'{"price": NaN}')
        assert r.status == 422
        assert r.json()["errors"][0] == {
            "error": "not_finite",
            "message": "This must be a finite number.",
            "field": "price",
        }

    def test_fastapi_validation_becomes_the_envelope(self) -> None:
        r = request(self.app(), path="/order", headers={"content-type": "application/json"}, body=b'{"price": "lots"}')
        assert r.status == 422
        body = r.json()
        assert body["error"] == "validation_failed"
        assert body["errors"][0]["field"] == "price"
        assert body["requestId"] == r.header("x-request-id")

    @pytest.mark.parametrize("debug", [False, True])
    def test_a_crash_never_echoes_even_in_debug(self, debug: bool) -> None:
        r = request(self.app(debug=debug), path="/crash", body=b"")
        assert r.status == 500
        assert b"SQLSTATE" not in r.body and b"Traceback" not in r.body
        assert r.json()["error"] == "server_error"

    def test_an_http_500_detail_is_not_echoed(self) -> None:
        r = request(self.app(), path="/http500", body=b"")
        assert r.status == 500 and b"SQLSTATE" not in r.body

    def test_an_unknown_route_is_an_envelope_with_an_id(self) -> None:
        r = request(self.app(), path="/nope", body=b"")
        assert r.status == 404
        assert r.json()["error"] == "not_found"
        assert r.json()["requestId"] == r.header("x-request-id")

    def test_per_route_limits_by_decorator(self) -> None:
        app = self.app()
        big = b"x" * 2_000_000
        assert (
            request(
                app, path="/order", headers={"content-type": "application/octet-stream"}, body=big, chunk=65536
            ).status
            == 413
        )
        assert (
            request(
                app, path="/upload", headers={"content-type": "application/octet-stream"}, body=big, chunk=65536
            ).status
            == 200
        )
        assert (
            request(app, path="/tiny", headers={"content-type": "application/json"}, body=b'{"a":12345}').status == 413
        )

    def test_per_route_limits_by_route_name(self) -> None:
        from fastapi import FastAPI

        api = FastAPI()

        @api.post("/import", name="import_rows")
        async def import_rows() -> dict[str, int]:
            return {"ok": 1}

        g.install(api, routes={"import_rows": {"maxListLength": 2}})
        r = request(api, path="/import", headers={"content-type": "application/json"}, body=b"[1,2,3]")
        assert r.status == 422 and r.json()["errors"][0]["limit"]["max"] == 2

    def test_a_mounted_app_is_resolved(self) -> None:
        from fastapi import FastAPI

        inner = FastAPI()

        @inner.post("/deep")
        @g.guard(maxDepth=2)
        async def deep() -> dict[str, int]:
            return {"ok": 1}

        outer = FastAPI()
        outer.mount("/v1", inner)
        g.install(outer)
        r = request(outer, path="/v1/deep", headers={"content-type": "application/json"}, body=b"[[[1]]]")
        assert r.status == 422 and r.json()["errors"][0]["error"] == "too_deep"
