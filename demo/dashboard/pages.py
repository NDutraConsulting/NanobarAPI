"""HTML page routes for the Nanobar Dashboard demo app.

Each route serves a static HTML file as-is (no server-side templating) from
`demo/web/{page}/{page}.html` — the page itself fetches its data client-side from the JSON
API in `api.py`. See `demo/web/` for the page bundles (html/css/api.js/ui.js/controller.js
per page, matching `focusari_kahnban`'s established frontend pattern).
"""

from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


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
