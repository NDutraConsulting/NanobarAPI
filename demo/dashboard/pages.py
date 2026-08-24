"""Server-rendered HTML routes for the Nanobar Dashboard demo app."""

from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from nanobar_api.bricks import store as bricks_store
from nanobar_api.bricks.schema import REVIEW_STATUSES, Nanobar, RegressionBrick
from nanobar_api.eventbus import store as events_store

from .db import get_connection
from .events_db import get_connection as get_events_connection
from .templates import (
    render_brick_page,
    render_dashboard_page,
    render_nanobar_page,
    render_not_found,
    render_trace_detail_page,
    render_traces_list_page,
    render_triage_page,
)

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def _db_path(request: Request) -> str:
    db_path: str = request.app.state.db_path
    return db_path


def _events_db_path(request: Request) -> str:
    db_path: str = request.app.state.events_db_path
    return db_path


def _group_by_target_type(nanobars: list[Nanobar]) -> dict[str, list[Nanobar]]:
    """Groups nanobars by each distinct `monitor_target_refs[].target_type` they carry.

    A nanobar referencing more than one target type appears in each of those groups — the
    dashboard is meant to be browsable from any of a nanobar's monitored entry points, not
    just its first one. A nanobar with no target refs at all still shows up, under an
    "(untargeted)" bucket, rather than being silently dropped.
    """
    groups: dict[str, list[Nanobar]] = {}
    for nanobar in nanobars:
        target_types = sorted({ref.target_type for ref in nanobar.monitor_target_refs}) or ["(untargeted)"]
        for target_type in target_types:
            groups.setdefault(target_type, []).append(nanobar)
    return dict(sorted(groups.items()))


async def dashboard(request: Request) -> HTMLResponse:
    """GET / and GET /dashboard: Nanobars grouped by target_type."""
    conn = get_connection(_db_path(request))
    try:
        groups = _group_by_target_type(bricks_store.list_nanobars(conn))
        return HTMLResponse(render_dashboard_page(groups))
    finally:
        conn.close()


async def nanobar_detail(request: Request) -> HTMLResponse:
    """GET /nanobars/{nanobar_id}: one Nanobar plus its bound bricks and their review status."""
    nanobar_id = request.path_params["nanobar_id"]
    conn = get_connection(_db_path(request))
    try:
        nanobar = bricks_store.get_nanobar(conn, nanobar_id)
        if nanobar is None:
            return HTMLResponse(render_not_found(f"nanobar {nanobar_id!r} not found"), status_code=404)
        bricks = bricks_store.get_bricks_for_nanobar(conn, nanobar_id)
        bricks_with_status = [
            (brick, bricks_store.get_review_status(conn, brick.regression_brick_id)) for brick in bricks
        ]
        return HTMLResponse(render_nanobar_page(nanobar, bricks_with_status))
    finally:
        conn.close()


async def brick_detail(request: Request) -> HTMLResponse:
    """GET /bricks/{brick_id}: full brick detail plus which Nanobar(s) it's bound to."""
    brick_id = request.path_params["brick_id"]
    conn = get_connection(_db_path(request))
    try:
        brick = bricks_store.get_brick(conn, brick_id)
        if brick is None:
            return HTMLResponse(render_not_found(f"brick {brick_id!r} not found"), status_code=404)
        status = bricks_store.get_review_status(conn, brick_id)
        nanobars = bricks_store.get_nanobars_for_brick(conn, brick_id)
        return HTMLResponse(render_brick_page(brick, status, nanobars))
    finally:
        conn.close()


async def triage_board(request: Request) -> HTMLResponse:
    """GET /triage: kanban board of bricks grouped by review status."""
    conn = get_connection(_db_path(request))
    try:
        bricks_by_status: dict[str, list[RegressionBrick]] = {
            status: bricks_store.list_bricks_by_review_status(conn, status) for status in REVIEW_STATUSES
        }
        return HTMLResponse(render_triage_page(bricks_by_status))
    finally:
        conn.close()


async def triage_js(request: Request) -> Response:
    """GET /static/triage.js: the triage board's drag-and-drop JavaScript."""
    content = (_STATIC_DIR / "triage.js").read_text(encoding="utf-8")
    return Response(content, media_type="text/javascript")


async def traces_list(request: Request) -> HTMLResponse:
    """GET /traces: traces captured on the "trace" channel, most-recently-completed first."""
    conn = get_events_connection(_events_db_path(request))
    try:
        summaries = events_store.list_trace_ids(conn, "trace")
        return HTMLResponse(render_traces_list_page(summaries))
    finally:
        conn.close()


async def trace_detail(request: Request) -> HTMLResponse:
    """GET /traces/{trace_id}: that trace's spans, ordered by monotonic_ns."""
    trace_id = request.path_params["trace_id"]
    conn = get_events_connection(_events_db_path(request))
    try:
        events = events_store.get_events_by_trace_id(conn, trace_id, channel="trace")
        if not events:
            return HTMLResponse(render_not_found(f"trace {trace_id!r} not found"), status_code=404)
        return HTMLResponse(render_trace_detail_page(trace_id, events))
    finally:
        conn.close()
