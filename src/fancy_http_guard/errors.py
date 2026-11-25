"""Exceptions an application raises on purpose."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


class HttpError(Exception):
    """A deliberate HTTP error.

    On a 4xx, ``message`` is the app's own sentence and is shown. On a 5xx it is
    NEVER shown, unless ``public=True`` declares it safe: that is the one way to
    give a 503 a sentence of your own (``"Back at 14:00 UTC."``). ``error`` sets
    the code (default: from the status); a code that is not snake_case is
    treated as a programming error and rendered as a 500.

    ``max_message_length`` lets THIS error's message be longer (or shorter) than
    the route's ``maxMessageLength`` setting, for one long data-built refusal.
    It is capped at 4000 code points, and the message is sanitised as always.
    It has no effect where the message is never shown (a non-public 5xx).
    """

    def __init__(
        self,
        status: int,
        message: str | None = None,
        *,
        error: str | None = None,
        public: bool = False,
        retry_after: int | None = None,
        headers: Mapping[str, str] | None = None,
        max_message_length: int | None = None,
    ) -> None:
        super().__init__(message or "")
        self.status = status
        self.message = message
        self.error = error
        self.public = public
        self.retry_after = retry_after
        self.headers = dict(headers or {})
        self.max_message_length = _override("max_message_length", max_message_length)


class ValidationFailed(Exception):
    """A 422 from the app's own validation, for a raw ASGI app with no
    framework validator: ``[{"field": "email", "message": "..."}]``. Each item may
    carry its own ``error`` code (default ``invalid``) and its own
    ``maxMessageLength`` (capped at 4000), which applies to that item alone."""

    def __init__(self, errors: Sequence[Mapping[str, Any]]) -> None:
        super().__init__("validation failed")
        self.errors = [dict(e) for e in errors]
        for e in self.errors:
            if "maxMessageLength" in e:
                _override("maxMessageLength", e["maxMessageLength"])


def _override(name: str, value: Any) -> int | None:
    """A per-error length: absent, or a positive integer. A wrong one is a
    programming error, refused where the app raises it."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"fancy-http-guard: {name} must be a positive integer, got {value!r}")
    return value
