"""`TelemetryDrainWorker` -- the designated worker draining the shared `EventQueueRepository`
into `nanobar_api_telemetry.db`, replacing `nanobar_api/eventbus/event_thread.py`'s `EventThread`
for trace/span capture (Decision 4/5). Plain `threading.Thread` subclass, same shape as
`EventThread` itself (batch/flush timing via `batch_size`/`batch_window_s`) -- **not** a
`NanobarWorker` subclass, per the research findings in
`.focusari/telemetry-domain-refactor-plan-with-tasks.md`: `NanobarWorker`'s whole lifecycle is
built around claiming *already-persisted* rows with lease semantics, which doesn't fit draining
an in-memory queue at all (`app/services/blog_publisher_worker.py`'s `PostPublisherThread` already
established this same reasoning for an unrelated periodic sweep).

Writes each drained `Event` through the real pipeline (`TelemetryValidatorGate` ->
`TelemetryController` -> `IngestSpanService` -> `[TraceRepository, SpanRepository]`), never
`nanobar_api.eventbus.store.insert_events()` directly.

**"It should not capture itself"** -- resolved at the source, not by routing around it: none of
`TelemetryValidatorGate`/`TelemetryController`/`IngestSpanService` extend the framework's
capture-producing base classes (see `telemetry_service.py`'s own docstring), so this worker's own
ingestion of an event never produces a new event of its own -- there's no `NanobarTelemetry`
instance in this pipeline at all to worry about pointing at the right (or wrong) queue.
"""

from __future__ import annotations

import threading
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any

import anyio.to_thread
from sqlalchemy.orm import Session, sessionmaker

from nanobar_api.eventbus.events import Event
from nanobar_api.eventbus.queue_repository import EventQueueRepository
from nanobar_api.eventbus.store import derive_component
from nanobar_api.telemetry.telemetry_validator_gate import TelemetryValidatorGate

#: `(entry_point, app_box)` for a captured `Event` -- swappable so a future, fuller resolution of
#: the `entry_point`/`app_box` derivation question (see `default_entry_point_resolver`'s own
#: docstring) can replace only this function, not the worker's own drain/batch mechanics.
EntryPointResolver = Callable[[Event], "tuple[str, str | None]"]


def default_entry_point_resolver(event: Event) -> tuple[str, str | None]:
    """Best-effort `entry_point` derivation from an already-captured `Event`, reusing
    `nanobar_api.eventbus.store.derive_component`'s existing span-name classification -- the same
    function `SpanRepository.distinct_facets` already uses for the identical `"kind:name"`
    component-tag shape.

    For `kind == "api"` (an `EventBusTraceMiddleware` top-level HTTP span), `derive_component`'s
    own `name` output is *already* exactly `f"{method} {path}"` -- Decision 2's own HTTP
    `entry_point` convention, no translation needed.

    For every other kind (worker/controller/validator/service/event/other), this falls back to
    the same `f"{kind}:{name}"` shape rather than Decision 2's more specific
    `f"worker-{channel}"`/`f"event-{channel}"` conventions -- reconciling a *worker's own logical
    span name* (what `derive_component` extracts) against its *channel identity* (what those
    conventions actually key on) is flagged as unresolved in both
    `.focusari/regression-brick-refactor-plan-with-tasks.md`'s Decision 3 and
    `.focusari/appbox-plan-with-tasks.md` ("resolve once, not twice") -- not guessed at here.

    `app_box` is always `None` -- `.focusari/appbox-plan-with-tasks.md` hasn't landed yet
    (Decision 2's own nullability rationale), so there's nothing real to compute.
    """
    name = event.payload.get("name")
    if not isinstance(name, str):
        return f"unknown:{event.channel}", None
    kind, component_name = derive_component(name)
    if kind == "api":
        return component_name, None
    return f"{kind}:{component_name}", None


class TelemetryDrainWorker(threading.Thread):
    def __init__(
        self,
        channels: Sequence[str],
        source_repository: EventQueueRepository,
        session_factory: sessionmaker[Session],
        *,
        entry_point_resolver: EntryPointResolver = default_entry_point_resolver,
        batch_size: int = 50,
        batch_window_s: float = 0.5,
        poll_timeout_s: float = 0.1,
    ) -> None:
        super().__init__(daemon=True)
        self._channels = tuple(channels)
        self._source_repository = source_repository
        self._session_factory = session_factory
        self._entry_point_resolver = entry_point_resolver
        self._batch_size = batch_size
        self._batch_window_s = batch_window_s
        self._poll_timeout_s = poll_timeout_s
        self._stop_event = threading.Event()
        #: Every span-ingest attempt that came back `status != "success"` (malformed shape, or a
        #: repository-layer exception the gate never sees) -- mirrors `EventThread.write_failures`.
        self.write_failures = 0
        #: Events with no ambient trace context (`trace_id`/`span_id` both required, non-nullable
        #: columns on `Span`/`Trace` -- Decision 2) -- can't become a `Span` at all, dropped
        #: rather than crashing the batch.
        self.skipped_no_trace_context = 0

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        session = self._session_factory()
        gate = TelemetryValidatorGate(session)
        try:
            batch: list[Event] = []
            last_flush = time.monotonic()

            while not self._stop_event.is_set():
                event = self._source_repository.get_any(self._channels, timeout=self._poll_timeout_s)
                if event is not None:
                    batch.append(event)

                now = time.monotonic()
                should_flush = bool(batch) and (
                    len(batch) >= self._batch_size or now - last_flush >= self._batch_window_s
                )
                if should_flush:
                    self._flush(gate, batch)
                    batch = []
                    last_flush = now

            while True:
                event = self._source_repository.get_any(self._channels, timeout=0.0)
                if event is None:
                    break
                batch.append(event)

            self._flush(gate, batch)
        finally:
            session.close()

    def _flush(self, gate: TelemetryValidatorGate, batch: list[Event]) -> None:
        """One `TelemetryValidatorGate` call per event, not one batched transaction -- matches
        this codebase's own repository convention (every real repository commits per-operation,
        not in bulk); `batch_size`/`batch_window_s` govern how often a drain *pass* runs, same as
        `EventThread`, not a single multi-row write."""
        for event in batch:
            self._ingest_one(gate, event)

    def _ingest_one(self, gate: TelemetryValidatorGate, event: Event) -> None:
        if event.trace_id is None or event.span_id is None:
            self.skipped_no_trace_context += 1
            return

        entry_point, app_box = self._entry_point_resolver(event)
        raw: dict[str, Any] = {
            "event_id": event.event_id,
            "trace_id": event.trace_id,
            "span_id": event.span_id,
            "channel": event.channel,
            "recorded_at_ns": event.recorded_at_ns,
            "monotonic_ns": event.monotonic_ns,
            "payload": event.payload,
            "entry_point": entry_point,
            "app_box": app_box,
        }
        envelope = gate(raw)
        if envelope["status"] != "success":
            self.write_failures += 1


@asynccontextmanager
async def telemetry_drain_worker_lifespan(
    channels: Sequence[str],
    source_repository: EventQueueRepository,
    session_factory: sessionmaker[Session],
) -> AsyncIterator[TelemetryDrainWorker]:
    """Same shape as `nanobar_api.eventbus.lifespan.eventbus_lifespan` -- starts the worker in
    its own daemon thread, stops and joins it (off the event loop, via `anyio.to_thread.run_sync`)
    on exit, so a shutdown never leaves the thread running past the app's own lifetime."""
    worker = TelemetryDrainWorker(channels, source_repository, session_factory)
    worker.start()
    try:
        yield worker
    finally:
        worker.stop()
        await anyio.to_thread.run_sync(worker.join)
