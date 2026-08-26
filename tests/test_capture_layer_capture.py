from __future__ import annotations

from dataclasses import dataclass

from nanobar_api.capture.layer_capture import capture_layer, to_payload_dict
from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.middleware.trace import current_span_id, current_trace_id


def _repository(*channels: str) -> EventQueueRepository:
    return EventQueueRepository([ChannelConfig(name=c) for c in channels or ("snapshot",)])


def test_capture_layer_defaults_nanobar_type_from_layer() -> None:
    repository = _repository()

    capture_layer(repository, "validator", {"body": "in"}, {"body": "out"})

    event = repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None
    assert event.channel == "snapshot"
    assert event.payload["nanobar_type"] == "validator-request-response"
    assert event.payload["request"] == {"body": "in"}
    assert event.payload["response"] == {"body": "out"}
    assert event.payload["error"] is False
    assert isinstance(event.payload["content_hash"], str) and event.payload["content_hash"]


def test_capture_layer_explicit_nanobar_type_overrides_default() -> None:
    repository = _repository()

    capture_layer(repository, "event", {}, {}, nanobar_type="event-to-subscriber")

    event = repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None
    assert event.payload["nanobar_type"] == "event-to-subscriber"


def test_capture_layer_error_flag_propagates() -> None:
    repository = _repository()

    capture_layer(repository, "validator", {"body": "in"}, {"errors": ["bad"]}, error=True)

    event = repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None
    assert event.payload["error"] is True


def test_capture_layer_custom_channel() -> None:
    repository = _repository("snapshot", "other")

    capture_layer(repository, "controller", {}, {}, channel="other")

    assert repository.get_any(["snapshot"], timeout=0.05) is None
    event = repository.get_any(["other"], timeout=1.0)
    assert event is not None
    assert event.channel == "other"


def test_capture_layer_same_content_hash_for_identical_payloads() -> None:
    repository = _repository()

    capture_layer(repository, "controller", {"a": 1}, {"b": 2})
    capture_layer(repository, "controller", {"a": 1}, {"b": 2})

    first = repository.get_any(["snapshot"], timeout=1.0)
    second = repository.get_any(["snapshot"], timeout=1.0)
    assert first is not None
    assert second is not None
    assert first.payload["content_hash"] == second.payload["content_hash"]
    assert first.event_id != second.event_id


def test_capture_layer_threads_current_trace_and_span_ids() -> None:
    repository = _repository()

    trace_token = current_trace_id.set("trace-123")
    span_token = current_span_id.set("span-456")
    try:
        capture_layer(repository, "validator", {}, {})
    finally:
        current_trace_id.reset(trace_token)
        current_span_id.reset(span_token)

    event = repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None
    assert event.trace_id == "trace-123"
    assert event.span_id == "span-456"


def test_to_payload_dict_passes_through_plain_dict() -> None:
    original = {"a": 1}

    assert to_payload_dict(original) is original


def test_to_payload_dict_converts_dataclass_instance() -> None:
    @dataclass
    class Thing:
        name: str

    assert to_payload_dict(Thing(name="x")) == {"name": "x"}


def test_to_payload_dict_wraps_plain_value() -> None:
    assert to_payload_dict(42) == {"value": 42}
    assert to_payload_dict("hello") == {"value": "hello"}
