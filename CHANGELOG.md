# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Pre-1.0, breaking changes land in MINOR releases.** The version number is not
promising otherwise until 1.0.

## [Unreleased]

### Changed

- **CI: the lab-certificate gate verifies against the lab's rotated key list.**
  The lab's first signing key is retired: certificates it already issued still
  verify, and nothing new it signs ever can. Consumers do nothing.

- **Docs:** `AGENTS.md` is now the guide to using this package (install, how to find it on the Fancy MCP catalog server once it is listed, API essentials, traps). The contributor notes it held moved to `CONTRIBUTING.md`, and `CLAUDE.md` is now a symlink to it. The README points to the MCP server and to `AGENTS.md`. No code changed; nothing to do.

## [0.1.0] - 2026-11-12

First release (CC-fancy-0031). Not tagged yet.

### Added

- **`GuardMiddleware`, pure ASGI middleware that checks a request before
  anything parses it:**
  - a `Content-Length` over the route's cap is refused without reading the body;
  - a bounded read catches a missing or lying length;
  - one scan of a JSON body refuses nesting deeper than `maxDepth`, more than
    `maxListLength` members, and `NaN`/`Infinity`/overflowing numbers, naming
    the field.
- **`install(app)`** for Starlette and FastAPI. It adds the middleware, plus
  handlers that turn HTTP exceptions and FastAPI's validation errors into the
  error envelope.
- **One envelope** with the request id in the body and the `X-Request-Id`
  header on every response. A crash is a fixed 500, in debug mode too.
- **`HttpError` and `ValidationFailed`**, for apps that raise their own errors.
- **Per-route limits** through `@guard(...)`, profiles (`upload` is 10 MiB) or
  route names. Every limit is a setting, validated at startup.
- **`read_workbook()` and `workbook_limits()`**, paired with holy-sheet's reader
  limits. A refusal becomes a 413 `workbook_too_large`.
- **Conformance:** passes all 88 rows of fancy-conformance
  `http/request-guards` and the 29 sentence-helper rows of `http/error-envelope`
  (0.48.0), through a raw ASGI app and through FastAPI.
- **A 16-mutation sabotage sweep** in CI.

### Dependencies

- **Runtime:** none.
- **Test only:** `starlette` and `fastapi`, the frameworks this package exists
  to integrate with. They fall under the standing framework carve-out of
  2026-10-06, which needs no approval. `fancy-holy-sheet` is first-party.
  `pytest`, `ruff` and `mypy` are the estate's standard tooling.
