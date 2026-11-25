"""Mutation sweep: weaken one guard at a time and require the suite to notice.

The suite and the implementation were written in one sitting, which is the
easiest way to write tests that assert only what the code already does. Each
mutation below is a mistake a competent port would plausibly make. A mutation
that SURVIVES (the suite still passes) is a gap in the suite.

    python tests/sabotage.py

Each file is restored in a `finally`. The suite's output goes to a file and the
verdict is read from the exit code, never through a pipe.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "fancy_http_guard"

# (name, file, find, replace). `find` must occur exactly once.
MUTATIONS = [
    ("trusts a declared length", "middleware.py", "if int(lengths[0]) > cap:", "if False:"),
    ("no bounded read (trusts the length)", "middleware.py", "if len(buf) > cap:", "if False:"),
    (
        "misses the NaN/Infinity tokens",
        "scan.py",
        'if text.startswith("NaN", i) or text.startswith("Infinity", i) or text.startswith("-Infinity", i):',
        "if False:",
    ),
    ("misses overflow (1e400)", "scan.py", "if not math.isfinite(float(text[start:i])):", "if False:"),
    ("depth off by one", "scan.py", "if len(stack) + 1 > max_depth:", "if len(stack) + 1 >= max_depth:"),
    ("counts arrays only", "scan.py", "if f.count > max_list:", 'if f.kind == "a" and f.count > max_list:'),
    ("trusts any inbound id", "middleware.py", "        if self.trust:\n", "        if True:\n"),
    (
        "echoes a deliberate 5xx message",
        "envelope.py",
        "return envelope(status, code, s.server_sentence(status, retry, rid), rid, rh, retry_after=retry)",
        "return envelope(status, code, message or s.server_sentence(status, retry, rid), rid, rh, retry_after=retry)",
    ),
    (
        "echoes a crash",
        "envelope.py",
        '    log.error("fancy-http-guard: unhandled %s [request %s]", type(exc).__name__, rid, exc_info=exc)\n    return crash(rid, rh)',
        '    return envelope(500, "server_error", str(exc), rid, rh)',
    ),
    ("lets the app forge the request id", "middleware.py", 'if k.lower() != self.rh.encode("latin-1")', "if True"),
    ("scans only a declared JSON type", "middleware.py", "return media is None or media == ", "return media == "),
    (
        "keeps control characters",
        "sentences.py",
        "if cp == 0x20 or cp <= 0x1F or 0x7F <= cp <= 0x9F:",
        "if cp == 0x20:",
    ),
    ("no part-size floor", "settings.py", '"maxPartBytes": max(65_536, 64 * cells),', '"maxPartBytes": 64 * cells,'),
    (
        "lets FastAPI answer an overflow",
        "middleware.py",
        'if overflow["hit"]:\n                    started["replaced"] = True',
        'if False:\n                    started["replaced"] = True',
    ),
    (
        "half-to-even byte rounding",
        "sentences.py",
        "tenths = (n * 10 + unit // 2) // unit",
        "tenths = round(n * 10 / unit)",
    ),
    (
        "echoes holy-sheet's text",
        "envelope.py",
        'return envelope(413, "workbook_too_large", s.workbook_sentence(limit), rid, rh, limit=limit)',
        'return envelope(413, "workbook_too_large", str(exc), rid, rh, limit=limit)',
    ),
    # CC-fancy-0063: the public message length.
    (
        "ignores the maxMessageLength setting",
        "middleware.py",
        'max_message_length=lim["maxMessageLength"]',
        "max_message_length=300",
    ),
    ("no bound on the setting", "settings.py", "if n > MESSAGE_LENGTH_BOUND:", "if False:"),
    (
        "no bound on a per-error override",
        "envelope.py",
        "return max(1, min(int(chosen), MESSAGE_LENGTH_BOUND))",
        "return max(1, int(chosen))",
    ),
    (
        "ignores an item's own maxMessageLength",
        "envelope.py",
        'mx = _length(route, e.get("maxMessageLength"))',
        "mx = route",
    ),
]


def run_suite() -> int:
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as log:
        path = log.name
    with open(path, "w", encoding="utf-8") as out:
        code = subprocess.call(
            [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"],
            cwd=ROOT,
            stdout=out,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    os.unlink(path)
    return code


def main() -> int:
    if run_suite() != 0:
        print("The unmutated suite fails; fix that before reading any mutation result.")
        return 1
    survived = []
    for name, file, find, replace in MUTATIONS:
        target = SRC / file
        original = target.read_bytes()
        text = original.decode("utf-8")
        if text.count(find) != 1:
            print(f"STALE  {name}: the text to mutate occurs {text.count(find)} times in {file}")
            survived.append(name)
            continue
        try:
            target.write_bytes(text.replace(find, replace).encode("utf-8"))
            code = run_suite()
        finally:
            target.write_bytes(original)
        verdict = "caught" if code != 0 else "SURVIVED"
        print(f"{verdict:8} {name}")
        if code == 0:
            survived.append(name)
    print(f"\n{len(MUTATIONS) - len(survived)} of {len(MUTATIONS)} mutations caught")
    return 1 if survived else 0


if __name__ == "__main__":
    raise SystemExit(main())
