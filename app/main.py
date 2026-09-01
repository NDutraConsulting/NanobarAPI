"""Builds the Nanobar Dashboard demo app (a NanobarAPI application)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx2
from starlette.middleware import Middleware
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from starlette.testclient import TestClient

from app.admin.app import login_routes as admin_app_login_routes, routes as admin_app_routes
from app.admin.app.auth_db import resolve_db_path as resolve_app_admin_db_path
from app.admin.nanobar import login_routes as admin_nanobar_login_routes, routes as admin_nanobar_routes
from app.admin.nanobar.auth_db import resolve_db_path as resolve_nanobar_admin_db_path
from app.admin.nanobar.db import resolve_db_path
from app.admin.nanobar.dynamic_taxonomy_db import resolve_db_path as resolve_nanobar_type_system_db_path
from app.admin.nanobar.events_db import resolve_db_path as resolve_events_db_path
from app.admin.nanobar.refresh_log import SQLiteRefreshLog
from app.admin.nanobar.replay_seeders import sweep_stale_shadow_seeds
from app.admin.nanobar.shadow_deployment import BLOG_SHADOW_PROFILE
from app.admin.nanobar.telemetry_db import resolve_db_path as resolve_telemetry_db_path
from app.api.routes import blog as blog_public_routes
from app.core.config import WEB_DIR, resolve_route_manifest_path
from app.db.blog_session import (
    build_session_factory as build_blog_session_factory,
    resolve_db_path as resolve_blog_db_path,
)
from app.services.blog_notification_callback import AppointmentNotificationCallback
from app.services.blog_publisher_worker import PostPublisherThread, post_publisher_lifespan
from nanobar_api import NanobarAPI, NanobarTelemetry
from nanobar_api.admin_auth import SQLiteAdminUserStore, SQLiteSessionBackend
from nanobar_api.bricks.shadow_profile import resolve_shadow_connection
from nanobar_api.eventbus import ChannelConfig, EventQueueRepository
from nanobar_api.eventbus.dispatch import NanobarEventBus, event_bus_lifespan
from nanobar_api.middleware.trace import EventBusTraceMiddleware, SQLiteTraceCaptureToggle, configure_tracing
from nanobar_api.persistence import build_session_factory as build_bricks_session_factory
from nanobar_api.regression_brick.replay_routes import build_replay_routes
from nanobar_api.route_manifest import write_route_manifest
from nanobar_api.shadow import ShadowModeMiddleware
from nanobar_api.taxonomy import load_taxonomy
from nanobar_api.telemetry.persistence import build_session_factory as build_telemetry_session_factory
from nanobar_api.telemetry.telemetry_drain_worker import telemetry_drain_worker_lifespan


def build_app(
    db_path: str | None = None,
    events_db_path: str | None = None,
    telemetry_db_path: str | None = None,
    app_admin_db_path: str | None = None,
    nanobar_admin_db_path: str | None = None,
    blog_db_path: str | None = None,
    nanobar_type_system_db_path: str | None = None,
    route_manifest_path: str | None = None,
    replay_client: httpx2.Client | None = None,
) -> NanobarAPI:
    """Builds the dashboard app.

    `db_path` is the regression-bricks SQLite database to read/write. When omitted, it's
    resolved from the `NANOBAR_REGRESSION_BRICKS_DB` environment variable (falling back to
    `app/admin/nanobar/data/regression_bricks.db`) — see `app.admin.nanobar.db.resolve_db_path`.

    `events_db_path` is the eventbus SQLite database -- **worker-registry bookkeeping only** as
    of `.focusari/telemetry-domain-refactor-plan-with-tasks.md` Decision 6 (the `workers`/
    `worker_log` tables `NanobarWorker`'s own claim-lease liveness mechanism uses; nothing in
    this app's own lifespan writes to it today). Trace/span capture moved to `telemetry_db_path`
    below. This app still instruments itself: `EventBusTraceMiddleware` gives every dashboard
    HTTP request a real span, and `app.state.telemetry` (a `NanobarTelemetry` sharing the same
    `EventQueueRepository`) is used by `app/admin/nanobar/api.py`'s DB-access handlers to add a
    nested "api-to-db" span, per `.focusari/nanobar-telemetry-adr.md`'s design. Trace capture is
    on by default here (`configure_tracing(enabled=True)`, unlike the framework's own opt-in-only
    default) since this app is a dev/observability tool, not a production service -- `app.state.
    trace_capture_toggle` (a `SQLiteTraceCaptureToggle` durably stored in `nanobar_admin_db_path`)
    is the actual runtime on/off switch, controlled from `/admin/nanobar/dashboard/settings`.
    When omitted, resolved from `NANOBAR_EVENTS_DB` (falling back to `app/db/events.db`) —
    see `app.admin.nanobar.events_db.resolve_db_path`.

    `telemetry_db_path` is the new trace/span SQLite database (`nanobar_api_telemetry.db`) a
    `TelemetryDrainWorker` (replacing `EventThread` for this data) drains the shared
    `EventQueueRepository`'s `"trace"`/`"snapshot"` channels into, via the real
    `TelemetryValidatorGate` -> `TelemetryController` -> `IngestSpanService` pipeline. Its
    repository also carries `"snapshot"` (the blog domain's real `NanobarAPIValidatorGate`/
    `NanobarAPIController`/`NanobarAPIService` pipeline calls `capture_layer()`, whose default
    channel is `"snapshot"`) and `"domain.appointments"` (the blog domain's own business-event
    channel). When omitted, resolved from `NANOBAR_TELEMETRY_DB` (falling back to
    `app/db/nanobar_api_telemetry.db`) — see `app.admin.nanobar.telemetry_db.resolve_db_path`.

    Two fully independent admin surfaces, two databases, two logins -- not one shared admin auth
    store gating both. `app_admin_db_path` ("app_admin.db") is the blog/booking admin's own
    session/CSRF/user-store database, backing `/admin/app/login` and everything under
    `/admin/app/*`. `nanobar_admin_db_path` ("nanobar_admin.db") is the nanobar-admin's own,
    backing `/admin/nanobar/login` and everything under `/admin/nanobar/*` -- it also holds
    `SQLiteTraceCaptureToggle` and `SQLiteRefreshLog`, since both are managed exclusively from
    that surface's own Settings page. Each database also holds its own `SQLiteAdminUserStore`'s
    seeded admin account (`admin`/`changeme123` on first use, real salted-hash storage after
    that -- change it via the store once a real credential-rotation flow exists, see that
    class's own docstring for what's still unbuilt). Session/CSRF cookies for the two surfaces
    are further isolated by cookie path (`/admin/app` vs `/admin/nanobar`) so being logged into
    one never disturbs the other -- see `nanobar_api.admin_auth.CSRFMiddleware`'s docstring.
    When omitted, resolved from `NANOBAR_APP_ADMIN_DB`/`NANOBAR_ADMIN_DB` respectively (falling
    back to `app/admin/app/data/app_admin.db`/`app/admin/nanobar/data/nanobar_admin.db`) — see
    `app.admin.app.auth_db.resolve_db_path`/
    `app.admin.nanobar.auth_db.resolve_db_path`.

    `blog_db_path` is the blog domain's SQLite database (posts/appointments/notifications),
    accessed through real SQLAlchemy ORM models (`app.models.blog_model`) via
    `NanobarAPIRepository` (`app.repositories.blog_repository`) — this domain's first real usage
    anywhere in the codebase. When omitted, resolved from `NANOBAR_BLOG_DB` (falling back to
    `app/db/blog.db`) — see `app.db.blog_session.resolve_db_path`.

    `nanobar_type_system_db_path` is the dynamic (runtime-writable) nanobar-type-system SQLite
    database (`nanobar_api/dynamic_taxonomy.py`) -- per-`(key, key_name)` coverage rules (e.g.
    one entry per worker channel) the static, checked-in `nanobar.types.lock` file can't hold.
    When omitted, resolved from `NANOBAR_TYPE_SYSTEM_DB` (falling back to
    `app/admin/nanobar/data/nanobar_type_system.db`) — see
    `app.admin.nanobar.dynamic_taxonomy_db.resolve_db_path`.

    `route_manifest_path` is where `nanobar_api.route_manifest.write_route_manifest` writes
    `nanobar.api-routes.json` -- a static inventory of every route this app declares
    (domain/method/path), regenerated fresh on every call to this function ("built on launch")
    so it never drifts from the actual route tree. Used by the dashboard's "API refresh" and
    "Nanobar refresh" actions -- both, plus "Regression-brick refresh"
    (`generate_bricks_action`), record their outcome to `app.state.refresh_log` (a
    `SQLiteRefreshLog`, stored in `nanobar_admin_db_path`), shown on
    `/admin/nanobar/dashboard/settings`. When omitted, resolved from
    `NANOBAR_API_ROUTES_MANIFEST` (falling back to `app/nanobar.api-routes.json`) — see
    `app.core.config.resolve_route_manifest_path`.

    `replay_client` is the `httpx2.Client` `app/admin/nanobar/api.py`'s `replay_brick_action`
    dispatches regression-brick replays through -- production leaves this `None`, resolving to a
    `starlette.testclient.TestClient(app)` bound to this same app instance (itself a real
    `httpx2.Client` subclass with its own sync-compatible in-process ASGI bridge -- no real
    socket, no second process). Replay isolation from live data comes from the request-scoped
    `nanobar-mode: shadow` header (`nanobar_api.shadow`) `replay_brick_action` attaches, which
    routes the blog domain's ORM session onto `blog_shadow_session_factory` instead of the live
    one for just that one request -- not from talking to a separately-run deployment the way the
    old `shadow_server.py` did. Passing an explicit `replay_client` (as tests do) overrides this.

    Passing any of these explicitly is how tests point the app at temp databases without
    touching the environment.
    """
    resolved_db_path = db_path if db_path is not None else resolve_db_path()
    resolved_events_db_path = events_db_path if events_db_path is not None else resolve_events_db_path()
    resolved_telemetry_db_path = telemetry_db_path if telemetry_db_path is not None else resolve_telemetry_db_path()
    resolved_app_admin_db_path = app_admin_db_path if app_admin_db_path is not None else resolve_app_admin_db_path()
    resolved_nanobar_admin_db_path = (
        nanobar_admin_db_path if nanobar_admin_db_path is not None else resolve_nanobar_admin_db_path()
    )
    resolved_blog_db_path = blog_db_path if blog_db_path is not None else resolve_blog_db_path()
    resolved_nanobar_type_system_db_path = (
        nanobar_type_system_db_path
        if nanobar_type_system_db_path is not None
        else resolve_nanobar_type_system_db_path()
    )
    resolved_route_manifest_path = (
        route_manifest_path if route_manifest_path is not None else resolve_route_manifest_path()
    )

    # This demo is a dev/observability tool, not a production service guarding against silently
    # -active instrumentation -- unlike the framework's own opt-in-only default
    # (`NANOBAR_TRACING_ENABLED`, see `configure_tracing`'s docstring), trace capture should just
    # work out of the box here. The actual on/off control a user gets is the runtime
    # `SQLiteTraceCaptureToggle` below (wired to the /admin/nanobar/dashboard/settings page), not
    # this env var -- the OTel tracer provider it configures is a process-wide global that can
    # only ever move from no-op to real, never back, so it can't be the real toggle.
    configure_tracing(enabled=True)
    trace_capture_toggle = SQLiteTraceCaptureToggle(
        resolved_nanobar_admin_db_path, default_enabled=True)
    refresh_log = SQLiteRefreshLog(resolved_nanobar_admin_db_path)

    repository = EventQueueRepository(
        [ChannelConfig(name="trace"), ChannelConfig(
            name="snapshot"), ChannelConfig(name="domain.appointments")]
    )
    telemetry = NanobarTelemetry(repository, channel="trace")

    # Two fully independent admin surfaces -- two SessionBackends, two SQLiteAdminUserStores,
    # each in its own database. Logging into one never authenticates, or otherwise disturbs, a
    # session on the other.
    app_admin_session_backend = SQLiteSessionBackend(
        resolved_app_admin_db_path)
    app_admin_user_store = SQLiteAdminUserStore(resolved_app_admin_db_path)
    nanobar_admin_session_backend = SQLiteSessionBackend(
        resolved_nanobar_admin_db_path)
    nanobar_admin_user_store = SQLiteAdminUserStore(
        resolved_nanobar_admin_db_path)

    blog_session_factory = build_blog_session_factory(
        resolved_blog_db_path, repository=repository)
    # The shadow-mode counterpart `resolve_session_factory()` (app/db/blog_session.py) picks
    # between at request time, keyed on the `nanobar-mode: shadow` header
    # (`nanobar_api.shadow`) -- a disposable replica so a regression-brick replay's own writes
    # never touch the live blog data above, without needing a second process/app instance the
    # way the old `shadow_server.py` did. Built the same way as the live factory, against a
    # second SQLite file resolved via the same `ShadowPersistenceProfile` mechanism replay
    # already used (`resolve_shadow_connection`/`BLOG_SHADOW_PROFILE`) -- only how the two get
    # *selected* per request has changed, not where the shadow file lives or how it's overridden
    # (`NANOBAR_BLOG_SHADOW_DB`).
    resolved_blog_shadow_db_path = resolve_shadow_connection(
        resolved_blog_db_path, profile=BLOG_SHADOW_PROFILE)
    if "://" not in resolved_blog_shadow_db_path:
        Path(resolved_blog_shadow_db_path).parent.mkdir(
            parents=True, exist_ok=True)
    blog_shadow_session_factory = build_blog_session_factory(
        resolved_blog_shadow_db_path, repository=repository)
    # Recovers any shadow-db row a prior process's crash left seeded mid-replay (a synchronous
    # seed()/teardown() pair, app/db/blog_seeders.py, can't survive a hard kill between the two
    # calls) -- see shadow_seed_log.py's own module docstring for why a startup sweep, not a
    # background worker, is the right (and sufficient) place for this.
    sweep_stale_shadow_seeds(blog_shadow_session_factory)
    post_publisher = PostPublisherThread(
        blog_session_factory, telemetry=telemetry)
    # `resolve_db_path()` (app.admin.nanobar.db) only creates its own parent directory on the
    # "not overridden" branch -- an explicit `db_path=` argument (as tests pass) bypasses it
    # entirely, and unlike the old per-request `get_connection()` (which created the directory
    # lazily on first use), `build_session_factory()` opens the engine right here, once, at
    # app-build time. Confirmed live: without this, a not-yet-existing parent directory raised
    # `OperationalError: unable to open database file` on the very first request.
    Path(resolved_db_path).parent.mkdir(parents=True, exist_ok=True)
    bricks_session_factory = build_bricks_session_factory(
        resolved_db_path, repository=repository)
    # Same "not-yet-existing parent directory" bug `bricks_session_factory` above already hit
    # once, applied preemptively here -- `nanobar_api.telemetry.persistence.build_session_factory`
    # opens its engine at app-build time too, not lazily on first request.
    Path(resolved_telemetry_db_path).parent.mkdir(parents=True, exist_ok=True)
    telemetry_session_factory = build_telemetry_session_factory(
        resolved_telemetry_db_path)

    domain_bus = NanobarEventBus(repository, telemetry)
    domain_bus.subscribe("domain.appointments",
                         AppointmentNotificationCallback(blog_session_factory))

    @asynccontextmanager
    async def lifespan(app: NanobarAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(
                telemetry_drain_worker_lifespan(
                    ["trace", "snapshot"], repository, telemetry_session_factory)
            )
            await stack.enter_async_context(event_bus_lifespan(domain_bus))
            await stack.enter_async_context(post_publisher_lifespan(post_publisher))
            yield

    app = NanobarAPI(
        title="Nanobar Dashboard",
        lifespan=lifespan,
        middleware=[
            Middleware(
                EventBusTraceMiddleware,
                repository=repository,
                channel="trace",
                is_enabled=trace_capture_toggle.is_enabled,
            ),
            Middleware(ShadowModeMiddleware),
        ],
        # No exception_handlers= registration needed for NotFoundError/RegressionBrickNotFoundError/
        # NanobarNotFoundError anymore -- all three are now NanobarAPIError subclasses,
        # caught directly by NanobarAPIValidatorGate.__call__ and turned into a real 404
        # Response before they'd ever reach a Starlette-level exception handler.
        routes=[
            *admin_app_login_routes.build_routes(
                backend=app_admin_session_backend, user_store=app_admin_user_store),
            *admin_nanobar_login_routes.build_routes(
                backend=nanobar_admin_session_backend, user_store=nanobar_admin_user_store
            ),
            admin_nanobar_routes.build_mount(
                backend=nanobar_admin_session_backend),
            admin_app_routes.build_mount(backend=app_admin_session_backend),
            *blog_public_routes.build_routes(),
            *build_replay_routes(),
            Mount("/static", app=StaticFiles(directory=WEB_DIR), name="static"),
        ],
    )
    # "Built on launch": every route this app declares is known once the routes= list above is
    # assembled, so the manifest can be written synchronously right here -- no async I/O
    # involved, no need to wait for the lifespan to start.
    manifest_entries = write_route_manifest(app, resolved_route_manifest_path)
    domain_count = len({entry.domain for entry in manifest_entries})
    refresh_log.record(
        "api",
        last_run_at=datetime.now(UTC).isoformat(),
        summary=f"{len(manifest_entries)} route(s) across {domain_count} domain(s)",
    )

    app.state.db_path = resolved_db_path
    app.state.events_db_path = resolved_events_db_path
    app.state.telemetry_db_path = resolved_telemetry_db_path
    app.state.app_admin_db_path = resolved_app_admin_db_path
    app.state.nanobar_admin_db_path = resolved_nanobar_admin_db_path
    app.state.blog_db_path = resolved_blog_db_path
    app.state.nanobar_type_system_db_path = resolved_nanobar_type_system_db_path
    app.state.route_manifest_path = resolved_route_manifest_path
    app.state.telemetry = telemetry
    app.state.trace_capture_toggle = trace_capture_toggle
    app.state.refresh_log = refresh_log
    app.state.event_bus = domain_bus
    app.state.blog_session_factory = blog_session_factory
    app.state.blog_shadow_session_factory = blog_shadow_session_factory
    app.state.bricks_session_factory = bricks_session_factory
    app.state.telemetry_session_factory = telemetry_session_factory
    app.state.taxonomy = load_taxonomy()
    # In-process by default: a real httpx2.Client subclass with its own sync-compatible ASGI
    # bridge, bound to this same app -- no second process/port. See resolve_session_factory()'s
    # own docstring (app/db/blog_session.py) for how a replay dispatched through this actually
    # ends up isolated from live data (the `nanobar-mode: shadow` header, not this client).
    app.state.replay_client = replay_client if replay_client is not None else TestClient(
        app)
    return app
