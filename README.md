# NanobarAPI

An opinionated ASGI API framework wrapping [Starlette](https://github.com/encode/starlette) —
the same relationship FastAPI has to Starlette, but with its own architecture (stdlib
`dataclasses` for validation instead of Pydantic, no `Depends()`-style dependency injection, and
a built-in regression-brick observability/replay system).

**This is the beta v0.1 build**, targeting real upstream Starlette. A separate, later build on
[`focusari_asgi`](https://github.com/focusari/focusari_asgi) (a security-hardened Starlette
fork) is planned but not started — see
`.focusari/complete/archive/nanobarapi-beta-with-starlette-build-plan-and-tasks.md` for why, and
`.focusari/complete/archive/NanobarAPI-build-plan-and-tasks.md` for that future branch's own
plan.

## Install

Not published anywhere yet. For local development:

```shell
$ uv sync
```

## Development

```shell
$ scripts/test    # run the test suite (lint + type-check + tests + coverage)
$ scripts/test-js # run the example dashboard's static JS bundle tests
$ scripts/lint    # auto-format and fix lint issues
$ scripts/check   # lint/type-check only, no auto-fix
```

## File structure

```
nanobar_api/                  # repo root
├── nanobar_api/                # the installed framework package (pip-distributable)
│   ├── bricks/                   # RegressionBrick schema/store/generate/binding/replay/verdict
│   ├── eventbus/                  # durable SQLite-backed pub/sub (events.db)
│   ├── middleware/                 # trace + snapshot capture middleware
│   ├── capture/                     # capture policy -- what gets redacted vs. allow-listed
│   ├── controllers.py, services.py, repositories.py, validator_gate.py, models.py
│   │                                  # base classes for the route -> validator -> controller
│   │                                  # -> service -> repository pipeline every app builds on
│   ├── admin_auth.py                # session/CSRF middleware, admin user store
│   ├── route_manifest.py             # static route-tree scanner (`nanobar routes`)
│   ├── taxonomy.py / dynamic_taxonomy.py   # nanobar_type -> expected-coverage rules
│   ├── telemetry.py                  # NanobarTelemetry: @span/@trace, NanobarProps
│   └── cli.py                         # `nanobar dev` / `nanobar routes`
│
├── app/                         # example application: one real NanobarAPI app, organized
│   │                               # by layer rather than by domain
│   ├── main.py                    # composition root (build_app())
│   ├── admin/
│   │   ├── nanobar/                 # the regression-brick/observability admin dashboard
│   │   └── app/                      # a small blog/booking admin -- the worked pipeline example
│   ├── validators/ controllers/ services/ crud/ models/ libraries/ db/
│   │                               # the blog domain's own layers (one blog_*.py file per layer)
│   ├── api/routes/                 # public-facing route registrations
│   ├── pages/                      # static per-page HTML/CSS/JS bundles (no server-rendered HTML)
│   ├── core/config.py               # cross-cutting path config
│   └── db/*.db, admin/*/data/*.db     # runtime SQLite data, alongside the domain code that owns it
│
├── examples/                    # standalone scripts (seed traffic, generate bricks from a
│   │                               # terminal) -- not part of the framework or the app itself
│   └── ...
├── server.py                    # `nanobar dev`/`uvicorn` entrypoint for app/
├── tests/
└── scripts/                     # test/lint/check/coverage/build
```

## Core concepts: Nanobars, RegressionBricks, and traces

Three distinct things, related in one direction — a Nanobar is a *class*, a RegressionBrick is
one *member* of that class, and a trace/span is the raw *telemetry evidence* a brick was built
from.

- **A Nanobar is a class of tests, not an individual scenario** — the stable identity of one
  component's input/output boundary (e.g. "`POST /orders`'s controller boundary"). Its identity
  is `(nanobar_type, monitor_target)` — `nanobar_type` (`api-response`, `controller-to-db`,
  `service-response`, `worker-{channel}`, ...) says *what kind* of boundary crossing this is;
  `monitor_target_refs` says *which real route/service/controller* it's anchored to.
- **A RegressionBrick is one immutable, versioned example of that class** — an actual captured
  request/response pair. A single Nanobar accumulates many bricks over time: one shaped like a
  success response, one like a 404, one like a validation error — all the same *class* of test,
  each a different *test object*. Bricks are never edited in place; a later replay that produces
  new evidence forks a new brick (`forked_from_regression_brick_id`) instead. Deduped by
  content-hash, so capturing the same request+response twice never creates a duplicate.
- **`nanobar_regression_bricks`** is the join table recording how bricks bind to nanobars
  (`match_method`: `exact`/`regex`/`fuzzy`/`trace`/`manual`, plus a `confidence`) — a brick's
  association with a class is itself evidenced, not assumed.

**How a brick connects back to a real trace/span**, concretely, end to end:

1. A request flows through the app. `EventBusTraceMiddleware` (for the HTTP boundary) or
   `@NanobarTelemetry.span(...)`/`.trace(...)` (for arbitrary code) gives it a real OTel-style
   span with a `trace_id`/`span_id`.
2. A boundary worth capturing as evidence tags that span with `NanobarProps(type=...)` —
   `NanobarController.handle()`, `NanobarService.__call__()`, `NanobarValidatorGate.__call__()`,
   and `NanobarORMWrapper`'s DB-boundary hook all do this automatically via `capture_layer()`.
   Tagging doesn't write a database row synchronously — it just puts a request/response payload
   onto the eventbus's `"snapshot"` channel, carrying that span's `trace_id`/`span_id` along
   with it.
3. **`generate_bricks()`** (`nanobar_api/bricks/generate.py`) is a deliberately *explicit batch
   step* — not a continuous worker — that reads unprocessed `"snapshot"`-channel events and
   turns each into a `RegressionBrick`, whose `trace_refs: [{trace_id, span_ids}]` field points
   straight back at the span that produced it. Explicit and human-reviewable on purpose:
   continuous re-inference would just bless whatever the app currently does, bugs included (the
   "oracle problem").
4. **`bind_new_bricks_to_nanobars()`** then creates or reuses the `Nanobar` row for that
   `(nanobar_type, monitor_target)` identity and records the `nanobar_regression_bricks` binding.

So: **span → tagged snapshot-channel event → RegressionBrick (captures the payload, keeps
`trace_refs` pointing at the span) → bound to a Nanobar (the class it's one example of)**. Given
a brick, you can always get back to the exact trace/span evidence it came from; given a Nanobar,
you can see every brick — and thus every trace — that's ever been evidence for that class of
test.

## Building an app

Every mutating route follows one pipeline, and every layer is a base class you subclass:

```
route -> NanobarValidatorGate -> NanobarController (orchestrates services) -> NanobarService(s)
       -> NanobarRepository / models
```

- **`NanobarValidatorGate`** (`nanobar_api/validator_gate.py`) — set a `controller_cls` class
  attribute, implement `validate(request) -> T` (raise `ValidationError`, or return a parsed
  dataclass via `nanobar_api.validation.parse`). Invalid input never reaches a controller.
- **`NanobarController`** (`nanobar_api/controllers.py`) — implement `load_required_services()`
  (build the services this request needs from ambient app state), `run_etl_workflow(validated)`
  (call the service(s), return the result), and `build_response(result)` (shape the HTTP
  response). A controller may orchestrate more than one service.
- **`NanobarService`** (`nanobar_api/services.py`) — implement `handle(request) ->
  ServiceResult` (`{status: "success"|"error"|"timeout", result: {type, data, msg_summary}}`).
  **Services never call other services or controllers** — cross-service coordination belongs in
  the controller.
- **`NanobarRepository`** (`nanobar_api/repositories.py`) — a thin SQLAlchemy `Session` wrapper
  with optional caching; owns persistence, not business logic.

Every layer's call is automatically captured (see "Core concepts" above) — you don't instrument
anything yourself to get regression evidence; building on these base classes is what wires it
up. `app/`'s blog/booking domain (`app/validators/blog_validator_gateway.py`,
`app/controllers/blog_controller.py`, `app/services/blog_service.py`,
`app/crud/blog_crud.py`, `app/models/blog_model.py`) is the worked example, meant to be read
start to finish.

## Regression testing with the nanobar dashboard

```shell
$ uv run nanobar dev server.py --port 8001
```

Then open `http://127.0.0.1:8001/admin/nanobar/login` (seeded credentials
`admin`/`changeme123`). The workflow:

1. **Traffic happens** — real requests through the app (or `examples/seed_kahnban_bricks.py` for
   synthetic traffic). Every capture-worthy boundary tags a span and lands on the `"snapshot"`
   channel, per "Core concepts" above. The example dashboard runs with tracing **on by default**
   (unlike the framework's own opt-in-only default) since it's a dev/observability tool.
2. **Generate bricks** — click "Generate bricks" on the nanobars list page (or Settings page),
   or run `uv run python examples/generate_dashboard_bricks.py` from a terminal. Turns captured
   snapshot events into `RegressionBrick`s and binds them to `Nanobar`s — safe to run
   repeatedly, dedupes by content-hash.
3. **Browse** `/admin/nanobar/dashboard` — the nanobar list, filterable by `nanobar_type` and by
   `domain`. Open one to see its bound bricks and coverage gaps (a `nanobar_type` with no
   taxonomy entry yet shows as "needs classification" rather than silently passing).
4. **Triage** (`/admin/nanobar/triage`) — a kanban board for reviewing new bricks
   (`new` → `reviewed`/`flagged`/`promoted`).
5. **Replay** — a brick's detail page has a **Run** tab: hermetically replays the brick's
   captured request against a shadow app instance (real route/validation/controller/service
   code, but writes redirected away from live data via
   `nanobar_api.bricks.shadow_profile.ShadowPersistenceProfile` — the shadow target is
   configurable, including a genuine remote database, via `connection_secret_ref`-style env
   vars) and produces a **Verdict**: does the replayed response still match what was originally
   captured? This *is* the regression check — "does this endpoint still behave the way this
   evidence says it should," re-run on demand against current code.
6. **Traces** (`/admin/nanobar/traces`) — browse raw trace/span evidence directly, independent
   of whether it's been turned into a brick yet.

## Status

Beta v0.1, early. See `.focusari/complete/archive/nanobarapi-architecture-rules.md` for the
full architecture rules, `.focusari/complete/adr/` for design ADRs (eventbus, scaling, data
retention, data privacy, Kafka integration), and `.focusari/` itself for active build plans.
