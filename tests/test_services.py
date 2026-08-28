from __future__ import annotations

from typing import Any

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider

from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.framework.nanobar_api_service import (
    NanobarAPIService,
    ServiceResult,
    ServiceResultBody,
    SourceInfoEntry,
)
from nanobar_api.telemetry import NanobarTelemetry

if isinstance(otel_trace.get_tracer_provider(), otel_trace.NoOpTracerProvider | otel_trace.ProxyTracerProvider):
    otel_trace.set_tracer_provider(TracerProvider())


class _EchoService(NanobarAPIService):
    def handle(self, request: Any) -> ServiceResult:
        return ServiceResult(status="success", result=ServiceResultBody(type="object", data=request, msg_summary=""))


def _repository() -> EventQueueRepository:
    return EventQueueRepository([ChannelConfig(name="trace"), ChannelConfig(name="snapshot")])


def test_cannot_instantiate_abstract_service_directly() -> None:
    telemetry = NanobarTelemetry(_repository(), channel="trace")

    with pytest.raises(TypeError):
        NanobarAPIService(telemetry)  # type: ignore[abstract]


def test_service_result_defaults_to_empty_source_info() -> None:
    result = ServiceResult(status="success", result=ServiceResultBody(type="object", data=None, msg_summary=""))

    assert result.source_info == []


def test_source_info_entry_round_trips_fields() -> None:
    entry = SourceInfoEntry(source_type="db", source_file_url="sqlite:///x.db", source_status_code=200)

    assert entry.source_type == "db"
    assert entry.source_status_code == 200


def test_call_invokes_handle_and_returns_result() -> None:
    telemetry = NanobarTelemetry(_repository(), channel="trace")
    service = _EchoService(telemetry)

    result = service({"x": 1})

    assert result.status == "success"
    assert result.result.data == {"x": 1}


def test_call_captures_service_request_response_brick() -> None:
    repository = _repository()
    telemetry = NanobarTelemetry(repository, channel="trace")
    service = _EchoService(telemetry)

    service({"x": 1}, route_key="POST /orders")

    event = repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None
    assert event.payload["nanobar_type"] == "service-request-response"
    assert event.payload["request"] == {"x": 1}
    assert event.payload["route_key"] == "POST /orders"


def test_call_without_route_key_omits_it_from_capture() -> None:
    repository = _repository()
    telemetry = NanobarTelemetry(repository, channel="trace")
    service = _EchoService(telemetry)

    service({"x": 1})

    event = repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None
    assert "route_key" not in event.payload
