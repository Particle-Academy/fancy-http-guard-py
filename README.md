# fancy-http-guard

Request guards for **Starlette and FastAPI**, checked **before the body is
parsed**, and one error envelope with a request id on every response. Pure
ASGI, zero runtime dependencies.

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
    ...
```

## Why middleware

FastAPI reads the body BEFORE it resolves dependencies, so a dependency that
checks the body runs after the parse it was meant to prevent. Python's
`json.loads` also ACCEPTS `NaN` and `Infinity` by default, so a FastAPI model
with a `float` field takes them today. The guard sits in front:

| Check | Answer |
|---|---|
| `Content-Length` over the route's cap | **413, and not one body byte is read** |
| a missing or lying `Content-Length` | 413 once `maxBodyBytes + 1` bytes have arrived; the rest is never pulled |
| a malformed `Content-Length` (letters, a sign, two values) | 400 |
| nesting deeper than `maxDepth` | 422 `too_deep`, naming the field; the scan stops there |
| an array or object with more than `maxListLength` members | 422 `list_too_long`; the scan stops there |
| `NaN`, `Infinity`, `-Infinity`, `1e400`, a 310-digit integer | 422 `not_finite`, naming the field: `items.3.price` |
| not JSON at all | 400 `malformed_body` |

JSON bodies, and bodies with no `Content-Type` (FastAPI parses those as JSON),
are scanned in one pass with no tree built, then replayed to your app byte for
byte. Uploads are not buffered: they are counted as the app reads them.

## The envelope

Every error is one shape, with the request id in the body and the
`X-Request-Id` header (on every response, a pass included):

```json
{"error": "body_too_large", "message": "This request is 12.4 MB, over this endpoint's limit of 10 MB.",
 "limit": {"name": "maxBodyBytes", "max": 10485760, "actual": 13002342}, "requestId": "4f8c2a9e-..."}
```

- a **422** carries `errors: [{error, message, field?}]`, with FastAPI's own
  validation errors converted to it;
- a **429** or **503** carries `retryAfter` and `Retry-After`;
- **a crash is a fixed 500, in every environment, debug included:** "Something
  went wrong on our side. If it keeps happening, quote reference 4f8c2a9e-...".
  The exception goes to the `fancy_http_guard` logger under that id, never to
  the response;
- a deliberate 4xx keeps your sentence (`HTTPException(410, "This invite has
  expired.")`); a deliberate 5xx never does, unless you raise
  `HttpError(503, "Back at 14:00 UTC.", error="maintenance", public=True)`;
- responses your app RETURNS are never rewritten. Only the request-id header is
  added, so Inertia's 409s pass through.

`@particle-academy/fancy-http-errors` turns any of these, or anything else a
server sends, into one sentence for the UI.

## Settings: every limit is a setting

```python
guard.install(
    app,
    limits={
        "maxBodyBytes": 1_048_576,
        "maxDepth": 32,
        "maxListLength": 10_000,
        "maxErrors": 20,
        "finiteNumbers": True,
        "workbook": {"maxRows": 100_000, "maxColumns": 1_000, "maxCells": 100_000},
    },
    profiles={"bulk": {"maxListLength": 100_000}},
    routes={"import_rows": "bulk"},  # by route name
    request_id_header="x-request-id",
    trust_incoming_request_id=False,  # True only behind a proxy that sets it
)
```

A bad key or value is refused when the app starts, never on the first request.
Per route: `@guard.guard(profile="upload")`, `@guard.guard(maxDepth=8)`, or the
`routes` mapping. Mounted sub-apps are resolved.

Without Starlette, wrap any ASGI app: `GuardMiddleware(app, limits=...)`. Raise
`HttpError` and `ValidationFailed([{"field", "message"}])` there instead of a
framework's exceptions.

## Workbooks

Rows x columns cannot be checked from the raw bytes: an xlsx's `<dimension>` is
a claim the file makes about itself. So the guard sets the limits and
[holy-sheet](https://pypi.org/project/fancy-holy-sheet/) counts them, streaming
each part before one cell exists. `request.state.fancy_guard.workbook_limits` is
holy-sheet's `limits` option for the route. The archive cap is the route's body
cap, and the part caps follow `maxCells` at 64 bytes per cell.
`read_workbook(data, limits, field="file")` reads under those limits. A refusal
becomes a 413 `workbook_too_large` with holy-sheet's numbers, and a file that is
not a spreadsheet becomes a 422 naming the field. holy-sheet is optional and
only imported when you use this.

## Contract

The rules are fancy-conformance `suites/http/SPEC.md`. This package runs all 88
rows of `http/request-guards` in CI, through a raw ASGI app and through FastAPI,
and the Laravel guard runs the same rows. MIT.
