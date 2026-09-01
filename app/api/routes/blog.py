"""Public-facing blog + appointment-booking routes -- plain `Route` objects, not a
`NanobarRouteSet` (per `nanobar_default_domains_buildplan-with-tasks.md` §2.3: root-level routes
needing no dedicated middleware don't need `Mount`-building machinery).

`GET` routes are plain reads (repository layer directly). `POST /book-appointment` goes through
the full gate/controller/service pipeline -- it's the one public route with real validation and
a real side effect (a domain event).
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from app.core.config import WEB_DIR
from app.repositories.blog_repository import PostRepository
from app.db.blog_session import resolve_session_factory as resolve_blog_session_factory
from app.libraries.blog_serializer import post_to_dict
from app.validators.blog_validator_gateway import BookAppointmentGate
from nanobar_api.envelope import error, success
from nanobar_api.framework.nanobar_api_validator_gate import NanobarAPIValidatorGate
from nanobar_api.routing import adapt_handler


def _gate_endpoint(gate_cls: type[NanobarAPIValidatorGate], request_type: str) -> Any:
    async def endpoint(request: Request) -> Any:
        return await gate_cls()(request, request_type)

    return endpoint


async def _blog_index_page(request: Request) -> Response:
    return FileResponse(WEB_DIR / "blog" / "blog.html")


async def _post_detail_page(request: Request) -> Response:
    return FileResponse(WEB_DIR / "post" / "post.html")


async def _book_appointment_page(request: Request) -> Response:
    return FileResponse(WEB_DIR / "book-appointment" / "book-appointment.html")


async def _list_published_posts(request: Request) -> Response:
    session = resolve_blog_session_factory(request)()
    try:
        posts = PostRepository(session).list_published()
        return JSONResponse(success([post_to_dict(p) for p in posts], type_="array"))
    finally:
        session.close()


async def _get_post(request: Request) -> Response:
    post_id = request.path_params["post_id"]
    session = resolve_blog_session_factory(request)()
    try:
        post = PostRepository(session).get(post_id)
        if post is None or post.status != "published":
            return JSONResponse(error(f"post {post_id!r} not found"), status_code=404)
        return JSONResponse(success(post_to_dict(post)))
    finally:
        session.close()


def build_routes() -> list[Route]:
    return [
        Route("/", _blog_index_page, methods=["GET"]),
        Route("/posts/{post_id}", _post_detail_page, methods=["GET"]),
        Route("/book-appointment", _book_appointment_page, methods=["GET"]),
        Route("/api/posts", _list_published_posts, methods=["GET"]),
        Route("/api/posts/{post_id}", _get_post, methods=["GET"]),
        Route(
            "/book-appointment",
            adapt_handler(_gate_endpoint(
                BookAppointmentGate, "POST /book-appointment")),
            methods=["POST"],
        ),
    ]
