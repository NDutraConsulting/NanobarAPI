"""JSON API routes for the Nanobar Dashboard demo app.

Every response uses this project's envelope contract (`nanobar_api.success` /
`nanobar_api.error`) — see `nanobar_api/envelope.py`.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session
from starlette.requests import Request
from starlette.responses import JSONResponse

from nanobar_api import NanobarProps, NanobarTelemetry, error, success
from nanobar_api.admin_auth import ADMIN_SESSION_COOKIE, CSRF_COOKIE_NAME, CSRF_HEADER_NAME, SQLiteSessionBackend
from nanobar_api.dynamic_taxonomy import get_or_create_entry, list_entries, split_dynamic_nanobar_type
from nanobar_api.eventbus import store as events_store
from nanobar_api.middleware.trace import SQLiteTraceCaptureToggle
from nanobar_api.nanobar.model import nanobar_to_dict
from nanobar_api.nanobar.repository import NanobarRepository
from nanobar_api.regression_brick.model import RegressionBrick
from nanobar_api.regression_brick.regression_brick_analysis_service import ReplayBrickRequest, ReplayBrickService
from nanobar_api.regression_brick.repository import RegressionBrickRepository
from nanobar_api.route_manifest import load_route_manifest, write_route_manifest
from nanobar_api.shadow import SHADOW_MODE_HEADER, SHADOW_MODE_VALUE
from nanobar_api.taxonomy import (
    NanobarTypeTaxonomy,
    detect_coverage_gaps,
    resolve_taxonomy_entry,
)
from nanobar_api.telemetry.span_repository import SpanRepository
from nanobar_api.telemetry.trace_repository import TraceRepository
from nanobar_api.worker_utils import get_worker_log

from .dynamic_taxonomy_db import get_connection as get_dynamic_taxonomy_connection
from .events_db import get_connection as get_events_connection
from .generate_bricks import generate_dashboard_bricks
from .nanobar_refresh import refresh_nanobars_from_manifest
from .refresh_log import SQLiteRefreshLog
from .replay_seeders import seed_for_replay

#: Fixed prefixes this project's own runtime actually produces as dynamically-suffixed
#: nanobar_type values (see nanobar_api/telemetry.py's NanobarProps.type call sites) -- not an
#: open-ended guess at every hyphen in a nanobar_type string. "replay-*" resolves by judging the
#: *original* type it replayed (nanobar_api.taxonomy.resolve_taxonomy_entry already handles
#: that at the pure-function layer); "worker-*" is the one that genuinely needs its own
#: per-(key, key_name) entry, since a worker's expected failure modes are channel-specific.
_DYNAMIC_TAXONOMY_KEYS = ("worker",)

_DEFAULT_PAGE_SIZE = 50


def _parse_page_params(request: Request) -> tuple[int, int, str | None]:
    params = request.query_params
    try:
        page = int(params.get("page", "1"))
    except ValueError:
        return 0, 0, "'page' must be an integer"
    try:
        page_size = int(params.get("page_size", str(_DEFAULT_PAGE_SIZE)))
    except ValueError:
        return 0, 0, "'page_size' must be an integer"
    if page < 1:
        return 0, 0, "'page' must be >= 1"
    if page_size < 1:
        return 0, 0, "'page_size' must be >= 1"
    return page, page_size, None


def _parse_datetime_param(value: str) -> int | None:
    """ISO 8601 date or datetime string -> nanoseconds since epoch. A naive value (no timezone)
    is interpreted in the server's own local timezone, matching `_local_midnight_today_ns()`'s
    own "the operator's own midnight, not UTC" framing -- both need to agree on what "today"
    means for `created_after`/`created_before` to combine sensibly."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return int(parsed.timestamp() * 1_000_000_000)


def _local_midnight_today_ns() -> int:
    midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return int(midnight.timestamp() * 1_000_000_000)


def _resolve_created_after_ns(request: Request) -> tuple[int | None, str | None]:
    """Resolves the traces list's default row-limiting window: an explicit `created_after` wins
    if given; else `since_hours` (the "last N hours" alternative); else, unless `show_all=1` is
    set, defaults to local midnight today -- what actually keeps `list_trace_ids()`'s query
    bounded as `events.db` grows, not just what keeps the rendered list short. `since_hours`
    itself is a convenience preset, not a strict input -- an unparseable value falls back to the
    same default a missing one would, rather than erroring."""
    params = request.query_params
    if "created_after" in params:
        parsed = _parse_datetime_param(params["created_after"])
        if parsed is None:
            return None, "'created_after' must be a valid ISO 8601 date or datetime"
        return parsed, None
    if "since_hours" in params:
        try:
            hours = float(params["since_hours"])
        except ValueError:
            hours = None
        if hours is not None:
            now_ns = int(datetime.now().astimezone().timestamp() * 1_000_000_000)
            return now_ns - int(hours * 3_600 * 1_000_000_000), None
    if params.get("show_all") == "1":
        return None, None
    return _local_midnight_today_ns(), None


def _parse_created_before_ns(request: Request) -> tuple[int | None, str | None]:
    params = request.query_params
    if "created_before" not in params:
        return None, None
    parsed = _parse_datetime_param(params["created_before"])
    if parsed is None:
        return None, "'created_before' must be a valid ISO 8601 date or datetime"
    return parsed, None


def _parse_list_param(request: Request, name: str) -> list[str] | None:
    """Repeatable (`?x=a&x=b`) or comma-separated (`?x=a,b`) -- matches this codebase's existing
    comma-separated convention (e.g. `capture/policy.py`'s allowlists)."""
    values = request.query_params.getlist(name)
    if not values:
        return None
    result = [part.strip() for value in values for part in value.split(",") if part.strip()]
    return result or None


def _brick_to_dict(brick: RegressionBrick) -> dict[str, Any]:
    """Same field set `dataclasses.asdict()` produced against the old `bricks.schema.
    RegressionBrick` frozen dataclass -- built explicitly since the new SQLAlchemy ORM row isn't
    a dataclass and carries extra ORM-only fields (`created_at`) this API contract never
    exposed."""
    return {
        "regression_brick_id": brick.regression_brick_id,
        "schema_version": brick.schema_version,
        "brick_version": brick.brick_version,
        "source": brick.source,
        "request": brick.request,
        "response": brick.response,
        "content_hash": brick.content_hash,
        "created_by": brick.created_by,
        "trace_refs": brick.trace_refs,
        "capture_policy_id": brick.capture_policy_id,
        "forked_from_regression_brick_id": brick.forked_from_regression_brick_id,
        "regression_scenario_type": brick.regression_scenario_type,
    }


def _events_db_path(request: Request) -> str:
    db_path: str = request.app.state.events_db_path
    return db_path


def _telemetry_session(request: Request) -> Session:
    """A fresh session against `nanobar_api_telemetry.db` -- caller closes it, same convention
    as `request.app.state.bricks_session_factory()`'s own call sites in this file."""
    session_factory = request.app.state.telemetry_session_factory
    session: Session = session_factory()
    return session


def _span_to_dict(span: Any) -> dict[str, Any]:
    """Same JSON shape `dataclasses.asdict()` produced against the old raw `Event` -- `event_id`
    is `Span`'s own real primary key (see `nanobar_api/telemetry/model.py`'s `Span` docstring:
    `span_id` is a real but **not unique** correlation column, since `EventBusTraceMiddleware`/
    `SnapshotMiddleware` can both capture the same span under the same `span_id`), so the
    frontend's own use of `event_id` as an opaque unique-id string for DOM anchoring/selection
    (`app/pages/trace/trace-{controller,ui}.js`) still gets a genuinely unique value.
    """
    return {
        "event_id": span.event_id,
        "channel": span.channel,
        "trace_id": span.trace_id,
        "span_id": span.span_id,
        "recorded_at_ns": span.recorded_at_ns,
        "monotonic_ns": span.monotonic_ns,
        "payload": span.payload_json,
    }


def _telemetry(request: Request) -> NanobarTelemetry:
    telemetry: NanobarTelemetry = request.app.state.telemetry
    return telemetry


def _taxonomy(request: Request) -> NanobarTypeTaxonomy:
    taxonomy: NanobarTypeTaxonomy = request.app.state.taxonomy
    return taxonomy


def _effective_taxonomy(request: Request, nanobar_type: str) -> NanobarTypeTaxonomy:
    """The static (`nanobar.types.lock`) taxonomy, plus -- for a recognized dynamic
    `nanobar_type` (currently only `"worker-{channel}"`) with no static entry -- the
    per-`(key, key_name)` entry from `nanobar_type_system.db`, auto-registered (seeded from the
    static generic `"worker"` baseline) the first time this exact channel is ever seen. Returns
    the *unmodified* static taxonomy unchanged whenever `nanobar_type` already has a static
    entry, isn't a recognized dynamic form, or has no generic baseline to seed from -- this
    never mutates `request.app.state.taxonomy` itself, only the dict handed to this one call's
    `compute_regression_weight()`/`detect_coverage_gaps()`.
    """
    static_taxonomy = _taxonomy(request)
    if nanobar_type in static_taxonomy:
        return static_taxonomy

    split = split_dynamic_nanobar_type(nanobar_type, known_keys=_DYNAMIC_TAXONOMY_KEYS)
    if split is None:
        return static_taxonomy
    key, key_name = split

    default_entry = static_taxonomy.get(key)
    if default_entry is None:
        return static_taxonomy

    conn = get_dynamic_taxonomy_connection(request.app.state.nanobar_type_system_db_path)
    try:
        entry, _created = get_or_create_entry(conn, key, key_name, default_entry=default_entry, created_by="dashboard")
    finally:
        conn.close()

    return {**static_taxonomy, nanobar_type: entry}


def _brick_detail_dict(brick_repository: RegressionBrickRepository, brick: RegressionBrick) -> dict[str, Any]:
    status = brick_repository.get_review_status(brick.regression_brick_id)
    scenario = brick_repository.get_scenario(brick.regression_brick_id)
    tags = brick_repository.tags_for(brick.regression_brick_id)
    return {
        **_brick_to_dict(brick),
        "review_status": dataclasses.asdict(status),
        "scenario": dataclasses.asdict(scenario),
        "tags": tags,
    }


def _record_refresh(request: Request, kind: str, summary: str) -> None:
    refresh_log: SQLiteRefreshLog = request.app.state.refresh_log
    refresh_log.record(kind, last_run_at=datetime.now(UTC).isoformat(), summary=summary)


async def generate_bricks_action(request: Request) -> JSONResponse:
    """POST /api/generate-bricks -> identifies captured spans on nanobar_api_telemetry.db's
    "snapshot" channel and generates their regression_bricks.db bricks (`generate_bricks()` +
    `bind_new_bricks_to_nanobars()`), the same operation
    `examples/generate_dashboard_bricks.py` runs from a terminal -- exposed here as a
    dashboard button so it doesn't require dropping to a shell. Runs synchronously against real
    SQLite files; for this local-beta single-operator dashboard that's acceptable (the live run
    that motivated this feature: 2,276 events -> 18 bricks in well under a second). Recorded as
    the `"bricks"` refresh cycle on `/admin/nanobar/dashboard/settings`."""
    telemetry_session = _telemetry_session(request)
    bricks_session = request.app.state.bricks_session_factory()
    try:
        manifest_entries = load_route_manifest(request.app.state.route_manifest_path)
        result = generate_dashboard_bricks(telemetry_session, bricks_session, route_manifest_entries=manifest_entries)
    finally:
        telemetry_session.close()
        bricks_session.close()
    _record_refresh(
        request,
        "bricks",
        f"{result.new_bricks} new brick(s) -- {result.nanobars_created} nanobar(s) created, "
        f"{result.bindings_created} binding(s) created",
    )
    return JSONResponse(success(dataclasses.asdict(result)))


async def refresh_nanobars_action(request: Request) -> JSONResponse:
    """POST /api/refresh/nanobars -> reconciles nanobars against the current route manifest
    (`nanobar.api-routes.json`): creates an `unclassified` placeholder for any declared route
    with no nanobar yet, and backfills/corrects `domain`/`app_box` on existing route-keyed
    nanobars. See `admin/nanobar/nanobar_refresh.py`'s module docstring for why. Recorded as the
    `"nanobars"` refresh cycle on `/admin/nanobar/dashboard/settings`."""
    manifest_entries = load_route_manifest(request.app.state.route_manifest_path)
    session = request.app.state.bricks_session_factory()
    try:
        result = refresh_nanobars_from_manifest(NanobarRepository(session), manifest_entries)
    finally:
        session.close()
    _record_refresh(
        request,
        "nanobars",
        f"{result.routes_scanned} route(s) scanned -- {result.nanobars_created} nanobar(s) created, "
        f"{result.domains_updated} domain(s) updated, {result.app_boxes_updated} app_box(es) updated",
    )
    return JSONResponse(success(dataclasses.asdict(result)))


async def refresh_api_routes_action(request: Request) -> JSONResponse:
    """POST /api/refresh/api-routes -> re-scans `request.app`'s live route tree and rewrites
    `nanobar.api-routes.json` on demand, the same write `build_app()` already does once at
    launch -- for picking up route changes without restarting the process (this demo's own
    route tree is static per-process, but a real consumer of `nanobar_api.route_manifest` could
    register routes dynamically). Recorded as the `"api"` refresh cycle on
    `/admin/nanobar/dashboard/settings`."""
    entries = write_route_manifest(request.app, request.app.state.route_manifest_path)
    domain_count = len({entry.domain for entry in entries})
    data = {"routes_scanned": len(entries), "domains": domain_count}
    _record_refresh(request, "api", f"{len(entries)} route(s) across {domain_count} domain(s)")
    return JSONResponse(success(data))


async def refresh_status(request: Request) -> JSONResponse:
    """GET /api/refresh/status -> `{"api": {...} | null, "nanobars": {...} | null,
    "bricks": {...} | null}`, each either `{"last_run_at": <iso8601>, "summary": <str>}` or
    `null` if that refresh cycle has never run yet."""
    refresh_log: SQLiteRefreshLog = request.app.state.refresh_log
    recorded = refresh_log.get_all()
    data = {kind: recorded.get(kind) for kind in ("api", "nanobars", "bricks")}
    return JSONResponse(success(data))


async def list_nanobars(request: Request) -> JSONResponse:
    """GET /api/nanobars?nanobar_type=&domain=&app_box=&target_type=&q=&page=&page_size= ->
    envelope success with `{"items": [...], "page":, "page_size":, "total":}` -- `q` free-text
    searches the same fields already rendered on the list/detail pages (label/scenario/component/
    domain/app_box/type/id), `page_size` defaults to 50.

    `nanobar_type` is the dashboard's real navigation axis -- the taxonomy layer type
    (`validator-request-response`, `orm-request-response`, ...; see `nanobar_api/taxonomy.py`)
    that groups regression-brick spans by application layer, which is what the list/detail pages
    actually group and filter by. `target_type` (`monitor_target_refs[].target_type`, e.g. an
    HTTP route) is kept as a lower-level, independent filter for API callers that want it, but
    this demo's own UI never sends it -- every nanobar this demo produces shares the same
    `target_type` ("route"), so it carries no navigational signal here.

    `domain` is the *application* this nanobar belongs to -- the route manifest's Mount prefix
    (`""` for a root-level route, `"admin/app"`, `"admin/nanobar"`, ...; see
    `nanobar_api/route_manifest.py`), stamped by `get_or_create_nanobar_by_route_key`/backfilled
    by "Nanobar refresh". This is what actually separates this demo's own nanobars from an
    unrelated traffic source seeded into the same database (e.g. `seed_kahnban_bricks.py`,
    whose nanobars carry its own domain values like `"boards"`/`"lists"`/`"cards"` and never
    show up under this app's own domains). `nanobar_api.nanobar.repository.UNMAPPED_DOMAIN` selects nanobars with
    no domain at all (`domain IS NULL` -- never touched by anything domain-aware).

    `app_box` is `domain`'s additive sibling (per `.focusari/appbox-plan-with-tasks.md`) -- a
    purely structural classification (`"admin/app"`, `"admin/nanobar"`, `"api"`, `"workers"`),
    independently filterable, never a replacement for `domain`.
    `nanobar_api.nanobar.repository.UNMAPPED_APP_BOX` is `UNMAPPED_DOMAIN`'s exact counterpart.

    The DB query is wrapped in its own nested `NanobarTelemetry` span — the first real
    "api-to-db" boundary in this project, nested under `EventBusTraceMiddleware`'s HTTP-layer
    span for this same request (both share one `EventQueueRepository`, see `app.py`). A
    `NanobarTelemetry.span(...)` is used here as a context manager rather than `@decorator`
    because `telemetry` is only available per-request (`request.app.state`), constructed after
    this module is imported — the decorator form needs the instance to exist at import time,
    which doesn't fit this call site.
    """
    target_type = request.query_params.get("target_type")
    nanobar_type = request.query_params.get("nanobar_type")
    domain = request.query_params.get("domain")
    app_box = request.query_params.get("app_box")
    q = request.query_params.get("q") or None
    page, page_size, page_error = _parse_page_params(request)
    if page_error is not None:
        return JSONResponse(error(page_error), status_code=400)

    session = request.app.state.bricks_session_factory()
    try:
        nanobar_repository = NanobarRepository(session)
        with _telemetry(request).span("dashboard.nanobars.list", nanobar=NanobarProps(type="api-to-db")):
            nanobars = nanobar_repository.list_nanobars(
                target_type=target_type,
                nanobar_type=nanobar_type,
                domain=domain,
                app_box=app_box,
                q=q,
                page=page,
                page_size=page_size,
            )
            total = nanobar_repository.count_nanobars(
                target_type=target_type, nanobar_type=nanobar_type, domain=domain, app_box=app_box, q=q
            )
        data = {
            "items": [nanobar_to_dict(n) for n in nanobars],
            "page": page,
            "page_size": page_size,
            "total": total,
        }
        return JSONResponse(success(data))
    finally:
        session.close()


async def nanobar_detail(request: Request) -> JSONResponse:
    """GET /api/nanobars/{nanobar_id} -> one nanobar's full detail.

    Added alongside pagination (Design Decision D of the search-and-replay-upgrade plan):
    the nanobar detail page previously found its own summary fields by fetching the *entire*
    unpaginated nanobar list and filtering client-side -- silently wrong once `list_nanobars()`
    only returns one page at a time (the target nanobar might not even be on page 1).
    """
    nanobar_id = request.path_params["nanobar_id"]
    session = request.app.state.bricks_session_factory()
    try:
        nanobar = NanobarRepository(session).get(nanobar_id)
        if nanobar is None:
            return JSONResponse(error(f"nanobar {nanobar_id!r} not found"), status_code=404)
        return JSONResponse(success(nanobar_to_dict(nanobar)))
    finally:
        session.close()


async def nanobar_bricks(request: Request) -> JSONResponse:
    """GET /api/nanobars/{nanobar_id}/bricks -> bricks bound to that nanobar, with review status."""
    nanobar_id = request.path_params["nanobar_id"]
    session = request.app.state.bricks_session_factory()
    try:
        nanobar_repository = NanobarRepository(session)
        if nanobar_repository.get(nanobar_id) is None:
            return JSONResponse(error(f"nanobar {nanobar_id!r} not found"), status_code=404)
        bricks = nanobar_repository.bricks_for(nanobar_id)
        brick_repository = RegressionBrickRepository(session)
        data = [_brick_detail_dict(brick_repository, brick) for brick in bricks]
        return JSONResponse(success(data, type_="array"))
    finally:
        session.close()


async def brick_detail(request: Request) -> JSONResponse:
    """GET /api/bricks/{brick_id} -> one brick's full detail plus its review status."""
    brick_id = request.path_params["brick_id"]
    session = request.app.state.bricks_session_factory()
    try:
        brick_repository = RegressionBrickRepository(session)
        brick = brick_repository.get(brick_id)
        if brick is None:
            return JSONResponse(error(f"brick {brick_id!r} not found"), status_code=404)
        return JSONResponse(success(_brick_detail_dict(brick_repository, brick)))
    finally:
        session.close()


async def replay_brick_action(request: Request) -> JSONResponse:
    """POST /api/bricks/{brick_id}/replay -> replays this brick against **this same running
    app**, over an in-process `httpx2.Client` (`request.app.state.replay_client`, a
    `starlette.testclient.TestClient` bound to this app -- see `app/main.py`'s own docstring),
    then evaluates a `Verdict` comparing the replay to the brick's originally captured response.

    Isolation from live data comes from the `nanobar-mode: shadow` header attached below, not
    from talking to a separately-run deployment -- `nanobar_api.shadow.ShadowModeMiddleware`
    (mounted on this very app) reads it and sets `current_shadow_mode` for the duration of the
    replayed request, which `app.db.blog_session.resolve_session_factory()` checks to route the
    replay's own blog-domain reads/writes onto `blog_shadow_session_factory` instead of the live
    one. Replaces the earlier two designs this route went through in turn: the original
    per-request, in-process `replay_app.py` shadow app (whose ASGI lifespan was never entered, so
    its background workers never ran), and then a genuinely separate, persistently-running
    `shadow_server.py` process on its own port (which fixed that, at the cost of a second process
    to keep running) -- collapsing back to one process, now that the header-flag mechanism above
    makes a second app instance unnecessary for lifespan/background-worker correctness too.

    A synthesized `traceparent` header threads a trace id this handler chooses through the
    replayed request -- `EventBusTraceMiddleware` (this same app's own, already running)
    extracts it (W3C trace context propagation, `propagate.extract()`), so every span the replay
    produces (the HTTP-layer span, and every nested controller/validator/service/orm
    `capture_layer()` span it triggers) shares this same, real, fetchable trace id — returned
    here as `trace_id`, what the Run tab's "Refresh" button passes to
    `GET .../api/traces/{trace_id}/spans`. Genuinely fetchable now for the same reason isolation
    works: this app's own `TelemetryDrainWorker` is already running for real (it's the live
    server, not a per-request app whose lifespan is never entered).
    """
    brick_id = request.path_params["brick_id"]
    session = request.app.state.bricks_session_factory()
    try:
        brick = RegressionBrickRepository(session).get(brick_id)
    finally:
        session.close()
    if brick is None:
        return JSONResponse(error(f"brick {brick_id!r} not found"), status_code=404)

    # Ensures the shadow db has whatever pre-existing row this brick's replay depends on (e.g.
    # an update-post brick needs a Post with its own post_id to already exist) -- the shadow
    # replica is deliberately empty otherwise, so a replay targeting a resource that only exists
    # in live data would 404 through no fault of the app's own code. No-ops for any route_key
    # with no registered seeder. `seed_teardown`, when not None, removes whatever this call just
    # seeded once the replay is done (below, in this function's own `finally`) -- without it, the
    # shadow db would accumulate one permanent row per distinct brick ever replayed. See
    # app/admin/nanobar/replay_seeders.py's own module docstring.
    seed_teardown = seed_for_replay(brick, request.app.state.blog_shadow_session_factory)
    try:
        # A replayed mutating admin route (everything under /admin/app/*) is session-gated, but
        # CapturePolicy never captures authorization/cookie headers in the first place -- nothing
        # to resend. Bootstrapped directly (skipping password verification) since triggering a
        # replay is itself already an authenticated admin action; no separate credential to
        # prove. Same app_admin_db_path the replayed request will itself be checked against
        # (it's the same running app now, not a separate shadow instance with its own copy) -- a
        # session created here is valid there by construction.
        session_backend = SQLiteSessionBackend(request.app.state.app_admin_db_path)
        admin_session = session_backend.create(ttl_seconds=300.0)
        session_backend.authenticate(admin_session.session_id)
        csrf_token = uuid.uuid4().hex

        replay_trace_id = uuid.uuid4().hex
        replay_span_id = uuid.uuid4().hex[:16]

        analysis_session = request.app.state.bricks_session_factory()
        try:
            analysis_service = ReplayBrickService(
                request.app.state.telemetry,
                RegressionBrickRepository(analysis_session),
                request.app.state.replay_client,
            )
            result = analysis_service(
                ReplayBrickRequest(
                    regression_brick_id=brick_id,
                    extra_headers={
                        "traceparent": f"00-{replay_trace_id}-{replay_span_id}-01",
                        "Cookie": f"{ADMIN_SESSION_COOKIE}={admin_session.session_id}; {CSRF_COOKIE_NAME}={csrf_token}",
                        CSRF_HEADER_NAME: csrf_token,
                        SHADOW_MODE_HEADER.decode("latin-1"): SHADOW_MODE_VALUE.decode("latin-1"),
                    },
                )
            )
        finally:
            analysis_session.close()
    finally:
        # Runs whether the replay above succeeded, failed, or raised -- a failed replay must not
        # leak its seeded row any more than a successful one does.
        if seed_teardown is not None:
            seed_teardown()

    if result.status == "error" or result.result.data is None:
        return JSONResponse(error(result.result.msg_summary), status_code=400)

    return JSONResponse(
        success(
            {
                "trace_id": replay_trace_id,
                **result.result.data,
            }
        )
    )


async def nanobar_coverage_gaps(request: Request) -> JSONResponse:
    """GET /api/nanobars/{nanobar_id}/coverage-gaps -> envelope success with
    `{"status": "classified", "gaps": [...]}` -- required scenario types this nanobar's
    `nanobar_type` expects but has no bound brick for, the "missing coverage" section on the
    nanobar detail page -- or, for a `nanobar_type` this app's taxonomy genuinely can't resolve
    (no static entry, no recognized dynamic form -- see `nanobar_api.taxonomy.
    resolve_taxonomy_entry`), `{"status": "needs_classification", "gaps": [],
    "related_span": {...} | null}`.

    Before this distinction existed, an unresolvable type and a fully-covered one returned the
    *same* empty list -- silently indistinguishable, even though one means "nothing missing"
    and the other means "never actually measured." `related_span` is real evidence to act on
    instead of a bare name with nothing to investigate: the most recent captured span actually
    tagged with this `nanobar_type` (`GET .../traces/{trace_id}` with `#span-{event_id}`
    selects it directly on the trace detail page), or `null` if none has ever been captured.
    """
    nanobar_id = request.path_params["nanobar_id"]
    session = request.app.state.bricks_session_factory()
    try:
        nanobar_repository = NanobarRepository(session)
        nanobar = nanobar_repository.get(nanobar_id)
        if nanobar is None:
            return JSONResponse(error(f"nanobar {nanobar_id!r} not found"), status_code=404)

        effective_taxonomy = _effective_taxonomy(request, nanobar.nanobar_type)

        if resolve_taxonomy_entry(effective_taxonomy, nanobar.nanobar_type) is None:
            telemetry_session = _telemetry_session(request)
            try:
                span = SpanRepository(telemetry_session).find_latest_by_nanobar_type("trace", nanobar.nanobar_type)
            finally:
                telemetry_session.close()
            related_span = (
                None
                if span is None
                else {
                    "trace_id": span.trace_id,
                    "event_id": span.event_id,
                    "name": span.payload_json.get("name"),
                    "recorded_at_ns": span.recorded_at_ns,
                }
            )
            return JSONResponse(
                success(
                    {
                        "status": "needs_classification",
                        "nanobar_type": nanobar.nanobar_type,
                        "gaps": [],
                        "related_span": related_span,
                    }
                )
            )

        bound_bricks = nanobar_repository.bricks_for(nanobar_id)
        gaps = detect_coverage_gaps(nanobar, bound_bricks, effective_taxonomy)
        return JSONResponse(success({"status": "classified", "gaps": gaps}))
    finally:
        session.close()


async def list_dynamic_taxonomy_entries(request: Request) -> JSONResponse:
    """GET /api/dynamic-taxonomy?key= -> envelope success with every runtime-registered
    `(key, key_name)` entry from `nanobar_type_system.db`, optionally filtered to one `key` --
    the auditability `nanobar_api/dynamic_taxonomy.py`'s own module docstring names as the
    point of a dedicated SQLite file over an in-memory dict: every dynamic `nanobar_type` this
    app has ever actually seen (e.g. one row per worker channel), inspectable directly."""
    key = request.query_params.get("key")
    conn = get_dynamic_taxonomy_connection(request.app.state.nanobar_type_system_db_path)
    try:
        entries = list_entries(conn, key=key)
        data = [
            {
                "key": entry_key,
                "key_name": key_name,
                "nanobar_type": f"{entry_key}-{key_name}",
                "expected_scenarios": {
                    name: dataclasses.asdict(scenario) for name, scenario in entry.expected_scenarios.items()
                },
            }
            for entry_key, key_name, entry in entries
        ]
        return JSONResponse(success(data, type_="array"))
    finally:
        conn.close()


#: A worker with no heartbeat in this long is considered stale by default -- generous relative
#: to every real config's own `poll_interval_s` (1.0s default, still seconds-scale even when
#: overridden), so a worker between polls never flickers stale/healthy under normal operation.
_DEFAULT_STALE_SECONDS = 90.0


async def list_workers(request: Request) -> JSONResponse:
    """GET /api/workers?stale_seconds= -> envelope success with every registered worker
    (`eventbus.store.list_workers()`'s own liveness + configuration snapshot -- channels, mode,
    schedule, poll_interval_s, claim_limit, lease_seconds, started_at, last_heartbeat_at), each
    with a computed `is_stale` flag (no heartbeat within `stale_seconds`, default 90). The
    "reviewing configurations and monitoring lifecycles" list -- what's actually running, from
    what each worker's own code registered, not anything hand-maintained."""
    try:
        stale_seconds = float(request.query_params.get("stale_seconds", str(_DEFAULT_STALE_SECONDS)))
    except ValueError:
        return JSONResponse(error("'stale_seconds' must be a number"), status_code=400)

    conn = get_events_connection(_events_db_path(request))
    try:
        workers = events_store.list_workers(conn)
        stale_ids = set(events_store.list_stale_workers(conn, stale_seconds))
        data = [{**dataclasses.asdict(worker), "is_stale": worker.worker_id in stale_ids} for worker in workers]
        return JSONResponse(success(data, type_="array"))
    finally:
        conn.close()


async def worker_log(request: Request) -> JSONResponse:
    """GET /api/workers/{worker_id}/log?limit= -> envelope success with that worker's recent
    failure log entries (`worker_utils.get_worker_log()`), most recent first."""
    worker_id = request.path_params["worker_id"]
    try:
        limit = int(request.query_params.get("limit", "100"))
    except ValueError:
        return JSONResponse(error("'limit' must be an integer"), status_code=400)

    conn = get_events_connection(_events_db_path(request))
    try:
        entries = get_worker_log(conn, worker_id, limit)
        return JSONResponse(success([dataclasses.asdict(entry) for entry in entries], type_="array"))
    finally:
        conn.close()


async def list_traces(request: Request) -> JSONResponse:
    """GET /api/traces?channel=trace&page=&page_size=&created_after=&created_before=&
    since_hours=&show_all=1&nanobar_types=&components= -> envelope success with
    `{"items": [...], "page":, "page_size":, "total":}`, most-recently-completed first.

    Defaults to today (local midnight) when none of `created_after`/`since_hours`/`show_all=1`
    is given — this is what keeps `list_trace_ids()`'s query bounded as `events.db` grows, not
    just what keeps the rendered list short (see the search-and-replay-upgrade plan doc).
    `nanobar_types`/`components` are each either repeatable or comma-separated.
    """
    channel = request.query_params.get("channel", "trace")

    page, page_size, page_error = _parse_page_params(request)
    if page_error is not None:
        return JSONResponse(error(page_error), status_code=400)

    created_after_ns, after_error = _resolve_created_after_ns(request)
    if after_error is not None:
        return JSONResponse(error(after_error), status_code=400)

    created_before_ns, before_error = _parse_created_before_ns(request)
    if before_error is not None:
        return JSONResponse(error(before_error), status_code=400)

    nanobar_types = _parse_list_param(request, "nanobar_types")
    components = _parse_list_param(request, "components")

    telemetry_session = _telemetry_session(request)
    try:
        trace_repository = TraceRepository(telemetry_session)
        filters: dict[str, Any] = {
            "created_after_ns": created_after_ns,
            "created_before_ns": created_before_ns,
            "nanobar_types": nanobar_types,
            "components": components,
        }
        summaries = trace_repository.list_trace_summaries(channel, page=page, page_size=page_size, **filters)
        total = trace_repository.count_trace_summaries(channel, **filters)
        data = {
            "items": [dataclasses.asdict(s) for s in summaries],
            "page": page,
            "page_size": page_size,
            "total": total,
        }
        return JSONResponse(success(data))
    finally:
        telemetry_session.close()


async def trace_facets(request: Request) -> JSONResponse:
    """GET /api/traces/facets?channel=trace&created_after=&created_before=&since_hours=&
    show_all=1 -> envelope success with `{"nanobar_types": [...], "components": [...]}` --
    distinct values actually present (within the same default/explicit date window
    `list_traces` uses), for the traces list's filter-panel checkboxes."""
    channel = request.query_params.get("channel", "trace")

    created_after_ns, after_error = _resolve_created_after_ns(request)
    if after_error is not None:
        return JSONResponse(error(after_error), status_code=400)

    created_before_ns, before_error = _parse_created_before_ns(request)
    if before_error is not None:
        return JSONResponse(error(before_error), status_code=400)

    telemetry_session = _telemetry_session(request)
    try:
        nanobar_types, components = SpanRepository(telemetry_session).distinct_facets(
            channel, created_after_ns=created_after_ns, created_before_ns=created_before_ns
        )
        return JSONResponse(success({"nanobar_types": nanobar_types, "components": components}))
    finally:
        telemetry_session.close()


async def trace_spans(request: Request) -> JSONResponse:
    """GET /api/traces/{trace_id}/spans?channel=... -> that trace's spans, ordered by
    monotonic_ns. `channel` defaults to unset (all channels for this trace_id)."""
    trace_id = request.path_params["trace_id"]
    channel = request.query_params.get("channel")
    telemetry_session = _telemetry_session(request)
    try:
        spans = SpanRepository(telemetry_session).list_by_trace_id(trace_id, channel=channel)
        if not spans:
            return JSONResponse(error(f"trace {trace_id!r} not found"), status_code=404)
        return JSONResponse(success([_span_to_dict(s) for s in spans], type_="array"))
    finally:
        telemetry_session.close()


async def get_settings(request: Request) -> JSONResponse:
    """GET /api/settings -> envelope success with `{"tracing_enabled": bool}` -- the current
    value of the runtime `SQLiteTraceCaptureToggle`, backing the
    /admin/nanobar/dashboard/settings page's toggle."""
    toggle: SQLiteTraceCaptureToggle = request.app.state.trace_capture_toggle
    return JSONResponse(success({"tracing_enabled": toggle.is_enabled()}))


async def update_settings(request: Request) -> JSONResponse:
    """POST /api/settings with body {"tracing_enabled": bool} -> flips the runtime trace-capture
    toggle and returns the new value. This is the actual on/off control for span capture in this
    app -- unlike the OTel tracer provider (`configure_tracing()`, a process-wide global that can
    only ever move from no-op to real, never back), this toggle is a real, reversible switch
    `EventBusTraceMiddleware` checks fresh on every request."""
    toggle: SQLiteTraceCaptureToggle = request.app.state.trace_capture_toggle
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse(error("request body must be valid JSON"), status_code=400)
    if not isinstance(body, dict) or not isinstance(body.get("tracing_enabled"), bool):
        return JSONResponse(error("'tracing_enabled' must be a boolean"), status_code=400)

    toggle.set_enabled(body["tracing_enabled"])
    return JSONResponse(success({"tracing_enabled": toggle.is_enabled()}))
