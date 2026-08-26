"""Builds the Nanobar Dashboard demo app (a NanobarAPI application)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

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
from nanobar_api.middleware.trace import EventBusTraceMiddleware
from nanobar_api.taxonomy import load_taxonomy

from . import admin_app_routes, admin_login_routes, admin_nanobar_routes, blog_public_routes
from .admin_db import resolve_db_path as resolve_admin_db_path
from .blog_controllers import NotFoundError
from .blog_db import build_session_factory as build_blog_session_factory
from .blog_db import resolve_db_path as resolve_blog_db_path
from .blog_notifications import AppointmentNotificationCallback
from .blog_publisher_worker import PostPublisherThread, post_publisher_lifespan
from .db import resolve_db_path
from .events_db import resolve_db_path as resolve_events_db_path
from .pages import WEB_DIR


async def _not_found_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(error(str(exc)), status_code=404)


def build_app(
    db_path: str | None = None,
    events_db_path: str | None = None,
    admin_db_path: str | None = None,
    blog_db_path: str | None = None,
) -> NanobarAPI:
    """Builds the dashboard app.

    `db_path` is the regression-bricks SQLite database to read/write. When omitted, it's
    resolved from the `NANOBAR_REGRESSION_BRICKS_DB` environment variable (falling back to
    `demo/data/regression_bricks.db`) — see `demo.dashboard.db.resolve_db_path`.

    `events_db_path` is the eventbus SQLite database (trace/span data) to read *and now write*
    — this app instruments itself: `EventBusTraceMiddleware` gives every dashboard HTTP request
    a real span, and `app.state.telemetry` (a `NanobarTelemetry` sharing the same
    `EventQueueRepository`) is used by `api.py`'s DB-access handlers to add a nested "api-to-db"
    span, per `.focusari/nanobar-telemetry-adr.md`'s design. When omitted, resolved from
    `NANOBAR_EVENTS_DB` (falling back to `demo/data/events.db`) — see
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

    Passing any of these explicitly is how tests point the app at temp databases without
    touching the environment.
    """
    resolved_db_path = db_path if db_path is not None else resolve_db_path()
    resolved_events_db_path = events_db_path if events_db_path is not None else resolve_events_db_path()
    resolved_admin_db_path = admin_db_path if admin_db_path is not None else resolve_admin_db_path()
    resolved_blog_db_path = blog_db_path if blog_db_path is not None else resolve_blog_db_path()

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
        middleware=[Middleware(EventBusTraceMiddleware, repository=repository, channel="trace")],
        exception_handlers={NotFoundError: _not_found_handler},
        routes=[
            *admin_login_routes.build_routes(backend=session_backend, user_store=admin_user_store),
            admin_nanobar_routes.build_mount(backend=session_backend),
            admin_app_routes.build_mount(backend=session_backend),
            *blog_public_routes.build_routes(),
            Mount("/static", app=StaticFiles(directory=WEB_DIR), name="static"),
        ],
    )
    app.state.db_path = resolved_db_path
    app.state.events_db_path = resolved_events_db_path
    app.state.admin_db_path = resolved_admin_db_path
    app.state.blog_db_path = resolved_blog_db_path
    app.state.telemetry = telemetry
    app.state.event_bus = domain_bus
    app.state.blog_session_factory = blog_session_factory
    app.state.taxonomy = load_taxonomy()
    return app
