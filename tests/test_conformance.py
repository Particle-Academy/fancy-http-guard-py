"""Run this package against fancy-conformance http/request-guards and the
sentence helpers in http/error-envelope.

Four rules from fancy-conformance runners/README.md, all honoured:

1. Run on every push and PR.
2. A missing fixture checkout is a FAILURE, not a skip (the loader raises).
3. Print the summary unconditionally, including every skip and its reason.
4. Print and assert the pinned suite version.

`render` rows run TWICE when Starlette is installed: through a raw ASGI app
(what the no-dependencies CI job covers) and through a FastAPI app with
`install()`, which is the branch a FastAPI consumer actually reaches.
"""

from __future__ import annotations

import re
from pathlib import Path

import fancy_conformance as loader
import pytest

import runner
from fancy_http_guard.sentences import format_bytes, format_duration, sanitise

# The tag CI checks out (.github/workflows/ci.yml). Move the two together, after
# re-running the tables against the new checkout; test_ci_ref_is_the_pin fails
# otherwise.
PINNED_SUITE_VERSION = "0.48.0"
REPO = Path(__file__).resolve().parent.parent


def _show(summary: dict, capsys: pytest.CaptureFixture[str]) -> None:
    with capsys.disabled():
        print("\n" + loader.format_summary(summary))


def test_the_pinned_fixture_version_is_the_one_on_disk(capsys: pytest.CaptureFixture[str]) -> None:
    with capsys.disabled():
        print(f"\nfancy-conformance {loader.version()} at {loader.suites_root()}")
    assert loader.version() == PINNED_SUITE_VERSION


def test_ci_ref_is_the_pin() -> None:
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    refs = re.findall(r"repository: Particle-Academy/fancy-conformance\s+ref: (\S+)", ci)
    assert refs and set(refs) == {f"v{PINNED_SUITE_VERSION}"}


def _helpers(case: dict) -> object:
    inp = case["input"]
    match case["fn"]:
        case "formatBytes":
            return format_bytes(inp["n"])
        case "formatDuration":
            return format_duration(inp["s"])
        case "sanitise":
            return sanitise(inp["text"]) if "max" not in inp else sanitise(inp["text"], inp["max"])
    raise AssertionError(f"no runner for {case['fn']}")


def test_error_envelope_helpers(capsys: pytest.CaptureFixture[str]) -> None:
    summary = loader.run_table("http/error-envelope", _helpers, language="python")
    _show(summary, capsys)
    assert summary["failed"] == 0, [r for r in summary["results"] if r["status"] == "fail"]
    assert summary["passed"] == 29
    # Every normalise row, and nothing else, is skipped -- with its reason.
    assert summary["skipped"] == 53


def _guards(case: dict, *, starlette_branch: bool) -> object:
    suite = "http/request-guards"
    resolve = lambda ref: loader.fixture_bytes(suite, ref)  # noqa: E731
    match case["fn"]:
        case "guard":
            return runner.run_guard(case["input"], resolve)
        case "render":
            return runner.run_render(case["input"], starlette_branch=starlette_branch)
        case "workbookLimits":
            from fancy_http_guard import settings, workbook_limits

            return workbook_limits(settings(case["input"]["limits"]))
        case "workbook":
            return runner.run_workbook(case["input"], resolve)
    raise AssertionError(f"no runner for {case['fn']}")


def test_request_guards_raw_asgi(capsys: pytest.CaptureFixture[str]) -> None:
    summary = loader.run_table("http/request-guards", lambda c: _guards(c, starlette_branch=False), language="python")
    _show(summary, capsys)
    assert summary["failed"] == 0, [r for r in summary["results"] if r["status"] == "fail"]
    assert summary["skipped"] == 0
    assert summary["passed"] == 88


@pytest.mark.skipif(not runner.HAVE_STARLETTE, reason="covered by the main test job; this job proves zero dependencies")
def test_request_guards_render_through_fastapi(capsys: pytest.CaptureFixture[str]) -> None:
    rows = [c for c in loader.cases("http/request-guards") if c["fn"] == "render"]
    failures = []
    for c in rows:
        got = _guards(c, starlette_branch=True)
        if not loader.equals(got, c["expected"]):
            failures.append((c["id"], got, c["expected"]))
    with capsys.disabled():
        print(f"\nhttp/request-guards render rows through FastAPI: {len(rows) - len(failures)} of {len(rows)} passed")
    assert not failures
    assert len(rows) == 21


def test_workbook_rows_need_holy_sheet() -> None:
    # Fail, never skip: without holy-sheet the workbook rows would pass as
    # "unreadable" for the wrong reason, or not run at all. It is first-party
    # and dependency-free, so even the zero-dependency job installs it.
    import holy_sheet  # noqa: F401
