# AGENTS.md — fancy-http-guard (Python)

ASGI request guards and the error envelope. `CLAUDE.md` is a symlink to this
file. Process rules live in the envelope's `RULES.md` and are not repeated here.

## The contract

fancy-conformance `suites/http/SPEC.md` (CC-fancy-0031), sections 3 to 5, and
the `http/request-guards` table, plus the sentence helpers of
`http/error-envelope`. The Laravel guard (`particle-academy/fancy-http-guard`)
runs the same rows. **Behaviour changes in the spec and its rows first.**

## The rules that shape this repo

- **Zero runtime dependencies.** Starlette, FastAPI and holy-sheet are SOFT
  imports: `envelope.py` looks for their exception classes, `install()` imports
  Starlette because only a Starlette app can call it, and `read_workbook` imports
  holy-sheet when called. The `no-dependencies` CI job runs the whole suite
  without Starlette or FastAPI.
- **Middleware, never a dependency.** FastAPI reads the body before resolving
  dependencies. The guard must see `receive` first.
- **Refuse unread.** A declared length over the cap answers without calling
  `receive()` once (`test_a_declared_oversize_body_is_refused_without_calling_receive`).
  The bounded read pulls at most `maxBodyBytes + 1` bytes.
- **An overflow wins, even if the framework swallowed it.** FastAPI wraps any
  exception raised while reading the body into a 400 HTTPException, so the
  counting receive also sets a flag, and `guarded_send` replaces whatever the app
  answers with the 413.
- **Exception text is never echoed.** A crash is a fixed 500 in debug mode too;
  only `HttpError(public=True)` puts a 5xx sentence of the app's own into a
  response. holy-sheet's refusal is rebuilt from its numbers.
- **Settings are validated at construction** (`settings()`), with the contract's
  camelCase names, shared with Laravel's config and holy-sheet's `limits`.

## Testing

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -e . --group dev
pytest                      # the conformance tables + hostile cases
python tests/sabotage.py    # 16 mutations, every one must be CAUGHT
ruff check . && ruff format --check . && mypy
```

- **The tables come from a fancy-conformance checkout**, never PyPI (it is not
  published there, by owner ruling). Locally that is the envelope's sibling
  `../fancy-conformance` (pytest `pythonpath`). CI checks it out at
  `ref: v<PINNED_SUITE_VERSION>` and sets `PYTHONPATH`. Move the pin in
  `tests/test_conformance.py` and the ref in `ci.yml` together.
- **`tests/asgi.py` counts what the app pulled.** It offers the body in fixed
  chunks, which is how "refused unread" and "bounded" are measured rather than
  claimed. No httpx: a forty-line driver removes the need.
- **No `from __future__ import annotations` in a test module that defines
  FastAPI endpoints inside functions.** FastAPI resolves string annotations
  against module globals, and a locally imported `Request` becomes a required
  query field. Every row then reads 422.
- On Windows, set `PYTHONIOENCODING=utf-8`, or printing a summary fails on cp1252.
