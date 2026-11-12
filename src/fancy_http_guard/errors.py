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
    ) -> None:
        super().__init__(message or "")
        self.status = status
        self.message = message
        self.error = error
        self.public = public
        self.retry_after = retry_after
        self.headers = dict(headers or {})


class ValidationFailed(Exception):
    """A 422 from the app's own validation, for a raw ASGI app with no
    framework validator: ``[{"field": "email", "message": "..."}]``. Each item may
    carry its own ``error`` code (default ``invalid``)."""

    def __init__(self, errors: Sequence[Mapping[str, Any]]) -> None:
        super().__init__("validation failed")
        self.errors = [dict(e) for e in errors]
