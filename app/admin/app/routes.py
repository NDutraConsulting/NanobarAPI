"""The blog/booking demo's own admin surface, mounted at `/admin/app` and gated by
`nanobar_api.admin_auth.session_protected()` -- the notification feed the booking flow's domain
event ends up on, plus post creation/editing and marking notifications read.

Read-only routes (the dashboard page itself, `GET .../posts`, `GET .../notifications`) are plain
handlers reading the repository layer directly, matching `admin/nanobar/api.py`'s own established
convention for simple reads. The mutating routes go through the full `NanobarAPIValidatorGate` ->
`NanobarAPIController` -> `NanobarAPIService` pipeline (`app`'s `gates.py`/`controllers.py`/`services.py`)
-- real validation and business logic live there, unlike the plain reads.
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route

from app.core.config import WEB_DIR
from app.crud.blog_crud import NotificationRepository, PostRepository
from app.db.blog_session import resolve_session_factory as resolve_blog_session_factory
from app.libraries.blog_serializer import notification_to_dict, post_to_dict
from app.validators.blog_validator_gateway import CreatePostGate, MarkNotificationReadGate, UpdatePostGate
from nanobar_api.admin_auth import SessionBackend, session_protected
from nanobar_api.envelope import error, success
from nanobar_api.framework.nanobar_api_validator_gate import NanobarAPIValidatorGate
from nanobar_api.routing import adapt_handler


def _gate_endpoint(gate_cls: type[NanobarAPIValidatorGate], request_type: str) -> Any:
    """Same shape as `nanobar_api.routing`'s own (leading-underscore, private) `_gate_endpoint`
    -- reproduced locally rather than importing a private symbol across a package boundary."""

    async def endpoint(request: Request) -> Any:
        return await gate_cls()(request, request_type)

    return endpoint


async def _dashboard_page(request: Request) -> Response:
    return FileResponse(WEB_DIR / "admin-app" / "admin-app.html")


async def _edit_post_page(request: Request) -> Response:
    return FileResponse(WEB_DIR / "edit-post" / "edit-post.html")


async def _list_posts(request: Request) -> Response:
    session = resolve_blog_session_factory(request)()
    try:
        posts = PostRepository(session).list_all()
        return JSONResponse(success([post_to_dict(p) for p in posts], type_="array"))
    finally:
        session.close()


async def _get_post(request: Request) -> Response:
    # Unlike blog_public_routes.py's _get_post, this doesn't filter by status -- the admin has
    # to be able to open a draft or scheduled post's edit page, not just published ones.
    post_id = request.path_params["post_id"]
    session = resolve_blog_session_factory(request)()
    try:
        post = PostRepository(session).get(post_id)
        if post is None:
            return JSONResponse(error(f"post {post_id!r} not found"), status_code=404)
        return JSONResponse(success(post_to_dict(post)))
    finally:
        session.close()


async def _list_notifications(request: Request) -> Response:
    session = resolve_blog_session_factory(request)()
    try:
        notifications = NotificationRepository(session).list_all()
        return JSONResponse(success([notification_to_dict(n) for n in notifications], type_="array"))
    finally:
        session.close()


def build_mount(*, backend: SessionBackend) -> Mount:
    # JSON routes all carry an /api/ path segment (unlike the page route above) -- required, not
    # cosmetic: AdminSessionMiddleware's redirect-vs-JSON-401 decision keys on that exact
    # substring being present in the path, matching admin/nanobar/api.py's own established
    # convention. Found via live verification: without it, an unauthenticated request to e.g.
    # /admin/app/posts (no "/api/" segment) was misclassified as an HTML page and redirected to
    # /admin/app/login instead of getting a 401 JSON envelope.
    return Mount(
        "/admin/app",
        routes=[
            Route("/dashboard", _dashboard_page, methods=["GET"]),
            Route("/posts/{post_id}/edit", _edit_post_page, methods=["GET"]),
            Route("/api/posts", _list_posts, methods=["GET"]),
            Route(
                "/api/posts",
                adapt_handler(_gate_endpoint(CreatePostGate, "POST /admin/app/api/posts")),
                methods=["POST"],
            ),
            Route("/api/posts/{post_id}", _get_post, methods=["GET"]),
            Route(
                "/api/posts/{post_id}",
                adapt_handler(_gate_endpoint(UpdatePostGate, "POST /admin/app/api/posts/{post_id}")),
                methods=["POST"],
            ),
            Route("/api/notifications", _list_notifications, methods=["GET"]),
            Route(
                "/api/notifications/{notification_id}/read",
                adapt_handler(
                    _gate_endpoint(MarkNotificationReadGate, "POST /admin/app/api/notifications/{notification_id}/read")
                ),
                methods=["POST"],
            ),
        ],
        middleware=list(session_protected(backend=backend, login_url="/admin/app/login", cookie_path="/admin/app")),
    )
