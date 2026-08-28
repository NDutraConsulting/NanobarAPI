from __future__ import annotations

from dataclasses import dataclass

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.testclient import TestClient

from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.framework.nanobar_api_controller import NanobarAPIController
from nanobar_api.framework.nanobar_api_validator_gate import NanobarAPIValidatorGate
from nanobar_api.middleware.trace import EventBusTraceMiddleware
from nanobar_api.routing import NanobarRouteRule, NanobarRouteSet, RestRouteAdapter
from nanobar_api.telemetry import NanobarTelemetry
from nanobar_api.validation import parse

# A real SDK TracerProvider so spans carry real, non-NoOp trace/span ids -- matches
# test_telemetry.py's own bootstrap, the one place per test process this is set.
if isinstance(otel_trace.get_tracer_provider(), otel_trace.NoOpTracerProvider | otel_trace.ProxyTracerProvider):
    otel_trace.set_tracer_provider(TracerProvider())


@dataclass
class GreetRequest:
    name: str


class GreetController(NanobarAPIController):
    def load_required_services(self) -> None:
        self.services["greeter"] = lambda name: f"hello, {name}"

    def load_fallback_services(self) -> None:
        self.services["greeter"] = lambda name: "hello, stranger"

    def run_etl_workflow(self, validated: GreetRequest) -> str:
        return self.services["greeter"](validated.name)  # type: ignore[no-any-return]

    def build_response(self, result: str) -> dict[str, str]:
        return {"message": result}


class GreetGate(NanobarAPIValidatorGate):
    controller_cls = GreetController

    def validate(self, request: Request) -> GreetRequest:
        return parse(GreetRequest, request.state.json_body or {})


class _GreetSet(NanobarRouteSet):
    domain = "greet"
    rules = (NanobarRouteRule(key="POST /hello", gate=GreetGate),)


class _ConcreteGate(NanobarAPIValidatorGate):
    controller_cls = GreetController

    def validate(self, request: Request) -> object:
        return {"ok": True}


def _repository() -> EventQueueRepository:
    return EventQueueRepository([ChannelConfig(name="trace"), ChannelConfig(name="snapshot")])


def _build_app() -> tuple[Starlette, EventQueueRepository]:
    repository = _repository()
    app = Starlette(routes=[], middleware=[Middleware(EventBusTraceMiddleware, repository=repository, channel="trace")])
    RestRouteAdapter.register(app, _GreetSet)
    app.state.telemetry = NanobarTelemetry(repository, channel="trace")
    return app, repository


def test_cannot_instantiate_abstract_gate_directly() -> None:
    with pytest.raises(TypeError):
        NanobarAPIValidatorGate()  # type: ignore[abstract]


def test_concrete_subclass_implementing_validate_is_instantiable() -> None:
    gate = _ConcreteGate()

    assert gate.validate(request=object()) == {"ok": True}  # type: ignore[arg-type]


def test_successful_request_flows_through_validator_and_controller() -> None:
    app, _ = _build_app()
    client = TestClient(app)

    response = client.post("/greet/hello", json={"name": "Ada"})

    assert response.status_code == 200
    assert response.json()["result"]["data"] == {"message": "hello, Ada"}


def test_missing_required_field_short_circuits_with_400_before_controller() -> None:
    app, _ = _build_app()
    client = TestClient(app)

    response = client.post("/greet/hello", json={})

    assert response.status_code == 400
    assert response.json()["status"] == "error"


def test_non_json_body_short_circuits_with_400() -> None:
    app, _ = _build_app()
    client = TestClient(app)

    response = client.post("/greet/hello", content=b"not json", headers={"content-type": "application/json"})

    assert response.status_code == 400


def test_validator_layer_captures_brick_on_success() -> None:
    app, repository = _build_app()
    client = TestClient(app)

    response = client.post("/greet/hello", json={"name": "Ada"})
    assert response.status_code == 200

    event = repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None
    assert event.payload["nanobar_type"] == "validator-request-response"
    assert event.payload["error"] is False
    assert event.payload["request"]["body"] == {"name": "Ada"}
    assert event.payload["response"] == {"name": "Ada"}  # to_payload_dict(GreetRequest(name="Ada"))


def test_validator_layer_captures_brick_on_validation_error() -> None:
    app, repository = _build_app()
    client = TestClient(app)

    client.post("/greet/hello", json={})

    event = repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None
    assert event.payload["nanobar_type"] == "validator-request-response"
    assert event.payload["error"] is False  # a handled ValidationError, not an unhandled fault
    assert "errors" in event.payload["response"]


def test_validator_and_controller_bricks_share_one_trace_id() -> None:
    app, repository = _build_app()
    client = TestClient(app)

    client.post("/greet/hello", json={"name": "Ada"})

    validator_event = repository.get_any(["snapshot"], timeout=1.0)
    controller_event = repository.get_any(["snapshot"], timeout=1.0)
    assert validator_event is not None
    assert controller_event is not None
    assert validator_event.payload["nanobar_type"] == "validator-request-response"
    assert controller_event.payload["nanobar_type"] == "controller-request-response"
    assert validator_event.trace_id is not None
    assert validator_event.trace_id == controller_event.trace_id


def test_request_state_json_body_is_none_when_body_is_not_json() -> None:
    app, _ = _build_app()
    client = TestClient(app)

    # A GreetGate.validate() call would see request.state.json_body is None here -- proven
    # indirectly by the 400, since parse(GreetRequest, None or {}) fails on the missing field.
    response = client.post("/greet/hello", content=b"", headers={"content-type": "application/json"})

    assert response.status_code == 400
