from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.testclient import TestClient
from starlette.types import Receive, Scope, Send

from nanobar_api.framework.nanobar_api_validator_gate import NanobarAPIValidatorGate
from nanobar_api.routing import (
    NanobarRouteRule,
    NanobarRouteSet,
    RestRouteAdapter,
    _parse_rest_route_key,
)


class _FakeGate(NanobarAPIValidatorGate):
    """A routing-adapter test double — overrides `__call__` directly (bypassing the real,
    telemetry/controller-backed dispatch `NanobarAPIValidatorGate` now implements) so these tests
    stay scoped to `Mount`/middleware wiring, not gate/controller lifecycle (covered instead by
    `test_validator_gate.py`/`test_controllers.py`)."""

    def validate(self, request: Request) -> object:
        return None

    async def __call__(self, request: Request, request_type: str) -> object:
        return {"reached": request_type}


class _RecordingMiddleware:
    def __init__(self, app: object, log: list[str], tag: str) -> None:
        self.app = app
        self.log = log
        self.tag = tag

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.log.append(self.tag)
        await self.app(scope, receive, send)  # type: ignore[operator]


class _PingSet(NanobarRouteSet):
    domain = "ping"
    rules = (
        NanobarRouteRule(key="GET /health", gate=_FakeGate),
        NanobarRouteRule(
            key="POST /admin-only",
            gate=_FakeGate,
            middleware=(Middleware(_RecordingMiddleware, log=[], tag="rule"),),
        ),
    )
    middleware = (Middleware(_RecordingMiddleware, log=[], tag="domain"),)


class _MixedTransportSet(NanobarRouteSet):
    domain = "mixed"
    rules = (
        NanobarRouteRule(key="GET /rest-only", gate=_FakeGate),
        NanobarRouteRule(key="BoardService/GetBoard", gate=_FakeGate, transport="grpc"),
    )


def test_parse_rest_route_key_splits_method_and_path() -> None:
    assert _parse_rest_route_key("GET /boards/{board_id}") == ("GET", "/boards/{board_id}")


def test_parse_rest_route_key_uppercases_method() -> None:
    assert _parse_rest_route_key("get /x") == ("GET", "/x")


@pytest.mark.parametrize("key", ["", "GET", "GET/x", " /x"])
def test_parse_rest_route_key_rejects_malformed_key(key: str) -> None:
    with pytest.raises(ValueError):
        _parse_rest_route_key(key)


def test_build_mount_creates_one_route_per_rest_rule() -> None:
    mounted = RestRouteAdapter.build_mount(_PingSet)

    assert mounted.mount.path == "/ping"
    assert len(mounted.mount.routes) == 2


def test_build_mount_captures_domain_and_rule_middleware_names() -> None:
    mounted = RestRouteAdapter.build_mount(_PingSet)

    assert mounted.rule_middleware_names["GET /health"] == ("_RecordingMiddleware",)
    assert mounted.rule_middleware_names["POST /admin-only"] == ("_RecordingMiddleware", "_RecordingMiddleware")


def test_build_mount_skips_non_rest_rules() -> None:
    mounted = RestRouteAdapter.build_mount(_MixedTransportSet)

    assert len(mounted.mount.routes) == 1
    assert "BoardService/GetBoard" not in mounted.rule_middleware_names


def test_register_appends_mount_to_app_routes() -> None:
    app = Starlette(routes=[])

    mounted = RestRouteAdapter.register(app, _PingSet)

    assert mounted.mount in app.routes


def test_registered_route_runs_domain_middleware_before_rule_middleware() -> None:
    log: list[str] = []

    class _Set(NanobarRouteSet):
        domain = "ping"
        rules = (
            NanobarRouteRule(
                key="GET /health",
                gate=_FakeGate,
                middleware=(Middleware(_RecordingMiddleware, log=log, tag="rule"),),
            ),
        )
        middleware = (Middleware(_RecordingMiddleware, log=log, tag="domain"),)

    app = Starlette(routes=[])
    RestRouteAdapter.register(app, _Set)
    client = TestClient(app)

    response = client.get("/ping/health")

    assert response.json()["result"]["data"] == {"reached": "GET /health"}
    assert log == ["domain", "rule"]


def test_registered_route_with_no_rule_middleware_still_runs_domain_middleware() -> None:
    log: list[str] = []

    class _Set(NanobarRouteSet):
        domain = "ping"
        rules = (NanobarRouteRule(key="GET /health", gate=_FakeGate),)
        middleware = (Middleware(_RecordingMiddleware, log=log, tag="domain"),)

    app = Starlette(routes=[])
    RestRouteAdapter.register(app, _Set)
    client = TestClient(app)

    response = client.get("/ping/health")

    assert response.json()["result"]["data"] == {"reached": "GET /health"}
    assert log == ["domain"]


def test_registered_route_reaches_gate_dispatch() -> None:
    app = Starlette(routes=[])
    RestRouteAdapter.register(app, _PingSet)
    client = TestClient(app)

    response = client.post("/ping/admin-only")

    assert response.json()["result"]["data"] == {"reached": "POST /admin-only"}
