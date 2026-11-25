# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Pre-1.0, breaking changes land in MINOR releases.** The version number is not
promising otherwise until 1.0.

## [Unreleased]

## [0.2.0] - 2026-11-24

### Added

- `maxMessageLength`, a limit setting for public error messages (CC-fancy-0063):
  how many code points of the app's own sentence a 4xx message, a validation
  item or a public 5xx keeps. Default 300, so **nothing changes unless you set
  it**. Set it in `limits`, a profile or `@guard(...)` like every other limit; a
  value over 4,000 (`MESSAGE_LENGTH_BOUND`) is refused at startup.
- A per-error override for one long refusal: `HttpError(...,
  max_message_length=n)` and a `ValidationFailed` item's `"maxMessageLength"`.
  It replaces the route's setting for that error or item alone, and counts as
  4,000 above 4,000. A non-positive or non-integer value raises `ValueError`
  where the error is created.
- `render(..., max_message_length=n)`, the route's setting, which the middleware
  and `install()` pass for you.
- Tests: `tests/test_message_length.py`, the shared rows' new `failed` kind and
  `input.limits` in `tests/runner.py`, and four sabotage mutations (20 in all).

### Changed

- CI: re-pin org third-party allowlist to 2614224
- Sanitising is unchanged at any length: control characters are still stripped,
  a message over its limit still truncates with an ellipsis, a crash is still
  the fixed 500 and a non-public 5xx still never shows its message.
- Conformance fixtures 0.64.0: the pin moves to fancy-conformance 0.64.0 (was
  0.62.0), in both CI checkout refs and `PINNED_SUITE_VERSION`, for the shared
  message-length rows `http/request-guards` 0089 to 0098. The table now passes
  98 rows (was 88), 31 of them render rows through FastAPI (was 21). Test-only.
- Conformance fixtures 0.62.0: the `http/error-envelope` and `http/request-guards` rows are now pinned at fancy-conformance 0.62.0 (was 0.48.0), in both CI checkout refs and `PINNED_SUITE_VERSION`. Test-only; no runtime change, nothing to do.

### Fixed

- AGENTS.md no longer says the package is not published to PyPI yet: 0.1.0 has
  been live there since 2026-11-18.

## [0.1.0] - 2026-11-18

First release (CC-fancy-0031).

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

### Changed

- **CI: the lab-certificate gate verifies against the lab's rotated key list.**
  The lab's first signing key is retired: certificates it already issued still
  verify, and nothing new it signs ever can. Consumers do nothing.

- **CI: the third-party gate and the lab-certificate verifier are pinned to
  `Particle-Academy/.github@8c620be`**, the allowlist that records the framework
  test carve-out for this package (Starlette and FastAPI, test-only). Consumers
  do nothing.

- **Lint:** ruff no longer lints the fancy-conformance checkout CI places at
  `.fancy-conformance` (another repo's code), and the `AGENTS.md` examples are
  in ruff's format. No code changed; nothing to do.

- **Docs:** `AGENTS.md` is now the guide to using this package (install, how to find it on the Fancy MCP catalog server once it is listed, API essentials, traps). The contributor notes it held moved to `CONTRIBUTING.md`, and `CLAUDE.md` is now a symlink to it. The README points to the MCP server and to `AGENTS.md`. No code changed; nothing to do.

### Dependencies

- **Runtime:** none.
- **Test only:** `starlette` and `fastapi`, the frameworks this package exists
  to integrate with. They fall under the standing framework carve-out of
  2026-10-06, which needs no approval. `fancy-holy-sheet` is first-party.
  `pytest`, `ruff` and `mypy` are the estate's standard tooling.
