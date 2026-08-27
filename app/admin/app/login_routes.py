"""`GET`/`POST /admin/app/login` -- the app-admin surface's own login, independent of
`admin/nanobar`'s own `/admin/nanobar/login`. Two admin surfaces, two logins, two
`SessionBackend`/`SQLiteAdminUserStore` pairs (`admin/app/auth_db.py`'s `app_admin.db`, here, vs.
`admin/nanobar/auth_db.py`'s `nanobar_admin.db`, there) -- not one shared login gating both.

`session_protected()` itself deliberately doesn't gate this route (a session must be
establishable before one exists to require). Per `.focusari/backlog/
nanobar_admin_auth_buildplan-with-tasks.md` §4's login flow: `GET` establishes an
*unauthenticated* session (so a CSRF token exists to embed before any credential is presented)
via `csrf_protected()` alone; `POST` validates the CSRF token and the presented username/password
against `SQLiteAdminUserStore`, then authenticates that same session.
"""

from __future__ import annotations

import json

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from app.core.config import WEB_DIR
from nanobar_api.admin_auth import ADMIN_SESSION_COOKIE, SessionBackend, SQLiteAdminUserStore, csrf_protected
from nanobar_api.envelope import error, success

#: How long an established session (authenticated or not) stays valid before needing a fresh
#: login -- matches session_protected()'s own default, kept explicit here since this is the one
#: call site that actually creates sessions.
SESSION_TTL_SECONDS = 3600.0


def build_routes(*, backend: SessionBackend, user_store: SQLiteAdminUserStore) -> list[Route]:
    async def login(request: Request) -> Response:
        if request.method == "GET":
            record = backend.create(ttl_seconds=SESSION_TTL_SECONDS)
            response: Response = FileResponse(WEB_DIR / "admin-app-login" / "admin-app-login.html")
            # path="/admin/app" -- this cookie must never be sent to /admin/nanobar/*, whose own
            # login sets a same-named cookie scoped to its own path instead. See
            # nanobar_api.admin_auth.CSRFMiddleware's docstring for the full reasoning (the CSRF
            # cookie is scoped the same way, via session_protected()'s own cookie_path).
            response.set_cookie(
                ADMIN_SESSION_COOKIE, record.session_id, httponly=True, samesite="lax", path="/admin/app"
            )
            return response

        session_id = request.cookies.get(ADMIN_SESSION_COOKIE)
        if session_id is None or backend.get(session_id) is None:
            return JSONResponse(error("session expired -- reload the login page and try again"), status_code=400)

        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse(error("request body must be valid JSON"), status_code=400)

        username = body.get("username") if isinstance(body, dict) else None
        password = body.get("password") if isinstance(body, dict) else None
        if not isinstance(username, str) or not isinstance(password, str):
            return JSONResponse(error("username and password are required"), status_code=400)
        if not user_store.verify(username, password):
            return JSONResponse(error("invalid username or password"), status_code=401)

        backend.authenticate(session_id)
        return JSONResponse(success({"redirect": "/admin/app/dashboard"}))

    # cookie_path must match admin/app/routes.py's own session_protected(cookie_path=...) --
    # both this route's CSRFMiddleware instance and the protected mount's bundled one read/write
    # the same double-submit cookie, so a token issued here stays valid for POSTs made after
    # login.
    return [
        Route(
            "/admin/app/login",
            login,
            methods=["GET", "POST"],
            middleware=list(csrf_protected(cookie_path="/admin/app")),
        )
    ]
