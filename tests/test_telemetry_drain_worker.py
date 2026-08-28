from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from nanobar_api.eventbus.events import Event
from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.eventbus.store import derive_component
from nanobar_api.telemetry.persistence import build_session_factory
from nanobar_api.telemetry.span_repository import SpanRepository
from nanobar_api.telemetry.telemetry_drain_worker import TelemetryDrainWorker, default_entry_point_resolver
from nanobar_api.telemetry.trace_repository import TraceRepository

_DEADLINE_S = 5.0


def _repository(channels: list[str] | None = None) -> EventQueueRepository:
    return EventQueueRepository([ChannelConfig(name=c) for c in (channels or ["trace", "snapshot"])])


def _make_event(
    *,
    span_id: str = "span-1",
    trace_id: str | None = "a" * 32,
    channel: str = "trace",
    name: str = "GET /x",
    **overrides: object,
) -> Event:
    defaults: dict[str, object] = {
        "event_id": f"evt-{span_id}",
        "channel": channel,
        "recorded_at_ns": time.time_ns(),
        "monotonic_ns": time.monotonic_ns(),
        "payload": {"name": name},
        "trace_id": trace_id,
        "span_id": span_id,
    }
    defaults.update(overrides)
    return Event(**defaults)  # type: ignore[arg-type]


def _wait_until(predicate: Callable[[], bool], deadline_s: float = _DEADLINE_S) -> bool:
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_drains_one_event_into_trace_and_span(tmp_path: Path) -> None:
    source = _repository()
    session_factory = build_session_factory(str(tmp_path / "telemetry.db"))
    worker = TelemetryDrainWorker(["trace", "snapshot"], source, session_factory, batch_window_s=0.05)
    worker.start()
    try:
        source.put("trace", _make_event())
        session = session_factory()
        assert _wait_until(lambda: SpanRepository(session).get("evt-span-1") is not None)
        span = SpanRepository(session).get("evt-span-1")
        assert span is not None
        assert span.trace_id == "a" * 32
        trace = TraceRepository(session).get("a" * 32)
        assert trace is not None
        assert trace.entry_point == "GET /x"
    finally:
        worker.stop()
        worker.join(timeout=5.0)
    assert not worker.is_alive()


def test_skips_events_with_no_trace_context(tmp_path: Path) -> None:
    source = _repository()
    session_factory = build_session_factory(str(tmp_path / "telemetry.db"))
    worker = TelemetryDrainWorker(["trace", "snapshot"], source, session_factory, batch_window_s=0.05)
    worker.start()
    try:
        source.put("trace", _make_event(trace_id=None))
        source.put("trace", _make_event(span_id="span-real"))
        session = session_factory()
        assert _wait_until(lambda: SpanRepository(session).get("evt-span-real") is not None)
        assert _wait_until(lambda: worker.skipped_no_trace_context >= 1)
    finally:
        worker.stop()
        worker.join(timeout=5.0)


def test_write_failures_increments_on_malformed_event(tmp_path: Path) -> None:
    source = _repository()
    session_factory = build_session_factory(str(tmp_path / "telemetry.db"))
    worker = TelemetryDrainWorker(["trace", "snapshot"], source, session_factory, batch_window_s=0.05)
    worker.start()
    try:
        source.put("trace", _make_event(recorded_at_ns="not-a-number"))
        assert _wait_until(lambda: worker.write_failures >= 1)
    finally:
        worker.stop()
        worker.join(timeout=5.0)


def test_custom_entry_point_resolver_is_used(tmp_path: Path) -> None:
    source = _repository()
    session_factory = build_session_factory(str(tmp_path / "telemetry.db"))
    worker = TelemetryDrainWorker(
        ["trace", "snapshot"],
        source,
        session_factory,
        entry_point_resolver=lambda event: ("custom-entry-point", "workers"),
        batch_window_s=0.05,
    )
    worker.start()
    try:
        source.put("trace", _make_event())
        session = session_factory()
        assert _wait_until(lambda: TraceRepository(session).get("a" * 32) is not None)
        trace = TraceRepository(session).get("a" * 32)
        assert trace is not None
        assert trace.entry_point == "custom-entry-point"
        assert trace.app_box == "workers"
    finally:
        worker.stop()
        worker.join(timeout=5.0)


def test_it_does_not_capture_itself(tmp_path: Path) -> None:
    """The core property this worker exists to guarantee: ingesting a span must not put any new
    event back onto the source queue it is itself draining -- confirmed by draining N events and
    checking the source queue is empty and stays empty (no self-amplification), not just "the
    worker didn't crash." Regression test for the `KeyError` this raised before
    `NullEventQueueRepository` existed (Phase 4's live smoke test caught it)."""
    source = _repository()
    session_factory = build_session_factory(str(tmp_path / "telemetry.db"))
    worker = TelemetryDrainWorker(["trace", "snapshot"], source, session_factory, batch_window_s=0.05)
    worker.start()
    try:
        for i in range(5):
            source.put("trace", _make_event(span_id=f"span-{i}", trace_id=f"{i:032x}"))
        session = session_factory()
        assert _wait_until(lambda: SpanRepository(session).get("evt-span-4") is not None)
        # Give any would-be self-capture a real chance to land before asserting it never does.
        time.sleep(0.3)
        assert source.dropped_counts["trace"] == 0
        assert source.dropped_counts["snapshot"] == 0
        assert source.get_any(["trace", "snapshot"], timeout=0.1) is None
    finally:
        worker.stop()
        worker.join(timeout=5.0)


def test_stop_flushes_events_already_queued_when_the_worker_starts(tmp_path: Path) -> None:
    """Mirrors `EventThread`'s own "drain everything remaining after `stop()`" final pass --
    `stop()` called before the worker ever runs means the main loop body never executes even
    once, so this specifically exercises that final drain loop, not the ordinary per-iteration
    path (long `batch_window_s` guards against the ordinary path picking it up instead)."""
    source = _repository()
    session_factory = build_session_factory(str(tmp_path / "telemetry.db"))
    worker = TelemetryDrainWorker(["trace", "snapshot"], source, session_factory, batch_window_s=60.0)
    source.put("trace", _make_event())

    worker.stop()
    worker.start()
    worker.join(timeout=5.0)

    session = session_factory()
    assert SpanRepository(session).get("evt-span-1") is not None


def test_default_entry_point_resolver_matches_derive_component_for_api_spans() -> None:
    entry_point, app_box = default_entry_point_resolver(_make_event(name="GET /x"))
    assert entry_point == derive_component("GET /x")[1]
    assert app_box is None


def test_default_entry_point_resolver_falls_back_to_kind_name_for_non_api_spans() -> None:
    entry_point, _ = default_entry_point_resolver(_make_event(name="worker.publish_due_posts.process"))
    assert entry_point == "worker:publish_due_posts"


def test_default_entry_point_resolver_handles_missing_name() -> None:
    entry_point, app_box = default_entry_point_resolver(_make_event(payload={}))
    assert entry_point == "unknown:trace"
    assert app_box is None
