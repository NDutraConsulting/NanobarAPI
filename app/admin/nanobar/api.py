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

from starlette.requests import Request
from starlette.responses import JSONResponse

from nanobar_api import NanobarProps, NanobarTelemetry, error, success
from nanobar_api.admin_auth import ADMIN_SESSION_COOKIE, CSRF_COOKIE_NAME, CSRF_HEADER_NAME, SQLiteSessionBackend
from nanobar_api.bricks import store as bricks_store
from nanobar_api.bricks.replay import replay_brick
from nanobar_api.bricks.schema import RegressionBrick
from nanobar_api.bricks.verdict import evaluate_verdict
from nanobar_api.dynamic_taxonomy import get_or_create_entry, list_entries, split_dynamic_nanobar_type
from nanobar_api.eventbus import store as events_store
from nanobar_api.middleware.trace import SQLiteTraceCaptureToggle, current_span_id, current_trace_id
from nanobar_api.route_manifest import load_route_manifest, write_route_manifest
from nanobar_api.taxonomy import (
    NanobarTypeTaxonomy,
    compute_regression_weight,
    detect_coverage_gaps,
    resolve_taxonomy_entry,
)
from nanobar_api.worker_utils import get_worker_log

from .db import get_connection
from .dynamic_taxonomy_db import get_connection as get_dynamic_taxonomy_connection
from .events_db import get_connection as get_events_connection
from .generate_bricks import generate_dashboard_bricks
from .nanobar_refresh import refresh_nanobars_from_manifest
from .refresh_log import SQLiteRefreshLog
from .replay_app import get_replay_app

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


def _db_path(request: Request) -> str:
    db_path: str = request.app.state.db_path
    return db_path


def _events_db_path(request: Request) -> str:
    db_path: str = request.app.state.events_db_path
    return db_path


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


def _brick_detail_dict(conn: Any, brick: RegressionBrick) -> dict[str, Any]:
    status = bricks_store.get_review_status(conn, brick.regression_brick_id)
    scenario = bricks_store.get_brick_scenario(conn, brick.regression_brick_id)
    tags = bricks_store.get_tags_for_brick(conn, brick.regression_brick_id)
    return {
        **dataclasses.asdict(brick),
        "review_status": dataclasses.asdict(status),
        "scenario": dataclasses.asdict(scenario),
        "tags": tags,
    }


def _record_refresh(request: Request, kind: str, summary: str) -> None:
    refresh_log: SQLiteRefreshLog = request.app.state.refresh_log
    refresh_log.record(kind, last_run_at=datetime.now(UTC).isoformat(), summary=summary)


async def generate_bricks_action(request: Request) -> JSONResponse:
    """POST /api/generate-bricks -> identifies captured event-spans on events.db's "snapshot"
    channel and generates their regression_bricks.db bricks (`generate_bricks()` +
    `bind_new_bricks_to_nanobars()`), the same operation
    `examples/generate_dashboard_bricks.py` runs from a terminal -- exposed here as a
    dashboard button so it doesn't require dropping to a shell. Runs synchronously against real
    SQLite files; for this local-beta single-operator dashboard that's acceptable (the live run
    that motivated this feature: 2,276 events -> 18 bricks in well under a second). Recorded as
    the `"bricks"` refresh cycle on `/admin/nanobar/dashboard/settings`."""
    events_conn = get_events_connection(_events_db_path(request))
    bricks_conn = get_connection(_db_path(request))
    try:
        manifest_entries = load_route_manifest(request.app.state.route_manifest_path)
        result = generate_dashboard_bricks(events_conn, bricks_conn, route_manifest_entries=manifest_entries)
    finally:
        events_conn.close()
        bricks_conn.close()
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
    with no nanobar yet, and backfills/corrects `domain` on existing route-keyed nanobars. See
    `admin/nanobar/nanobar_refresh.py`'s module docstring for why. Recorded as the
    `"nanobars"` refresh cycle on `/admin/nanobar/dashboard/settings`."""
    manifest_entries = load_route_manifest(request.app.state.route_manifest_path)
    conn = get_connection(_db_path(request))
    try:
        result = refresh_nanobars_from_manifest(conn, manifest_entries)
    finally:
        conn.close()
    _record_refresh(
        request,
        "nanobars",
        f"{result.routes_scanned} route(s) scanned -- {result.nanobars_created} nanobar(s) created, "
        f"{result.domains_updated} domain(s) updated",
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
    """GET /api/nanobars?nanobar_type=&domain=&target_type=&q=&page=&page_size= -> envelope
    success with `{"items": [...], "page":, "page_size":, "total":}` -- `q` free-text searches
    the same fields already rendered on the list/detail pages (label/scenario/component/
    domain/type/id), `page_size` defaults to 50.

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
    show up under this app's own domains). `bricks_store.UNMAPPED_DOMAIN` selects nanobars with
    no domain at all (`domain IS NULL` -- never touched by anything domain-aware).

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
    q = request.query_params.get("q") or None
    page, page_size, page_error = _parse_page_params(request)
    if page_error is not None:
        return JSONResponse(error(page_error), status_code=400)

    conn = get_connection(_db_path(request))
    try:
        with _telemetry(request).span("dashboard.nanobars.list", nanobar=NanobarProps(type="api-to-db")):
            nanobars = bricks_store.list_nanobars(
                conn,
                target_type=target_type,
                nanobar_type=nanobar_type,
                domain=domain,
                q=q,
                page=page,
                page_size=page_size,
            )
            total = bricks_store.count_nanobars(
                conn, target_type=target_type, nanobar_type=nanobar_type, domain=domain, q=q
            )
        data = {
            "items": [dataclasses.asdict(n) for n in nanobars],
            "page": page,
            "page_size": page_size,
            "total": total,
        }
        return JSONResponse(success(data))
    finally:
        conn.close()


async def nanobar_detail(request: Request) -> JSONResponse:
    """GET /api/nanobars/{nanobar_id} -> one nanobar's full detail.

    Added alongside pagination (Design Decision D of the search-and-replay-upgrade plan):
    the nanobar detail page previously found its own summary fields by fetching the *entire*
    unpaginated nanobar list and filtering client-side -- silently wrong once `list_nanobars()`
    only returns one page at a time (the target nanobar might not even be on page 1).
    """
    nanobar_id = request.path_params["nanobar_id"]
    conn = get_connection(_db_path(request))
    try:
        nanobar = bricks_store.get_nanobar(conn, nanobar_id)
        if nanobar is None:
            return JSONResponse(error(f"nanobar {nanobar_id!r} not found"), status_code=404)
        return JSONResponse(success(dataclasses.asdict(nanobar)))
    finally:
        conn.close()


async def nanobar_bricks(request: Request) -> JSONResponse:
    """GET /api/nanobars/{nanobar_id}/bricks -> bricks bound to that nanobar, with review status."""
    nanobar_id = request.path_params["nanobar_id"]
    conn = get_connection(_db_path(request))
    try:
        if bricks_store.get_nanobar(conn, nanobar_id) is None:
            return JSONResponse(error(f"nanobar {nanobar_id!r} not found"), status_code=404)
        bricks = bricks_store.get_bricks_for_nanobar(conn, nanobar_id)
        data = [_brick_detail_dict(conn, brick) for brick in bricks]
        return JSONResponse(success(data, type_="array"))
    finally:
        conn.close()


async def brick_detail(request: Request) -> JSONResponse:
    """GET /api/bricks/{brick_id} -> one brick's full detail plus its review status."""
    brick_id = request.path_params["brick_id"]
    conn = get_connection(_db_path(request))
    try:
        brick = bricks_store.get_brick(conn, brick_id)
        if brick is None:
            return JSONResponse(error(f"brick {brick_id!r} not found"), status_code=404)
        return JSONResponse(success(_brick_detail_dict(conn, brick)))
    finally:
        conn.close()


#: `regression_scenario_type` -> the HTTP status code that scenario implies, for capture_layer()
#: -sourced bricks specifically (`_classify_capture_layer_scenario()` in bricks/generate.py's
#: own narrower three-value vocabulary) -- the inverse of that classification, used only to give
#: `evaluate_verdict()`'s status layer something real to compare against (see
#: `_verdict_inputs()` below). Falls back to 200 (the common case) for a brick with no/unknown
#: `regression_scenario_type`.
_CAPTURE_LAYER_SCENARIO_STATUS_CODES = {"success": 200, "invalid_input": 400, "server_error": 500}


def _verdict_inputs(
    brick: RegressionBrick, replayed_response: dict[str, Any]
) -> tuple[RegressionBrick, dict[str, Any]]:
    """`evaluate_verdict()` expects both a brick's own `response` and the freshly
    `replayed_response` in the same `{"status_code", "payload"}` HTTP shape. A
    `capture_layer()`-sourced brick's `response` (`source["route_key"]` stamped) never had that
    shape at all -- it's the controller's raw return value (e.g. a post's own fields directly),
    since `capture_layer()` never observed an HTTP status code in the first place. Comparing it
    against `replayed_response` (always real-HTTP-shaped, from `replay_brick()`'s own
    `TestClient` round trip) unadapted would compare `status_code=None` against a real
    `status_code=200` on *every* capture_layer()-sourced brick, failing the status layer
    unconditionally regardless of whether the replay actually matched.

    Adapts both sides onto the same footing for a capture_layer()-sourced brick: the brick's own
    response wrapped as `{"status_code": <derived from regression_scenario_type>, "payload":
    response}`, and the replayed envelope's inner `result.data` unwrapped to match (the
    equivalent "just the controller's return value" shape). A `SnapshotMiddleware`-sourced brick
    (no `route_key`) is already HTTP-shaped -- passed through unchanged.
    """
    if brick.source.get("route_key") is None:
        return brick, replayed_response

    expected_status_code = _CAPTURE_LAYER_SCENARIO_STATUS_CODES.get(brick.regression_scenario_type or "", 200)
    comparison_brick = dataclasses.replace(
        brick, response={"status_code": expected_status_code, "payload": brick.response}
    )

    replayed_payload = replayed_response.get("payload")
    unwrapped_data = replayed_payload.get("result", {}).get("data") if isinstance(replayed_payload, dict) else None
    comparison_replayed_response = {"status_code": replayed_response.get("status_code"), "payload": unwrapped_data}

    return comparison_brick, comparison_replayed_response


async def replay_brick_action(request: Request) -> JSONResponse:
    """POST /api/bricks/{brick_id}/replay -> hermetically replays this brick against a shadow
    app instance (`replay_app.py` -- shares this app's bricks/events/admin databases, but a
    separate blog database, so replay writes never touch real local data), then evaluates a
    `Verdict` comparing the replay to the brick's originally captured response.

    A synthesized `traceparent` header threads a trace id this handler chooses through the
    replayed request, so every span the replay produces (the HTTP-layer span, and every nested
    controller/validator/service/orm `capture_layer()` span it triggers) shares one real,
    fetchable trace id — returned here as `trace_id`, what the Run tab's "Refresh" button passes
    to `GET .../api/traces/{trace_id}/spans`. One more span, tagged `replay-{original
    nanobar_type}`, is emitted directly under that same trace id before the replay fires — a
    first-class, filterable "this was a replay, not organic traffic" fact, through the same
    nanobar-type include-checkbox mechanism every other trace already uses.
    """
    brick_id = request.path_params["brick_id"]
    conn = get_connection(_db_path(request))
    try:
        brick = bricks_store.get_brick(conn, brick_id)
    finally:
        conn.close()
    if brick is None:
        return JSONResponse(error(f"brick {brick_id!r} not found"), status_code=404)

    shadow_app = get_replay_app(
        db_path=_db_path(request),
        events_db_path=_events_db_path(request),
        app_admin_db_path=request.app.state.app_admin_db_path,
        nanobar_admin_db_path=request.app.state.nanobar_admin_db_path,
        blog_db_path=request.app.state.blog_db_path,
    )

    # A replayed mutating admin route (everything under /admin/app/*) is session-gated, but
    # CapturePolicy never captures authorization/cookie headers in the first place -- nothing
    # to resend. Bootstrapped directly (skipping password verification) since triggering a
    # replay is itself already an authenticated admin action; no separate credential to prove.
    # Every capture_layer()-sourced brick in this app comes from the blog domain (admin/app's
    # mutating routes, or the public, ungated /book-appointment) -- app_admin's backend is the
    # only one ever relevant here; nanobar-admin's own routes never go through capture_layer().
    session_backend = SQLiteSessionBackend(shadow_app.state.app_admin_db_path)
    session = session_backend.create(ttl_seconds=300.0)
    session_backend.authenticate(session.session_id)
    csrf_token = uuid.uuid4().hex

    replay_trace_id = uuid.uuid4().hex
    replay_span_id = uuid.uuid4().hex[:16]

    trace_token = current_trace_id.set(replay_trace_id)
    span_token = current_span_id.set(replay_span_id)
    try:
        nanobar_type = brick.source.get("nanobar_type")
        with shadow_app.state.telemetry.span(
            f"replay.{brick_id}", nanobar=NanobarProps(type=f"replay-{nanobar_type}" if nanobar_type else "replay")
        ):
            pass
    finally:
        current_trace_id.reset(trace_token)
        current_span_id.reset(span_token)

    replayed_response = replay_brick(
        shadow_app,
        brick,
        extra_headers={
            "traceparent": f"00-{replay_trace_id}-{replay_span_id}-01",
            "Cookie": f"{ADMIN_SESSION_COOKIE}={session.session_id}; {CSRF_COOKIE_NAME}={csrf_token}",
            CSRF_HEADER_NAME: csrf_token,
        },
    )
    verdict_brick, verdict_replayed_response = _verdict_inputs(brick, replayed_response)
    verdict = evaluate_verdict(verdict_brick, verdict_replayed_response)

    return JSONResponse(
        success(
            {
                "trace_id": replay_trace_id,
                "replayed_response": replayed_response,
                "verdict": dataclasses.asdict(verdict),
            }
        )
    )


async def set_review_status(request: Request) -> JSONResponse:
    """PATCH/POST /api/bricks/{brick_id}/review-status with body {"status": "..."}.

    Returns envelope success with the updated status, or envelope error (never an unhandled
    500) when the brick doesn't exist, the body isn't valid JSON, the body has no string
    `status` field, or the status value isn't one of `REVIEW_STATUSES`.
    """
    brick_id = request.path_params["brick_id"]
    conn = get_connection(_db_path(request))
    try:
        if bricks_store.get_brick(conn, brick_id) is None:
            return JSONResponse(error(f"brick {brick_id!r} not found"), status_code=404)

        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse(error("request body must be valid JSON"), status_code=400)

        status_value = body.get("status") if isinstance(body, dict) else None
        if not isinstance(status_value, str):
            return JSONResponse(error("request body must include a 'status' string field"), status_code=400)

        try:
            bricks_store.set_review_status(conn, brick_id, status_value, updated_by="dashboard")
        except ValueError as exc:
            return JSONResponse(error(str(exc)), status_code=400)

        updated = bricks_store.get_review_status(conn, brick_id)
        return JSONResponse(success(dataclasses.asdict(updated)))
    finally:
        conn.close()


async def set_brick_scenario(request: Request) -> JSONResponse:
    """PATCH/POST /api/bricks/{brick_id}/scenario with body
    {"regression_scenario_label": "...", "description": "..."}.

    Both fields are optional and independent — an omitted field keeps its current stored
    value (partial update), it is not overwritten with null.
    """
    brick_id = request.path_params["brick_id"]
    conn = get_connection(_db_path(request))
    try:
        if bricks_store.get_brick(conn, brick_id) is None:
            return JSONResponse(error(f"brick {brick_id!r} not found"), status_code=404)

        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse(error("request body must be valid JSON"), status_code=400)
        if not isinstance(body, dict):
            return JSONResponse(error("request body must be a JSON object"), status_code=400)

        current = bricks_store.get_brick_scenario(conn, brick_id)
        label = body.get("regression_scenario_label", current.regression_scenario_label)
        description = body.get("description", current.description)
        if label is not None and not isinstance(label, str):
            return JSONResponse(error("'regression_scenario_label' must be a string"), status_code=400)
        if description is not None and not isinstance(description, str):
            return JSONResponse(error("'description' must be a string"), status_code=400)

        bricks_store.set_brick_scenario(
            conn, brick_id, regression_scenario_label=label, description=description, updated_by="dashboard"
        )
        updated = bricks_store.get_brick_scenario(conn, brick_id)
        return JSONResponse(success(dataclasses.asdict(updated)))
    finally:
        conn.close()


async def add_brick_tag(request: Request) -> JSONResponse:
    """POST /api/bricks/{brick_id}/tags with body {"tag": "..."} -> the brick's updated tag list."""
    brick_id = request.path_params["brick_id"]
    conn = get_connection(_db_path(request))
    try:
        if bricks_store.get_brick(conn, brick_id) is None:
            return JSONResponse(error(f"brick {brick_id!r} not found"), status_code=404)

        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse(error("request body must be valid JSON"), status_code=400)

        tag = body.get("tag") if isinstance(body, dict) else None
        if not isinstance(tag, str) or not tag:
            return JSONResponse(error("request body must include a non-empty 'tag' string field"), status_code=400)

        bricks_store.add_brick_tag(conn, brick_id, tag)
        return JSONResponse(success(bricks_store.get_tags_for_brick(conn, brick_id), type_="array"))
    finally:
        conn.close()


async def remove_brick_tag(request: Request) -> JSONResponse:
    """DELETE /api/bricks/{brick_id}/tags/{tag} -> the brick's updated tag list."""
    brick_id = request.path_params["brick_id"]
    tag = request.path_params["tag"]
    conn = get_connection(_db_path(request))
    try:
        if bricks_store.get_brick(conn, brick_id) is None:
            return JSONResponse(error(f"brick {brick_id!r} not found"), status_code=404)

        bricks_store.remove_brick_tag(conn, brick_id, tag)
        return JSONResponse(success(bricks_store.get_tags_for_brick(conn, brick_id), type_="array"))
    finally:
        conn.close()


async def update_nanobar(request: Request) -> JSONResponse:
    """PATCH /api/nanobars/{nanobar_id} with body
    {"label": "...", "scenario_description": "...", "component_source_description": "...",
    "domain": "...", "criticality": 0.0-1.0}.

    All five fields are optional and independent — an omitted field keeps its current
    stored value (partial update), it is not overwritten with null. `source_info` is not
    editable here — it's auto-derived structured data, not a human-edited field.
    """
    nanobar_id = request.path_params["nanobar_id"]
    conn = get_connection(_db_path(request))
    try:
        current = bricks_store.get_nanobar(conn, nanobar_id)
        if current is None:
            return JSONResponse(error(f"nanobar {nanobar_id!r} not found"), status_code=404)

        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse(error("request body must be valid JSON"), status_code=400)
        if not isinstance(body, dict):
            return JSONResponse(error("request body must be a JSON object"), status_code=400)

        string_fields = {
            "label": body.get("label", current.label),
            "scenario_description": body.get("scenario_description", current.scenario_description),
            "component_source_description": body.get(
                "component_source_description", current.component_source_description
            ),
            "domain": body.get("domain", current.domain),
        }
        for name, value in string_fields.items():
            if value is not None and not isinstance(value, str):
                return JSONResponse(error(f"{name!r} must be a string"), status_code=400)

        criticality = body.get("criticality", current.criticality)
        if (
            not isinstance(criticality, (int, float))
            or isinstance(criticality, bool)
            or not (0.0 <= criticality <= 1.0)
        ):
            return JSONResponse(error("'criticality' must be a number between 0.0 and 1.0"), status_code=400)

        bricks_store.update_nanobar(conn, nanobar_id, criticality=float(criticality), **string_fields)

        if float(criticality) != current.criticality:
            # regression_weight depends on criticality (nanobar_api.taxonomy.
            # compute_regression_weight) -- recompute it too, same "recompute on criticality
            # change" trigger the taxonomy plan's own Phase B calls for.
            bound_bricks = bricks_store.get_bricks_for_nanobar(conn, nanobar_id)
            refreshed = bricks_store.get_nanobar(conn, nanobar_id)
            assert refreshed is not None
            weight = compute_regression_weight(
                refreshed, bound_bricks, _effective_taxonomy(request, refreshed.nanobar_type)
            )
            bricks_store.set_regression_weight(conn, nanobar_id, weight)

        updated = bricks_store.get_nanobar(conn, nanobar_id)
        assert updated is not None
        return JSONResponse(success(dataclasses.asdict(updated)))
    finally:
        conn.close()


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
    conn = get_connection(_db_path(request))
    try:
        nanobar = bricks_store.get_nanobar(conn, nanobar_id)
        if nanobar is None:
            return JSONResponse(error(f"nanobar {nanobar_id!r} not found"), status_code=404)

        effective_taxonomy = _effective_taxonomy(request, nanobar.nanobar_type)

        if resolve_taxonomy_entry(effective_taxonomy, nanobar.nanobar_type) is None:
            events_conn = get_events_connection(_events_db_path(request))
            try:
                span = events_store.find_latest_span_by_nanobar_type(events_conn, "trace", nanobar.nanobar_type)
            finally:
                events_conn.close()
            related_span = (
                None
                if span is None
                else {
                    "trace_id": span.trace_id,
                    "event_id": span.event_id,
                    "name": span.payload.get("name"),
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

        bound_bricks = bricks_store.get_bricks_for_nanobar(conn, nanobar_id)
        gaps = detect_coverage_gaps(nanobar, bound_bricks, effective_taxonomy)
        return JSONResponse(success({"status": "classified", "gaps": gaps}))
    finally:
        conn.close()


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

    conn = get_events_connection(_events_db_path(request))
    try:
        filters: dict[str, Any] = {
            "created_after_ns": created_after_ns,
            "created_before_ns": created_before_ns,
            "nanobar_types": nanobar_types,
            "components": components,
        }
        summaries = events_store.list_trace_ids(conn, channel, page=page, page_size=page_size, **filters)
        total = events_store.count_trace_ids(conn, channel, **filters)
        data = {
            "items": [dataclasses.asdict(s) for s in summaries],
            "page": page,
            "page_size": page_size,
            "total": total,
        }
        return JSONResponse(success(data))
    finally:
        conn.close()


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

    conn = get_events_connection(_events_db_path(request))
    try:
        nanobar_types, components = events_store.get_trace_facets(
            conn, channel, created_after_ns=created_after_ns, created_before_ns=created_before_ns
        )
        return JSONResponse(success({"nanobar_types": nanobar_types, "components": components}))
    finally:
        conn.close()


async def trace_spans(request: Request) -> JSONResponse:
    """GET /api/traces/{trace_id}/spans?channel=... -> that trace's events, ordered by
    monotonic_ns. `channel` defaults to unset (all channels for this trace_id)."""
    trace_id = request.path_params["trace_id"]
    channel = request.query_params.get("channel")
    conn = get_events_connection(_events_db_path(request))
    try:
        events = events_store.get_events_by_trace_id(conn, trace_id, channel=channel)
        if not events:
            return JSONResponse(error(f"trace {trace_id!r} not found"), status_code=404)
        return JSONResponse(success([dataclasses.asdict(e) for e in events], type_="array"))
    finally:
        conn.close()


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
