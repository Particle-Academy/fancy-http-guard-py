"""Limits: secure defaults, every one an app setting (SPEC section 3.1).

The names are camelCase on purpose: they are the contract's names, identical in
the Laravel package's config and in holy-sheet's ``limits`` option, so a value
copied between them means the same thing.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

DEFAULTS: dict[str, Any] = {
    "maxBodyBytes": 1_048_576,
    "maxDepth": 32,
    "maxListLength": 10_000,
    "maxErrors": 20,
    "finiteNumbers": True,
    "workbook": {"maxRows": 100_000, "maxColumns": 1_000, "maxCells": 100_000},
}

PROFILES: dict[str, dict[str, Any]] = {
    "default": {},
    "upload": {"maxBodyBytes": 10_485_760},
}

_INTS = ("maxBodyBytes", "maxDepth", "maxListLength", "maxErrors")
_WORKBOOK = ("maxRows", "maxColumns", "maxCells")


def _refuse(why: str) -> ValueError:
    return ValueError(f"fancy-http-guard: {why}")


def _positive(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _refuse(f"{name} must be a positive integer, got {value!r}")
    return value


def settings(limits: Mapping[str, Any] | None = None, base: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The defaults (or ``base``) overridden key by key. A bad key or value is
    refused HERE, at construction, never on the first request."""
    out = copy.deepcopy(dict(base) if base is not None else DEFAULTS)
    for key, value in (limits or {}).items():
        if key in _INTS:
            out[key] = _positive(key, value)
        elif key == "finiteNumbers":
            if not isinstance(value, bool):
                raise _refuse(f"finiteNumbers must be true or false, got {value!r}")
            out[key] = value
        elif key == "workbook":
            if not isinstance(value, Mapping):
                raise _refuse("workbook must be a mapping of maxRows / maxColumns / maxCells")
            for wk, wv in value.items():
                if wk not in _WORKBOOK:
                    raise _refuse(f"unknown workbook limit {wk!r}; known: {', '.join(_WORKBOOK)}")
                out["workbook"][wk] = _positive(f"workbook.{wk}", wv)
        else:
            raise _refuse(f"unknown limit {key!r}; known: {', '.join((*_INTS, 'finiteNumbers', 'workbook'))}")
    return out


def workbook_limits(s: Mapping[str, Any]) -> dict[str, int]:
    """holy-sheet's ``limits`` option for a route (SPEC section 5).

    64 bytes per cell is holy-sheet's measured 55-58, rounded up; the floors keep
    a tiny cell cap from refusing a small file's styles part. The archive cap is
    the route's body cap, so a file the guard would have refused cannot be read
    through a looser one.
    """
    w = s["workbook"]
    cells = int(w["maxCells"])
    return {
        "maxRows": int(w["maxRows"]),
        "maxColumns": int(w["maxColumns"]),
        "maxCells": cells,
        "maxSharedStrings": cells,
        "maxArchiveBytes": int(s["maxBodyBytes"]),
        "maxPartBytes": max(65_536, 64 * cells),
        "maxExpandedBytes": max(262_144, 256 * cells),
    }
