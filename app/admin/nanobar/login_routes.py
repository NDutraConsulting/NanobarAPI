"""`GET`/`POST /admin/nanobar/login` -- the nanobar-admin surface's own login, independent of
`admin/app`'s own `/admin/app/login`. Two admin surfaces, two logins, two
`SessionBackend`/`SQLiteAdminUserStore` pairs (`admin/nanobar/auth_db.py`'s `nanobar_admin.db`,
here, vs. `admin/app/auth_db.py`'s `app_admin.db`, there) -- not one shared login gating both.

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
from nanobar_api.admin_auth import (
    ADMIN_SESSION_COOKIE,
    CSRF_COOKIE_NAME,
    SessionBackend,
    SQLiteAdminUserStore,
    csrf_protected,
)
from nanobar_api.envelope import error, success

#: How long an established session (authenticated or not) stays valid before needing a fresh
#: login -- matches session_protected()'s own default, kept explicit here since this is the one
#: call site that actually creates sessions.
SESSION_TTL_SECONDS = 3600.0


def build_routes(*, backend: SessionBackend, user_store: SQLiteAdminUserStore) -> list[Route]:
    async def login(request: Request) -> Response:
        if request.method == "GET":
            record = backend.create(ttl_seconds=SESSION_TTL_SECONDS)
            response: Response = FileResponse(WEB_DIR / "admin-nanobar-login" / "admin-nanobar-login.html")
            # path="/admin/nanobar" -- this cookie must never be sent to /admin/app/*, whose own
            # login sets a same-named cookie scoped to its own path instead. See
            # nanobar_api.admin_auth.CSRFMiddleware's docstring for the full reasoning (the CSRF
            # cookie is scoped the same way, via session_protected()'s own cookie_path).
            response.set_cookie(
                ADMIN_SESSION_COOKIE, record.session_id, httponly=True, samesite="lax", path="/admin/nanobar"
            )
            # Self-heal a real, previously-shipped bug: an earlier build of this route set
            # ADMIN_SESSION_COOKIE with no `path=` at all (defaulting to "/"), before this
            # path-scoping existed. Any browser that ever loaded that old login page is still
            # carrying that root-scoped cookie alongside this one -- the browser sends both on
            # every request here, and `request.cookies.get(...)` below only keeps one of them
            # (whichever the dict-building parser saw last), which is indistinguishable from a
            # real expired/unknown session. Explicitly expiring the root-scoped cookie here
            # clears it out of the browser's jar on the very next login-page visit, rather than
            # leaving it to silently collide forever.
            response.delete_cookie(ADMIN_SESSION_COOKIE, path="/")
            # Same self-heal for CSRF_COOKIE_NAME: the same old commit also ran csrf_protected()
            # with no cookie_path (defaulting to "/") before this surface's own "/admin/nanobar"
            # scoping existed -- a browser could be carrying a stale root-scoped CSRF cookie too.
            response.delete_cookie(CSRF_COOKIE_NAME, path="/")
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
        return JSONResponse(success({"redirect": "/admin/nanobar/dashboard"}))

    # cookie_path must match admin/nanobar/routes.py's own session_protected(cookie_path=...) --
    # both this route's CSRFMiddleware instance and the protected mount's bundled one read/write
    # the same double-submit cookie, so a token issued here stays valid for POSTs made after
    # login.
    return [
        Route(
            "/admin/nanobar/login",
            login,
            methods=["GET", "POST"],
            middleware=list(csrf_protected(cookie_path="/admin/nanobar")),
        )
    ]
