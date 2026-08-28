from __future__ import annotations

from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.testclient import TestClient

from nanobar_api.eventbus.dispatch import NanobarCallback, NanobarEventBus
from nanobar_api.eventbus.events import Event
from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.regression_brick.replay_routes import REPLAY_SPANS_PATH, REPLAY_TRIGGER_EVENT_PATH, build_replay_routes
from nanobar_api.telemetry import NanobarTelemetry
from nanobar_api.telemetry.persistence import build_session_factory as build_telemetry_session_factory


class _EchoCallback(NanobarCallback):
    def handle(self, event: Event) -> Any:
        return {"received": event.payload}


def _app(tmp_path: Path) -> Starlette:
    repository = EventQueueRepository(
        [ChannelConfig(name="domain.orders"), ChannelConfig(name="snapshot"), ChannelConfig(name="trace")]
    )
    telemetry_session_factory = build_telemetry_session_factory(str(tmp_path / "telemetry.db"))
    event_bus = NanobarEventBus(repository, NanobarTelemetry(repository, channel="trace"))
    event_bus.subscribe("domain.orders", _EchoCallback())

    app = Starlette(routes=build_replay_routes())
    app.state.event_bus = event_bus
    app.state.telemetry_session_factory = telemetry_session_factory
    return app


def test_trigger_event_dispatches_synchronously_and_returns_ids(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))

    response = client.post(REPLAY_TRIGGER_EVENT_PATH, json={"channel": "domain.orders", "payload": {"x": 1}})

    assert response.status_code == 200
    body = response.json()
    assert body["event_id"]
    assert body["trace_id"] is None  # no ambient trace context in this test


def test_trigger_event_rejects_a_non_domain_channel(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))

    response = client.post(REPLAY_TRIGGER_EVENT_PATH, json={"channel": "not-a-domain-channel", "payload": {}})

    assert response.status_code == 400
    assert "must start with" in response.json()["error"]


def test_trigger_event_defaults_payload_to_empty_dict(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))

    response = client.post(REPLAY_TRIGGER_EVENT_PATH, json={"channel": "domain.orders"})

    assert response.status_code == 200


def test_spans_for_trace_returns_empty_list_when_none_recorded(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))

    response = client.get(REPLAY_SPANS_PATH.format(trace_id="does-not-exist"))

    assert response.status_code == 200
    assert response.json() == []


def test_spans_for_trace_filters_by_channel(tmp_path: Path) -> None:
    from nanobar_api.telemetry.model import Span
    from nanobar_api.telemetry.span_repository import SpanRepository
    from nanobar_api.telemetry.trace_repository import TraceRepository

    app = _app(tmp_path)
    session_factory = app.state.telemetry_session_factory
    session = session_factory()
    trace_repository = TraceRepository(session)
    span_repository = SpanRepository(session)
    trace_repository.get_or_create("trace-1", entry_point="event-domain.orders")
    span_repository.create(
        Span(
            event_id="ev-1",
            span_id="sp-1",
            trace_id="trace-1",
            channel="snapshot",
            recorded_at_ns=1,
            monotonic_ns=1,
            payload_json={"nanobar_type": "event-to-subscriber", "response": {"received": {"x": 1}}},
        )
    )
    span_repository.create(
        Span(
            event_id="ev-2",
            span_id="sp-2",
            trace_id="trace-1",
            channel="trace",
            recorded_at_ns=2,
            monotonic_ns=2,
            payload_json={"name": "event-domain.orders"},
        )
    )
    session.close()

    client = TestClient(app)
    response = client.get(REPLAY_SPANS_PATH.format(trace_id="trace-1"), params={"channel": "snapshot"})

    assert response.status_code == 200
    spans = response.json()
    assert len(spans) == 1
    assert spans[0]["channel"] == "snapshot"
    assert spans[0]["payload"]["response"] == {"received": {"x": 1}}
