from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from demo.dashboard.app import build_app
from demo.dashboard.db import DB_PATH_ENV_VAR, DEFAULT_DB_PATH, resolve_db_path
from demo.dashboard.events_db import (
    DB_PATH_ENV_VAR as EVENTS_DB_PATH_ENV_VAR,
    DEFAULT_DB_PATH as EVENTS_DEFAULT_DB_PATH,
    resolve_db_path as resolve_events_db_path,
)
from nanobar_api.bricks.schema import MonitorTargetRef, Nanobar, NanobarBrickBinding, RegressionBrick
from nanobar_api.bricks.store import bind_brick_to_nanobar, connect, insert_brick, insert_nanobar, set_review_status
from nanobar_api.eventbus.events import Event
from nanobar_api.eventbus.store import connect as events_connect, insert_events


def _make_brick(
    brick_id: str,
    content_hash: str,
    *,
    method: str | None = "GET",
    path: str | None = "/checkout",
    status_code: int | None = 200,
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
    )


def _make_nanobar(
    nanobar_id: str,
    target_refs: list[MonitorTargetRef] | None = None,
    *,
    system_name: str = "checkout-service",
) -> Nanobar:
    return Nanobar(
        nanobar_id=nanobar_id,
        schema_version="1.0",
        system_name=system_name,
        system_version="1.0.0",
        regression_scenario_type="happy_path",
        request_object_id="req-1",
        response_object_id="res-1",
        regression_weight=0.5,
        endpoint_scenario_frequency={"state": "unmeasured"},
        created_by="test",
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
) -> Event:
    return Event(
        event_id=event_id,
        channel="trace",
        recorded_at_ns=recorded_at_ns,
        monotonic_ns=monotonic_ns,
        payload={
            "name": name,
            "http.request.method": method,
            "http.route": route,
            "status_code": status_code,
            "attributes": {},
            "error": error,
        },
        trace_id=trace_id,
        span_id=span_id,
    )


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "regression_bricks.db")


@pytest.fixture
def events_db_path(tmp_path: Path) -> str:
    return str(tmp_path / "events.db")


@pytest.fixture
def client(db_path: str, events_db_path: str) -> TestClient:
    return TestClient(build_app(db_path=db_path, events_db_path=events_db_path))


# --------------------------------------------------------------------------------- api ---


def test_api_list_nanobars_empty(client: TestClient) -> None:
    response = client.get("/api/nanobars")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["result"] == {"type": "array", "data": []}


def test_api_list_nanobars_returns_all(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-route", [MonitorTargetRef("openapi_operation", "checkout")]))
    insert_nanobar(conn, _make_nanobar("nb-service", [MonitorTargetRef("service", "billing")]))
    conn.close()

    response = client.get("/api/nanobars")

    ids = {n["nanobar_id"] for n in response.json()["result"]["data"]}
    assert ids == {"nb-route", "nb-service"}


def test_api_list_nanobars_filters_by_target_type(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_nanobar(conn, _make_nanobar("nb-route", [MonitorTargetRef("openapi_operation", "checkout")]))
    insert_nanobar(conn, _make_nanobar("nb-service", [MonitorTargetRef("service", "billing")]))
    conn.close()

    response = client.get("/api/nanobars", params={"target_type": "service"})

    data = response.json()["result"]["data"]
    assert [n["nanobar_id"] for n in data] == ["nb-service"]


def test_api_nanobar_bricks_not_found(client: TestClient) -> None:
    response = client.get("/api/nanobars/does-not-exist/bricks")

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

    response = client.get("/api/nanobars/nb-1/bricks")

    assert response.status_code == 200
    data = {b["regression_brick_id"]: b for b in response.json()["result"]["data"]}
    assert data["rbrick-1"]["review_status"]["status"] == "new"
    assert data["rbrick-2"]["review_status"]["status"] == "flagged"


def test_api_brick_detail_found(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one"))
    conn.close()

    response = client.get("/api/bricks/rbrick-1")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    data = body["result"]["data"]
    assert data["regression_brick_id"] == "rbrick-1"
    assert data["request"]["path"] == "/checkout"
    assert data["review_status"]["status"] == "new"


def test_api_brick_detail_not_found(client: TestClient) -> None:
    response = client.get("/api/bricks/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert body["status"] == "error"
    assert "does-not-exist" in body["msg"]


def test_api_set_review_status_valid(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one"))
    conn.close()

    response = client.post("/api/bricks/rbrick-1/review-status", json={"status": "flagged"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["result"]["data"]["status"] == "flagged"
    assert body["result"]["data"]["updated_by"] == "dashboard"

    # And it actually persisted.
    followup = client.get("/api/bricks/rbrick-1")
    assert followup.json()["result"]["data"]["review_status"]["status"] == "flagged"


def test_api_set_review_status_via_patch(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one"))
    conn.close()

    response = client.patch("/api/bricks/rbrick-1/review-status", json={"status": "reviewed"})

    assert response.status_code == 200
    assert response.json()["result"]["data"]["status"] == "reviewed"


def test_api_set_review_status_invalid_status_value(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one"))
    conn.close()

    response = client.post("/api/bricks/rbrick-1/review-status", json={"status": "bogus"})

    assert response.status_code == 400
    body = response.json()
    assert body["status"] == "error"
    assert "invalid review status" in body["msg"]


def test_api_set_review_status_brick_not_found(client: TestClient) -> None:
    response = client.post("/api/bricks/does-not-exist/review-status", json={"status": "flagged"})

    assert response.status_code == 404
    assert response.json()["status"] == "error"


def test_api_set_review_status_malformed_json_body(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one"))
    conn.close()

    response = client.post(
        "/api/bricks/rbrick-1/review-status",
        content=b"{not valid json",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert "JSON" in response.json()["msg"]


def test_api_set_review_status_missing_status_field(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one"))
    conn.close()

    response = client.post("/api/bricks/rbrick-1/review-status", json={})

    assert response.status_code == 400
    assert "status" in response.json()["msg"]


def test_api_set_review_status_body_not_an_object(db_path: str, client: TestClient) -> None:
    conn = connect(db_path)
    insert_brick(conn, _make_brick("rbrick-1", "sha256:one"))
    conn.close()

    response = client.post("/api/bricks/rbrick-1/review-status", json=["flagged"])

    assert response.status_code == 400
    assert "status" in response.json()["msg"]


# ------------------------------------------------------------------------------- pages ---
#
# Pages are served as static files (no server-side templating or database access) — the
# page's own JS fetches its data client-side from the JSON API tested above. So these tests
# only verify routing/serving: the right file comes back, regardless of what's in the db.


def test_nanobars_page_served(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Nanobars · Nanobar Dashboard" in response.text


def test_dashboard_route_alias_serves_same_page(client: TestClient) -> None:
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Nanobars · Nanobar Dashboard" in response.text


def test_nanobar_detail_page_served(client: TestClient) -> None:
    response = client.get("/nanobars/nb-1")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Nanobar · NanobarAPI" in response.text


def test_brick_detail_page_served(client: TestClient) -> None:
    response = client.get("/bricks/rbrick-1")

    assert response.status_code == 200
    assert "Brick · NanobarAPI" in response.text


def test_triage_page_served(client: TestClient) -> None:
    response = client.get("/triage")

    assert response.status_code == 200
    assert "Triage board · Nanobar Dashboard" in response.text


def test_static_assets_served_for_each_page(client: TestClient) -> None:
    for page in ("nanobars", "nanobar", "brick", "triage", "traces", "trace"):
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
    response = client.get("/api/traces")

    assert response.status_code == 200
    assert response.json()["result"]["data"] == []


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

    response = client.get("/api/traces")

    assert response.status_code == 200
    data = response.json()["result"]["data"]
    assert len(data) == 1
    assert data[0]["trace_id"] == "tr-1"
    assert data[0]["span_count"] == 2
    assert data[0]["any_error"] is False


def test_api_list_traces_invalid_limit(client: TestClient) -> None:
    response = client.get("/api/traces?limit=not-a-number")

    assert response.status_code == 400
    assert response.json()["status"] == "error"


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

    ok = client.get("/api/traces/tr-1/spans")
    missing = client.get("/api/traces/does-not-exist/spans")

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
    response = client.get("/traces")

    assert response.status_code == 200
    assert "Traces · Nanobar Dashboard" in response.text


def test_trace_detail_page_served(client: TestClient) -> None:
    response = client.get("/traces/tr-1")

    assert response.status_code == 200
    assert "Trace · Nanobar Dashboard" in response.text


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
    response = client.get("/api/nanobars")
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


def test_dashboard_app_handles_not_yet_existing_database_directory(tmp_path: Path) -> None:
    """The db path's parent directory doesn't exist yet (mirrors demo/data/ before the seed
    script has ever run) — the app must still respond, not crash.
    """
    nested_db_path = str(tmp_path / "not-yet-created" / "regression_bricks.db")
    app = build_app(db_path=nested_db_path)
    client = TestClient(app)

    response = client.get("/api/nanobars")

    assert response.status_code == 200
    assert response.json()["result"]["data"] == []
    assert Path(nested_db_path).exists()
