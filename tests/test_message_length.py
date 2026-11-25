"""The public message length (CC-fancy-0063, fancy-conformance SPEC 3.1 and 4.0).

One app setting, ``maxMessageLength`` (default 300, refused above 4000), and an
optional per-error override on ``HttpError`` and on ``ValidationFailed`` items,
capped at the same 4000. Only the length changes: control characters are still
stripped, a crash is still the fixed 500 and a non-public 5xx still never shows
its message, at any length.

Written BEFORE the implementation; every test here failed first.
"""

# No `from __future__ import annotations`: FastAPI must see the real classes of
# the endpoints defined inside the tests below (see tests/runner.py).
from typing import Any

import pytest

import fancy_http_guard as g
from asgi import request
from runner import HAVE_STARLETTE

ELLIPSIS = chr(0x2026)
ROWS = " ".join(f"Row {i} has a quantity below the minimum of 1." for i in range(1, 9))
MANY = " ".join(f"Row {i} has a quantity below the minimum of 1." for i in range(1, 96))
TRACE = (
    "Traceback (most recent call last):"
    + chr(10)
    + chr(10).join(f'  File "/srv/app/imports/rows.py", line {i}, in check_row_{i}' for i in range(1, 13))
    + chr(10)
    + "psycopg.errors.UndefinedTable: relation prod.users does not exist"
)


def cut(text: str, mx: int) -> str:
    return text if len(text) <= mx else text[: mx - 1] + ELLIPSIS


def raising(exc: BaseException) -> Any:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        raise exc

    return app


def answer(exc: BaseException, **limits: Any) -> dict[str, Any]:
    r = request(g.GuardMiddleware(raising(exc), limits=limits), body=b"")
    body: dict[str, Any] = r.json()
    body["status"] = r.status
    body["raw"] = r.body
    return body


def test_the_fixtures_are_long_enough_to_mean_something() -> None:
    assert 300 < len(ROWS) < 1000
    assert len(MANY) > 4000
    assert len(TRACE) > 300


# ---------------------------------------------------------------------------
# the default is unchanged
# ---------------------------------------------------------------------------


def test_the_default_is_300() -> None:
    assert g.DEFAULTS["maxMessageLength"] == 300
    assert g.settings()["maxMessageLength"] == 300
    assert g.MESSAGE_LENGTH_BOUND == 4000


def test_with_no_setting_a_long_4xx_message_still_truncates_at_300() -> None:
    body = answer(g.HttpError(400, ROWS))
    assert body["message"] == cut(ROWS, 300)
    assert len(body["message"]) == 300


def test_with_no_setting_validation_items_still_truncate_at_300() -> None:
    body = answer(g.ValidationFailed([{"field": "rows", "message": ROWS}]))
    assert body["errors"][0]["message"] == cut(ROWS, 300)


# ---------------------------------------------------------------------------
# the setting
# ---------------------------------------------------------------------------


def test_the_setting_raises_the_limit_for_a_4xx_message() -> None:
    assert answer(g.HttpError(400, ROWS), maxMessageLength=1000)["message"] == ROWS


def test_the_setting_raises_the_limit_for_validation_items() -> None:
    body = answer(g.ValidationFailed([{"field": "rows", "message": ROWS}]), maxMessageLength=1000)
    assert body["errors"][0]["message"] == ROWS


def test_the_setting_can_also_lower_the_limit() -> None:
    assert answer(g.HttpError(400, ROWS), maxMessageLength=50)["message"] == cut(ROWS, 50)


def test_a_message_over_the_setting_still_truncates() -> None:
    assert answer(g.HttpError(400, MANY), maxMessageLength=1000)["message"] == cut(MANY, 1000)


def test_the_setting_at_its_bound_is_accepted() -> None:
    assert g.settings({"maxMessageLength": 4000})["maxMessageLength"] == 4000
    assert answer(g.HttpError(400, MANY), maxMessageLength=4000)["message"] == cut(MANY, 4000)


@pytest.mark.parametrize("value", [4001, 10**9, 0, -1, "300", True, 300.0])
def test_a_bad_or_unbounded_setting_is_refused_at_construction(value: Any) -> None:
    with pytest.raises(ValueError, match="maxMessageLength"):
        g.GuardMiddleware(raising(RuntimeError()), limits={"maxMessageLength": value})


def test_the_bound_is_named_in_the_refusal() -> None:
    with pytest.raises(ValueError, match="4000"):
        g.settings({"maxMessageLength": 4001})


def test_a_profile_can_set_it() -> None:
    mw = g.GuardMiddleware(raising(g.HttpError(400, ROWS)), profiles={"long": {"maxMessageLength": 1000}})
    assert mw.profiles["long"]["maxMessageLength"] == 1000
    assert mw.profiles["default"]["maxMessageLength"] == 300


# ---------------------------------------------------------------------------
# the per-error override
# ---------------------------------------------------------------------------


def test_a_public_error_override_lets_one_message_through_whole() -> None:
    exc = g.HttpError(400, ROWS, error="rows_refused", public=True, max_message_length=1000)
    body = answer(exc)
    assert body["error"] == "rows_refused"
    assert body["message"] == ROWS


def test_a_public_5xx_override_works_too() -> None:
    exc = g.HttpError(503, ROWS, error="maintenance", public=True, max_message_length=1000)
    assert answer(exc)["message"] == ROWS


def test_the_override_on_a_plain_4xx_works() -> None:
    assert answer(g.HttpError(409, ROWS, max_message_length=1000))["message"] == ROWS


def test_an_override_may_be_smaller_than_the_setting() -> None:
    exc = g.HttpError(400, ROWS, error="rows_refused", public=True, max_message_length=100)
    assert answer(exc, maxMessageLength=1000)["message"] == cut(ROWS, 100)


def test_an_override_over_the_bound_counts_as_the_bound() -> None:
    exc = g.HttpError(400, MANY, error="rows_refused", public=True, max_message_length=100_000)
    body = answer(exc)
    assert body["message"] == cut(MANY, 4000)
    assert len(body["message"]) == 4000


def test_an_item_override_is_its_own_and_does_not_reach_its_sibling() -> None:
    exc = g.ValidationFailed(
        [
            {"field": "rows", "error": "rows_refused", "message": ROWS, "maxMessageLength": 1000},
            {"field": "note", "message": ROWS},
        ]
    )
    body = answer(exc)
    assert body["message"] == "2 fields need attention."
    assert body["errors"] == [
        {"error": "rows_refused", "message": ROWS, "field": "rows"},
        {"error": "invalid", "message": cut(ROWS, 300), "field": "note"},
    ]


def test_an_item_override_over_the_bound_counts_as_the_bound() -> None:
    exc = g.ValidationFailed([{"field": "rows", "message": MANY, "maxMessageLength": 10**9}])
    assert answer(exc)["errors"][0]["message"] == cut(MANY, 4000)


@pytest.mark.parametrize("value", [0, -5, "1000", True, 1000.0])
def test_a_bad_override_is_refused_where_it_is_raised(value: Any) -> None:
    with pytest.raises(ValueError, match="max_message_length"):
        g.HttpError(400, ROWS, max_message_length=value)
    with pytest.raises(ValueError, match="maxMessageLength"):
        g.ValidationFailed([{"field": "rows", "message": ROWS, "maxMessageLength": value}])


def test_render_caps_a_limit_handed_to_it_directly() -> None:
    r = g.render(g.HttpError(400, MANY), request_id="r-1", max_message_length=10**9)
    assert r.body["message"] == cut(MANY, 4000)


# ---------------------------------------------------------------------------
# no opt-out from sanitising
# ---------------------------------------------------------------------------


def test_at_the_bound_control_characters_are_still_stripped() -> None:
    lines = [f"Row {i} has a quantity below the minimum of 1." for i in range(1, 9)]
    noisy = (chr(10) + chr(9)).join(lines[:4]) + chr(0) + chr(0x85) + (chr(13) + chr(10)).join(lines[4:])
    body = answer(g.HttpError(400, noisy), maxMessageLength=4000)
    assert body["message"] == ROWS
    assert not any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F for c in body["message"])


def test_a_public_override_does_not_skip_sanitising() -> None:
    exc = g.HttpError(400, "One" + chr(10) + chr(0) + "two", error="rows_refused", public=True, max_message_length=4000)
    assert answer(exc)["message"] == "One two"


def test_at_the_bound_a_crash_is_still_the_fixed_500() -> None:
    body = answer(RuntimeError(TRACE), maxMessageLength=4000)
    assert body["status"] == 500
    assert body["error"] == "server_error"
    assert b"Traceback" not in body["raw"] and b"/srv/app" not in body["raw"] and b"prod.users" not in body["raw"]


def test_at_the_bound_a_non_public_5xx_never_shows_its_message() -> None:
    body = answer(g.HttpError(503, TRACE, max_message_length=4000), maxMessageLength=4000)
    assert body["status"] == 503
    assert body["message"].startswith("The service is unavailable right now.")
    assert b"/srv/app" not in body["raw"] and b"Traceback" not in body["raw"]


# ---------------------------------------------------------------------------
# through FastAPI: the branch a FastAPI consumer reaches
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAVE_STARLETTE, reason="covered by the main test job; this job proves zero dependencies")
class TestThroughFastAPI:
    def app(self, **kw: Any) -> Any:
        from fastapi import FastAPI
        from pydantic import BaseModel, field_validator
        from starlette.exceptions import HTTPException

        class Rows(BaseModel):
            n: int

            @field_validator("n")
            @classmethod
            def long(cls, v: int) -> int:
                raise ValueError(ROWS)

        api = FastAPI()

        @api.post("/http")
        async def http() -> None:
            raise HTTPException(400, detail=ROWS)

        @api.post("/validate")
        async def validate(rows: Rows) -> None:
            return None

        @api.post("/failed")
        async def failed() -> None:
            raise g.ValidationFailed([{"field": "rows", "message": ROWS, "maxMessageLength": 1000}])

        @api.post("/crash")
        async def crash() -> None:
            raise RuntimeError(TRACE)

        @api.post("/route")
        @g.guard(maxMessageLength=1000)
        async def route() -> None:
            raise HTTPException(400, detail=ROWS)

        g.install(api, **kw)
        return api

    def test_the_default_is_unchanged(self) -> None:
        r = request(self.app(), path="/http", body=b"")
        assert r.json()["message"] == cut(ROWS, 300)

    def test_the_setting_reaches_a_starlette_http_exception(self) -> None:
        r = request(self.app(limits={"maxMessageLength": 1000}), path="/http", body=b"")
        assert r.json()["message"] == ROWS

    def test_the_setting_reaches_fastapi_validation(self) -> None:
        app = self.app(limits={"maxMessageLength": 1000})
        r = request(app, path="/validate", headers={"content-type": "application/json"}, body=b'{"n": 1}')
        assert r.status == 422
        assert ROWS in r.json()["errors"][0]["message"]
        default = request(self.app(), path="/validate", headers={"content-type": "application/json"}, body=b'{"n": 1}')
        assert len(default.json()["errors"][0]["message"]) == 300

    def test_an_item_override_reaches_the_handler(self) -> None:
        r = request(self.app(), path="/failed", body=b"")
        assert r.json()["errors"][0]["message"] == ROWS

    def test_a_per_route_setting(self) -> None:
        app = self.app()
        assert request(app, path="/route", body=b"").json()["message"] == ROWS
        assert request(app, path="/http", body=b"").json()["message"] == cut(ROWS, 300)

    def test_a_crash_at_the_bound_is_still_the_fixed_500(self) -> None:
        r = request(self.app(limits={"maxMessageLength": 4000}), path="/crash", body=b"")
        assert r.status == 500
        assert b"Traceback" not in r.body and b"/srv/app" not in r.body
