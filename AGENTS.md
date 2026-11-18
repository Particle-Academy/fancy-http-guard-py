# Using fancy-http-guard (Python) — guide for agents

How an agent installs and uses this package in a consumer app. What the package
is, is in [README.md](./README.md). Notes for working ON this repo's code are in
[CONTRIBUTING.md](./CONTRIBUTING.md).

## Install

```bash
pip install fancy-http-guard
```

- **Not published to PyPI yet** (version `0.1.0` in the repo). The command above
  works once it is.
- Python `>=3.11`. Zero runtime dependencies: Starlette, FastAPI and
  holy-sheet (`fancy-holy-sheet`, for workbooks) are used when present, never
  required.

## Find it on the Fancy MCP server

Not listed on the Fancy MCP catalog server
(`https://ui.particle.academy/mcp`, MCP id `fancy-ui`) yet; it appears there
once published. Look for it later with `search-backend-packages` (stack
`python`). The Laravel twin is `particle-academy/fancy-http-guard`, and the
browser side that turns the envelope into a sentence is
`@particle-academy/fancy-http-errors`.

## API essentials

```python
from fastapi import FastAPI, Request
import fancy_http_guard as guard

app = FastAPI()
guard.install(app)  # 1 MiB, depth 32, 10,000 members, finite numbers


@app.post("/imports")
@guard.guard(profile="upload")  # 10 MiB for this route only
async def imports(request: Request):
    data = await request.body()
    sheet = request.state.fancy_guard.read_workbook(data, field="file")
```

- `install(app, limits=..., profiles=..., routes=..., request_id_header=...,
  trust_incoming_request_id=False)` for a Starlette or FastAPI app. Limits use
  the contract's camelCase names: `maxBodyBytes`, `maxDepth`, `maxListLength`,
  `maxErrors`, `finiteNumbers`, `workbook` (`maxRows`, `maxColumns`,
  `maxCells`).
- Per route: `@guard.guard(profile="upload")`, `@guard.guard(maxDepth=8)`, or the
  `routes` mapping by route name.
- Any other ASGI app: `GuardMiddleware(app, limits=...)`, raising `HttpError`
  and `ValidationFailed([{"field", "message"}])` instead of a framework's
  exceptions.
- Every error is one envelope: `{error, message, limit?, errors?, retryAfter?,
  requestId}`, with the request id also in `X-Request-Id` on every response.
- `request.state.fancy_guard.workbook_limits` is holy-sheet's `limits` option
  for the route; `read_workbook(data, limits, field=...)` reads under them.

## Traps

- **It is middleware on purpose.** A FastAPI dependency that checks the body
  runs after the parse it was meant to prevent; do not move the checks into
  dependencies.
- **A crash is a fixed 500, debug mode included.** The exception goes to the
  `fancy_http_guard` logger under the request id, never to the response. For a
  deliberate 5xx sentence, raise `HttpError(503, "...", error="maintenance",
  public=True)`.
- **Responses your app returns are never rewritten**; only the request-id header
  is added.
- **A bad setting fails at startup**, not on the first request.
- `trust_incoming_request_id=True` only behind a proxy that sets the header.
- Python's `json.loads` accepts `NaN` and `Infinity`; the guard refuses them
  (`not_finite`) while `finiteNumbers` is on.
