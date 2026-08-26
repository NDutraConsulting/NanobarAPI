"""Admin-domain auth: session cookie + CSRF, per `.focusari/backlog/
nanobar_admin_auth_buildplan-with-tasks.md` §3-4 -- un-backlogged for the `/admin/*` surface. See
`.focusari/nanobarAPI_plans_and_open_decisions_summary.md` Tier 3 for the current build context.

Two auth modes exist project-wide, mirroring Laravel Sanctum's SPA/token split: this module is
the cookie+CSRF ("SPA mode") half, for first-party, same-origin, browser-operated admin surfaces.
`nanobar_api/access.py`'s domain-agnostic tiers (`public`/`private`/`rate_limited`) stay separate
-- this module is admin-specific.

**An `HttpOnly` session cookie can't be read by JS at all** -- an XSS payload can act within the
session while it runs, but can't exfiltrate the credential for reuse later or elsewhere. Session
cookies also revoke instantly (`SessionBackend.delete`) where a self-contained bearer token stays
valid until its own expiry. The cost is CSRF exposure, which is a bounded, one-middleware-solved
problem (`csrf_protected()` below) -- a better trade than bearer-token XSS-theft risk for a route
real people click around in a browser.

**Deviates from the backlog doc in one place, documented not silent:** the backlog's
`protected()`/`AdminAuthMiddleware` (a general bearer-token *route* tier) isn't built here --
nothing in this build needs a bearer-only-gated route; the admin credential is only ever checked
once, at `POST /admin/login`.

**Real username/password auth, not the v1 shared-bearer-token mechanism the backlog doc
originally scoped.** That doc's own Open Decision 1 explicitly deferred real credential storage
("not building past v1 without explicit confirmation, given the security surface") -- the user
gave that confirmation directly, asking for an actual login page and a seeded `admin`/
`changeme123` account rather than one shared token everyone reads out of an env var.
`SQLiteAdminUserStore` below seeds that one account on first use (idempotent -- a second
`install_default_domains`-style construction against an already-seeded database is a no-op) and
verifies against a salted `PBKDF2-HMAC-SHA256` hash, never the plaintext password, stored
alongside `SQLiteSessionBackend`'s own sessions table in the same "adminDB" file. Still
single-account, not a real user-management system -- multi-account admin storage remains a
real, unaddressed gap (unchanged from the backlog doc's own framing), just no longer the
*credential-check mechanism* itself.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from nanobar_api.envelope import error

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSRF_COOKIE_NAME = "nanobar_csrftoken"
CSRF_HEADER_NAME = "x-nanobar-csrf-token"
ADMIN_SESSION_COOKIE = "nanobar_admin_session"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    created_at: float
    expires_at: float
    authenticated: bool


class SessionBackend(Protocol):
    def create(self, *, ttl_seconds: float) -> SessionRecord:
        """Creates a new, unauthenticated session (the `GET /admin/login` case) and returns it."""
        ...

    def get(self, session_id: str) -> SessionRecord | None:
        """Returns the session if it exists and hasn't expired, else `None`."""
        ...

    def authenticate(self, session_id: str) -> None:
        """Marks an existing session authenticated -- the `POST /admin/login` success case."""
        ...

    def delete(self, session_id: str) -> None:
        """Invalidates a session (logout)."""
        ...


class InMemorySessionBackend:
    """Default. A process-local dict, `session_id -> SessionRecord`. Two limitations, same shape
    as `access.py`'s `InMemoryRateLimitBackend`: no persistence at all (a durability problem, not
    specifically a "remote" one -- a local durable store fixes this half too), and no sharing
    across worker processes. Use `SQLiteSessionBackend` below when either matters."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}

    def create(self, *, ttl_seconds: float) -> SessionRecord:
        now = time.time()
        record = SessionRecord(
            session_id=secrets.token_urlsafe(32), created_at=now, expires_at=now + ttl_seconds, authenticated=False
        )
        self._sessions[record.session_id] = record
        return record

    def get(self, session_id: str) -> SessionRecord | None:
        record = self._sessions.get(session_id)
        if record is None or record.expires_at < time.time():
            return None
        return record

    def authenticate(self, session_id: str) -> None:
        record = self._sessions.get(session_id)
        if record is not None:
            self._sessions[session_id] = SessionRecord(
                session_id=record.session_id,
                created_at=record.created_at,
                expires_at=record.expires_at,
                authenticated=True,
            )

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


SESSION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    authenticated INTEGER NOT NULL DEFAULT 0 CHECK (authenticated IN (0, 1))
);

CREATE TABLE IF NOT EXISTS admin_users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    """Opens a connection to the admin-auth SQLite database ("adminDB"), creating the schema
    idempotently -- same `connect()` shape as `nanobar_api.bricks.store.connect`/
    `nanobar_api.eventbus.store.connect`, this project's established per-database-file pattern.
    Both `SQLiteSessionBackend` and `SQLiteAdminUserStore` share this one file/schema."""
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.executescript(SESSION_SCHEMA_SQL)
    conn.commit()
    return conn


_PBKDF2_ITERATIONS = 200_000
DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "changeme123"


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS).hex()


class SQLiteAdminUserStore:
    """Seeds and verifies admin username/password credentials in the same SQLite file
    `SQLiteSessionBackend` uses. Constructing this seeds `seed_username`/`seed_password` the
    first time only -- idempotent, checked via `SELECT ... LIMIT 1` before inserting, so a
    second construction against an already-seeded database (a second worker process, a test
    re-running against the same file) never overwrites a since-changed password."""

    def __init__(
        self,
        db_path: str,
        *,
        seed_username: str = DEFAULT_ADMIN_USERNAME,
        seed_password: str = DEFAULT_ADMIN_PASSWORD,
    ) -> None:
        self._db_path = db_path
        conn = connect(db_path)
        try:
            if conn.execute("SELECT 1 FROM admin_users LIMIT 1").fetchone() is None:
                self._insert(conn, seed_username, seed_password)
        finally:
            conn.close()

    def _insert(self, conn: sqlite3.Connection, username: str, password: str) -> None:
        salt = secrets.token_bytes(16)
        password_hash = _hash_password(password, salt)
        with conn:
            conn.execute(
                "INSERT INTO admin_users (username, password_hash, salt) VALUES (?, ?, ?)",
                (username, password_hash, salt.hex()),
            )

    def verify(self, username: str, password: str) -> bool:
        """Constant-time against a real stored hash. Fails closed (no timing difference exposed
        to distinguish "unknown username" from "wrong password") by still hashing `password`
        against a throwaway salt when `username` doesn't exist, rather than returning early."""
        conn = connect(self._db_path)
        try:
            row = conn.execute("SELECT password_hash, salt FROM admin_users WHERE username = ?", (username,)).fetchone()
        finally:
            conn.close()

        if row is None:
            _hash_password(password, secrets.token_bytes(16))
            return False

        candidate_hash = _hash_password(password, bytes.fromhex(row["salt"]))
        return hmac.compare_digest(candidate_hash, row["password_hash"])


class SQLiteSessionBackend:
    """Durable session storage in its own SQLite file ("adminDB") -- resolves
    `InMemorySessionBackend`'s two limitations for anything meant to survive a process restart.
    Opens a fresh connection per call, matching `demo/dashboard/api.py`'s own established
    per-request-connection convention, rather than holding one connection open across calls."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        connect(db_path).close()  # create the schema up front so every later call can assume it exists

    def create(self, *, ttl_seconds: float) -> SessionRecord:
        now = time.time()
        record = SessionRecord(
            session_id=secrets.token_urlsafe(32), created_at=now, expires_at=now + ttl_seconds, authenticated=False
        )
        conn = connect(self._db_path)
        try:
            with conn:
                conn.execute(
                    "INSERT INTO sessions (session_id, created_at, expires_at, authenticated) VALUES (?, ?, ?, 0)",
                    (record.session_id, record.created_at, record.expires_at),
                )
        finally:
            conn.close()
        return record

    def get(self, session_id: str) -> SessionRecord | None:
        conn = connect(self._db_path)
        try:
            row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        finally:
            conn.close()
        if row is None or row["expires_at"] < time.time():
            return None
        return SessionRecord(
            session_id=row["session_id"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            authenticated=bool(row["authenticated"]),
        )

    def authenticate(self, session_id: str) -> None:
        conn = connect(self._db_path)
        try:
            with conn:
                conn.execute("UPDATE sessions SET authenticated = 1 WHERE session_id = ?", (session_id,))
        finally:
            conn.close()

    def delete(self, session_id: str) -> None:
        conn = connect(self._db_path)
        try:
            with conn:
                conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        finally:
            conn.close()


def _set_cookie_headers(name: str, value: str, *, httponly: bool, samesite: str) -> list[tuple[bytes, bytes]]:
    """Builds just the `Set-Cookie` header bytes-tuple for `name`/`value`, reusing Starlette's own
    cookie serialization (attribute quoting, `Max-Age`, etc.) rather than hand-rolling the
    `Set-Cookie` string format. A throwaway `Response()` also carries `content-length`/other
    headers we don't want appended to the real response -- filtered out here."""
    cookie_response = Response()
    cookie_response.set_cookie(name, value, httponly=httponly, samesite=samesite)  # type: ignore[arg-type]
    return [(k, v) for k, v in cookie_response.raw_headers if k == b"set-cookie"]


class CSRFMiddleware:
    """Double-submit cookie CSRF protection. Issues `cookie_name` if absent (`Set-Cookie`, NOT
    `HttpOnly` -- JS must be able to read it to attach the matching header, `cookie_samesite` as
    defense-in-depth alongside the token check, never instead of it). For any method not in
    `_SAFE_METHODS`, requires `header_name` to match the existing cookie value via
    `secrets.compare_digest` -- mismatched or missing fails closed with 403.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        cookie_name: str = CSRF_COOKIE_NAME,
        header_name: str = CSRF_HEADER_NAME,
        cookie_samesite: str = "lax",
    ) -> None:
        self.app = app
        self.cookie_name = cookie_name
        self.header_name = header_name
        self.cookie_samesite = cookie_samesite

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        token = request.cookies.get(self.cookie_name)

        if request.method not in _SAFE_METHODS:
            header_token = request.headers.get(self.header_name)
            if not token or not header_token or not secrets.compare_digest(token, header_token):
                response = JSONResponse(error("CSRF token missing or invalid"), status_code=403)
                await response(scope, receive, send)
                return

        if token is not None:
            await self.app(scope, receive, send)
            return

        new_token = secrets.token_urlsafe(32)
        cookie_headers = _set_cookie_headers(self.cookie_name, new_token, httponly=False, samesite=self.cookie_samesite)

        async def wrapped_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + cookie_headers
            await send(message)

        await self.app(scope, receive, wrapped_send)


def csrf_protected(*, cookie_samesite: str = "lax") -> tuple[Middleware, ...]:
    return (Middleware(CSRFMiddleware, cookie_samesite=cookie_samesite),)


class AdminSessionMiddleware:
    """Reads `ADMIN_SESSION_COOKIE`, resolves it via `backend.get()`. Missing/expired/unknown/
    unauthenticated: a path containing `/api/` gets a 401 `envelope.error()` (matches
    `demo/dashboard/api.py`'s existing JSON-API convention exactly); any other path (an HTML admin
    page) gets a redirect to `/admin/login` -- resolves the backlog doc's own Open Decision 2
    (redirect vs. JSON 401 depends on route shape) via that already-established path convention,
    not a new per-rule flag or `Accept`-header sniff.
    """

    def __init__(self, app: ASGIApp, *, backend: SessionBackend, ttl_seconds: float = 3600.0) -> None:
        self.app = app
        self.backend = backend
        self.ttl_seconds = ttl_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        session_id = request.cookies.get(ADMIN_SESSION_COOKIE)
        record = self.backend.get(session_id) if session_id is not None else None

        if record is None or not record.authenticated:
            response: Response
            if "/api/" in scope["path"]:
                response = JSONResponse(error("authentication required"), status_code=401)
            else:
                response = RedirectResponse(url="/admin/login", status_code=302)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def session_protected(
    *, cookie_samesite: str = "lax", backend: SessionBackend | None = None, ttl_seconds: float = 3600.0
) -> tuple[Middleware, ...]:
    """Sanctum-SPA-mode equivalent for the admin surface. Bundles `AdminSessionMiddleware` with
    `csrf_protected()` automatically -- there's no legitimate reason to use cookie-session auth
    without CSRF protection, so the factory that grants the cookie also grants the protection, by
    construction, not by convention."""
    return (
        Middleware(
            AdminSessionMiddleware,
            backend=backend if backend is not None else InMemorySessionBackend(),
            ttl_seconds=ttl_seconds,
        ),
    ) + csrf_protected(cookie_samesite=cookie_samesite)
