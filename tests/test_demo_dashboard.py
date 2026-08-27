from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app.admin.app.auth_db import (
    DB_PATH_ENV_VAR as APP_ADMIN_DB_PATH_ENV_VAR,
    DEFAULT_DB_PATH as APP_ADMIN_DEFAULT_DB_PATH,
    resolve_db_path as resolve_app_admin_db_path,
)
from app.admin.nanobar.auth_db import (
    DB_PATH_ENV_VAR as NANOBAR_ADMIN_DB_PATH_ENV_VAR,
    DEFAULT_DB_PATH as NANOBAR_ADMIN_DEFAULT_DB_PATH,
    resolve_db_path as resolve_nanobar_admin_db_path,
)
from app.admin.nanobar.db import DB_PATH_ENV_VAR, DEFAULT_DB_PATH, resolve_db_path
from app.admin.nanobar.events_db import (
    DB_PATH_ENV_VAR as EVENTS_DB_PATH_ENV_VAR,
    DEFAULT_DB_PATH as EVENTS_DEFAULT_DB_PATH,
    resolve_db_path as resolve_events_db_path,
)
from app.core.config import (
    ROUTE_MANIFEST_DEFAULT_PATH,
    ROUTE_MANIFEST_PATH_ENV_VAR,
    resolve_route_manifest_path,
)
from app.main import build_app
from nanobar_api.admin_auth import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME
from nanobar_api.bricks.schema import MonitorTargetRef, Nanobar, NanobarBrickBinding, RegressionBrick
from nanobar_api.bricks.store import bind_brick_to_nanobar, connect, insert_brick, insert_nanobar, set_review_status
from nanobar_api.eventbus.events import Event
from nanobar_api.eventbus.store import connect as events_connect, insert_events, register_worker
from nanobar_api.worker_utils import WorkerLogEntry, log_worker_failure


def _make_brick(
    brick_id: str,
    content_hash: str,
    *,
    method: str | None = "GET",
    path: str | None = "/checkout",
    status_code: int | None = 200,
    regression_scenario_type: str | None = None,
) -> RegressionBrick:
    return RegressionBrick(
        regression_brick_id=brick_id,
        schema_version="1.0",
        brick_version=1,
        source={"host": "test"},
        request={"method": method, "path": path, "headers": {}, "query_params": {}, "payload": {}},
        response={"status_code": status_code, "payload": {"ok": True}},
        content_hash=content_hash,
        created_by="test",
        regression_scenario_type=regression_scenario_type,
    )


def _make_nanobar(
    nanobar_id: str,
    target_refs: list[MonitorTargetRef] | None = None,
    *,
    system_name: str = "checkout-service",
    nanobar_type: str = "api-response",
    domain: str | None = None,
) -> Nanobar:
    return Nanobar(
        nanobar_id=nanobar_id,
        schema_version="1.0",
        system_name=system_name,
        system_version="1.0.0",
        nanobar_type=nanobar_type,
        request_object_id="req-1",
        response_object_id="res-1",
        regression_weight=0.5,
        endpoint_scenario_frequency={"state": "unmeasured"},
        created_by="test",
        domain=domain,
        monitor_target_refs=(
            target_refs if target_refs is not None else [MonitorTargetRef("openapi_operation", "checkout")]
        ),
    )


def _bind(db_path: str, nanobar_id: str, brick_id: str) -> None:
    conn = connect(db_path)
    try:
        bind_brick_to_nanobar(
            conn,
            NanobarBrickBinding(
                nanobar_id=nanobar_id,
                regression_brick_id=brick_id,
                match_method="exact",
                matcher_version="v1",
                matched_by="test",
            ),
        )
    finally:
        conn.close()


def _make_span_event(
    event_id: str,
    trace_id: str,
    *,
    span_id: str = "span-1",
    monotonic_ns: int = 1_000_000,
    recorded_at_ns: int = 1_700_000_000_000_000_000,
    name: str = "GET /checkout",
    method: str = "GET",
    route: str | None = "/checkout",
    status_code: int | None = 200,
    error: bool = False,
    nanobar_type: str | None = None,
) -> Event:
    payload: dict[str, object] = {
        "name": name,
        "http.request.method": method,
        "http.route": route,
        "status_code": status_code,
        "attributes": {},
        "error": error,
    }
    if nanobar_type is not None:
        payload["nanobar_type"] = nanobar_type
    return Event(
        event_id=event_id,
        channel="trace",
        recorded_at_ns=recorded_at_ns,
        monotonic_ns=monotonic_ns,
        payload=payload,
        trace_id=trace_id,
        span_id=span_id,
    )


NANOBAR_LOGIN_PATH = "/admin/nanobar/login"
APP_LOGIN_PATH = "/admin/app/login"


def _authenticate(
    test_client: TestClient, *, login_path: str = NANOBAR_LOGIN_PATH, set_default_header: bool = True
) -> str:
    """Drives a real `GET`+`POST login_path` flow against `test_client` -- not a bypass, the
    actual round trip (`GET` issues session + CSRF cookies scoped to that surface's own path;
    `POST` verifies the username/password against `SQLiteAdminUserStore`'s seeded
    `admin`/`changeme123` account, authenticates the session). Reads the CSRF token off the
    `GET` response's own cookies, not the client's aggregate cookie jar -- once a test
    authenticates against *both* independent admin surfaces (each sets a same-named
    `nanobar_csrftoken`, scoped to a different path), the aggregate jar holds two same-named
    cookies and `client.cookies[name]` raises `CookieConflict`.

    Returns the CSRF token used. `set_default_header=True` (the default) also sets it as a
    *default* header on the client (`httpx.Client.headers` applies to every subsequent request),
    so most callers' own POST/PATCH/DELETE calls never need to attach it individually -- correct
    as long as a test only ever authenticates against one surface. A test exercising both
    surfaces in one session should pass `set_default_header=False` for (at least) the second
    call, and attach the returned token explicitly per-request instead, since only one surface's
    token can be "the default" at a time.
    """
    get_response = test_client.get(login_path)
    csrf_token = get_response.cookies["nanobar_csrftoken"]
    if set_default_header:
        test_client.headers["x-nanobar-csrf-token"] = csrf_token
    login_response = test_client.post(
        login_path,
        json={"username": DEFAULT_ADMIN_USERNAME, "password": DEFAULT_ADMIN_PASSWORD},
        headers={"x-nanobar-csrf-token": csrf_token},
    )
    assert login_response.status_code == 200
    return csrf_token


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "regression_bricks.db")


@pytest.fixture
def events_db_path(tmp_path: Path) -> str:
    return str(tmp_path / "events.db")


@pytest.fixture
def app_admin_db_path(tmp_path: Path) -> str:
    return str(tmp_path / "app_admin.db")


@pytest.fixture
def nanobar_admin_db_path(tmp_path: Path) -> str:
    return str(tmp_path / "nanobar_admin.db")


@pytest.fixture
def nanobar_type_system_db_path(tmp_path: Path) -> str:
    return str(tmp_path / "nanobar_type_system.db")


@pytest.fixture
def route_manifest_path(tmp_path: Path) -> str:
    return str(tmp_path / "nanobar.api-routes.json")


@pytest.fixture
def client(
    db_path: str,
    events_db_path: str,
    app_admin_db_path: str,
    nanobar_admin_db_path: str,
    nanobar_type_system_db_path: str,
    route_manifest_path: str,
) -> TestClient:
    test_client = TestClient(
        build_app(
            db_path=db_path,
            events_db_path=events_db_path,
            app_admin_db_path=app_admin_db_path,
            nanobar_admin_db_path=nanobar_admin_db_path,
            nanobar_type_system_db_path=nanobar_type_system_db_path,
            route_manifest_path=route_manifest_path,
        )
    )
    _authenticate(test_client)
    return test_client


# ---------------------------------------------------------------------- admin auth wiring ---
#
# The unit-level behavior of session_protected()/CSRFMiddleware/SQLiteSessionBackend is covered
# by tests/test_admin_auth.py. These verify the *real app* is actually wired up to use two fully
# independent admin surfaces -- an unauthenticated request to either real mount really is gated,
# each real login route really does establish and authenticate its own session end to end, and
# (this app's actual point) authenticating one surface never authenticates, or otherwise
# disturbs, the other.


def _unauthenticated_client(
    db_path: str, events_db_path: str, app_admin_db_path: str, nanobar_admin_db_path: str
) -> TestClient:
    return TestClient(
        build_app(
            db_path=db_path,
            events_db_path=events_db_path,
            app_admin_db_path=app_admin_db_path,
            nanobar_admin_db_path=nanobar_admin_db_path,
        ),
        follow_redirects=False,
    )


@pytest.mark.parametrize(
    ("login_path", "dashboard_path", "api_path"),
    [
        (NANOBAR_LOGIN_PATH, "/admin/nanobar/dashboard", "/admin/nanobar/api/nanobars"),
        (APP_LOGIN_PATH, "/admin/app/dashboard", "/admin/app/api/posts"),
    ],
    ids=["nanobar", "app"],
)
def test_unauthenticated_dashboard_request_redirects_to_that_surfaces_own_login(
    db_path: str,
    events_db_path: str,
    app_admin_db_path: str,
    nanobar_admin_db_path: str,
    login_path: str,
    dashboard_path: str,
    api_path: str,
) -> None:
    client = _unauthenticated_client(db_path, events_db_path, app_admin_db_path, nanobar_admin_db_path)

    response = client.get(dashboard_path)

    assert response.status_code == 302
    assert response.headers["location"] == login_path


@pytest.mark.parametrize(
    ("login_path", "dashboard_path", "api_path"),
    [
        (NANOBAR_LOGIN_PATH, "/admin/nanobar/dashboard", "/admin/nanobar/api/nanobars"),
        (APP_LOGIN_PATH, "/admin/app/dashboard", "/admin/app/api/posts"),
    ],
    ids=["nanobar", "app"],
)
def test_unauthenticated_api_request_gets_401_envelope(
    db_path: str,
    events_db_path: str,
    app_admin_db_path: str,
    nanobar_admin_db_path: str,
    login_path: str,
    dashboard_path: str,
    api_path: str,
) -> None:
    client = _unauthenticated_client(db_path, events_db_path, app_admin_db_path, nanobar_admin_db_path)

    response = client.get(api_path)

    assert response.status_code == 401
    assert response.json()["status"] == "error"


@pytest.mark.parametrize("login_path", [NANOBAR_LOGIN_PATH, APP_LOGIN_PATH], ids=["nanobar", "app"])
def test_login_get_serves_the_login_page_and_issues_cookies(
    db_path: str, events_db_path: str, app_admin_db_path: str, nanobar_admin_db_path: str, login_path: str
) -> None:
    client = _unauthenticated_client(db_path, events_db_path, app_admin_db_path, nanobar_admin_db_path)

    response = client.get(login_path)

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "nanobar_admin_session" in response.cookies
    assert "nanobar_csrftoken" in response.cookies


@pytest.mark.parametrize("login_path", [NANOBAR_LOGIN_PATH, APP_LOGIN_PATH], ids=["nanobar", "app"])
def test_login_post_without_a_prior_get_is_rejected(
    db_path: str, events_db_path: str, app_admin_db_path: str, nanobar_admin_db_path: str, login_path: str
) -> None:
    client = _unauthenticated_client(db_path, events_db_path, app_admin_db_path, nanobar_admin_db_path)

    # No prior GET -> no session cookie, no CSRF cookie/header -- CSRFMiddleware rejects first.
    response = client.post(login_path, json={"username": DEFAULT_ADMIN_USERNAME, "password": DEFAULT_ADMIN_PASSWORD})

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("login_path", "api_path"),
    [(NANOBAR_LOGIN_PATH, "/admin/nanobar/api/nanobars"), (APP_LOGIN_PATH, "/admin/app/api/posts")],
    ids=["nanobar", "app"],
)
def test_login_post_with_wrong_password_is_rejected(
    db_path: str,
    events_db_path: str,
    app_admin_db_path: str,
    nanobar_admin_db_path: str,
    login_path: str,
    api_path: str,
) -> None:
    client = _unauthenticated_client(db_path, events_db_path, app_admin_db_path, nanobar_admin_db_path)
    get_response = client.get(login_path)
    csrf_token = get_response.cookies["nanobar_csrftoken"]

    response = client.post(
        login_path,
        json={"username": DEFAULT_ADMIN_USERNAME, "password": "wrong-password"},
        headers={"x-nanobar-csrf-token": csrf_token},
    )

    assert response.status_code == 401
    # A wrong-password attempt must not authenticate the session -- confirmed by a follow-up
    # request still being gated, not just by this response's own status code.
    still_gated = client.get(api_path)
    assert still_gated.status_code == 401


@pytest.mark.parametrize("login_path", [NANOBAR_LOGIN_PATH, APP_LOGIN_PATH], ids=["nanobar", "app"])
def test_login_post_with_unknown_username_is_rejected(
    db_path: str, events_db_path: str, app_admin_db_path: str, nanobar_admin_db_path: str, login_path: str
) -> None:
    client = _unauthenticated_client(db_path, events_db_path, app_admin_db_path, nanobar_admin_db_path)
    get_response = client.get(login_path)
    csrf_token = get_response.cookies["nanobar_csrftoken"]

    response = client.post(
        login_path,
        json={"username": "not-a-real-user", "password": DEFAULT_ADMIN_PASSWORD},
        headers={"x-nanobar-csrf-token": csrf_token},
    )

    assert response.status_code == 401


@pytest.mark.parametrize("login_path", [NANOBAR_LOGIN_PATH, APP_LOGIN_PATH], ids=["nanobar", "app"])
def test_login_post_missing_password_field_is_rejected(
    db_path: str, events_db_path: str, app_admin_db_path: str, nanobar_admin_db_path: str, login_path: str
) -> None:
    client = _unauthenticated_client(db_path, events_db_path, app_admin_db_path, nanobar_admin_db_path)
    get_response = client.get(login_path)
    csrf_token = get_response.cookies["nanobar_csrftoken"]

    response = client.post(
        login_path, json={"username": DEFAULT_ADMIN_USERNAME}, headers={"x-nanobar-csrf-token": csrf_token}
    )

    assert response.status_code == 400


@pytest.mark.parametrize(
    ("login_path", "dashboard_path", "redirect"),
    [
        (NANOBAR_LOGIN_PATH, "/admin/nanobar/dashboard", "/admin/nanobar/dashboard"),
        (APP_LOGIN_PATH, "/admin/app/dashboard", "/admin/app/dashboard"),
    ],
    ids=["nanobar", "app"],
)
def test_full_login_flow_grants_access_to_that_surfaces_own_gated_dashboard(
    db_path: str,
    events_db_path: str,
    app_admin_db_path: str,
    nanobar_admin_db_path: str,
    login_path: str,
    dashboard_path: str,
    redirect: str,
) -> None:
    client = _unauthenticated_client(db_path, events_db_path, app_admin_db_path, nanobar_admin_db_path)

    csrf_token = _authenticate(client, login_path=login_path)
    login_response = client.post(
        login_path,
        json={"username": DEFAULT_ADMIN_USERNAME, "password": DEFAULT_ADMIN_PASSWORD},
        headers={"x-nanobar-csrf-token": csrf_token},
    )

    assert login_response.status_code == 200
    assert login_response.json()["result"]["data"]["redirect"] == redirect

    dashboard_response = client.get(dashboard_path)
    assert dashboard_response.status_code == 200


def test_authenticating_one_admin_surface_never_authenticates_the_other(
    db_path: str, events_db_path: str, app_admin_db_path: str, nanobar_admin_db_path: str
) -> None:
    client = _unauthenticated_client(db_path, events_db_path, app_admin_db_path, nanobar_admin_db_path)

    _authenticate(client, login_path=NANOBAR_LOGIN_PATH)

    assert client.get("/admin/nanobar/dashboard").status_code == 200
    # Still gated -- a session on the nanobar-admin surface grants nothing on the app-admin one.
    assert client.get("/admin/app/dashboard").status_code == 302
    assert client.get("/admin/app/api/posts").status_code == 401

    _authenticate(client, login_path=APP_LOGIN_PATH, set_default_header=False)

    # Now both are authenticated, independently, at the same time.
    assert client.get("/admin/app/dashboard").status_code == 200
    assert client.get("/admin/nanobar/dashboard").status_code == 200


# --------------------------------------------------------------------------------- api ---


def test_api_list_nanobars_empty(client: TestClient) -> None:
    response = client.get("/admin/nanobar/api/nanobars")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["result"]["data"] == {"items": [], "page": 1, "page_size": 50, "total": 0}


def test_api_list_nanobars_returns_all(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-route", [MonitorTargetRef("openapi_operation", "checkout")]))
    insert_nanobar(conn, _make_nanobar("nb-service", [MonitorTargetRef("service", "billing")]))
    conn.close()

    response = client.get("/admin/nanobar/api/nanobars")

    body = response.json()["result"]["data"]
    ids = {n["nanobar_id"] for n in body["items"]}
    assert ids == {"nb-route", "nb-service"}
    assert body["total"] == 2


def test_api_list_nanobars_filters_by_target_type(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-route", [MonitorTargetRef("openapi_operation", "checkout")]))
    insert_nanobar(conn, _make_nanobar("nb-service", [MonitorTargetRef("service", "billing")]))
    conn.close()

    response = client.get("/admin/nanobar/api/nanobars", params={"target_type": "service"})

    items = response.json()["result"]["data"]["items"]
    assert [n["nanobar_id"] for n in items] == ["nb-service"]


def test_api_list_nanobars_filters_by_domain(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-app", domain="admin/app"))
    insert_nanobar(conn, _make_nanobar("nb-kanban", domain="boards"))
    insert_nanobar(conn, _make_nanobar("nb-unmapped", domain=None))
    conn.close()

    app_only = client.get("/admin/nanobar/api/nanobars", params={"domain": "admin/app"}).json()["result"]["data"][
        "items"
    ]
    unmapped_only = client.get("/admin/nanobar/api/nanobars", params={"domain": "(unmapped)"}).json()["result"]["data"][
        "items"
    ]

    assert [n["nanobar_id"] for n in app_only] == ["nb-app"]
    assert [n["nanobar_id"] for n in unmapped_only] == ["nb-unmapped"]


def test_api_list_nanobars_filters_by_nanobar_type(db_path: str, client: TestClient) -> None:
    # nanobar_type is what the dashboard list page actually groups/filters by -- see
    # app/pages/nanobars/nanobars-controller.js.
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-1", nanobar_type="validator-request-response"))
    insert_nanobar(conn, _make_nanobar("nb-2", nanobar_type="orm-request-response"))
    conn.close()

    response = client.get("/admin/nanobar/api/nanobars", params={"nanobar_type": "validator-request-response"})

    items = response.json()["result"]["data"]["items"]
    assert [n["nanobar_id"] for n in items] == ["nb-1"]


def test_api_list_nanobars_searches_label(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-route", [MonitorTargetRef("openapi_operation", "checkout")]))
    insert_nanobar(conn, _make_nanobar("nb-service", [MonitorTargetRef("service", "billing")]))
    conn.close()
    client.patch("/admin/nanobar/api/nanobars/nb-service", json={"label": "Billing service"})

    response = client.get("/admin/nanobar/api/nanobars", params={"q": "billing"})

    items = response.json()["result"]["data"]["items"]
    assert [n["nanobar_id"] for n in items] == ["nb-service"]


def test_api_list_nanobars_paginates(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    for i in range(3):
        insert_nanobar(conn, _make_nanobar(f"nb-{i}"))
    conn.close()

    response = client.get("/admin/nanobar/api/nanobars", params={"page": 2, "page_size": 2})

    body = response.json()["result"]["data"]
    assert len(body["items"]) == 1
    assert body["page"] == 2
    assert body["page_size"] == 2
    assert body["total"] == 3


def test_api_list_nanobars_invalid_page_is_rejected(client: TestClient) -> None:
    response = client.get("/admin/nanobar/api/nanobars", params={"page": "not-a-number"})

    assert response.status_code == 400
    assert response.json()["status"] == "error"


def test_api_generate_bricks_action_identifies_captured_snapshot_event_spans(
    events_db_path: str, client: TestClient
) -> None:
    """Same shape `capture_layer()` writes onto the "snapshot" channel for a real controller-
    layer call -- inserted directly, standing in for a real `/admin/app/*` request, so this
    test doesn't need to drive the full blog domain just to prove the route identifies the
    captured event-span in events.db and generates its brick in regression_bricks.db, the same
    way `examples/generate_dashboard_bricks.py` does.
    """
    conn = events_connect(events_db_path)
    try:
        insert_events(
            conn,
            [
                Event(
                    event_id="evt-capture-1",
                    channel="snapshot",
                    recorded_at_ns=1,
                    monotonic_ns=1,
                    payload={
                        "request": {"title": "Hello"},
                        "response": {"id": "post-1", "title": "Hello"},
                        "content_hash": "deadbeef",
                        "error": False,
                        "nanobar_type": "controller-request-response",
                        "route_key": "POST /admin/app/api/posts",
                    },
                    trace_id="tr-1",
                    span_id="span-1",
                )
            ],
        )
    finally:
        conn.close()

    response = client.post("/admin/nanobar/api/generate-bricks")

    assert response.status_code == 200
    data = response.json()["result"]["data"]
    assert data["new_bricks"] == 1
    assert data["nanobars_created"] == 1
    assert data["bindings_created"] == 1
    assert data["total_nanobars"] == 1

    nanobars = client.get("/admin/nanobar/api/nanobars").json()["result"]["data"]["items"]
    assert len(nanobars) == 1
    assert nanobars[0]["nanobar_type"] == "controller-request-response"
    # generate_bricks_action loads the route manifest and passes it through, so a
    # newly-created nanobar for a real declared route is stamped with its domain immediately
    # -- not left None until a later "Nanobar refresh" backfills it.
    assert nanobars[0]["domain"] == "admin/app"


def test_api_generate_bricks_action_is_a_safe_noop_when_nothing_to_process(client: TestClient) -> None:
    response = client.post("/admin/nanobar/api/generate-bricks")

    assert response.status_code == 200
    data = response.json()["result"]["data"]
    assert data == {"new_bricks": 0, "nanobars_created": 0, "bindings_created": 0, "skipped": 0, "total_nanobars": 0}


# -------------------------------------------------------------------- refresh/nanobars ---


def test_api_refresh_nanobars_action_creates_unclassified_placeholders_for_every_route(
    route_manifest_path: str, client: TestClient
) -> None:
    manifest_route_count = len(json.loads(Path(route_manifest_path).read_text())["routes"])

    response = client.post("/admin/nanobar/api/refresh/nanobars")

    assert response.status_code == 200
    data = response.json()["result"]["data"]
    assert data["routes_scanned"] == manifest_route_count
    assert data["nanobars_created"] == manifest_route_count
    assert data["domains_updated"] == 0

    nanobars = client.get(
        "/admin/nanobar/api/nanobars", params={"nanobar_type": "unclassified", "page_size": 200}
    ).json()["result"]["data"]["items"]
    by_route_key = {n["monitor_target_refs"][0]["stable_name"]: n for n in nanobars}
    assert by_route_key["GET /admin/nanobar/dashboard"]["domain"] == "admin/nanobar"
    assert by_route_key["GET /admin/app/dashboard"]["domain"] == "admin/app"


def test_api_refresh_nanobars_action_does_not_duplicate_on_a_second_call(client: TestClient) -> None:
    client.post("/admin/nanobar/api/refresh/nanobars")

    response = client.post("/admin/nanobar/api/refresh/nanobars")

    assert response.json()["result"]["data"]["nanobars_created"] == 0


def test_api_refresh_nanobars_action_backfills_domain_on_an_existing_nanobar(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_nanobar(
        conn,
        _make_nanobar(
            "nb-1",
            [MonitorTargetRef(target_type="route", stable_name="GET /admin/nanobar/dashboard")],
            nanobar_type="validator-request-response",
        ),
    )
    conn.close()
    assert client.get("/admin/nanobar/api/nanobars/nb-1").json()["result"]["data"]["domain"] is None

    response = client.post("/admin/nanobar/api/refresh/nanobars")

    assert response.json()["result"]["data"]["domains_updated"] == 1
    assert client.get("/admin/nanobar/api/nanobars/nb-1").json()["result"]["data"]["domain"] == "admin/nanobar"


# ------------------------------------------------------------------------- refresh status ---


def test_api_refresh_status_is_all_null_before_anything_but_launch_has_run(client: TestClient) -> None:
    # build_app() itself records the "api" cycle on launch ("built on launch") -- "nanobars"
    # and "bricks" have never run yet in a fresh client.
    response = client.get("/admin/nanobar/api/refresh/status")

    assert response.status_code == 200
    data = response.json()["result"]["data"]
    assert data["api"] is not None
    assert "route(s) across" in data["api"]["summary"]
    assert data["nanobars"] is None
    assert data["bricks"] is None


def test_api_refresh_status_reflects_nanobars_and_bricks_after_running_them(client: TestClient) -> None:
    client.post("/admin/nanobar/api/refresh/nanobars")
    client.post("/admin/nanobar/api/generate-bricks")

    data = client.get("/admin/nanobar/api/refresh/status").json()["result"]["data"]

    assert data["nanobars"] is not None
    assert data["bricks"] is not None
    assert "last_run_at" in data["nanobars"]


def test_api_refresh_api_routes_action_rewrites_the_manifest_and_records_status(
    route_manifest_path: str, client: TestClient
) -> None:
    original_generated_at = json.loads(Path(route_manifest_path).read_text())["generated_at"]

    response = client.post("/admin/nanobar/api/refresh/api-routes")

    assert response.status_code == 200
    data = response.json()["result"]["data"]
    assert data["routes_scanned"] > 0
    assert data["domains"] == 3  # "", "admin/app", "admin/nanobar"

    # A real rewrite happened -- not a no-op returning stale/cached data.
    refreshed_generated_at = json.loads(Path(route_manifest_path).read_text())["generated_at"]
    assert refreshed_generated_at >= original_generated_at

    status = client.get("/admin/nanobar/api/refresh/status").json()["result"]["data"]
    assert status["api"]["summary"] == f"{data['routes_scanned']} route(s) across {data['domains']} domain(s)"


def test_api_nanobar_detail_found_and_not_found(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-1"))
    conn.close()

    found = client.get("/admin/nanobar/api/nanobars/nb-1")
    missing = client.get("/admin/nanobar/api/nanobars/does-not-exist")

    assert found.status_code == 200
    assert found.json()["result"]["data"]["nanobar_id"] == "nb-1"
    assert missing.status_code == 404


def test_api_replay_brick_action_end_to_end(tmp_path: Path) -> None:
    """The one real end-to-end proof of Design Decision G (nanobar-dashboard-search-and-replay-
    upgrade-plan.md): create a real post through the live app's own session-gated
    /admin/app/api/posts (real capture_layer() output), turn that into a real bound brick via
    the already-tested generate-bricks action, then Run it -- and confirm the replay produced a
    real verdict/trace_id *and* never touched the live app's own blog.db (only the sibling
    blog_shadow.db).
    """
    db_path = str(tmp_path / "regression_bricks.db")
    events_db_path = str(tmp_path / "events.db")
    app_admin_db_path = str(tmp_path / "app_admin.db")
    nanobar_admin_db_path = str(tmp_path / "nanobar_admin.db")
    blog_db_path = str(tmp_path / "blog.db")
    shadow_blog_db_path = str(tmp_path / "blog_shadow.db")

    app = build_app(
        db_path=db_path,
        events_db_path=events_db_path,
        app_admin_db_path=app_admin_db_path,
        nanobar_admin_db_path=nanobar_admin_db_path,
        blog_db_path=blog_db_path,
        route_manifest_path=str(tmp_path / "nanobar.api-routes.json"),
    )
    with TestClient(app) as test_client:
        # Two independent admin surfaces exercised in one session -- nanobar's token becomes the
        # client's default header (every /admin/nanobar/* call below relies on it); app's token
        # is attached explicitly to the one /admin/app/* mutating call, per _authenticate's own
        # docstring on why only one surface's token can be "the default" at a time.
        _authenticate(test_client, login_path=NANOBAR_LOGIN_PATH)
        app_csrf_token = _authenticate(test_client, login_path=APP_LOGIN_PATH, set_default_header=False)

        created = test_client.post(
            "/admin/app/api/posts",
            json={"title": "Hello", "body": "World"},
            headers={"x-nanobar-csrf-token": app_csrf_token},
        )
        assert created.status_code == 200

        # capture_layer()'s events reach events.db via a background EventThread that flushes in
        # batches on a timer (see eventbus/event_thread.py) -- not synchronously with the POST
        # above -- so generate-bricks may need a couple of tries before anything's landed yet.
        for _ in range(50):
            generated = test_client.post("/admin/nanobar/api/generate-bricks")
            if generated.json()["result"]["data"]["new_bricks"] > 0:
                break
            time.sleep(0.05)
        else:
            pytest.fail("captured events never reached events.db")

        nanobars = test_client.get("/admin/nanobar/api/nanobars").json()["result"]["data"]["items"]
        controller_nanobar = next(n for n in nanobars if n["nanobar_type"] == "controller-request-response")
        bricks = test_client.get(f"/admin/nanobar/api/nanobars/{controller_nanobar['nanobar_id']}/bricks").json()[
            "result"
        ]["data"]
        brick = next(b for b in bricks if b["source"].get("route_key") == "POST /admin/app/api/posts")

        assert not Path(shadow_blog_db_path).exists()

        response = test_client.post(f"/admin/nanobar/api/bricks/{brick['regression_brick_id']}/replay")

        assert response.status_code == 200
        data = response.json()["result"]["data"]
        assert isinstance(data["trace_id"], str) and data["trace_id"]
        assert data["verdict"]["overall_passed"] is True
        assert data["replayed_response"]["status_code"] == 200

        # The replay's own write landed in the shadow blog db, not the live app's real one.
        assert Path(shadow_blog_db_path).exists()
        real_posts = test_client.get("/admin/app/api/posts").json()["result"]["data"]
        assert len(real_posts) == 1  # only the original create, not a second one from the replay


def test_api_replay_brick_action_honors_a_shadow_db_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same end-to-end shape as `test_api_replay_brick_action_end_to_end`, but with
    `NANOBAR_BLOG_SHADOW_DB` set -- proves `ShadowPersistenceProfile`'s env var override actually
    redirects the replay's write, not just that `resolve_shadow_connection()` returns the right
    string in isolation (see `tests/test_bricks_shadow_profile.py` for that unit-level proof)."""
    override_shadow_db_path = str(tmp_path / "custom_shadow.db")
    default_shadow_db_path = str(tmp_path / "blog_shadow.db")
    monkeypatch.setenv("NANOBAR_BLOG_SHADOW_DB", override_shadow_db_path)

    app = build_app(
        db_path=str(tmp_path / "regression_bricks.db"),
        events_db_path=str(tmp_path / "events.db"),
        app_admin_db_path=str(tmp_path / "app_admin.db"),
        nanobar_admin_db_path=str(tmp_path / "nanobar_admin.db"),
        blog_db_path=str(tmp_path / "blog.db"),
        route_manifest_path=str(tmp_path / "nanobar.api-routes.json"),
    )
    with TestClient(app) as test_client:
        _authenticate(test_client, login_path=NANOBAR_LOGIN_PATH)
        app_csrf_token = _authenticate(test_client, login_path=APP_LOGIN_PATH, set_default_header=False)

        created = test_client.post(
            "/admin/app/api/posts",
            json={"title": "Hello", "body": "World"},
            headers={"x-nanobar-csrf-token": app_csrf_token},
        )
        assert created.status_code == 200

        for _ in range(50):
            generated = test_client.post("/admin/nanobar/api/generate-bricks")
            if generated.json()["result"]["data"]["new_bricks"] > 0:
                break
            time.sleep(0.05)
        else:
            pytest.fail("captured events never reached events.db")

        nanobars = test_client.get("/admin/nanobar/api/nanobars").json()["result"]["data"]["items"]
        controller_nanobar = next(n for n in nanobars if n["nanobar_type"] == "controller-request-response")
        bricks = test_client.get(f"/admin/nanobar/api/nanobars/{controller_nanobar['nanobar_id']}/bricks").json()[
            "result"
        ]["data"]
        brick = next(b for b in bricks if b["source"].get("route_key") == "POST /admin/app/api/posts")

        response = test_client.post(f"/admin/nanobar/api/bricks/{brick['regression_brick_id']}/replay")

        assert response.status_code == 200
        assert Path(override_shadow_db_path).exists()
        assert not Path(default_shadow_db_path).exists()


def test_api_replay_brick_action_not_found(client: TestClient) -> None:
    response = client.post("/admin/nanobar/api/bricks/does-not-exist/replay")

    assert response.status_code == 404


def test_api_nanobar_bricks_not_found(client: TestClient) -> None:
    response = client.get("/admin/nanobar/api/nanobars/does-not-exist/bricks")

    assert response.status_code == 404
    body = response.json()
    assert body["status"] == "error"
    assert "does-not-exist" in body["msg"]


def test_api_nanobar_bricks_includes_review_status_defaulting_to_new(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-1"))
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one"))
    insert_brick(conn, _make_brick("rbrick-2", "sha256:two"))
    conn.close()
    _bind(db_path, "nb-1", "rbrick-1")
    _bind(db_path, "nb-1", "rbrick-2")
    set_review_status(connect(db_path), "rbrick-2", "flagged", updated_by="alice")

    response = client.get("/admin/nanobar/api/nanobars/nb-1/bricks")

    assert response.status_code == 200
    data = {b["regression_brick_id"]: b for b in response.json()["result"]["data"]}
    assert data["rbrick-1"]["review_status"]["status"] == "new"
    assert data["rbrick-2"]["review_status"]["status"] == "flagged"


def test_api_nanobar_coverage_gaps_not_found(client: TestClient) -> None:
    response = client.get("/admin/nanobar/api/nanobars/does-not-exist/coverage-gaps")

    assert response.status_code == 404


def test_api_nanobar_coverage_gaps_lists_missing_required_scenarios(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-1"))  # nanobar_type="api-response"
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one", regression_scenario_type="success"))
    conn.close()
    _bind(db_path, "nb-1", "rbrick-1")

    response = client.get("/admin/nanobar/api/nanobars/nb-1/coverage-gaps")

    assert response.status_code == 200
    data = response.json()["result"]["data"]
    assert data["status"] == "classified"
    # "api-response" requires success/invalid_input/server_error -- only success is covered.
    assert set(data["gaps"]) == {"invalid_input", "server_error"}


def test_api_nanobar_coverage_gaps_empty_when_fully_covered(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-1"))
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one", regression_scenario_type="success"))
    insert_brick(conn, _make_brick("rbrick-2", "sha256:two", regression_scenario_type="invalid_input"))
    insert_brick(conn, _make_brick("rbrick-3", "sha256:three", regression_scenario_type="server_error"))
    conn.close()
    _bind(db_path, "nb-1", "rbrick-1")
    _bind(db_path, "nb-1", "rbrick-2")
    _bind(db_path, "nb-1", "rbrick-3")

    response = client.get("/admin/nanobar/api/nanobars/nb-1/coverage-gaps")

    data = response.json()["result"]["data"]
    assert data["status"] == "classified"
    assert data["gaps"] == []


# --------------------------------------------------------------- dynamic taxonomy (worker-*) ---


def test_api_nanobar_coverage_gaps_auto_registers_a_worker_channel_on_first_sight(
    db_path: str, client: TestClient
) -> None:
    """A worker-{channel} nanobar has no static taxonomy entry -- the first coverage-gaps (or
    criticality-changing update) request for it auto-registers a per-channel entry in
    nanobar_type_system.db, seeded from the static generic "worker" baseline, and every later
    request for the *same* channel reuses that same entry rather than re-seeding it."""
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-1", nanobar_type="worker-domain.appointments"))
    conn.close()

    response = client.get("/admin/nanobar/api/nanobars/nb-1/coverage-gaps")

    assert response.status_code == 200
    data = response.json()["result"]["data"]
    assert data["status"] == "classified"
    # The vendored "worker" entry requires success + server_error; nothing bound yet -- both gaps.
    assert set(data["gaps"]) == {"success", "server_error"}

    registered = client.get("/admin/nanobar/api/dynamic-taxonomy").json()["result"]["data"]
    assert len(registered) == 1
    assert registered[0]["key"] == "worker"
    assert registered[0]["key_name"] == "domain.appointments"
    assert registered[0]["nanobar_type"] == "worker-domain.appointments"
    assert set(registered[0]["expected_scenarios"]) == {"success", "server_error"}


def test_api_dynamic_taxonomy_filters_by_key(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-1", nanobar_type="worker-domain.appointments"))
    insert_nanobar(conn, _make_nanobar("nb-2", nanobar_type="worker-domain.orders"))
    conn.close()
    client.get("/admin/nanobar/api/nanobars/nb-1/coverage-gaps")
    client.get("/admin/nanobar/api/nanobars/nb-2/coverage-gaps")

    all_entries = client.get("/admin/nanobar/api/dynamic-taxonomy").json()["result"]["data"]
    assert len(all_entries) == 2

    worker_entries = client.get("/admin/nanobar/api/dynamic-taxonomy", params={"key": "worker"}).json()["result"][
        "data"
    ]
    assert len(worker_entries) == 2

    other_key_entries = client.get("/admin/nanobar/api/dynamic-taxonomy", params={"key": "replay"}).json()["result"][
        "data"
    ]
    assert other_key_entries == []


def test_api_update_nanobar_criticality_change_recomputes_weight_for_a_worker_channel(
    db_path: str, client: TestClient
) -> None:
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-1", nanobar_type="worker-domain.appointments"))
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one", regression_scenario_type="success"))
    conn.close()
    _bind(db_path, "nb-1", "rbrick-1")

    response = client.patch("/admin/nanobar/api/nanobars/nb-1", json={"criticality": 1.0})

    assert response.status_code == 200
    # worker's required weights: success=1.0, server_error=0.3 -> total 1.3; only success (1.0)
    # covered -> (1.0/1.3) * criticality(1.0).
    assert response.json()["result"]["data"]["regression_weight"] == pytest.approx((1.0 / 1.3) * 1.0)


def test_api_nanobar_coverage_gaps_unrecognized_dynamic_prefix_needs_classification(
    db_path: str, client: TestClient
) -> None:
    # A nanobar_type that isn't in the static taxonomy and doesn't match any recognized dynamic
    # prefix (only "worker-" is registered) can't have coverage computed for it -- it needs a
    # human to classify it, not a silently guessed-at empty gaps list.
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-1", nanobar_type="totally-unrecognized-type"))
    conn.close()

    response = client.get("/admin/nanobar/api/nanobars/nb-1/coverage-gaps")

    data = response.json()["result"]["data"]
    assert data == {
        "status": "needs_classification",
        "nanobar_type": "totally-unrecognized-type",
        "gaps": [],
        "related_span": None,
    }
    assert client.get("/admin/nanobar/api/dynamic-taxonomy").json()["result"]["data"] == []


def test_api_nanobar_coverage_gaps_needs_classification_links_the_latest_matching_span(
    db_path: str, events_db_path: str, client: TestClient
) -> None:
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-1", nanobar_type="totally-unrecognized-type"))
    conn.close()

    events_conn = events_connect(events_db_path)
    try:
        insert_events(
            events_conn,
            [
                _make_span_event(
                    "evt-old",
                    "tr-old",
                    monotonic_ns=1_000,
                    recorded_at_ns=1_700_000_000_000_000_000,
                    name="old span",
                    nanobar_type="totally-unrecognized-type",
                ),
                _make_span_event(
                    "evt-new",
                    "tr-new",
                    monotonic_ns=2_000,
                    recorded_at_ns=1_700_000_001_000_000_000,
                    name="new span",
                    nanobar_type="totally-unrecognized-type",
                ),
            ],
        )
    finally:
        events_conn.close()

    response = client.get("/admin/nanobar/api/nanobars/nb-1/coverage-gaps")

    data = response.json()["result"]["data"]
    assert data["status"] == "needs_classification"
    assert data["related_span"]["trace_id"] == "tr-new"
    assert data["related_span"]["event_id"] == "evt-new"
    assert data["related_span"]["name"] == "new span"


def test_api_brick_detail_found(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one"))
    conn.close()

    response = client.get("/admin/nanobar/api/bricks/rbrick-1")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    data = body["result"]["data"]
    assert data["regression_brick_id"] == "rbrick-1"
    assert data["request"]["path"] == "/checkout"
    assert data["review_status"]["status"] == "new"


def test_api_brick_detail_not_found(client: TestClient) -> None:
    response = client.get("/admin/nanobar/api/bricks/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert body["status"] == "error"
    assert "does-not-exist" in body["msg"]


def test_api_set_review_status_valid(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one"))
    conn.close()

    response = client.post("/admin/nanobar/api/bricks/rbrick-1/review-status", json={"status": "flagged"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["result"]["data"]["status"] == "flagged"
    assert body["result"]["data"]["updated_by"] == "dashboard"

    # And it actually persisted.
    followup = client.get("/admin/nanobar/api/bricks/rbrick-1")
    assert followup.json()["result"]["data"]["review_status"]["status"] == "flagged"


def test_api_set_review_status_via_patch(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one"))
    conn.close()

    response = client.patch("/admin/nanobar/api/bricks/rbrick-1/review-status", json={"status": "reviewed"})

    assert response.status_code == 200
    assert response.json()["result"]["data"]["status"] == "reviewed"


def test_api_set_review_status_invalid_status_value(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one"))
    conn.close()

    response = client.post("/admin/nanobar/api/bricks/rbrick-1/review-status", json={"status": "bogus"})

    assert response.status_code == 400
    body = response.json()
    assert body["status"] == "error"
    assert "invalid review status" in body["msg"]


def test_api_set_review_status_brick_not_found(client: TestClient) -> None:
    response = client.post("/admin/nanobar/api/bricks/does-not-exist/review-status", json={"status": "flagged"})

    assert response.status_code == 404
    assert response.json()["status"] == "error"


def test_api_set_review_status_malformed_json_body(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one"))
    conn.close()

    response = client.post(
        "/admin/nanobar/api/bricks/rbrick-1/review-status",
        content=b"{not valid json",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert "JSON" in response.json()["msg"]


def test_api_set_review_status_missing_status_field(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one"))
    conn.close()

    response = client.post("/admin/nanobar/api/bricks/rbrick-1/review-status", json={})

    assert response.status_code == 400
    assert "status" in response.json()["msg"]


def test_api_set_review_status_body_not_an_object(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one"))
    conn.close()

    response = client.post("/admin/nanobar/api/bricks/rbrick-1/review-status", json=["flagged"])

    assert response.status_code == 400
    assert "status" in response.json()["msg"]


def test_api_list_nanobars_includes_type_and_navigation_fields(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-1"))
    conn.close()

    data = client.get("/admin/nanobar/api/nanobars").json()["result"]["data"]["items"]

    assert data[0]["nanobar_type"] == "api-response"
    assert data[0]["label"] is None
    assert data[0]["scenario_description"] is None
    assert data[0]["component_source_description"] is None


def test_api_brick_detail_includes_scenario_type_scenario_and_tags(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one", status_code=404))
    conn.close()

    data = client.get("/admin/nanobar/api/bricks/rbrick-1").json()["result"]["data"]

    assert data["regression_scenario_type"] is None  # store round-trip only -- not classified here
    assert data["scenario"] == {
        "regression_brick_id": "rbrick-1",
        "regression_scenario_label": None,
        "description": None,
        "updated_by": "system",
    }
    assert data["tags"] == []


def test_api_update_nanobar_partial_update(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-1"))
    conn.close()

    first = client.patch("/admin/nanobar/api/nanobars/nb-1", json={"label": "Get order"})
    assert first.status_code == 200
    assert first.json()["result"]["data"]["label"] == "Get order"

    # Second call only sets scenario_description -- label from the first call must survive.
    second = client.patch("/admin/nanobar/api/nanobars/nb-1", json={"scenario_description": "Fetches an order."})

    assert second.status_code == 200
    data = second.json()["result"]["data"]
    assert data["label"] == "Get order"
    assert data["scenario_description"] == "Fetches an order."


def test_api_update_nanobar_not_found(client: TestClient) -> None:
    response = client.patch("/admin/nanobar/api/nanobars/does-not-exist", json={"label": "X"})

    assert response.status_code == 404
    assert response.json()["status"] == "error"


def test_api_update_nanobar_rejects_non_string_field(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-1"))
    conn.close()

    response = client.patch("/admin/nanobar/api/nanobars/nb-1", json={"label": 123})

    assert response.status_code == 400
    assert "label" in response.json()["msg"]


def test_api_update_nanobar_rejects_malformed_json(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-1"))
    conn.close()

    response = client.patch(
        "/admin/nanobar/api/nanobars/nb-1", content=b"{not valid json", headers={"content-type": "application/json"}
    )

    assert response.status_code == 400
    assert "JSON" in response.json()["msg"]


def test_api_update_nanobar_rejects_body_not_an_object(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-1"))
    conn.close()

    response = client.patch("/admin/nanobar/api/nanobars/nb-1", json=["label"])

    assert response.status_code == 400


def test_api_update_nanobar_sets_criticality(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-1"))
    conn.close()

    response = client.patch("/admin/nanobar/api/nanobars/nb-1", json={"criticality": 0.9})

    assert response.status_code == 200
    assert response.json()["result"]["data"]["criticality"] == 0.9


def test_api_update_nanobar_criticality_defaults_to_current_value_when_omitted(
    db_path: str, client: TestClient
) -> None:
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-1"))
    conn.close()

    client.patch("/admin/nanobar/api/nanobars/nb-1", json={"criticality": 0.8})
    response = client.patch("/admin/nanobar/api/nanobars/nb-1", json={"label": "Get order"})

    assert response.json()["result"]["data"]["criticality"] == 0.8


def test_api_update_nanobar_criticality_change_recomputes_regression_weight(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-1"))
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one", regression_scenario_type="success"))
    conn.close()
    _bind(db_path, "nb-1", "rbrick-1")

    response = client.patch("/admin/nanobar/api/nanobars/nb-1", json={"criticality": 1.0})

    # "api-response" taxonomy: success(1.0)+invalid_input(0.6)+server_error(0.3) required,
    # total 1.9 -- only "success" covered here.
    assert response.json()["result"]["data"]["regression_weight"] == pytest.approx((1.0 / 1.9) * 1.0)


def test_api_update_nanobar_without_criticality_change_does_not_recompute_weight(
    db_path: str, client: TestClient
) -> None:
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-1"))
    conn.close()

    response = client.patch("/admin/nanobar/api/nanobars/nb-1", json={"label": "Get order"})

    assert response.json()["result"]["data"]["regression_weight"] == 0.5  # unchanged placeholder


def test_api_update_nanobar_rejects_criticality_out_of_range(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-1"))
    conn.close()

    response = client.patch("/admin/nanobar/api/nanobars/nb-1", json={"criticality": 1.5})

    assert response.status_code == 400
    assert "criticality" in response.json()["msg"]


def test_api_update_nanobar_rejects_criticality_bool(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-1"))
    conn.close()

    response = client.patch("/admin/nanobar/api/nanobars/nb-1", json={"criticality": True})

    assert response.status_code == 400


def test_api_update_nanobar_rejects_criticality_non_number(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-1"))
    conn.close()

    response = client.patch("/admin/nanobar/api/nanobars/nb-1", json={"criticality": "high"})

    assert response.status_code == 400


def test_api_set_brick_scenario_partial_update(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one"))
    conn.close()

    first = client.post(
        "/admin/nanobar/api/bricks/rbrick-1/scenario", json={"regression_scenario_label": "Order not found"}
    )
    assert first.status_code == 200
    assert first.json()["result"]["data"]["regression_scenario_label"] == "Order not found"
    assert first.json()["result"]["data"]["updated_by"] == "dashboard"

    # Second call (via PATCH this time) only sets description -- label must survive.
    second = client.patch(
        "/admin/nanobar/api/bricks/rbrick-1/scenario", json={"description": "The order id does not exist."}
    )

    assert second.status_code == 200
    data = second.json()["result"]["data"]
    assert data["regression_scenario_label"] == "Order not found"
    assert data["description"] == "The order id does not exist."

    # And it actually persisted onto the brick detail response.
    followup = client.get("/admin/nanobar/api/bricks/rbrick-1")
    assert followup.json()["result"]["data"]["scenario"]["description"] == "The order id does not exist."


def test_api_set_brick_scenario_brick_not_found(client: TestClient) -> None:
    response = client.post("/admin/nanobar/api/bricks/does-not-exist/scenario", json={"regression_scenario_label": "X"})

    assert response.status_code == 404
    assert response.json()["status"] == "error"


def test_api_set_brick_scenario_rejects_non_string_field(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one"))
    conn.close()

    response = client.post("/admin/nanobar/api/bricks/rbrick-1/scenario", json={"description": 123})

    assert response.status_code == 400
    assert "description" in response.json()["msg"]


def test_api_set_brick_scenario_rejects_malformed_json(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one"))
    conn.close()

    response = client.post(
        "/admin/nanobar/api/bricks/rbrick-1/scenario",
        content=b"{not valid json",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert "JSON" in response.json()["msg"]


def test_api_set_brick_scenario_rejects_body_not_an_object(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one"))
    conn.close()

    response = client.post("/admin/nanobar/api/bricks/rbrick-1/scenario", json=["label"])

    assert response.status_code == 400


def test_api_add_and_remove_brick_tags(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one"))
    conn.close()

    added_first = client.post("/admin/nanobar/api/bricks/rbrick-1/tags", json={"tag": "flaky"})
    added_second = client.post("/admin/nanobar/api/bricks/rbrick-1/tags", json={"tag": "checkout"})

    assert added_first.status_code == 200
    assert added_first.json()["result"]["data"] == ["flaky"]
    assert added_second.json()["result"]["data"] == ["checkout", "flaky"]

    removed = client.delete("/admin/nanobar/api/bricks/rbrick-1/tags/flaky")

    assert removed.status_code == 200
    assert removed.json()["result"]["data"] == ["checkout"]

    # And it actually persisted onto the brick detail response.
    followup = client.get("/admin/nanobar/api/bricks/rbrick-1")
    assert followup.json()["result"]["data"]["tags"] == ["checkout"]


def test_api_add_brick_tag_is_idempotent(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one"))
    conn.close()

    client.post("/admin/nanobar/api/bricks/rbrick-1/tags", json={"tag": "flaky"})
    response = client.post("/admin/nanobar/api/bricks/rbrick-1/tags", json={"tag": "flaky"})

    assert response.json()["result"]["data"] == ["flaky"]


def test_api_add_brick_tag_brick_not_found(client: TestClient) -> None:
    response = client.post("/admin/nanobar/api/bricks/does-not-exist/tags", json={"tag": "flaky"})

    assert response.status_code == 404
    assert response.json()["status"] == "error"


def test_api_add_brick_tag_rejects_missing_tag_field(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one"))
    conn.close()

    response = client.post("/admin/nanobar/api/bricks/rbrick-1/tags", json={})

    assert response.status_code == 400
    assert "tag" in response.json()["msg"]


def test_api_add_brick_tag_rejects_empty_tag(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one"))
    conn.close()

    response = client.post("/admin/nanobar/api/bricks/rbrick-1/tags", json={"tag": ""})

    assert response.status_code == 400


def test_api_add_brick_tag_rejects_malformed_json(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one"))
    conn.close()

    response = client.post(
        "/admin/nanobar/api/bricks/rbrick-1/tags",
        content=b"{not valid json",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert "JSON" in response.json()["msg"]


def test_api_remove_brick_tag_brick_not_found(client: TestClient) -> None:
    response = client.delete("/admin/nanobar/api/bricks/does-not-exist/tags/flaky")

    assert response.status_code == 404
    assert response.json()["status"] == "error"


def test_api_remove_brick_tag_not_present_is_a_no_op(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one"))
    conn.close()

    response = client.delete("/admin/nanobar/api/bricks/rbrick-1/tags/never-added")

    assert response.status_code == 200
    assert response.json()["result"]["data"] == []


# ------------------------------------------------------------------------------- pages ---
#
# Pages are served as static files (no server-side templating or database access) — the
# page's own JS fetches its data client-side from the JSON API tested above. So these tests
# only verify routing/serving: the right file comes back, regardless of what's in the db.


def test_nanobars_page_served(client: TestClient) -> None:
    response = client.get("/admin/nanobar/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Nanobars · Nanobar Dashboard" in response.text


def test_dashboard_route_alias_serves_same_page(client: TestClient) -> None:
    response = client.get("/admin/nanobar/dashboard")

    assert response.status_code == 200
    assert "Nanobars · Nanobar Dashboard" in response.text


def test_nanobar_detail_page_served(client: TestClient) -> None:
    response = client.get("/admin/nanobar/nanobars/nb-1")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Nanobar · NanobarAPI" in response.text


def test_triage_page_served(client: TestClient) -> None:
    response = client.get("/admin/nanobar/triage")

    assert response.status_code == 200
    assert "Triage board · Nanobar Dashboard" in response.text


def test_static_assets_served_for_each_page(client: TestClient) -> None:
    for page in ("nanobars", "nanobar", "triage", "traces", "trace", "workers"):
        css = client.get(f"/static/{page}/{page}.css")
        controller = client.get(f"/static/{page}/{page}-controller.js")
        api_js = client.get(f"/static/{page}/{page}-api.js")
        ui_js = client.get(f"/static/{page}/{page}-ui.js")

        assert css.status_code == 200, page
        assert "css" in css.headers["content-type"], page
        for asset in (controller, api_js, ui_js):
            assert asset.status_code == 200, page
            assert "javascript" in asset.headers["content-type"], page


# --------------------------------------------------------------------------- traces api ---


def test_api_list_traces_empty(client: TestClient) -> None:
    response = client.get("/admin/nanobar/api/traces")

    assert response.status_code == 200
    assert response.json()["result"]["data"]["items"] == []


def test_api_list_traces_returns_summaries(events_db_path: str, client: TestClient) -> None:
    conn = events_connect(events_db_path)
    try:
        insert_events(
            conn,
            [
                _make_span_event("evt-1", "tr-1", monotonic_ns=1_000),
                _make_span_event("evt-2", "tr-1", monotonic_ns=2_000),
            ],
        )
    finally:
        conn.close()

    # _make_span_event's fixed historical recorded_at_ns predates "today" -- show_all=1 opts out
    # of the traces list's real, server-side default date window (see the search-and-replay
    # plan doc) so these fixture events are visible without needing a clock-dependent timestamp.
    response = client.get("/admin/nanobar/api/traces", params={"show_all": "1"})

    assert response.status_code == 200
    body = response.json()["result"]["data"]
    assert len(body["items"]) == 1
    assert body["items"][0]["trace_id"] == "tr-1"
    assert body["items"][0]["span_count"] == 2
    assert body["items"][0]["any_error"] is False
    assert body["total"] == 1


def test_api_list_traces_defaults_to_todays_traces_only(events_db_path: str, client: TestClient) -> None:
    conn = events_connect(events_db_path)
    try:
        insert_events(conn, [_make_span_event("evt-old", "tr-old", monotonic_ns=1_000)])
    finally:
        conn.close()

    response = client.get("/admin/nanobar/api/traces")

    assert response.status_code == 200
    assert response.json()["result"]["data"]["items"] == []


def test_api_list_traces_invalid_page(client: TestClient) -> None:
    response = client.get("/admin/nanobar/api/traces?page=not-a-number")

    assert response.status_code == 400
    assert response.json()["status"] == "error"


def test_api_list_traces_invalid_created_after(client: TestClient) -> None:
    response = client.get("/admin/nanobar/api/traces?created_after=not-a-date")

    assert response.status_code == 400
    assert response.json()["status"] == "error"


def test_api_trace_facets_returns_distinct_values(events_db_path: str, client: TestClient) -> None:
    conn = events_connect(events_db_path)
    try:
        insert_events(
            conn,
            [
                _make_span_event(
                    "evt-1",
                    "tr-1",
                    monotonic_ns=1_000,
                    name="controller.POST /x",
                    recorded_at_ns=1_700_000_000_000_000_000,
                ),
            ],
        )
    finally:
        conn.close()

    response = client.get("/admin/nanobar/api/traces/facets", params={"show_all": "1"})

    assert response.status_code == 200
    data = response.json()["result"]["data"]
    assert "components" in data
    assert "nanobar_types" in data


def test_api_trace_spans_ordered_and_not_found(events_db_path: str, client: TestClient) -> None:
    conn = events_connect(events_db_path)
    try:
        insert_events(
            conn,
            [
                _make_span_event("evt-late", "tr-1", monotonic_ns=3_000, span_id="span-late"),
                _make_span_event("evt-early", "tr-1", monotonic_ns=1_000, span_id="span-early"),
            ],
        )
    finally:
        conn.close()

    ok = client.get("/admin/nanobar/api/traces/tr-1/spans")
    missing = client.get("/admin/nanobar/api/traces/does-not-exist/spans")

    assert ok.status_code == 200
    data = ok.json()["result"]["data"]
    assert [e["event_id"] for e in data] == ["evt-early", "evt-late"]
    assert missing.status_code == 404
    assert missing.json()["status"] == "error"


# ------------------------------------------------------------------------- traces pages ---
#
# Same static-serving story as the pages section above — content assertions belong to the
# API tests; these just confirm the right static file is routed to.


def test_traces_list_page_served(client: TestClient) -> None:
    response = client.get("/admin/nanobar/traces")

    assert response.status_code == 200
    assert "Traces · Nanobar Dashboard" in response.text


def test_trace_detail_page_served(client: TestClient) -> None:
    response = client.get("/admin/nanobar/traces/tr-1")

    assert response.status_code == 200
    assert "Trace · Nanobar Dashboard" in response.text


# -------------------------------------------------------------------------- workers api ---


def test_workers_page_served(client: TestClient) -> None:
    response = client.get("/admin/nanobar/workers")

    assert response.status_code == 200
    assert "Workers · Nanobar Dashboard" in response.text


def test_api_list_workers_empty(client: TestClient) -> None:
    response = client.get("/admin/nanobar/api/workers")

    assert response.status_code == 200
    assert response.json()["result"]["data"] == []


def test_api_list_workers_returns_config_and_computed_is_stale(events_db_path: str, client: TestClient) -> None:
    conn = events_connect(events_db_path)
    try:
        register_worker(
            conn,
            "worker-fresh",
            ["ch1"],
            mode="listening",
            poll_interval_s=1.0,
            claim_limit=10,
            lease_seconds=30.0,
        )
        register_worker(conn, "worker-stale", ["ch2"], mode="cron", schedule="0 * * * *")
        conn.execute(
            "UPDATE workers SET last_heartbeat_at = datetime('now', '-1 hour') WHERE worker_id = ?",
            ("worker-stale",),
        )
        conn.commit()
    finally:
        conn.close()

    response = client.get("/admin/nanobar/api/workers", params={"stale_seconds": "60"})

    assert response.status_code == 200
    by_id = {w["worker_id"]: w for w in response.json()["result"]["data"]}
    assert by_id["worker-fresh"]["is_stale"] is False
    assert by_id["worker-fresh"]["channels"] == ["ch1"]
    assert by_id["worker-fresh"]["poll_interval_s"] == 1.0
    assert by_id["worker-stale"]["is_stale"] is True
    assert by_id["worker-stale"]["schedule"] == "0 * * * *"


def test_api_list_workers_rejects_a_non_numeric_stale_seconds(client: TestClient) -> None:
    response = client.get("/admin/nanobar/api/workers", params={"stale_seconds": "not-a-number"})

    assert response.status_code == 400
    assert response.json()["status"] == "error"


def test_api_worker_log_returns_entries_for_that_worker_only(events_db_path: str, client: TestClient) -> None:
    conn = events_connect(events_db_path)
    try:
        log_worker_failure(
            conn,
            WorkerLogEntry(worker_id="worker-a", event_id="evt-1", error="boom", logged_at="2026-01-01 00:00:00"),
            log_dir=str(Path(events_db_path).parent / "logs"),
        )
        log_worker_failure(
            conn,
            WorkerLogEntry(worker_id="worker-b", event_id="evt-2", error="other", logged_at="2026-01-01 00:00:01"),
            log_dir=str(Path(events_db_path).parent / "logs"),
        )
    finally:
        conn.close()

    response = client.get("/admin/nanobar/api/workers/worker-a/log")

    assert response.status_code == 200
    data = response.json()["result"]["data"]
    assert len(data) == 1
    assert data[0]["event_id"] == "evt-1"
    assert data[0]["error"] == "boom"


def test_api_worker_log_empty_for_a_worker_with_no_failures(client: TestClient) -> None:
    response = client.get("/admin/nanobar/api/workers/no-such-worker/log")

    assert response.status_code == 200
    assert response.json()["result"]["data"] == []


def test_api_worker_log_rejects_a_non_integer_limit(client: TestClient) -> None:
    response = client.get("/admin/nanobar/api/workers/worker-a/log", params={"limit": "not-a-number"})

    assert response.status_code == 400
    assert response.json()["status"] == "error"


# ------------------------------------------------------------------------- settings ---


def test_settings_page_served(client: TestClient) -> None:
    response = client.get("/admin/nanobar/dashboard/settings")

    assert response.status_code == 200
    assert "Settings · Nanobar Dashboard" in response.text


def test_api_get_settings_defaults_to_tracing_enabled(client: TestClient) -> None:
    # build_app() forces trace capture on by default for this dev app -- see app.py's own
    # configure_tracing(enabled=True)/SQLiteTraceCaptureToggle(default_enabled=True) wiring.
    response = client.get("/admin/nanobar/api/settings")

    assert response.status_code == 200
    assert response.json()["result"]["data"] == {"tracing_enabled": True}


def test_api_update_settings_flips_and_persists_the_toggle(client: TestClient) -> None:
    response = client.post("/admin/nanobar/api/settings", json={"tracing_enabled": False})

    assert response.status_code == 200
    assert response.json()["result"]["data"] == {"tracing_enabled": False}

    # Persisted -- a later GET (a different request, own DB connection) sees the new value.
    assert client.get("/admin/nanobar/api/settings").json()["result"]["data"] == {"tracing_enabled": False}


def test_api_update_settings_rejects_a_non_boolean(client: TestClient) -> None:
    response = client.post("/admin/nanobar/api/settings", json={"tracing_enabled": "yes"})

    assert response.status_code == 400
    assert response.json()["status"] == "error"


def test_api_update_settings_rejects_invalid_json(client: TestClient) -> None:
    response = client.post(
        "/admin/nanobar/api/settings", content=b"not json", headers={"content-type": "application/json"}
    )

    assert response.status_code == 400
    assert response.json()["status"] == "error"


def test_disabling_tracing_via_settings_stops_new_spans_from_being_captured(
    events_db_path: str, client: TestClient
) -> None:
    client.post("/admin/nanobar/api/settings", json={"tracing_enabled": False})

    conn = events_connect(events_db_path)
    try:

        def _trace_event_count() -> int:
            return int(conn.execute("SELECT COUNT(*) FROM events WHERE channel = 'trace'").fetchone()[0])

        # Let anything already in flight (the login flow, or the settings POST above --
        # both issued while tracing was still enabled) finish its background-thread flush
        # before taking the baseline, so a delayed flush can't be mistaken for a new event.
        baseline = -1
        for _ in range(30):
            current = _trace_event_count()
            if current == baseline:
                break
            baseline = current
            time.sleep(0.05)

        client.get("/admin/nanobar/api/nanobars")
        time.sleep(1.0)  # longer than EventThread's 0.5s batch window, so a wrongly-emitted
        # event would have already flushed by the time this checks.

        count_after = _trace_event_count()
    finally:
        conn.close()

    assert count_after == baseline


# ------------------------------------------------------------------------------ db/app ---


def test_resolve_db_path_uses_env_var_when_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    override = str(tmp_path / "custom.db")
    monkeypatch.setenv(DB_PATH_ENV_VAR, override)

    assert resolve_db_path() == override


def test_resolve_db_path_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DB_PATH_ENV_VAR, raising=False)

    assert resolve_db_path() == str(DEFAULT_DB_PATH)


def test_build_app_without_explicit_db_path_uses_resolve_db_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    override = str(tmp_path / "env-configured.db")
    monkeypatch.setenv(DB_PATH_ENV_VAR, override)

    app = build_app()

    assert app.state.db_path == override

    client = TestClient(app)
    _authenticate(client)
    response = client.get("/admin/nanobar/api/nanobars")
    assert response.status_code == 200


def test_resolve_events_db_path_uses_env_var_when_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    override = str(tmp_path / "custom-events.db")
    monkeypatch.setenv(EVENTS_DB_PATH_ENV_VAR, override)

    assert resolve_events_db_path() == override


def test_resolve_events_db_path_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(EVENTS_DB_PATH_ENV_VAR, raising=False)

    assert resolve_events_db_path() == str(EVENTS_DEFAULT_DB_PATH)


def test_build_app_without_explicit_events_db_path_uses_resolve_events_db_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    override = str(tmp_path / "env-configured-events.db")
    monkeypatch.setenv(EVENTS_DB_PATH_ENV_VAR, override)

    app = build_app(db_path=str(tmp_path / "regression_bricks.db"))

    assert app.state.events_db_path == override


def test_resolve_app_admin_db_path_uses_env_var_when_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    override = str(tmp_path / "custom-app-admin.db")
    monkeypatch.setenv(APP_ADMIN_DB_PATH_ENV_VAR, override)

    assert resolve_app_admin_db_path() == override


def test_resolve_app_admin_db_path_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(APP_ADMIN_DB_PATH_ENV_VAR, raising=False)

    assert resolve_app_admin_db_path() == str(APP_ADMIN_DEFAULT_DB_PATH)


def test_resolve_nanobar_admin_db_path_uses_env_var_when_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    override = str(tmp_path / "custom-nanobar-admin.db")
    monkeypatch.setenv(NANOBAR_ADMIN_DB_PATH_ENV_VAR, override)

    assert resolve_nanobar_admin_db_path() == override


def test_resolve_nanobar_admin_db_path_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(NANOBAR_ADMIN_DB_PATH_ENV_VAR, raising=False)

    assert resolve_nanobar_admin_db_path() == str(NANOBAR_ADMIN_DEFAULT_DB_PATH)


def test_resolve_route_manifest_path_uses_env_var_when_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    override = str(tmp_path / "custom-manifest.json")
    monkeypatch.setenv(ROUTE_MANIFEST_PATH_ENV_VAR, override)

    assert resolve_route_manifest_path() == override


def test_resolve_route_manifest_path_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ROUTE_MANIFEST_PATH_ENV_VAR, raising=False)

    assert resolve_route_manifest_path() == str(ROUTE_MANIFEST_DEFAULT_PATH)


def test_build_app_writes_the_route_manifest_on_launch(route_manifest_path: str, client: TestClient) -> None:
    # "Built on launch" -- the manifest already exists with real content by the time build_app()
    # returns, before any request is ever made.
    document = json.loads(Path(route_manifest_path).read_text())

    route_keys = {r["route_key"] for r in document["routes"]}
    assert "GET /admin/nanobar/dashboard" in route_keys
    assert "GET /admin/app/dashboard" in route_keys
    domains = {r["domain"] for r in document["routes"]}
    assert domains == {"", "admin/app", "admin/nanobar"}


def test_dashboard_app_handles_not_yet_existing_database_directory(tmp_path: Path) -> None:
    """The db path's parent directory doesn't exist yet (mirrors a domain's data/ directory
    before the seed script has ever run) — the app must still respond, not crash.
    """
    nested_db_path = str(tmp_path / "not-yet-created" / "regression_bricks.db")
    app = build_app(db_path=nested_db_path)
    client = TestClient(app)
    _authenticate(client)

    response = client.get("/admin/nanobar/api/nanobars")

    assert response.status_code == 200
    assert response.json()["result"]["data"]["items"] == []
    assert Path(nested_db_path).exists()
