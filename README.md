# NanobarAPI

> **⚠️ Beta prototype — not for production use.** This is early, actively-changing exploratory
> work (v0.1). APIs, schemas, and on-disk data formats break between commits without a migration
> path (see "No users yet" precedent throughout `.focusari/`'s build plans). No users, no
> guarantees, no support. Do not deploy this to serve real traffic or store data you care about.

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

Running the `app/` demo app itself (see "Regression testing with the nanobar dashboard" below
for the full workflow) is a separate `./nanobar` wrapper at the repo root, not under `scripts/`
-- it's specific to this repo's own demo app, not a lint/test/build step:

```shell
$ ./nanobar dev              # run the demo app (uv run nanobar dev server.py --port 8000)
$ ./nanobar reset            # delete every local db under app/ and rebuild schemas fresh --
                              # this project has no migration system, so this is the real fix
                              # for schema drift (e.g. a "no such column" error)
$ ./nanobar migrate          # create any missing tables on an existing db; cannot alter one
                              # that already exists -- see `./nanobar migrate`'s own warning
```

## File structure

```
nanobar_api/                  # repo root
├── nanobar_api/                # the installed framework package (pip-distributable)
│   ├── bricks/                   # RegressionBrick schema/store/generate/binding/verdict
│   ├── regression_brick/          # RegressionBrick ORM model + analysis service (replay/verdict
│   │                                 dispatch, folded together) + framework replay routes
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
├── server.py                    # `nanobar dev`/`uvicorn` entrypoint for app/ -- also what
│                                  # regression-brick replay dispatches back into, in-process
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

**One Nanobar, many RegressionBricks** — a class and its members:

```
┌───────────────────────────────────────────────────┐
│ Nanobar   nb-a1b2c3                               │
│                                                   │
│ identity: (nanobar_type, monitor_target)          │
│   nanobar_type   = "controller-request-response"  │
│   monitor_target = "POST /orders"   (a route ref) │
│                                                   │
│ "a class of tests, not an individual scenario"    │
└───────────────────────────────────────────────────┘
        │
        │  nanobar_regression_bricks
        │  (match_method: exact/regex/fuzzy/trace/manual, + confidence)
        │
        ├──▶ RegressionBrick rb-001   regression_scenario_type: success (201)
        │      content_hash: sha256:9f2a…   brick_version: 1
        │
        ├──▶ RegressionBrick rb-002   regression_scenario_type: invalid_input (400)
        │      content_hash: sha256:71cd…   brick_version: 1
        │
        └──▶ RegressionBrick rb-003   regression_scenario_type: server_error (500)
               content_hash: sha256:0e88…   brick_version: 2
               forked_from_regression_brick_id: rb-003-v1  (new evidence, old one kept)
```

**How a brick connects back to a real trace/span**, concretely, end to end:

1. A request flows through the app. `EventBusTraceMiddleware` (for the HTTP boundary) or
   `@NanobarTelemetry.span(...)`/`.trace(...)` (for arbitrary code) gives it a real OTel-style
   span with a `trace_id`/`span_id`.
2. A boundary worth capturing as evidence tags that span with `NanobarProps(type=...)` —
   `NanobarAPIController.handle()`, `NanobarAPIService.__call__()`,
   `NanobarAPIValidatorGate.__call__()`, and `NanobarORMWrapper`'s DB-boundary hook all do this
   automatically via `capture_layer()`.
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

**The full flow, capture through replay verdict:**

```
┌─────────────────────────────┐
│ a request (or domain event) │
│ flows through the app       │
└─────────────────────────────┘
                │  a capture-worthy boundary tags the span
                │  (capture_layer() / SnapshotMiddleware)
                ▼
┌──────────────────────────┐
│ "snapshot" channel event │
│ (EventQueueRepository)   │
└──────────────────────────┘
                │  TelemetryDrainWorker drains it into
                │  Span/Trace rows (nanobar_api_telemetry.db)
                ▼
┌──────────────────────────────────┐
│ generate_bricks()                │
│ (nanobar_api/bricks/generate.py) │
└──────────────────────────────────┘
                │  explicit batch step, content-hash deduped --
                │  NOT continuous re-inference (the "oracle problem")
                ▼
┌──────────────────────────────────────┐
│ RegressionBrick  (frozen, immutable) │
│ regression_bricks.db                 │
└──────────────────────────────────────┘
                │  self-contained: entry_point, app_box, nanobar_type
                │  all stamped at creation -- zero queries at replay time
                ▼
┌───────────────────────────────┐
│ bind_new_bricks_to_nanobars() │
└───────────────────────────────┘
                │  creates/reuses the Nanobar row + binding
                ▼
┌──────────────────────────────────────────┐
│ bound to a Nanobar                       │
│ (see the Nanobar + bricks diagram above) │
└──────────────────────────────────────────┘

     ── later, on demand: the dashboard's "Run" tab, or an IntegrationTestWorker sweep ──

┌───────────────────────────────────────────────────────┐
│ RegressionBrick (the same frozen, self-contained row) │
└───────────────────────────────────────────────────────┘
                │  brick.entry_point / brick.nanobar_type say
                │  WHERE and HOW to replay it -- no lookup needed
                ▼
┌────────────────────────────────────────────────┐
│ regression_brick_analysis_service.py   ("ACT") │
└────────────────────────────────────────────────┘
                │  dispatch by nanobar_type:
                │   - HTTP-shaped         -> a real httpx2 request
                │   - event-to-subscriber -> trigger-event + poll for
                │                            the resulting span
                ▼
┌──────────────────────────────────────────────────┐
│ this same running app, in-process                │
│ (nanobar-mode: shadow header routes blog writes  │
│ to a disposable replica -- no second process)    │
└──────────────────────────────────────────────────┘
                │  replayed_response
                ▼
┌────────────────────────────────────────────────────────┐
│ evaluate_verdict()   ("ASSERT")                        │
│ "run it, diff it, pass or show the diff. that is all." │
└────────────────────────────────────────────────────────┘
                │
      ┌─────────┴──────────┐
      ▼                    ▼
┌──────┐        ┌───────────────┐
│ PASS │        │ show the diff │
└──────┘        └───────────────┘
```

## Building an app

Every mutating route follows one pipeline, and every layer is a base class you subclass:

```
route -> NanobarAPIValidatorGate -> NanobarAPIController (orchestrates services) -> NanobarAPIService(s)
       -> NanobarAPIRepository / models
```

- **`NanobarAPIValidatorGate`** (`nanobar_api/framework/nanobar_api_validator_gate.py`) — set a
  `controller_cls` class attribute, implement `validate(request) -> T` (raise `ValidationError`,
  or return a parsed dataclass via `nanobar_api.validation.parse`). Invalid input never reaches
  a controller.
- **`NanobarAPIController`** (`nanobar_api/framework/nanobar_api_controller.py`) — implement
  `load_required_services()` (build the services this request needs from ambient app state),
  `run_etl_workflow(validated)` (call the service(s), return the result), and
  `build_response(result)` (shape the HTTP response). A controller may orchestrate more than one
  service.
- **`NanobarAPIService`** (`nanobar_api/framework/nanobar_api_service.py`) — implement
  `handle(request) -> ServiceResult` (`{status: "success"|"error"|"timeout", result: {type,
  data, msg_summary}}`). **Services never call other services or controllers** —
  cross-service coordination belongs in the controller.
- **`NanobarAPIRepository`** (`nanobar_api/framework/nanobar_api_repository.py`) — a thin
  SQLAlchemy `Session` wrapper with optional caching; owns persistence, not business logic.

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
5. **Replay** — a brick's detail page has a **Run** tab: replays the brick's captured
   request/event back against **this same running app**, in-process (no separate server/port to
   run) — real route/validation/controller/service code, but blog-domain writes redirected away
   from live data by a `nanobar-mode: shadow` header (`nanobar_api.shadow`) that routes them onto
   a disposable replica instead, configurable including a genuine remote database via
   `nanobar_api.bricks.shadow_profile.ShadowPersistenceProfile`'s `connection_secret_ref`-style
   env vars (`NANOBAR_BLOG_SHADOW_DB`). Produces a **Verdict**: does the replayed response still
   match what was originally captured? This *is* the regression check — "does this endpoint
   still behave the way this evidence says it should," re-run on demand against current code.
6. **Traces** (`/admin/nanobar/traces`) — browse raw trace/span evidence directly, independent
   of whether it's been turned into a brick yet.

## Status

**Beta v0.1, early — a prototype, not production software.** No stability guarantees on the
API, the on-disk SQLite schemas, or the shadow-deployment/replay mechanisms — any of it can
change or break between commits. There are no real users yet, so migrations for old data are
deliberately skipped rather than built (see recent entries in `.focusari/` for concrete
examples) — don't point this at data or traffic you need to keep.

See `.focusari/complete/archive/nanobarapi-architecture-rules.md` for the full architecture
rules, `.focusari/complete/adr/` for design ADRs (eventbus, scaling, data retention, data
privacy, Kafka integration), and `.focusari/` itself for active build plans.
