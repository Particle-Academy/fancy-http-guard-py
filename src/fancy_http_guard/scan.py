"""The pre-parse scan (fancy-conformance suites/http/SPEC.md section 3.3).

One pass over the text with an explicit stack: no tree is built, nothing
recurses, no regular expression runs. It answers three questions a parser would
only answer after paying for the whole document: how deep, how many members,
and is every number a finite double. It stops at the first bomb, so a
100,000-bracket body costs 33 steps.

Python's ``json.loads`` ACCEPTS ``NaN``, ``Infinity`` and ``-Infinity`` by
default, which is how FastAPI takes them today. They are recognised here only so
they can be refused.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .sentences import NOT_FINITE, format_count

_WS = " \t\n\r"
_SIMPLE = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
_HEX = set("0123456789abcdefABCDEF")
_DIGITS = set("0123456789")


@dataclass
class ScanResult:
    fault: bool = False
    items: list[dict[str, Any]] = field(default_factory=list)


class _Fault(Exception):
    pass


class _Stop(Exception):
    pass


@dataclass
class _Frame:
    kind: str  # "a" or "o"
    segs: tuple[str, ...]
    count: int = 0


def _with_field(item: dict[str, Any], segs: tuple[str, ...]) -> dict[str, Any]:
    if segs:
        item["field"] = ".".join(segs)
    return item


def scan(text: str, limits: dict[str, Any]) -> ScanResult:
    """Scan decoded JSON text. ``fault`` means a grammar fault (a 400);
    otherwise ``items`` are the 422 items, in document order."""
    max_depth: int = limits["maxDepth"]
    max_list: int = limits["maxListLength"]
    max_errors: int = limits["maxErrors"]
    finite: bool = limits["finiteNumbers"]
    items: list[dict[str, Any]] = []
    n = len(text)
    i = 0
    stack: list[_Frame] = []

    def ws() -> None:
        nonlocal i
        while i < n and text[i] in _WS:
            i += 1

    def string() -> str:
        nonlocal i
        i += 1  # the opening quote
        out: list[str] = []
        while i < n:
            c = text[i]
            if c == '"':
                i += 1
                return "".join(out)
            if ord(c) < 0x20:
                raise _Fault
            if c == "\\":
                if i + 1 >= n:
                    raise _Fault
                e = text[i + 1]
                i += 2
                if e in _SIMPLE:
                    out.append(_SIMPLE[e])
                    continue
                if e != "u":
                    raise _Fault
                hx = text[i : i + 4]
                if len(hx) != 4 or not set(hx) <= _HEX:
                    raise _Fault
                i += 4
                cu = int(hx, 16)
                if 0xD800 <= cu <= 0xDBFF and text[i : i + 2] == "\\u":
                    lo = text[i + 2 : i + 6]
                    if len(lo) == 4 and set(lo) <= _HEX and 0xDC00 <= int(lo, 16) <= 0xDFFF:
                        i += 6
                        out.append(chr(0x10000 + ((cu - 0xD800) << 10) + (int(lo, 16) - 0xDC00)))
                        continue
                out.append("�" if 0xD800 <= cu <= 0xDFFF else chr(cu))
                continue
            out.append(c)
            i += 1
        raise _Fault

    def member(f: _Frame) -> None:
        f.count += 1
        if f.count > max_list:
            items.append(
                _with_field(
                    {
                        "error": "list_too_long",
                        "message": f"This has more than {format_count(max_list)} items.",
                        "limit": {"name": "maxListLength", "max": max_list},
                    },
                    f.segs,
                )
            )
            raise _Stop

    def not_finite(segs: tuple[str, ...]) -> None:
        if not finite:
            return
        items.append(_with_field({"error": "not_finite", "message": NOT_FINITE}, segs))
        if len(items) >= max_errors:
            raise _Stop

    def key() -> str:
        nonlocal i
        if i >= n or text[i] != '"':
            raise _Fault
        k = string()
        ws()
        if i >= n or text[i] != ":":
            raise _Fault
        i += 1
        return k

    def digits() -> None:
        nonlocal i
        if i >= n or text[i] not in _DIGITS:
            raise _Fault
        while i < n and text[i] in _DIGITS:
            i += 1

    try:
        mode = "value"
        segs: tuple[str, ...] = ()
        while True:
            if mode == "value":
                ws()
                if i >= n:
                    raise _Fault
                c = text[i]
                if c in "[{":
                    if len(stack) + 1 > max_depth:
                        items.append(
                            _with_field(
                                {
                                    "error": "too_deep",
                                    "message": f"This is nested more than {format_count(max_depth)} levels deep.",
                                    "limit": {"name": "maxDepth", "max": max_depth},
                                },
                                segs,
                            )
                        )
                        raise _Stop
                    i += 1
                    f = _Frame("a" if c == "[" else "o", segs)
                    stack.append(f)
                    ws()
                    if i < n and text[i] == ("]" if f.kind == "a" else "}"):
                        i += 1
                        stack.pop()
                        mode = "after"
                        continue
                    if f.kind == "a":
                        member(f)
                        segs = (*f.segs, "0")
                    else:
                        k = key()
                        member(f)
                        segs = (*f.segs, k)
                    continue
                if c == '"':
                    string()
                    mode = "after"
                    continue
                if text.startswith("NaN", i) or text.startswith("Infinity", i) or text.startswith("-Infinity", i):
                    i += 3 if text.startswith("NaN", i) else 8 if text.startswith("Infinity", i) else 9
                    not_finite(segs)
                    mode = "after"
                    continue
                if c == "-" or c in _DIGITS:
                    start = i
                    if text[i] == "-":
                        i += 1
                    if i < n and text[i] == "0":
                        i += 1
                    else:
                        digits()
                    if i < n and text[i] == ".":
                        i += 1
                        digits()
                    if i < n and text[i] in "eE":
                        i += 1
                        if i < n and text[i] in "+-":
                            i += 1
                        digits()
                    if not math.isfinite(float(text[start:i])):
                        not_finite(segs)
                    mode = "after"
                    continue
                for literal in ("true", "false", "null"):
                    if text.startswith(literal, i):
                        i += len(literal)
                        mode = "after"
                        break
                else:
                    raise _Fault
                continue

            # after a value
            ws()
            if not stack:
                if i < n:
                    raise _Fault
                return ScanResult(items=items)
            f = stack[-1]
            if i >= n:
                raise _Fault
            c = text[i]
            if c == ",":
                i += 1
                ws()
                if f.kind == "a":
                    member(f)
                    segs = (*f.segs, str(f.count - 1))
                else:
                    k = key()
                    member(f)
                    segs = (*f.segs, k)
                mode = "value"
                continue
            if c == ("]" if f.kind == "a" else "}"):
                i += 1
                stack.pop()
                continue
            raise _Fault
    except _Fault:
        return ScanResult(fault=True)
    except _Stop:
        return ScanResult(items=items)
