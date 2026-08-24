# NanobarAPI Architecture Rules

NanobarAPI is an opinionated Python ASGI API for building observable, integration-tested systems with a small dependency surface.

## Base Stack

```text
Python ASGI
focusari_asgi (a security-hardened fork of Starlette — see focusari_asgi/.focusari/
  stripdown-refactor-plan.md and focusari_asgi/.focusari/focusari_asgi_agent_context.md
  for its full divergences from upstream Starlette before writing code against it)
OpenAPI
SQLite
SQLAlchemy
```

NanobarAPI wraps `focusari_asgi` the way FastAPI wraps Starlette: `focusari_asgi` is a
dependency, never modified in place for NanobarAPI's sake.

## Request Flow

```text
API route
  → validation
  → controller
  → services
  → rule engines | recommendation engine | vector search | ETL/ELT
    | libraries | external APIs | repositories | models
```

## Architecture Rules

1. **Routes are transport adapters.** They accept the request, invoke validation, call a controller, and translate the controller result into an HTTP response.
2. **Validation is explicit.** Invalid input must not reach a controller or service.
3. **Controllers orchestrate work.** A controller may coordinate multiple services and may invoke agents or workflows.
4. **Services own one responsibility boundary.** A service may use rule engines, recommendation engines, vector search, ETL/ELT components, libraries, external APIs, repositories, and models.
5. **Services must not call other services or controllers.** Cross-service coordination belongs in a controller, agent, or workflow.
6. **Repositories own persistence operations.** Business logic must not be hidden in SQL queries or ORM models.
7. **Every service returns the same result envelope:**

```json
{
  "status": "success",
  "msg": "",
  "result": {
    "type": "object",
    "data": {}
  }
}
```

Allowed statuses are `success`, `error`, and `timeout`. Allowed result types are `object`, `array`, and `map`. Exceptions should be converted into this envelope at the service boundary unless the process must terminate.

## RegressionBrick Shadow Persistence

A **RegressionBrick** is an immutable, versioned record of integration evidence. It may be copied or forked to create new evidence, but it must never be modified in place.

RegressionBricks use a shadow persistence layer separate from operational application data. The layer supports:

- building and replaying integration-test suites;
- comparing behavior across software versions;
- continuous post-production validation of third-party APIs;
- root-cause analysis using approved seed accounts;
- trace and span correlation;
- controlled review through administrative dashboards.

Sensitive fields must be excluded, redacted, tokenized, or encrypted according to capture policy. Access control, audit logging, key management, retention, and deletion policies are also required when the system is operated in a HIPAA-regulated environment. RegressionBricks can support HIPAA-compliant operation; encryption and redaction alone do not establish compliance. See `data-retention-adr.md` for the retention/deletion mechanism (per-data-class policy, evidentiary deletion kept distinct from routine housekeeping) and `data-privacy-adr.md` for what "capture policy" actually means as a structured object, and the fail-closed rule for unclassified fields.

## Nanobar Concept & Dashboard

A **Nanobar** is a stable, long-lived *regression scenario* — distinct from a RegressionBrick,
which is one immutable piece of *captured evidence*. A Nanobar is the thing being regression-
tested (e.g. "checkout times out when the payment provider is slow"); RegressionBricks are the
individual real request/response instances captured over time that support or refute it. This
distinction was previously only implicit in the schema (`nanobars`,`nanobar_regression_bricks`,
the canonical Nanobar JSON example above) — stated explicitly here since the dashboard depends
on it.

**`monitor_target_refs`** (a field already on Nanobar, schema above) is the mapping between a
Nanobar and the real system entry point(s) it monitors: `{"target_type": ..., "stable_name":
...}`. `target_type` spans this project's own request-flow model
(`Route → Validation → Controller → Services → Agents/Workflows`, "Request Flow" above) — expect
`openapi_operation` (an API route, `stable_name` matching the OpenAPI operation id), plus
`controller_span`, `agent`, and `service` as the framework grows enough real usage to instrument
those boundaries. A single Nanobar can reference more than one target (a scenario can span a
route and the service it calls).

**`nanobar_regression_bricks`** (schema above) is how many RegressionBricks accumulate under one
Nanobar over time: each binding row records `match_method` (`exact`/`regex`/`fuzzy`/`trace`/
`manual`) and a `confidence`, so a brick's association with a scenario is itself evidenced, not
assumed. Duplicate bricks (the same request/response pair captured more than once) are prevented
at brick-generation time via `content_hash` equality — `regression_brick_id`'s own `UNIQUE`
`content_hash` column plus a check-before-insert (`nanobar_api.bricks.generate.generate_bricks`)
already do this; a second capture of the same underlying request/response is recognized as a
duplicate and skipped, not silently re-stored, so it's never bound to a Nanobar as if it were new
evidence.

**Dashboard drill-down**, the "controlled review through administrative dashboards" line above,
made concrete: Nanobar Dashboard (grouped/filterable by `monitor_target_refs.target_type` —
api-routes, controller spans, agents, services) → one **Nanobar** → its bound
**RegressionBricks** (via `nanobar_regression_bricks`) → one **brick** → a triage view (review
status: new/reviewed/flagged/promoted) presented as a kanban-style board — reusing the
`focusari_kahnban` project's already-built, already-verified drag-and-drop interaction pattern
for a new, brick-shaped data model, not `focusari_kahnban`'s own Board/List/Card schema, which
isn't a natural fit for what a brick actually is.

## Shadow Execution and Persistence Rerouting

**Shadow-persistence rerouting** is an execution mode in which an authenticated RegressionBrick replay enters through the normal API contract but runs against an environment-approved shadow persistence profile. The request exercises the same route, validation, controller, service, repository, and model code while preventing access to the production write database.

### v0.1 Replay Control Plane

In v0.1, every replay is started by a human through the auto-generated NanobarAPI administrative dashboard. Scheduled, agent-triggered, public, and header-only replay initiation are outside the v0.1 scope.

The dashboard requires authenticated administrative access. Authorization is enforced by the replay control API, not by the browser interface alone. Recommended permissions are:

- `nanobar:view`: view Nanobars, RegressionBricks, traces, spans, and replay results;
- `nanobar:replay`: initiate a replay;
- `nanobar:configure`: manage shadow profiles and external-boundary policies;
- `nanobar:admin`: manage administrative access, retention, and security policies.

The dashboard is generated from registered route and OpenAPI metadata together with stored Nanobars, RegressionBricks, approved shadow profiles, and replay-run records. Before starting a replay, it must show the source RegressionBrick, target system version, selected shadow profile, request summary, and configured behavior for external boundaries.

```text
human administrator
  → generated admin dashboard
  → replay control API
  → create and authorize ReplayRun
  → dispatch internally to Shadow Worker
  → execute against shadow persistence and boundary adapters
  → capture response and trace/span evidence
  → create a new immutable RegressionBrick
  → display status and comparison in the dashboard
```

The minimum v0.1 control-plane routes are:

```text
GET  /_nanobar/admin
POST /_nanobar/replay-runs
GET  /_nanobar/replay-runs/{replay_run_id}
GET  /_nanobar/replay-runs/{replay_run_id}/diff
```

Creating a replay requires an idempotency key so that retries or repeated dashboard actions do not create duplicate executions. Every attempt is audited with the requesting administrator, source RegressionBrick, selected profile, timestamps, status, and trace ID.

### Shadow Worker

A **Shadow Worker** is an isolated NanobarAPI runtime that executes authorized RegressionBrick replays using the same route, validation, controller, service, repository, and model code as the target application without production write access.

The Shadow Worker:

- accepts replay instructions only from the trusted replay control API;
- loads the immutable source RegressionBrick and approved shadow profile;
- reconstructs the recorded request for the selected target system version;
- executes the request against the shadow ASGI application;
- obtains repository sessions only from the `ShadowExecutionContext`;
- redirects external effects to configured sandboxes, sinks, shadow services, or approved seed accounts;
- creates and propagates trace and span context;
- captures the observed response, execution evidence, and blocked side effects;
- creates a new immutable RegressionBrick linked to the source; and
- reports execution status and comparison data to the administrative dashboard.

In post-production, the Shadow Worker must run as a separate process or deployment and must not possess production write credentials. It is not publicly accessible, cannot accept an arbitrary database connection, and cannot be activated by untrusted client headers.

### Internal Worker Header Contract

```http
POST /checkout
Authorization: Bearer <signed-internal-replay-token>
Nanobar-Mode: shadow
Nanobar-Replay-Run-Id: run-1024
Nanobar-Regression-Brick-Id: rbrick-8401
Nanobar-Shadow-Profile: postprod-full
```

These headers are an internal dispatch contract between the replay control API and the Shadow Worker. Public ingress must strip or reject them. The headers identify the replay run, execution mode, immutable source RegressionBrick, and requested profile. They must never contain a database URL, credential, or arbitrary environment name. `ShadowRoutingMiddleware` verifies the signed internal caller, resolves the run and profile from server-side configuration, and creates a `ShadowExecutionContext` before endpoint code runs. Captured authorization headers, cookies, CSRF tokens, and credentials must not be replayed; the Shadow Worker uses an approved seed or shadow identity (`data-privacy-adr.md` §2).

```text
admin dashboard request
  → replay control API
  → ReplayRun creation and authorization
  → internal dispatch
  → ShadowRoutingMiddleware
  → internal authentication
  → ReplayRun, RegressionBrick, and profile resolution
  → isolated shadow ASGI worker
  → route → validation → controller → services
  → shadow repositories and configured boundary adapters
  → response, trace/span evidence, and new RegressionBrick fork
```

### Environment Profile

```json
{
  "profile_id": "postprod-full",
  "execution_isolation": "separate_worker",
  "persistence": {
    "type": "writable_clone",
    "connection_secret_ref": "NANOBAR_POSTPROD_FULL_DB",
    "allow_production_writes": false
  },
  "external_boundaries": {
    "payment_provider": "seed_account",
    "email": "sink",
    "queue": "shadow"
  }
}
```

Supported persistence profiles are:

- `postprod-full`: an isolated writable clone or snapshot of production;
- `postprod-sqlite`: a lightweight SQLite replica or fixture;
- `postprod-readonly`: a read-only replica for non-mutating comparisons.

A conventional production replica is usually read-only. Tests that modify state require an isolated writable clone or SQLite representation.

### Routing and Safety Rules

1. The server resolves profiles from trusted configuration; request headers cannot define connection details.
2. An invalid header, unauthorized caller, missing RegressionBrick, unavailable profile, or failed shadow connection must fail closed. It must never fall back to production persistence.
3. Every repository receives its SQLAlchemy engine and session from the `ShadowExecutionContext`. Shadow executions must not use a global production session.
4. In post-production environments, shadow requests run in a separate worker or deployment that does not receive production write credentials. In-process routing is permitted only in development or controlled test environments.
5. Database isolation is not sufficient by itself. Queues, email, webhooks, object storage, scheduled work, and third-party APIs must use profile-specific adapters, sandboxes, sinks, or approved seed accounts.
6. Each replay receives a trace ID. Its observed request, response, spans, environment, and profile produce a new immutable RegressionBrick fork linked through `forked_from_regression_brick_id`; the source RegressionBrick remains unchanged.
7. The administrative dashboard must show the source brick, generated brick, profile, trace, execution status, external boundaries, and any blocked side effects.
8. Only a human with `nanobar:replay` permission may initiate a replay in v0.1. The initiating identity and authorization decision must be recorded.
9. The replay control API must create a ReplayRun before dispatch. A failed, rejected, or timed-out execution remains visible even when no new RegressionBrick is produced.
10. The Shadow Worker must reject an unknown ReplayRun, mismatched source brick or profile, invalid internal signature, expired instruction, or repeated dispatch outside the idempotency policy.

## Canonical JSON Examples

### Nanobar

```json
{
  "nanobar_id": "nb-checkout-timeout-v182",
  "schema_version": "1.0",
  "system": {
    "name": "checkout-service",
    "version": "1.8.2"
  },
  "regression_scenario_type": "third_party_api_timeout",
  "request_object_id": "req-checkout-timeout",
  "response_object_id": "res-checkout-safe-timeout",
  "regression_weight": 0.86,
  "endpoint_scenario_frequency": {
    "state": "measured",
    "value": 0.031,
    "window": "30d",
    "source": "production_traces"
  },
  "monitor_target_refs": [
    {
      "target_type": "openapi_operation",
      "stable_name": "createOrder"
    }
  ],
  "regression_brick_ids": ["rbrick-8401", "rbrick-8519"],
  "trace_refs": [
    {
      "trace_id": "tr-84ac91",
      "span_ids": ["sp-001", "sp-002"]
    }
  ]
}
```

### RegressionBrick

```json
{
  "regression_brick_id": "rbrick-8401",
  "schema_version": "1.0",
  "brick_version": 1,
  "forked_from_regression_brick_id": null,
  "source_json": {
    "host": "checkout-7f94",
    "project": "checkout-service",
    "url": "https://checkout.internal/checkout",
    "mapping": {
      "folder": "src/checkout",
      "file": "order_service.py",
      "class": "OrderService",
      "function": "create"
    }
  },
  "request_json": {
    "method": "POST",
    "headers": {},
    "payload": {}
  },
  "response_json": {
    "status_code": 503,
    "payload": {
      "error": "payment provider unavailable"
    }
  },
  "trace_refs": [
    {
      "trace_id": "tr-84ac91",
      "span_ids": ["sp-001", "sp-002"]
    }
  ],
  "capture_policy_id": "cp-redacted-production-v3",
  "content_hash": "sha256:8bda...",
  "created_at": "2026-08-24T16:00:00Z",
  "created_by": "nanobarapi"
}
```

Changing recorded evidence creates a new RegressionBrick with a new identifier and content hash. The original row remains unchanged.

## SQLite Table Examples

```sql
CREATE TABLE nanobars (
    nanobar_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    system_name TEXT NOT NULL,
    system_version TEXT NOT NULL,
    regression_scenario_type TEXT NOT NULL,
    request_object_id TEXT NOT NULL,
    response_object_id TEXT NOT NULL,
    regression_weight REAL NOT NULL
        CHECK (regression_weight BETWEEN 0.0 AND 1.0),
    endpoint_scenario_frequency_json TEXT NOT NULL
        CHECK (json_valid(endpoint_scenario_frequency_json)),
    monitor_target_refs_json TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(monitor_target_refs_json)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT NOT NULL
);

CREATE TABLE regression_bricks (
    regression_brick_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    brick_version INTEGER NOT NULL CHECK (brick_version > 0),
    forked_from_regression_brick_id TEXT,
    source_json TEXT NOT NULL CHECK (json_valid(source_json)),
    request_json TEXT NOT NULL CHECK (json_valid(request_json)),
    response_json TEXT NOT NULL CHECK (json_valid(response_json)),
    trace_refs_json TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(trace_refs_json)),
    capture_policy_id TEXT,
    content_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT NOT NULL,
    FOREIGN KEY (forked_from_regression_brick_id)
        REFERENCES regression_bricks(regression_brick_id)
);

CREATE TABLE shadow_execution_runs (
    replay_run_id TEXT PRIMARY KEY,
    source_regression_brick_id TEXT NOT NULL,
    generated_regression_brick_id TEXT,
    shadow_profile_id TEXT NOT NULL,
    target_system_version TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN (
            'requested', 'authorized', 'dispatched', 'running',
            'captured', 'rejected', 'timeout', 'failed', 'cancelled'
        )),
    trace_id TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    completed_at TEXT,
    requested_by TEXT NOT NULL,
    failure_code TEXT,
    failure_message TEXT,
    FOREIGN KEY (source_regression_brick_id)
        REFERENCES regression_bricks(regression_brick_id) ON DELETE RESTRICT,
    FOREIGN KEY (generated_regression_brick_id)
        REFERENCES regression_bricks(regression_brick_id) ON DELETE RESTRICT
);

CREATE TABLE nanobar_regression_bricks (
    nanobar_id TEXT NOT NULL,
    regression_brick_id TEXT NOT NULL,
    match_method TEXT NOT NULL
        CHECK (match_method IN ('exact', 'regex', 'fuzzy', 'trace', 'manual')),
    match_rule TEXT,
    confidence REAL CHECK (confidence BETWEEN 0.0 AND 1.0),
    matcher_version TEXT NOT NULL,
    matched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    matched_by TEXT NOT NULL,
    PRIMARY KEY (nanobar_id, regression_brick_id),
    FOREIGN KEY (nanobar_id)
        REFERENCES nanobars(nanobar_id) ON DELETE CASCADE,
    FOREIGN KEY (regression_brick_id)
        REFERENCES regression_bricks(regression_brick_id) ON DELETE RESTRICT
);

CREATE TRIGGER regression_bricks_are_immutable
BEFORE UPDATE ON regression_bricks
BEGIN
    SELECT RAISE(ABORT, 'RegressionBricks are immutable; fork a new brick');
END;
```

The binding table keeps scenario assignment and matcher provenance separate from the immutable RegressionBrick. Controlled deletion may still be performed through the retention workflow after bindings and audit requirements are resolved — formalized in `data-retention-adr.md` §3.

## ADR: Database Boundaries

**Decision:** Use separate database instances where service responsibility boundaries justify independent concurrency, throughput, lifecycle, or access control. Join tables only when they belong to the same service responsibility boundary.

**Implementation rules:**

- Give each database its own SQLAlchemy engine and session boundary.
- Keep repository access inside the owning service boundary.
- Combine cross-boundary results in controllers, agents, or workflows using identifiers or explicit read models.
- Do not create cross-service database joins or hidden cross-database transactions.

**Tradeoffs:** Multiple SQLite databases can reduce unrelated write contention and isolate failures, but they add migration, backup, consistency, and operational complexity. SQLite still serializes writes within each database file; separation helps only when the responsibility boundaries also separate the workload.

## ADR: Eventbus & Background Processing

**Decision:** Logs, traces, and fire-and-forget service triggers (sending an email or SMS, for
example) are handled by a durable, SQLite-backed eventbus owned by NanobarAPI — not an
in-process-only mechanism, and not an external broker (Redis/RabbitMQ + Celery/RQ/arq, the
standard answer to this problem elsewhere in the Python ecosystem, and what `focusari_asgi`'s
own upstream, FastAPI, points to once its `BackgroundTasks` isn't enough). Full design in
`regression-brick-system-plan.md` §2. This lives in NanobarAPI, not `focusari_asgi`:
`focusari_asgi`'s own scope is explicitly limited to a thin, close-to-upstream ASGI substrate
with no application-architecture or observability code
(`focusari_asgi/.focusari/stripdown-refactor-plan.md` §1) — an opinionated eventbus is squarely
an application-layer concern, the same way FastAPI (not Starlette) owns request validation and
dependency injection.

**Implementation rules:**

- Producers (API request handlers) enqueue via `EventQueueRepository.put(channel, event)` — a
  non-blocking, in-memory, thread-safe `queue.Queue` put. Never call an external service or do
  disk I/O directly from a request handler for anything eventbus-shaped. This holds regardless of
  a channel's storage driver (below) — driver choice never leaks into the producer call site.
- Each channel is configured (`ChannelConfig`: `thread="shared"|"dedicated"`, `priority`,
  `store="local"|"local-multicore"|"distributed"`), not hardcoded. Every `event-thread`,
  regardless of channel, does exactly one job: durably insert into that channel's configured
  `EventStore`. It never calls an external service itself.
- Storage is a pluggable driver behind one `EventStore` interface
  (`insert`/`claim`/`ack`/`fail`) — the same shape as Laravel's per-queue-connection drivers, but
  three tiers rather than two, since "more cores on this box" and "needs a networked,
  multi-machine store" are different problems with different costs. `store="local"` (default) and
  `store="local-multicore"` are the *same* SQLite `EventStore` (`events.db`, dedicated to this
  purpose, separate from domain data, WAL mode) — `local` runs it from one process,
  `local-multicore` runs it from N processes on the same box (real cores, no new infrastructure),
  which requires `busy_timeout`/retry handling on `SQLITE_BUSY` that single-process `local`
  doesn't need. `store="distributed"` swaps in a genuinely different, networked backend for
  channels that must span machines or have outgrown one file's write-serialization even with
  retries; exact backend unresolved (`regression-brick-system-plan.md` §2).
- All channel-specific processing — building RegressionBricks, exporting traces, actually
  sending an email/SMS — happens in an independently-scheduled worker
  (`WorkerConfig.mode="cron"` for low-urgency, analytical channels; `mode="listening"`, a
  persistent tight-polling process, for latency-sensitive service-triggering channels), entirely
  decoupled from the API process's lifecycle. A `local-multicore` or `distributed` channel's
  worker is N processes claiming from the same `EventStore` instead of one; for `mode="cron"`
  those N invocations are supervised by the external scheduler that triggers them, while
  `mode="listening"` still needs its own keep-alive supervision (open, `regression-brick-system-plan.md` §9).
- Channels needing delivery guarantees, not just best-effort, use `attempt_count`/`last_error`
  on the `events` row for retry; purely observational channels don't need to.
- Workers claim rows via a `BEGIN IMMEDIATE`-atomic, time-bounded lease (`claimed_by`/
  `lease_expires_at`), not a bare `WHERE processed_at IS NULL` read — required once
  `local-multicore`/`distributed` make more than one worker process per channel real. A dead
  worker's lease simply expires and another live worker reclaims the row; a `workers` heartbeat
  table makes worker liveness observable.
- A `SupervisorConfig` check/restart/escalate loop (10s default interval) restarts a crashed
  `mode="listening"` worker locally, escalating to a dated log file plus a direct (non-eventbus)
  notification after repeated failures. Covers `local`/`local-multicore`; a `distributed` worker
  on another machine still needs real process-management infrastructure, not this loop (design
  detail and remaining open question: `regression-brick-system-plan.md` §2/§9).

**See also:** `kafka-integration-adr.md` — an open (not decided) exploration of whether Kafka
could serve as a `distributed`-tier backend, the real semantic gaps that raises (row-level
claim/lease/ack vs. Kafka's partition-assigned, offset-committed, immutable-log model), and
whether that support, if ever built, belongs in `nanobar_api` or a separate package.
`scaling-throughput-adr.md` — decides *when* a channel escalates `local` → `local-multicore` →
`distributed` (signal-driven, never capacity-planned ahead of evidence) and catalogs the
tradeoff behind every tuning knob this ADR's implementation rules reference, without inventing
numeric defaults this project has no production traffic yet to justify. `data-retention-adr.md`
— the raw `events` table's retention (routine housekeeping, reuses `WorkerConfig`'s
`mode="cron"` machinery) versus RegressionBricks' retention (evidentiary, admin-gated,
formalizing the "retention workflow" this project's own RegressionBrick section already assumes
exists). `data-privacy-adr.md` — unifies this project's scattered capture/redaction rules into
one `CapturePolicy` object applied at every capture point, plus one new rule: an unclassified
field defaults to redacted, enforced at the same schema-review gate
`regression-brick-system-plan.md` §6 already requires for induced contracts.

**Tradeoffs:** No extra infrastructure to run (SQLite is a file, not a service) versus a
Redis/RabbitMQ-backed system — real operational simplicity, and it also solves a subtlety many
naive Celery/FastAPI integrations skip (enqueuing itself, e.g. `task.delay()`, is a blocking
network call if done carelessly from an async route; this design's in-memory queue keeps that
off the request path too). The three-tier `store` split (design detail:
`regression-brick-system-plan.md` §2) exists so that ceiling isn't an all-or-nothing choice:
`local-multicore` gets a channel real multi-core throughput — more OS processes on the same
dedicated instance, same SQLite file, no new service — before reaching for `distributed`'s actual
operational cost (a networked store to run and operate). Only a channel that must span *machines*,
or has outgrown what one file's writers can absorb even with `SQLITE_BUSY` retry handling, needs
`distributed` (Postgres or a network-native broker) — decided per channel, not forced on the whole
system.

## Core Principle

```text
Keep transport thin.
Keep validation explicit.
Keep orchestration in controllers, agents, and workflows.
Keep service responsibilities isolated.
Keep integration evidence immutable, versioned, and inspectable.
```
