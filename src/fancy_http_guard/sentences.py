"""The shared sentences (fancy-conformance suites/http/SPEC.md section 2).

The TypeScript normaliser and the Laravel guard write and read the same
sentences, pinned by the same rows, so a sentence a server writes is the one the
client would have built. Change them in the spec first.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

STATUS: dict[int, tuple[str, str]] = {
    0: ("network_error", "We could not reach the server. Check your connection and try again."),
    400: ("bad_request", "The request could not be understood."),
    401: ("unauthenticated", "Please sign in to continue."),
    403: ("forbidden", "You do not have permission to do that."),
    404: ("not_found", "That could not be found."),
    405: ("method_not_allowed", "That action is not allowed here."),
    408: ("request_timeout", "The request took too long. Please try again."),
    409: ("conflict", "This conflicts with a change made elsewhere. Reload and try again."),
    410: ("gone", "This is no longer available."),
    413: ("body_too_large", "This request is too large for the server."),
    415: ("unsupported_media_type", "That type of content is not accepted here."),
    422: ("validation_failed", "Some fields need attention."),
    429: ("rate_limited", "Too many requests."),
    500: ("server_error", "Something went wrong on our side."),
    502: ("upstream_error", "A service this depends on did not respond."),
    503: ("unavailable", "The service is unavailable right now."),
    504: ("upstream_error", "A service this depends on did not respond."),
}

NOT_FINITE = "This must be a finite number."
MALFORMED = "The request body is not valid JSON."
UNREADABLE_WORKBOOK = "This file could not be read as a spreadsheet."


def status_defaults(status: int) -> tuple[str, str]:
    """The code and default sentence for a status."""
    if status in STATUS:
        return STATUS[status]
    if status >= 500:
        return STATUS[500]
    return ("client_error", "The request could not be completed.")


def format_count(n: int) -> str:
    """``1048576`` -> ``"1,048,576"``."""
    return f"{int(n):,}"


_UNITS = ((1024**3, "GB"), (1024**2, "MB"), (1024, "KB"))


def format_bytes(n: int) -> str:
    """A byte count for a person. Tenths are rounded in INTEGER arithmetic,
    half up: Python's ``round`` is half-to-even and disagrees on exact halves."""
    if n < 1024:
        return "1 byte" if n == 1 else f"{format_count(n)} bytes"
    unit, name = next((u, label) for u, label in _UNITS if u <= n)
    tenths = (n * 10 + unit // 2) // unit
    whole, frac = divmod(tenths, 10)
    return f"{format_count(whole)}{f'.{frac}' if frac else ''} {name}"


def format_duration(seconds: int) -> str:
    """The retry clause, without its leading space. Minutes and hours round UP."""
    if seconds <= 0:
        return "Try again now."

    def unit(n: int, one: str) -> str:
        return f"{format_count(n)} {one}{'' if n == 1 else 's'}"

    if seconds < 60:
        return f"Try again in {unit(seconds, 'second')}."
    if seconds < 3600:
        return f"Try again in {unit(-(-seconds // 60), 'minute')}."
    return f"Try again in {unit(-(-seconds // 3600), 'hour')}."


def sanitise(text: str, max: int = 300) -> str:
    """Controls become spaces, runs collapse, the ends trim, and anything over
    ``max`` CODE POINTS keeps ``max - 1`` and gains an ellipsis."""
    out: list[str] = []
    space = True
    for ch in text:
        cp = ord(ch)
        if cp == 0x20 or cp <= 0x1F or 0x7F <= cp <= 0x9F:
            if not space:
                out.append(" ")
            space = True
        else:
            out.append(ch)
            space = False
    if out and out[-1] == " ":
        out.pop()
    if len(out) > max:
        return "".join(out[: max - 1]) + "…"
    return "".join(out)


def rate_limited(retry_after: int | None) -> str:
    return "Too many requests. " + ("Try again shortly." if retry_after is None else format_duration(retry_after))


def server_sentence(status: int, retry_after: int | None, request_id: str | None) -> str:
    """Every 5xx sentence: the default, the retry clause, the reference. Nothing
    an exception said reaches it."""
    s = status_defaults(status)[1]
    if retry_after is not None:
        s += " " + format_duration(retry_after)
    if request_id is not None and status != 0:
        s += f" If it keeps happening, quote reference {request_id}."
    return s


def body_too_large(max_bytes: int, actual: int | None) -> str:
    if actual is None:
        return f"This request is over this endpoint's limit of {format_bytes(max_bytes)}."
    return f"This request is {format_bytes(actual)}, over this endpoint's limit of {format_bytes(max_bytes)}."


def workbook_sentence(limit: Mapping[str, Any]) -> str:
    """The sentence for a holy-sheet refusal, built from its numbers, never its text."""
    name, mx, actual = limit.get("name"), limit.get("max"), limit.get("actual")
    if name == "maxCompressionRatio":
        return "This spreadsheet is compressed too tightly to open safely."
    if not isinstance(actual, int) or not isinstance(mx, int):
        return "This spreadsheet is too large to open here."
    c, b = format_count, format_bytes
    templates = {
        "maxRows": f"This spreadsheet has {c(actual)} rows, over the limit of {c(mx)}.",
        "maxColumns": f"This spreadsheet has {c(actual)} columns, over the limit of {c(mx)}.",
        "maxCells": f"This spreadsheet has at least {c(actual)} cells, over the limit of {c(mx)}.",
        "maxSharedStrings": f"This spreadsheet has {c(actual)} shared strings, over the limit of {c(mx)}.",
        "maxEntries": f"This spreadsheet has {c(actual)} parts, over the limit of {c(mx)}.",
        "maxArchiveBytes": f"This spreadsheet is {b(actual)}, over the limit of {b(mx)}.",
        "maxPartBytes": f"This spreadsheet needs at least {b(actual)} to open, over the limit of {b(mx)}.",
        "maxExpandedBytes": f"This spreadsheet needs at least {b(actual)} to open, over the limit of {b(mx)}.",
    }
    return templates.get(str(name), "This spreadsheet is too large to open here.")
