"""The nanobar regression-brick admin surface, mounted at `/admin/nanobar` and gated by
`nanobar_api.admin_auth.session_protected()`.

Most of these wrap the *existing*, already-tested page handlers (below) and `api.py` JSON
handlers as-is -- plain Starlette endpoints reading `request.path_params`/`.json()` directly, not
routed through the full `NanobarRouteSet`/`NanobarAPIValidatorGate`/`NanobarAPIController`
pipeline. That remains the right call for routes doing simple reads or orchestration
(list/detail/refresh/replay) -- rewriting already-stable code through the pipeline for no
functional benefit isn't worth it.

**The four `RegressionBrick` "collection" routes and `Nanobar`'s own `update_nanobar` route are
the exception** (review-status/scenario/tag mutations to a brick's side-tables -- see
`nanobar_api/regression_brick/regression_brick_collection_service.py`'s module docstring) --
per direct user instruction, `RegressionBrick`/`Nanobar` each get a full, real, independently
owned application-layer stack (validator_gate/controller/service/repository/model), not just
scaffolding. These five are the first cutover; the rest of this surface's routes are unchanged.
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import FileResponse
from starlette.routing import Mount, Route

from app.core.config import WEB_DIR
from nanobar_api.admin_auth import SessionBackend, session_protected
from nanobar_api.framework.nanobar_api_validator_gate import NanobarAPIValidatorGate
from nanobar_api.nanobar.validator_gate import NanobarGate
from nanobar_api.regression_brick.regression_brick_collection_validator_gate import (
    AddBrickTagGate,
    RemoveBrickTagGate,
    SetBrickScenarioGate,
    SetReviewStatusGate,
)
from nanobar_api.routing import adapt_handler

from . import api


def _gate_endpoint(gate_cls: type[NanobarAPIValidatorGate], request_type: str) -> Any:
    """Same shape as `nanobar_api.routing`'s own (leading-underscore, private) `_gate_endpoint`
    -- reproduced locally rather than importing a private symbol across a package boundary, same
    as `app/admin/app/routes.py`'s own copy."""

    async def endpoint(request: Request) -> Any:
        return await gate_cls()(request, request_type)

    return endpoint


async def nanobars(request: Request) -> FileResponse:
    """GET / and GET /dashboard: the Nanobars list page."""
    return FileResponse(WEB_DIR / "nanobars" / "nanobars.html")


async def nanobar_detail(request: Request) -> FileResponse:
    """GET /nanobars/{nanobar_id}: one Nanobar's detail page -- bound bricks on the left, the
    selected brick's detail (or a Run tab) on the right. `/bricks/{brick_id}` (a separate page)
    is retired; a brick's detail now only ever lives embedded here."""
    return FileResponse(WEB_DIR / "nanobar" / "nanobar.html")


async def triage_board(request: Request) -> FileResponse:
    """GET /triage: the review-status kanban board."""
    return FileResponse(WEB_DIR / "triage" / "triage.html")


async def traces_list(request: Request) -> FileResponse:
    """GET /traces: the trace list page."""
    return FileResponse(WEB_DIR / "traces" / "traces.html")


async def trace_detail(request: Request) -> FileResponse:
    """GET /traces/{trace_id}: one trace's span timeline page."""
    return FileResponse(WEB_DIR / "trace" / "trace.html")


async def workers_list(request: Request) -> FileResponse:
    """GET /workers: registered workers -- configuration + liveness, and each one's recent
    failure log."""
    return FileResponse(WEB_DIR / "workers" / "workers.html")


async def settings(request: Request) -> FileResponse:
    """GET /dashboard/settings: the runtime settings page -- currently just the trace-capture
    on/off toggle (`SQLiteTraceCaptureToggle`)."""
    return FileResponse(WEB_DIR / "settings" / "settings.html")


def build_mount(*, backend: SessionBackend) -> Mount:
    return Mount(
        "/admin/nanobar",
        routes=[
            Route("/", nanobars, methods=["GET"]),
            Route("/dashboard", nanobars, methods=["GET"]),
            Route("/nanobars/{nanobar_id}", nanobar_detail, methods=["GET"]),
            Route("/triage", triage_board, methods=["GET"]),
            Route("/traces", traces_list, methods=["GET"]),
            Route("/traces/{trace_id}", trace_detail, methods=["GET"]),
            Route("/workers", workers_list, methods=["GET"]),
            Route("/dashboard/settings", settings, methods=["GET"]),
            Route("/api/settings", api.get_settings, methods=["GET"]),
            Route("/api/settings", api.update_settings, methods=["POST"]),
            Route("/api/generate-bricks", api.generate_bricks_action, methods=["POST"]),
            Route("/api/refresh/nanobars", api.refresh_nanobars_action, methods=["POST"]),
            Route("/api/refresh/api-routes", api.refresh_api_routes_action, methods=["POST"]),
            Route("/api/refresh/status", api.refresh_status, methods=["GET"]),
            Route("/api/dynamic-taxonomy", api.list_dynamic_taxonomy_entries, methods=["GET"]),
            Route("/api/workers", api.list_workers, methods=["GET"]),
            Route("/api/workers/{worker_id}/log", api.worker_log, methods=["GET"]),
            Route("/api/nanobars", api.list_nanobars, methods=["GET"]),
            Route("/api/nanobars/{nanobar_id}", api.nanobar_detail, methods=["GET"]),
            Route(
                "/api/nanobars/{nanobar_id}",
                adapt_handler(_gate_endpoint(NanobarGate, "PATCH /admin/nanobar/api/nanobars/{nanobar_id}")),
                methods=["PATCH"],
            ),
            Route("/api/nanobars/{nanobar_id}/bricks", api.nanobar_bricks, methods=["GET"]),
            Route("/api/nanobars/{nanobar_id}/coverage-gaps", api.nanobar_coverage_gaps, methods=["GET"]),
            Route("/api/bricks/{brick_id}", api.brick_detail, methods=["GET"]),
            Route("/api/bricks/{brick_id}/replay", api.replay_brick_action, methods=["POST"]),
            Route(
                "/api/bricks/{brick_id}/review-status",
                adapt_handler(
                    _gate_endpoint(SetReviewStatusGate, "PATCH /admin/nanobar/api/bricks/{brick_id}/review-status")
                ),
                methods=["PATCH", "POST"],
            ),
            Route(
                "/api/bricks/{brick_id}/scenario",
                adapt_handler(
                    _gate_endpoint(SetBrickScenarioGate, "PATCH /admin/nanobar/api/bricks/{brick_id}/scenario")
                ),
                methods=["PATCH", "POST"],
            ),
            Route(
                "/api/bricks/{brick_id}/tags",
                adapt_handler(_gate_endpoint(AddBrickTagGate, "POST /admin/nanobar/api/bricks/{brick_id}/tags")),
                methods=["POST"],
            ),
            Route(
                "/api/bricks/{brick_id}/tags/{tag}",
                adapt_handler(
                    _gate_endpoint(RemoveBrickTagGate, "DELETE /admin/nanobar/api/bricks/{brick_id}/tags/{tag}")
                ),
                methods=["DELETE"],
            ),
            Route("/api/traces", api.list_traces, methods=["GET"]),
            Route("/api/traces/facets", api.trace_facets, methods=["GET"]),
            Route("/api/traces/{trace_id}/spans", api.trace_spans, methods=["GET"]),
        ],
        middleware=list(
            session_protected(backend=backend, login_url="/admin/nanobar/login", cookie_path="/admin/nanobar")
        ),
    )
