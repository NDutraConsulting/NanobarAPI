from __future__ import annotations

import time
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from nanobar_api.admin_auth import (
    ADMIN_SESSION_COOKIE,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    DEFAULT_ADMIN_PASSWORD,
    DEFAULT_ADMIN_USERNAME,
    AdminSessionMiddleware,
    CSRFMiddleware,
    InMemorySessionBackend,
    SessionRecord,
    SQLiteAdminUserStore,
    SQLiteSessionBackend,
    csrf_protected,
    session_protected,
)


async def _ok(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


def _app_with_middleware(*middleware: Middleware, path: str = "/x", methods: list[str] | None = None) -> Starlette:
    return Starlette(routes=[Route(path, _ok, methods=methods)], middleware=list(middleware))


# ------------------------------------------------------------- InMemorySessionBackend


def test_in_memory_backend_create_get_round_trips() -> None:
    backend = InMemorySessionBackend()

    record = backend.create(ttl_seconds=60.0)

    assert backend.get(record.session_id) == record
    assert record.authenticated is False


def test_in_memory_backend_get_unknown_session_is_none() -> None:
    backend = InMemorySessionBackend()

    assert backend.get("nope") is None


def test_in_memory_backend_get_expired_session_is_none() -> None:
    backend = InMemorySessionBackend()
    record = backend.create(ttl_seconds=-1.0)  # already expired

    assert backend.get(record.session_id) is None


def test_in_memory_backend_authenticate_marks_session_authenticated() -> None:
    backend = InMemorySessionBackend()
    record = backend.create(ttl_seconds=60.0)

    backend.authenticate(record.session_id)

    fetched = backend.get(record.session_id)
    assert fetched is not None
    assert fetched.authenticated is True


def test_in_memory_backend_authenticate_unknown_session_is_a_noop() -> None:
    backend = InMemorySessionBackend()

    backend.authenticate("nope")  # must not raise


def test_in_memory_backend_delete_invalidates_session() -> None:
    backend = InMemorySessionBackend()
    record = backend.create(ttl_seconds=60.0)

    backend.delete(record.session_id)

    assert backend.get(record.session_id) is None


# --------------------------------------------------------------- SQLiteSessionBackend


def test_sqlite_backend_create_get_round_trips(tmp_path: Path) -> None:
    backend = SQLiteSessionBackend(str(tmp_path / "admin.db"))

    record = backend.create(ttl_seconds=60.0)

    fetched = backend.get(record.session_id)
    assert fetched == record


def test_sqlite_backend_get_unknown_session_is_none(tmp_path: Path) -> None:
    backend = SQLiteSessionBackend(str(tmp_path / "admin.db"))

    assert backend.get("nope") is None


def test_sqlite_backend_get_expired_session_is_none(tmp_path: Path) -> None:
    backend = SQLiteSessionBackend(str(tmp_path / "admin.db"))
    record = backend.create(ttl_seconds=-1.0)

    assert backend.get(record.session_id) is None


def test_sqlite_backend_authenticate_marks_session_authenticated(tmp_path: Path) -> None:
    backend = SQLiteSessionBackend(str(tmp_path / "admin.db"))
    record = backend.create(ttl_seconds=60.0)

    backend.authenticate(record.session_id)

    fetched = backend.get(record.session_id)
    assert fetched is not None
    assert fetched.authenticated is True


def test_sqlite_backend_authenticate_unknown_session_is_a_noop(tmp_path: Path) -> None:
    backend = SQLiteSessionBackend(str(tmp_path / "admin.db"))

    backend.authenticate("nope")  # must not raise


def test_sqlite_backend_delete_invalidates_session(tmp_path: Path) -> None:
    backend = SQLiteSessionBackend(str(tmp_path / "admin.db"))
    record = backend.create(ttl_seconds=60.0)

    backend.delete(record.session_id)

    assert backend.get(record.session_id) is None


def test_sqlite_backend_survives_a_fresh_instance_against_the_same_file(tmp_path: Path) -> None:
    # The whole point of this backend over InMemorySessionBackend: durable across a process
    # restart. Simulated here by constructing a second, independent backend instance pointing
    # at the same db_path -- if this failed, "adminDB" would be no better than in-memory.
    db_path = str(tmp_path / "admin.db")
    first = SQLiteSessionBackend(db_path)
    record = first.create(ttl_seconds=60.0)
    first.authenticate(record.session_id)

    second = SQLiteSessionBackend(db_path)
    fetched = second.get(record.session_id)

    assert fetched is not None
    assert fetched.authenticated is True


def test_sqlite_backend_connect_creates_schema_idempotently(tmp_path: Path) -> None:
    db_path = str(tmp_path / "admin.db")

    SQLiteSessionBackend(db_path)
    SQLiteSessionBackend(db_path)  # must not raise on a pre-existing schema


# ------------------------------------------------------------------ CSRFMiddleware


def test_csrf_issues_cookie_on_a_safe_method_when_absent() -> None:
    app = _app_with_middleware(Middleware(CSRFMiddleware))
    client = TestClient(app)

    response = client.get("/x")

    assert response.status_code == 200
    assert CSRF_COOKIE_NAME in response.cookies


def test_csrf_does_not_reissue_cookie_when_already_present() -> None:
    app = _app_with_middleware(Middleware(CSRFMiddleware))
    client = TestClient(app)
    client.get("/x")
    existing = client.cookies[CSRF_COOKIE_NAME]

    response = client.get("/x")

    assert "set-cookie" not in response.headers
    assert client.cookies[CSRF_COOKIE_NAME] == existing


def test_csrf_rejects_unsafe_method_with_no_token_at_all() -> None:
    app = _app_with_middleware(Middleware(CSRFMiddleware), methods=["GET", "POST"])
    client = TestClient(app)

    response = client.post("/x")

    assert response.status_code == 403


def test_csrf_rejects_unsafe_method_with_header_but_no_cookie() -> None:
    app = _app_with_middleware(Middleware(CSRFMiddleware), methods=["GET", "POST"])
    client = TestClient(app)

    response = client.post("/x", headers={CSRF_HEADER_NAME: "forged"})

    assert response.status_code == 403


def test_csrf_rejects_unsafe_method_when_header_does_not_match_cookie() -> None:
    app = _app_with_middleware(Middleware(CSRFMiddleware), methods=["GET", "POST"])
    client = TestClient(app)
    client.get("/x")

    response = client.post("/x", headers={CSRF_HEADER_NAME: "wrong-token"})

    assert response.status_code == 403


def test_csrf_allows_unsafe_method_when_header_matches_cookie() -> None:
    app = _app_with_middleware(Middleware(CSRFMiddleware), methods=["GET", "POST"])
    client = TestClient(app)
    client.get("/x")
    token = client.cookies[CSRF_COOKIE_NAME]

    response = client.post("/x", headers={CSRF_HEADER_NAME: token})

    assert response.status_code == 200


def test_csrf_protected_returns_one_middleware_wrapping_csrf() -> None:
    tiers = csrf_protected()

    assert len(tiers) == 1
    assert tiers[0].cls is CSRFMiddleware  # type: ignore[comparison-overlap]


def test_csrf_non_http_scope_passes_through() -> None:
    # lifespan-scope requests (app startup/shutdown) must never be CSRF-checked.
    reached = []

    async def raw_app(scope: object, receive: object, send: object) -> None:
        reached.append(scope)

    middleware = CSRFMiddleware(app=raw_app)

    async def fake_receive() -> dict[str, str]:
        return {"type": "lifespan.startup"}

    async def fake_send(message: object) -> None:
        pass

    import asyncio

    asyncio.run(middleware({"type": "lifespan"}, fake_receive, fake_send))

    assert reached == [{"type": "lifespan"}]


# ------------------------------------------------------------ AdminSessionMiddleware


def _login_client(*, ttl_seconds: float = 3600.0) -> tuple[TestClient, InMemorySessionBackend]:
    backend = InMemorySessionBackend()
    app = _app_with_middleware(Middleware(AdminSessionMiddleware, backend=backend, ttl_seconds=ttl_seconds), path="/x")
    return TestClient(app, follow_redirects=False), backend


def test_admin_session_redirects_html_path_when_no_cookie() -> None:
    client, _ = _login_client()

    response = client.get("/x")

    assert response.status_code == 302
    assert response.headers["location"] == "/admin/login"


def test_admin_session_401s_api_path_when_no_cookie() -> None:
    backend = InMemorySessionBackend()
    app = _app_with_middleware(Middleware(AdminSessionMiddleware, backend=backend), path="/admin/nanobar/api/nanobars")
    client = TestClient(app, follow_redirects=False)

    response = client.get("/admin/nanobar/api/nanobars")

    assert response.status_code == 401
    assert response.json()["status"] == "error"


def test_admin_session_redirects_when_session_is_unauthenticated() -> None:
    client, backend = _login_client()
    record = backend.create(ttl_seconds=60.0)
    client.cookies.set(ADMIN_SESSION_COOKIE, record.session_id)

    response = client.get("/x")

    assert response.status_code == 302


def test_admin_session_redirects_when_session_is_unknown() -> None:
    client, _ = _login_client()
    client.cookies.set(ADMIN_SESSION_COOKIE, "not-a-real-session")

    response = client.get("/x")

    assert response.status_code == 302


def test_admin_session_allows_authenticated_session() -> None:
    client, backend = _login_client()
    record = backend.create(ttl_seconds=60.0)
    backend.authenticate(record.session_id)
    client.cookies.set(ADMIN_SESSION_COOKIE, record.session_id)

    response = client.get("/x")

    assert response.status_code == 200


def test_admin_session_non_http_scope_passes_through() -> None:
    reached = []

    async def raw_app(scope: object, receive: object, send: object) -> None:
        reached.append(scope)

    middleware = AdminSessionMiddleware(app=raw_app, backend=InMemorySessionBackend())

    async def fake_receive() -> dict[str, str]:
        return {"type": "lifespan.startup"}

    async def fake_send(message: object) -> None:
        pass

    import asyncio

    asyncio.run(middleware({"type": "lifespan"}, fake_receive, fake_send))

    assert reached == [{"type": "lifespan"}]


def test_session_protected_bundles_session_and_csrf_middleware() -> None:
    tiers = session_protected()

    assert len(tiers) == 2
    assert tiers[0].cls is AdminSessionMiddleware  # type: ignore[comparison-overlap]
    assert tiers[1].cls is CSRFMiddleware  # type: ignore[comparison-overlap]


def test_session_protected_defaults_to_a_fresh_in_memory_backend() -> None:
    tiers = session_protected()

    assert isinstance(tiers[0].kwargs["backend"], InMemorySessionBackend)


# ------------------------------------------------------------------ SQLiteAdminUserStore


def test_sqlite_admin_user_store_seeds_the_default_account_on_first_use(tmp_path: Path) -> None:
    store = SQLiteAdminUserStore(str(tmp_path / "admin.db"))

    assert store.verify(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD) is True


def test_sqlite_admin_user_store_rejects_wrong_password(tmp_path: Path) -> None:
    store = SQLiteAdminUserStore(str(tmp_path / "admin.db"))

    assert store.verify(DEFAULT_ADMIN_USERNAME, "wrong") is False


def test_sqlite_admin_user_store_rejects_unknown_username(tmp_path: Path) -> None:
    store = SQLiteAdminUserStore(str(tmp_path / "admin.db"))

    assert store.verify("nope", DEFAULT_ADMIN_PASSWORD) is False


def test_sqlite_admin_user_store_accepts_a_custom_seed(tmp_path: Path) -> None:
    store = SQLiteAdminUserStore(str(tmp_path / "admin.db"), seed_username="alice", seed_password="s3cr3t")

    assert store.verify("alice", "s3cr3t") is True
    assert store.verify(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD) is False


def test_sqlite_admin_user_store_seeding_is_idempotent_across_instances(tmp_path: Path) -> None:
    db_path = str(tmp_path / "admin.db")
    SQLiteAdminUserStore(db_path)
    # A second construction against the same file must not re-seed (and so must not, say,
    # silently reset a password that was changed in between) -- verified indirectly here since
    # this store has no change-password method yet: re-seeding would raise on the PRIMARY KEY
    # collision if the "already seeded" check were missing.
    second = SQLiteAdminUserStore(db_path)

    assert second.verify(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD) is True


def test_sqlite_admin_user_store_password_is_never_stored_in_plaintext(tmp_path: Path) -> None:
    db_path = str(tmp_path / "admin.db")
    SQLiteAdminUserStore(db_path)

    raw = (tmp_path / "admin.db").read_bytes()

    assert DEFAULT_ADMIN_PASSWORD.encode() not in raw


# --------------------------------------------------------------------- SessionRecord


def test_session_record_is_a_frozen_dataclass() -> None:
    record = SessionRecord(session_id="a", created_at=1.0, expires_at=2.0, authenticated=False)

    with pytest.raises(AttributeError):
        record.authenticated = True  # type: ignore[misc]


def test_time_progresses_expiry_check_uses_real_clock() -> None:
    # Sanity check that expiry is wall-clock based, not a stub -- a record created "now" with a
    # tiny ttl really does expire almost immediately.
    backend = InMemorySessionBackend()
    record = backend.create(ttl_seconds=0.01)
    time.sleep(0.05)

    assert backend.get(record.session_id) is None
