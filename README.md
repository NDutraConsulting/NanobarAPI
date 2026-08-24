# NanobarAPI

An opinionated ASGI API framework wrapping [Starlette](https://github.com/encode/starlette) —
the same relationship FastAPI has to Starlette, but with its own architecture (stdlib
`dataclasses` for validation instead of Pydantic, no `Depends()`-style dependency injection, and
a built-in regression-brick observability/replay system).

**This is the beta v0.1 build**, targeting real upstream Starlette. A separate, later build on
[`focusari_asgi`](https://github.com/focusari/focusari_asgi) (a security-hardened Starlette
fork) is planned but not started — see `.focusari/nanobarapi-beta-with-starlette-build-plan-and-tasks.md`
for why, and `.focusari/NanobarAPI-build-plan-and-tasks.md` for that future branch's own plan.

## Install

Not published anywhere yet. For local development:

```shell
$ uv sync
```

## Development

```shell
$ scripts/test    # run the test suite (lint + type-check + tests + coverage)
$ scripts/lint    # auto-format and fix lint issues
$ scripts/check   # lint/type-check only, no auto-fix
```

## Status

Beta v0.1, early. See `.focusari/` for the full architecture rules, build plan, and design ADRs
(eventbus, scaling, data retention, data privacy, Kafka integration).
