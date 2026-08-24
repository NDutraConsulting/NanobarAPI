"""JSON API routes for the Nanobar Dashboard demo app.

Every response uses this project's envelope contract (`nanobar_api.success` /
`nanobar_api.error`) — see `nanobar_api/envelope.py`.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from nanobar_api import error, success
from nanobar_api.bricks import store as bricks_store
from nanobar_api.bricks.schema import RegressionBrick
from nanobar_api.eventbus import store as events_store

from .db import get_connection
from .events_db import get_connection as get_events_connection


def _db_path(request: Request) -> str:
    db_path: str = request.app.state.db_path
    return db_path


def _events_db_path(request: Request) -> str:
    db_path: str = request.app.state.events_db_path
    return db_path


def _brick_with_review_status(conn: Any, brick: RegressionBrick) -> dict[str, Any]:
    status = bricks_store.get_review_status(conn, brick.regression_brick_id)
    return {**dataclasses.asdict(brick), "review_status": dataclasses.asdict(status)}


async def list_nanobars(request: Request) -> JSONResponse:
    """GET /api/nanobars?target_type=... -> envelope success with a list of nanobars."""
    target_type = request.query_params.get("target_type")
    conn = get_connection(_db_path(request))
    try:
        nanobars = bricks_store.list_nanobars(conn, target_type=target_type)
        return JSONResponse(success([dataclasses.asdict(n) for n in nanobars], type_="array"))
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
        data = [_brick_with_review_status(conn, brick) for brick in bricks]
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
        return JSONResponse(success(_brick_with_review_status(conn, brick)))
    finally:
        conn.close()


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


async def list_traces(request: Request) -> JSONResponse:
    """GET /api/traces?channel=trace&limit=... -> envelope success with trace summaries,
    most-recently-completed first."""
    channel = request.query_params.get("channel", "trace")
    try:
        limit = int(request.query_params.get("limit", "100"))
    except ValueError:
        return JSONResponse(error("limit must be an integer"), status_code=400)

    conn = get_events_connection(_events_db_path(request))
    try:
        summaries = events_store.list_trace_ids(conn, channel, limit=limit)
        return JSONResponse(success([dataclasses.asdict(s) for s in summaries], type_="array"))
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
