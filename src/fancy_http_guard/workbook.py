"""The workbook cap, paired with holy-sheet's reader limits (SPEC section 5).

Rows x columns cannot be checked from the raw bytes: an xlsx's own
``<dimension>`` is a claim the file makes about itself. holy-sheet COUNTS, by
streaming each part before one cell exists, so the guard owns the policy and
holy-sheet does the counting. holy-sheet stays a soft dependency; this module
imports it only when ``read_workbook`` is called.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .errors import ValidationFailed
from .sentences import UNREADABLE_WORKBOOK
from .settings import settings, workbook_limits


def read_workbook(data: bytes, limits: Mapping[str, Any] | None = None, *, field: str = "file") -> dict[str, Any]:
    """Read an uploaded workbook under a route's limits.

    A limit refusal propagates as holy-sheet raised it; the renderer turns it
    into a 413 ``workbook_too_large`` from its numbers. A file holy-sheet cannot
    read at all becomes a 422 naming ``field``.
    """
    try:
        from holy_sheet import agent
        from holy_sheet.exceptions import ArchiveRefused
    except ImportError as e:  # pragma: no cover - a host without holy-sheet
        raise RuntimeError("fancy-http-guard: read_workbook needs fancy-holy-sheet installed") from e

    resolved = settings(limits)
    try:
        result: dict[str, Any] = agent.read(data, limits=workbook_limits(resolved))
    except ArchiveRefused as e:
        if getattr(e, "reason", None) in ("archive_limit", "read_limit"):
            raise
        raise ValidationFailed(
            [{"error": "workbook_unreadable", "message": UNREADABLE_WORKBOOK, "field": field}]
        ) from e
    except (ValueError, RuntimeError, KeyError) as e:
        raise ValidationFailed(
            [{"error": "workbook_unreadable", "message": UNREADABLE_WORKBOOK, "field": field}]
        ) from e
    return result
