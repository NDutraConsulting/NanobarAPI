from __future__ import annotations

import time
from typing import Any

import anyio
import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider

from nanobar_api.eventbus.dispatch import NanobarCallback, NanobarEventBus, event_bus_lifespan
from nanobar_api.eventbus.events import Event
from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.telemetry import NanobarTelemetry

if isinstance(otel_trace.get_tracer_provider(), otel_trace.NoOpTracerProvider | otel_trace.ProxyTracerProvider):
    otel_trace.set_tracer_provider(TracerProvider())


class _RecordingCallback(NanobarCallback):
    def __init__(self, name: str, calls: list[str], fail: bool = False) -> None:
        self.name = name
        self.calls = calls
        self.fail = fail

    def handle(self, event: Event) -> Any:
        self.calls.append(self.name)
        if self.fail:
            raise RuntimeError(f"{self.name} failed")
        return {"handled_by": self.name}


def _bus() -> tuple[NanobarEventBus, EventQueueRepository]:
    # telemetry's OWN span-tracking channel ("trace") must never be a channel NanobarEventBus
    # also drains ("domain.orders") -- otherwise telemetry's own span-emission events would be
    # popped right back off as if they were dispatchable domain events, feeding back into more
    # telemetry spans indefinitely. Exactly the channel-mixing failure mode §1 warns about.
    repository = EventQueueRepository(
        [ChannelConfig(name="domain.orders"), ChannelConfig(name="snapshot"), ChannelConfig(name="trace")]
    )
    telemetry = NanobarTelemetry(repository, channel="trace")
    return NanobarEventBus(repository, telemetry), repository


def test_cannot_instantiate_abstract_callback_directly() -> None:
    with pytest.raises(TypeError):
        NanobarCallback()  # type: ignore[abstract]


def test_subscribe_rejects_non_domain_channel() -> None:
    bus, _ = _bus()
    with pytest.raises(ValueError, match="domain\\."):
        bus.subscribe("orders", _RecordingCallback("a", []))


def test_publish_rejects_non_domain_channel() -> None:
    bus, _ = _bus()
    with pytest.raises(ValueError, match="domain\\."):
        bus.publish("orders", {})


def test_subscribe_registers_in_order_and_list_subscriptions_reflects_it() -> None:
    bus, _ = _bus()
    cb_a = _RecordingCallback("a", [])
    cb_b = _RecordingCallback("b", [])

    bus.subscribe("domain.orders", cb_a)
    bus.subscribe("domain.orders", cb_b)

    subscriptions = bus.list_subscriptions()
    assert list(subscriptions["domain.orders"]) == [cb_a, cb_b]


def test_publish_puts_an_event_on_the_repository() -> None:
    bus, repository = _bus()

    bus.publish("domain.orders", {"order_id": "o1"})

    event = repository.get_any(["domain.orders"], timeout=1.0)
    assert event is not None
    assert event.payload == {"order_id": "o1"}
    assert event.channel == "domain.orders"


def test_dispatch_invokes_all_subscribers_in_registration_order() -> None:
    bus, repository = _bus()
    calls: list[str] = []
    bus.subscribe("domain.orders", _RecordingCallback("first", calls))
    bus.subscribe("domain.orders", _RecordingCallback("second", calls))

    event = Event(event_id="evt-1", channel="domain.orders", recorded_at_ns=1, monotonic_ns=1, payload={})
    bus._dispatch(event)

    assert calls == ["first", "second"]


def test_dispatch_success_captures_event_to_subscriber_brick() -> None:
    bus, repository = _bus()
    bus.subscribe("domain.orders", _RecordingCallback("a", []))

    event = Event(event_id="evt-1", channel="domain.orders", recorded_at_ns=1, monotonic_ns=1, payload={"x": 1})
    bus._dispatch(event)

    captured = repository.get_any(["snapshot"], timeout=1.0)
    assert captured is not None
    assert captured.payload["nanobar_type"] == "event-to-subscriber"
    assert captured.payload["error"] is False
    assert captured.payload["request"] == {"x": 1}
    assert captured.payload["response"] == {"handled_by": "a"}


def test_dispatch_now_invokes_subscribers_synchronously_and_returns_the_event() -> None:
    bus, repository = _bus()
    calls: list[str] = []
    bus.subscribe("domain.orders", _RecordingCallback("a", calls))

    event = bus.dispatch_now("domain.orders", {"order_id": "o1"})

    assert calls == ["a"]
    assert event.channel == "domain.orders"
    assert event.payload == {"order_id": "o1"}
    # Never queued -- run_forever()'s loop never sees it, so it's not sitting in the repository.
    assert repository.get_any(["domain.orders"], timeout=0.05) is None


def test_dispatch_now_captures_an_event_to_subscriber_brick_like_normal_dispatch() -> None:
    bus, repository = _bus()
    bus.subscribe("domain.orders", _RecordingCallback("a", []))

    bus.dispatch_now("domain.orders", {"x": 1})

    captured = repository.get_any(["snapshot"], timeout=1.0)
    assert captured is not None
    assert captured.payload["nanobar_type"] == "event-to-subscriber"
    assert captured.payload["response"] == {"handled_by": "a"}


def test_dispatch_now_rejects_a_non_domain_channel() -> None:
    bus, _ = _bus()

    with pytest.raises(ValueError, match="must start with"):
        bus.dispatch_now("not-a-domain-channel", {})


def test_dispatch_now_propagates_ambient_trace_context_onto_the_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole reason `dispatch_now()` exists over `publish()` + the background loop: an
    ambient `current_trace_id` (e.g. set by `EventBusTraceMiddleware` for the HTTP request
    calling this) must be exactly what the resulting event carries -- `publish()` alone stamps
    it on the `Event`, but the *dispatch* (and its `capture_layer()` span) would still happen
    later, in a separate thread where the contextvar is unset."""
    from nanobar_api.middleware.trace import current_span_id, current_trace_id

    bus, _ = _bus()
    bus.subscribe("domain.orders", _RecordingCallback("a", []))
    trace_token = current_trace_id.set("trace-123")
    span_token = current_span_id.set("span-456")
    try:
        event = bus.dispatch_now("domain.orders", {})
    finally:
        current_trace_id.reset(trace_token)
        current_span_id.reset(span_token)

    assert event.trace_id == "trace-123"
    assert event.span_id == "span-456"


def test_dispatch_one_failing_subscriber_does_not_block_the_other() -> None:
    bus, repository = _bus()
    calls: list[str] = []
    bus.subscribe("domain.orders", _RecordingCallback("failing", calls, fail=True))
    bus.subscribe("domain.orders", _RecordingCallback("healthy", calls))

    event = Event(event_id="evt-1", channel="domain.orders", recorded_at_ns=1, monotonic_ns=1, payload={})
    bus._dispatch(event)

    assert calls == ["failing", "healthy"]


def test_dispatch_failure_captures_error_brick_and_calls_on_failure() -> None:
    bus, repository = _bus()

    on_failure_calls: list[tuple[Event, Exception]] = []

    class _FailingCallback(NanobarCallback):
        def handle(self, event: Event) -> Any:
            raise ValueError("boom")

        def on_failure(self, event: Event, exc: Exception) -> None:
            on_failure_calls.append((event, exc))

    bus.subscribe("domain.orders", _FailingCallback())

    event = Event(event_id="evt-1", channel="domain.orders", recorded_at_ns=1, monotonic_ns=1, payload={"x": 1})
    bus._dispatch(event)

    assert len(on_failure_calls) == 1
    assert isinstance(on_failure_calls[0][1], ValueError)

    captured = repository.get_any(["snapshot"], timeout=1.0)
    assert captured is not None
    assert captured.payload["nanobar_type"] == "event-to-subscriber"
    assert captured.payload["error"] is True
    assert captured.payload["response"]["error_type"] == "ValueError"


def test_default_on_failure_logs_and_does_not_raise() -> None:
    bus, _ = _bus()

    class _BoomCallback(NanobarCallback):
        def handle(self, event: Event) -> Any:
            raise RuntimeError("boom")

    bus.subscribe("domain.orders", _BoomCallback())

    event = Event(event_id="evt-1", channel="domain.orders", recorded_at_ns=1, monotonic_ns=1, payload={})
    bus._dispatch(event)  # must not raise


def test_dispatch_channel_with_no_subscribers_is_a_noop() -> None:
    bus, _ = _bus()

    event = Event(event_id="evt-1", channel="domain.orders", recorded_at_ns=1, monotonic_ns=1, payload={})
    bus._dispatch(event)  # no subscribers registered -- must not raise


def test_dispatch_survives_repository_with_no_snapshot_channel_configured() -> None:
    # A repository built with only domain channels (no "snapshot") is a legitimate, documented
    # configuration -- EventThread and NanobarEventBus are meant to drain disjoint channel sets.
    # capture_layer()'s default channel is "snapshot", so EventQueueRepository.put() would raise
    # KeyError for it; _dispatch() must swallow that rather than letting it kill the whole
    # dispatch loop, since telemetry capture is a side-observation, not the actual business logic.
    repository = EventQueueRepository([ChannelConfig(name="domain.orders")])
    telemetry = NanobarTelemetry(repository, channel="domain.orders")
    bus = NanobarEventBus(repository, telemetry)
    calls: list[str] = []
    bus.subscribe("domain.orders", _RecordingCallback("a", calls))

    event = Event(event_id="evt-1", channel="domain.orders", recorded_at_ns=1, monotonic_ns=1, payload={"x": 1})
    bus._dispatch(event)  # must not raise despite capture_layer() having nowhere to write

    assert calls == ["a"]


def test_dispatch_failure_survives_repository_with_no_snapshot_channel_configured() -> None:
    repository = EventQueueRepository([ChannelConfig(name="domain.orders")])
    telemetry = NanobarTelemetry(repository, channel="domain.orders")
    bus = NanobarEventBus(repository, telemetry)

    class _FailingCallback(NanobarCallback):
        def handle(self, event: Event) -> Any:
            raise ValueError("boom")

    bus.subscribe("domain.orders", _FailingCallback())

    event = Event(event_id="evt-1", channel="domain.orders", recorded_at_ns=1, monotonic_ns=1, payload={})
    bus._dispatch(event)  # must not raise despite capture_layer() having nowhere to write


@pytest.mark.anyio
async def test_run_forever_dispatches_published_events_via_lifespan() -> None:
    bus, repository = _bus()
    calls: list[str] = []
    bus.subscribe("domain.orders", _RecordingCallback("a", calls))
    bus.subscribe("domain.orders", _RecordingCallback("b", calls))

    async with event_bus_lifespan(bus, poll_timeout_s=0.05):
        bus.publish("domain.orders", {"order_id": "o1"})

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and calls != ["a", "b"]:
            await anyio.sleep(0.02)

    assert calls == ["a", "b"]


def test_run_forever_drains_events_still_queued_after_stop_is_called() -> None:
    # Deterministic (no real thread/timing race): stop() before run_forever() ever polls means
    # the main polling loop never executes even once, forcing the drain-on-stop pass to be what
    # picks up the already-published event.
    bus, _ = _bus()
    calls: list[str] = []
    bus.subscribe("domain.orders", _RecordingCallback("a", calls))
    bus.publish("domain.orders", {"order_id": "o1"})

    bus.stop()
    bus.run_forever(poll_timeout_s=0.01)

    assert calls == ["a"]


@pytest.mark.anyio
async def test_run_forever_stop_drains_remaining_events() -> None:
    bus, repository = _bus()
    calls: list[str] = []
    bus.subscribe("domain.orders", _RecordingCallback("a", calls))

    async with event_bus_lifespan(bus, poll_timeout_s=0.05):
        # Publish right before shutdown -- the final drain-on-stop pass must still pick it up.
        bus.publish("domain.orders", {"order_id": "o1"})

    assert calls == ["a"]
