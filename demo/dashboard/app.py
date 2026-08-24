"""Builds the Nanobar Dashboard demo app (a NanobarAPI application)."""

from __future__ import annotations

from starlette.routing import Route

from nanobar_api import NanobarAPI

from . import api, pages
from .db import resolve_db_path
from .events_db import resolve_db_path as resolve_events_db_path


def build_app(db_path: str | None = None, events_db_path: str | None = None) -> NanobarAPI:
    """Builds the dashboard app.

    `db_path` is the regression-bricks SQLite database to read/write. When omitted, it's
    resolved from the `NANOBAR_REGRESSION_BRICKS_DB` environment variable (falling back to
    `demo/data/regression_bricks.db`) — see `demo.dashboard.db.resolve_db_path`.

    `events_db_path` is the eventbus SQLite database (trace/span data) to read. When omitted,
    it's resolved from `NANOBAR_EVENTS_DB` (falling back to `demo/data/events.db`) — see
    `demo.dashboard.events_db.resolve_db_path`.

    Passing either explicitly is how tests point the app at a temp database without touching
    the environment.
    """
    resolved_db_path = db_path if db_path is not None else resolve_db_path()
    resolved_events_db_path = events_db_path if events_db_path is not None else resolve_events_db_path()

    app = NanobarAPI(
        title="Nanobar Dashboard",
        routes=[
            Route("/", pages.dashboard, methods=["GET"]),
            Route("/dashboard", pages.dashboard, methods=["GET"]),
            Route("/nanobars/{nanobar_id}", pages.nanobar_detail, methods=["GET"]),
            Route("/bricks/{brick_id}", pages.brick_detail, methods=["GET"]),
            Route("/triage", pages.triage_board, methods=["GET"]),
            Route("/traces", pages.traces_list, methods=["GET"]),
            Route("/traces/{trace_id}", pages.trace_detail, methods=["GET"]),
            Route("/static/triage.js", pages.triage_js, methods=["GET"]),
            Route("/api/nanobars", api.list_nanobars, methods=["GET"]),
            Route("/api/nanobars/{nanobar_id}/bricks", api.nanobar_bricks, methods=["GET"]),
            Route("/api/bricks/{brick_id}", api.brick_detail, methods=["GET"]),
            Route("/api/bricks/{brick_id}/review-status", api.set_review_status, methods=["PATCH", "POST"]),
            Route("/api/traces", api.list_traces, methods=["GET"]),
            Route("/api/traces/{trace_id}/spans", api.trace_spans, methods=["GET"]),
        ],
    )
    app.state.db_path = resolved_db_path
    app.state.events_db_path = resolved_events_db_path
    return app
