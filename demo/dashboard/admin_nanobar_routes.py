"""The nanobar regression-brick admin surface, mounted at `/admin/nanobar` and gated by
`nanobar_api.admin_auth.session_protected()`.

Wraps the *existing*, already-tested `pages.py` page handlers and `api.py` JSON handlers as-is --
this is a path/wiring change, not a rewrite through the full `NanobarRouteSet`/
`NanobarValidatorGate`/`NanobarController` pipeline. Those handlers don't validate through a
`NanobarValidatorGate` (they're plain Starlette endpoints reading `request.path_params`/`.json()`
directly), so forcing them through that pipeline here would be a real rewrite of stable code for
no functional benefit -- that pipeline is used instead for the new blog/booking domain
(`admin_app_routes.py`), which is genuinely new code and demonstrates it properly. A plain `Mount`
is a perfectly ordinary way to group routes behind shared middleware; it doesn't imply anything
about the framework's declarative `NanobarRouteSet` mechanism one way or another.
"""

from __future__ import annotations

from starlette.routing import Mount, Route

from nanobar_api.admin_auth import SessionBackend, session_protected

from . import api, pages


def build_mount(*, backend: SessionBackend) -> Mount:
    return Mount(
        "/admin/nanobar",
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
            Route("/api/nanobars/{nanobar_id}/coverage-gaps", api.nanobar_coverage_gaps, methods=["GET"]),
            Route("/api/bricks/{brick_id}", api.brick_detail, methods=["GET"]),
            Route("/api/bricks/{brick_id}/review-status", api.set_review_status, methods=["PATCH", "POST"]),
            Route("/api/bricks/{brick_id}/scenario", api.set_brick_scenario, methods=["PATCH", "POST"]),
            Route("/api/bricks/{brick_id}/tags", api.add_brick_tag, methods=["POST"]),
            Route("/api/bricks/{brick_id}/tags/{tag}", api.remove_brick_tag, methods=["DELETE"]),
            Route("/api/traces", api.list_traces, methods=["GET"]),
            Route("/api/traces/{trace_id}/spans", api.trace_spans, methods=["GET"]),
        ],
        middleware=list(session_protected(backend=backend)),
    )
