"""Builds the Nanobar Dashboard demo app (a NanobarAPI application)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from starlette.middleware import Middleware
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from nanobar_api import NanobarAPI, NanobarTelemetry
from nanobar_api.eventbus import ChannelConfig, EventQueueRepository, eventbus_lifespan
from nanobar_api.middleware.trace import EventBusTraceMiddleware

from . import api, pages
from .db import resolve_db_path
from .events_db import resolve_db_path as resolve_events_db_path
from .pages import WEB_DIR


def build_app(db_path: str | None = None, events_db_path: str | None = None) -> NanobarAPI:
    """Builds the dashboard app.

    `db_path` is the regression-bricks SQLite database to read/write. When omitted, it's
    resolved from the `NANOBAR_REGRESSION_BRICKS_DB` environment variable (falling back to
    `demo/data/regression_bricks.db`) — see `demo.dashboard.db.resolve_db_path`.

    `events_db_path` is the eventbus SQLite database (trace/span data) to read *and now write*
    — this app instruments itself: `EventBusTraceMiddleware` gives every dashboard HTTP request
    a real span, and `app.state.telemetry` (a `NanobarTelemetry` sharing the same
    `EventQueueRepository`) is used by `api.py`'s DB-access handlers to add a nested "api-to-db"
    span, per `.focusari/nanobar-telemetry-adr.md`'s design — the first real multi-span boundary
    in this project (previously `EventBusTraceMiddleware` only ever produced a single HTTP-layer
    span; nothing nested under it). When omitted, resolved from `NANOBAR_EVENTS_DB` (falling
    back to `demo/data/events.db`) — see `demo.dashboard.events_db.resolve_db_path`.

    Passing either explicitly is how tests point the app at a temp database without touching
    the environment.
    """
    resolved_db_path = db_path if db_path is not None else resolve_db_path()
    resolved_events_db_path = events_db_path if events_db_path is not None else resolve_events_db_path()

    repository = EventQueueRepository([ChannelConfig(name="trace")])
    telemetry = NanobarTelemetry(repository, channel="trace")

    @asynccontextmanager
    async def lifespan(app: NanobarAPI) -> AsyncIterator[None]:
        async with eventbus_lifespan(repository, resolved_events_db_path, channels=["trace"]):
            yield

    app = NanobarAPI(
        title="Nanobar Dashboard",
        lifespan=lifespan,
        middleware=[Middleware(EventBusTraceMiddleware, repository=repository, channel="trace")],
        routes=[
            Route("/", pages.nanobars, methods=["GET"]),
            Route("/dashboard", pages.nanobars, methods=["GET"]),
            Route("/nanobars/{nanobar_id}", pages.nanobar_detail, methods=["GET"]),
            Route("/bricks/{brick_id}", pages.brick_detail, methods=["GET"]),
            Route("/triage", pages.triage_board, methods=["GET"]),
            Route("/traces", pages.traces_list, methods=["GET"]),
            Route("/traces/{trace_id}", pages.trace_detail, methods=["GET"]),
            Route("/api/nanobars", api.list_nanobars, methods=["GET"]),
            Route("/api/nanobars/{nanobar_id}", api.update_nanobar, methods=["PATCH"]),
            Route("/api/nanobars/{nanobar_id}/bricks", api.nanobar_bricks, methods=["GET"]),
            Route("/api/bricks/{brick_id}", api.brick_detail, methods=["GET"]),
            Route("/api/bricks/{brick_id}/review-status", api.set_review_status, methods=["PATCH", "POST"]),
            Route("/api/bricks/{brick_id}/scenario", api.set_brick_scenario, methods=["PATCH", "POST"]),
            Route("/api/bricks/{brick_id}/tags", api.add_brick_tag, methods=["POST"]),
            Route("/api/bricks/{brick_id}/tags/{tag}", api.remove_brick_tag, methods=["DELETE"]),
            Route("/api/traces", api.list_traces, methods=["GET"]),
            Route("/api/traces/{trace_id}/spans", api.trace_spans, methods=["GET"]),
            Mount("/static", app=StaticFiles(directory=WEB_DIR), name="static"),
        ],
    )
    app.state.db_path = resolved_db_path
    app.state.events_db_path = resolved_events_db_path
    app.state.telemetry = telemetry
    return app
