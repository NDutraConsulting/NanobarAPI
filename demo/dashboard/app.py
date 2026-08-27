"""Builds the Nanobar Dashboard demo app (a NanobarAPI application)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime

from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from nanobar_api import NanobarAPI, NanobarTelemetry
from nanobar_api.admin_auth import SQLiteAdminUserStore, SQLiteSessionBackend
from nanobar_api.envelope import error
from nanobar_api.eventbus import ChannelConfig, EventQueueRepository, eventbus_lifespan
from nanobar_api.eventbus.dispatch import NanobarEventBus, event_bus_lifespan
from nanobar_api.middleware.trace import EventBusTraceMiddleware, SQLiteTraceCaptureToggle, configure_tracing
from nanobar_api.route_manifest import write_route_manifest
from nanobar_api.taxonomy import load_taxonomy

from . import admin_app_routes, admin_login_routes, admin_nanobar_routes, blog_public_routes
from .admin_db import resolve_db_path as resolve_admin_db_path
from .blog_controllers import NotFoundError
from .blog_db import build_session_factory as build_blog_session_factory, resolve_db_path as resolve_blog_db_path
from .blog_notifications import AppointmentNotificationCallback
from .blog_publisher_worker import PostPublisherThread, post_publisher_lifespan
from .db import resolve_db_path
from .dynamic_taxonomy_db import resolve_db_path as resolve_nanobar_type_system_db_path
from .events_db import resolve_db_path as resolve_events_db_path
from .pages import WEB_DIR
from .refresh_log import SQLiteRefreshLog
from .route_manifest_path import resolve_path as resolve_route_manifest_path


async def _not_found_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(error(str(exc)), status_code=404)


def build_app(
    db_path: str | None = None,
    events_db_path: str | None = None,
    admin_db_path: str | None = None,
    blog_db_path: str | None = None,
    nanobar_type_system_db_path: str | None = None,
    route_manifest_path: str | None = None,
) -> NanobarAPI:
    """Builds the dashboard app.

    `db_path` is the regression-bricks SQLite database to read/write. When omitted, it's
    resolved from the `NANOBAR_REGRESSION_BRICKS_DB` environment variable (falling back to
    `demo/data/regression_bricks.db`) — see `demo.dashboard.db.resolve_db_path`.

    `events_db_path` is the eventbus SQLite database (trace/span data) to read *and now write*
    — this app instruments itself: `EventBusTraceMiddleware` gives every dashboard HTTP request
    a real span, and `app.state.telemetry` (a `NanobarTelemetry` sharing the same
    `EventQueueRepository`) is used by `api.py`'s DB-access handlers to add a nested "api-to-db"
    span, per `.focusari/nanobar-telemetry-adr.md`'s design. Trace capture is on by default here
    (`configure_tracing(enabled=True)`, unlike the framework's own opt-in-only default) since
    this app is a dev/observability tool, not a production service -- `app.state.
    trace_capture_toggle` (a `SQLiteTraceCaptureToggle` durably stored in `admin_db_path`) is the
    actual runtime on/off switch, controlled from `/admin/nanobar/dashboard/settings`. When
    omitted, resolved from `NANOBAR_EVENTS_DB` (falling back to `demo/data/events.db`) — see
    `demo.dashboard.events_db.resolve_db_path`. Its repository now also carries `"snapshot"`
    (the blog domain's real `NanobarValidatorGate`/`NanobarController`/`NanobarService` pipeline
    calls `capture_layer()`, whose default channel is `"snapshot"` — unconfigured before this
    domain, since nothing in this app previously used that pipeline) and
    `"domain.appointments"` (the blog domain's own business-event channel).

    `admin_db_path` ("adminDB") is the admin-session SQLite database `session_protected()`'s
    `SQLiteSessionBackend` reads/writes -- durable across restarts, unlike the framework's
    in-memory default. Also holds `SQLiteAdminUserStore`'s seeded admin account
    (`admin`/`changeme123` on first use, real salted-hash storage after that -- change it via
    the store once a real credential-rotation flow exists, see that class's own docstring for
    what's still unbuilt). When omitted, resolved from `NANOBAR_ADMIN_DB` (falling back to
    `demo/data/admin.db`) — see `demo.dashboard.admin_db.resolve_db_path`.

    `blog_db_path` is the blog domain's SQLite database (posts/appointments/notifications),
    accessed through real SQLAlchemy ORM models (`blog_models.py`) via `NanobarRepository`
    (`blog_repositories.py`) — this domain's first real usage anywhere in the codebase. When
    omitted, resolved from `NANOBAR_BLOG_DB` (falling back to `demo/data/blog.db`) — see
    `demo.dashboard.blog_db.resolve_db_path`.

    `nanobar_type_system_db_path` is the dynamic (runtime-writable) nanobar-type-system SQLite
    database (`nanobar_api/dynamic_taxonomy.py`) -- per-`(key, key_name)` coverage rules (e.g.
    one entry per worker channel) the static, checked-in `nanobar.types.lock` file can't hold.
    When omitted, resolved from `NANOBAR_TYPE_SYSTEM_DB` (falling back to
    `demo/data/nanobar_type_system.db`) — see `demo.dashboard.dynamic_taxonomy_db.resolve_db_path`.

    `route_manifest_path` is where `nanobar_api.route_manifest.write_route_manifest` writes
    `nanobar.api-routes.json` -- a static inventory of every route this app declares
    (domain/method/path), regenerated fresh on every call to this function ("built on launch")
    so it never drifts from the actual route tree. Used by the dashboard's "API refresh" and
    "Nanobar refresh" actions -- both, plus "Regression-brick refresh"
    (`generate_bricks_action`), record their outcome to `app.state.refresh_log` (a
    `SQLiteRefreshLog`, also stored in `admin_db_path`), shown on
    `/admin/nanobar/dashboard/settings`. When omitted, resolved from
    `NANOBAR_API_ROUTES_MANIFEST` (falling back to `demo/data/nanobar.api-routes.json`) — see
    `demo.dashboard.route_manifest_path.resolve_path`.

    Passing any of these explicitly is how tests point the app at temp databases without
    touching the environment.
    """
    resolved_db_path = db_path if db_path is not None else resolve_db_path()
    resolved_events_db_path = events_db_path if events_db_path is not None else resolve_events_db_path()
    resolved_admin_db_path = admin_db_path if admin_db_path is not None else resolve_admin_db_path()
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
    trace_capture_toggle = SQLiteTraceCaptureToggle(resolved_admin_db_path, default_enabled=True)
    refresh_log = SQLiteRefreshLog(resolved_admin_db_path)

    repository = EventQueueRepository(
        [ChannelConfig(name="trace"), ChannelConfig(name="snapshot"), ChannelConfig(name="domain.appointments")]
    )
    telemetry = NanobarTelemetry(repository, channel="trace")
    session_backend = SQLiteSessionBackend(resolved_admin_db_path)
    # Seeds admin/changeme123 the first time this file is used -- idempotent, never overwrites
    # a since-changed password on a later call.
    admin_user_store = SQLiteAdminUserStore(resolved_admin_db_path)
    blog_session_factory = build_blog_session_factory(resolved_blog_db_path, repository=repository)
    post_publisher = PostPublisherThread(blog_session_factory)

    domain_bus = NanobarEventBus(repository, telemetry)
    domain_bus.subscribe("domain.appointments", AppointmentNotificationCallback(blog_session_factory))

    @asynccontextmanager
    async def lifespan(app: NanobarAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(
                eventbus_lifespan(repository, resolved_events_db_path, channels=["trace", "snapshot"])
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
            )
        ],
        exception_handlers={NotFoundError: _not_found_handler},
        routes=[
            *admin_login_routes.build_routes(backend=session_backend, user_store=admin_user_store),
            admin_nanobar_routes.build_mount(backend=session_backend),
            admin_app_routes.build_mount(backend=session_backend),
            *blog_public_routes.build_routes(),
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
    app.state.admin_db_path = resolved_admin_db_path
    app.state.blog_db_path = resolved_blog_db_path
    app.state.nanobar_type_system_db_path = resolved_nanobar_type_system_db_path
    app.state.route_manifest_path = resolved_route_manifest_path
    app.state.telemetry = telemetry
    app.state.trace_capture_toggle = trace_capture_toggle
    app.state.refresh_log = refresh_log
    app.state.event_bus = domain_bus
    app.state.blog_session_factory = blog_session_factory
    app.state.taxonomy = load_taxonomy()
    return app
