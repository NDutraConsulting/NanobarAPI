from __future__ import annotations

import asyncio
from typing import Any

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from starlette.applications import Starlette

from nanobar_api.controllers import NanobarController
from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.telemetry import NanobarTelemetry

if isinstance(otel_trace.get_tracer_provider(), otel_trace.NoOpTracerProvider | otel_trace.ProxyTracerProvider):
    otel_trace.set_tracer_provider(TracerProvider())


class _MinimalController(NanobarController):
    def load_required_services(self) -> None:
        pass

    def load_fallback_services(self) -> None:
        pass

    def run_etl_workflow(self, validated: Any) -> Any:
        return validated

    def build_response(self, result: Any) -> Any:
        return {"result": result}


class _FallbackController(NanobarController):
    def load_required_services(self) -> None:
        raise RuntimeError("required service unavailable")

    def load_fallback_services(self) -> None:
        self.services["mode"] = "fallback"

    def run_etl_workflow(self, validated: Any) -> Any:
        return self.services["mode"]

    def build_response(self, result: Any) -> Any:
        return {"mode": result}


def _repository() -> EventQueueRepository:
    return EventQueueRepository([ChannelConfig(name="trace"), ChannelConfig(name="snapshot")])


def test_cannot_instantiate_abstract_controller_directly() -> None:
    class _Request:
        app = None

    with pytest.raises(TypeError):
        NanobarController(_Request(), "test")  # type: ignore[abstract, arg-type]


def test_init_falls_back_when_load_required_services_raises() -> None:
    app = Starlette(routes=[])
    app.state.telemetry = NanobarTelemetry(_repository(), channel="trace")

    class _FakeApp:
        state = app.state

    class _FakeRequest:
        app = _FakeApp()

    controller = _FallbackController(_FakeRequest(), "test")  # type: ignore[arg-type]

    assert controller.services == {"mode": "fallback"}


def test_handle_runs_etl_workflow_and_build_response_then_captures_brick() -> None:
    app = Starlette(routes=[])
    repository = _repository()
    app.state.telemetry = NanobarTelemetry(repository, channel="trace")

    class _FakeApp:
        state = app.state

    class _FakeRequest:
        app = _FakeApp()

    controller = _MinimalController(_FakeRequest(), "test")  # type: ignore[arg-type]

    result = asyncio.run(controller.handle({"value": 42}))

    assert result == {"result": {"value": 42}}
    event = repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None
    assert event.payload["nanobar_type"] == "controller-request-response"
    assert event.payload["request"] == {"value": 42}
    assert event.payload["response"] == {"result": {"value": 42}}


def test_controller_failure_propagates_uncaught() -> None:
    class _BoomController(NanobarController):
        def load_required_services(self) -> None:
            pass

        def load_fallback_services(self) -> None:
            pass

        def run_etl_workflow(self, validated: Any) -> Any:
            raise RuntimeError("business logic exploded")

        def build_response(self, result: Any) -> Any:
            return result

    app = Starlette(routes=[])
    app.state.telemetry = NanobarTelemetry(_repository(), channel="trace")

    class _FakeApp:
        state = app.state

    class _FakeRequest:
        app = _FakeApp()

    controller = _BoomController(_FakeRequest(), "test")  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="business logic exploded"):
        asyncio.run(controller.handle(None))
