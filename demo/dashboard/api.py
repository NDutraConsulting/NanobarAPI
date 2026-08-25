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

from nanobar_api import NanobarProps, NanobarTelemetry, error, success
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


def _telemetry(request: Request) -> NanobarTelemetry:
    telemetry: NanobarTelemetry = request.app.state.telemetry
    return telemetry


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


async def list_nanobars(request: Request) -> JSONResponse:
    """GET /api/nanobars?target_type=... -> envelope success with a list of nanobars.

    The DB query is wrapped in its own nested `NanobarTelemetry` span — the first real
    "api-to-db" boundary in this project, nested under `EventBusTraceMiddleware`'s HTTP-layer
    span for this same request (both share one `EventQueueRepository`, see `app.py`). A
    `NanobarTelemetry.span(...)` is used here as a context manager rather than `@decorator`
    because `telemetry` is only available per-request (`request.app.state`), constructed after
    this module is imported — the decorator form needs the instance to exist at import time,
    which doesn't fit this call site.
    """
    target_type = request.query_params.get("target_type")
    conn = get_connection(_db_path(request))
    try:
        with _telemetry(request).span(
            "dashboard.nanobars.list", nanobar=NanobarProps(type="api-to-db")
        ):
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
    "domain": "..."}.

    All four fields are optional and independent — an omitted field keeps its current
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

        fields = {
            "label": body.get("label", current.label),
            "scenario_description": body.get("scenario_description", current.scenario_description),
            "component_source_description": body.get(
                "component_source_description", current.component_source_description
            ),
            "domain": body.get("domain", current.domain),
        }
        for name, value in fields.items():
            if value is not None and not isinstance(value, str):
                return JSONResponse(error(f"{name!r} must be a string"), status_code=400)

        bricks_store.update_nanobar(conn, nanobar_id, **fields)
        updated = bricks_store.get_nanobar(conn, nanobar_id)
        assert updated is not None
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
