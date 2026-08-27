"""`NanobarEventBus`/`NanobarCallback` — the *domain* eventbus (services publish business events,
subscribers get invoked per channel), a genuinely different concept from `nanobar_api.eventbus`'s
existing *telemetry* plumbing (trace/snapshot capture draining into `events.db`), despite sharing
vocabulary.

Per `.focusari/nanobar_EventSystemDomain_abstract_class_buildplan-with-tasks.md` §1 — the single
most important thing this module gets right: `EventQueueRepository.get_any()` is a **pop**, not a
broadcast, so `EventThread` (drains `"trace"`/`"snapshot"`) and `NanobarEventBus` (drains domain
channels) must never be pointed at the same channel, or they'd silently split events between them
instead of both seeing every one. Enforced here, not just documented: every domain channel name
must start with `"domain."` — `subscribe()`/`publish()` both reject anything else.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any

import anyio.to_thread

from nanobar_api.eventbus.events import Event
from nanobar_api.eventbus.queue_repository import EventQueueRepository
from nanobar_api.middleware.trace import current_span_id, current_trace_id
from nanobar_api.telemetry import NanobarProps, NanobarTelemetry

logger = logging.getLogger(__name__)

#: The channel-namespace separation §1 adopts, not merely proposes — enforced here, not just
#: documented, since a channel name collision with `EventThread`'s `"trace"`/`"snapshot"`
#: channels would silently steal events from one consumer or the other.
DOMAIN_CHANNEL_PREFIX = "domain."


def _check_domain_channel(channel: str) -> None:
    if not channel.startswith(DOMAIN_CHANNEL_PREFIX):
        raise ValueError(
            f"channel {channel!r} must start with {DOMAIN_CHANNEL_PREFIX!r} — domain business-event "
            "channels use a separate namespace from EventThread's trace/snapshot channels, so the two "
            "consumers never silently split events meant for each other"
        )


class NanobarCallback(ABC):
    @abstractmethod
    def handle(self, event: Event) -> Any: ...

    def on_success(self, event: Event, result: Any) -> None:
        """Default: no-op. Override for custom logging/metrics."""

    def on_failure(self, event: Event, exc: Exception) -> None:
        """Default: logs via the stdlib `logging` module. Never re-raises — a failing subscriber
        must never take down the dispatch loop or block sibling subscribers on the same event."""
        logger.exception(
            "subscriber callback failed for event %s on channel %s", event.event_id, event.channel, exc_info=exc
        )


class NanobarEventBus:
    def __init__(self, repository: EventQueueRepository, telemetry: NanobarTelemetry) -> None:
        self._repository = repository
        self._telemetry = telemetry
        self._subscribers: dict[str, list[NanobarCallback]] = {}
        self._stop_event = threading.Event()

    def subscribe(self, channel: str, callback: NanobarCallback) -> None:
        _check_domain_channel(channel)
        self._subscribers.setdefault(channel, []).append(callback)  # registration order preserved

    def list_subscriptions(self) -> Mapping[str, Sequence[NanobarCallback]]:
        """Per the source spec: "track all the event subscriber.method(eventProps)" — a
        queryable, process-local snapshot for a future dashboard page (not built here)."""
        return {channel: tuple(callbacks) for channel, callbacks in self._subscribers.items()}

    def publish(self, channel: str, payload: dict[str, Any]) -> None:
        """Thin wrapper: builds an `Event` and calls `repository.put(channel, event)` — the same
        non-blocking, fire-and-forget contract every other producer in this codebase relies on.
        """
        _check_domain_channel(channel)
        event = Event(
            event_id=str(uuid.uuid4()),
            channel=channel,
            recorded_at_ns=time.time_ns(),
            monotonic_ns=time.monotonic_ns(),
            payload=payload,
            trace_id=current_trace_id.get(),
            span_id=current_span_id.get(),
        )
        self._repository.put(channel, event)

    def stop(self) -> None:
        self._stop_event.set()

    def run_forever(self, poll_timeout_s: float = 0.1) -> None:
        """Blocking — the caller runs this in its own thread (matching `EventThread.run()`,
        which `threading.Thread.start()` invokes the same way), not something this method spawns
        itself. Same shutdown discipline as `EventThread`: stop polling once `stop()` is called,
        then drain whatever's still queued before returning, so a shutdown never silently drops
        events that were already published.
        """
        channels = list(self._subscribers.keys())
        while not self._stop_event.is_set():
            event = self._repository.get_any(channels, timeout=poll_timeout_s)
            if event is not None:
                self._dispatch(event)

        while True:
            event = self._repository.get_any(channels, timeout=0.0)
            if event is None:
                break
            self._dispatch(event)

    def _dispatch(self, event: Event) -> None:
        # Deferred, not module-level: nanobar_api.capture.layer_capture itself imports
        # nanobar_api.eventbus.events, which — importing any submodule of a package always
        # runs that package's __init__.py first — would otherwise make this a circular import
        # at `eventbus/__init__.py` load time (it re-exports this module). By the time
        # `_dispatch()` actually runs, both modules are already fully initialized.
        from nanobar_api.capture.layer_capture import capture_layer, to_payload_dict

        def _safe_capture(request_payload: dict[str, Any], response_payload: dict[str, Any], *, error: bool) -> None:
            # Telemetry capture must never take down the dispatch loop -- e.g. the repository
            # wasn't configured with a "snapshot" channel (EventQueueRepository.put() raises
            # KeyError for any channel it doesn't know about), or a DB error. Same "never let a
            # side-observation kill the actual dispatch" contract callback.on_failure() already
            # documents for subscriber failures, applied here to capture_layer() itself.
            try:
                capture_layer(
                    self._repository,
                    "event",
                    request_payload,
                    response_payload,
                    nanobar_type="event-to-subscriber",
                    error=error,
                )
            except Exception:
                logger.exception("failed to capture snapshot for event %s on channel %s", event.event_id, event.channel)

        for callback in self._subscribers.get(event.channel, []):
            try:
                with self._telemetry.span(
                    f"event-callback.{event.channel}", nanobar=NanobarProps(type="event-to-subscriber")
                ):
                    result = callback.handle(event)
            except Exception as exc:
                callback.on_failure(event, exc)
                _safe_capture(
                    to_payload_dict(event.payload),
                    {"error_type": type(exc).__name__, "error_message": str(exc)},
                    error=True,
                )
                continue
            callback.on_success(event, result)
            _safe_capture(to_payload_dict(event.payload), to_payload_dict(result), error=False)


@asynccontextmanager
async def event_bus_lifespan(bus: NanobarEventBus, poll_timeout_s: float = 0.1) -> AsyncIterator[NanobarEventBus]:
    """`eventbus_lifespan`'s exact shape (`nanobar_api/eventbus/lifespan.py`), for `NanobarEventBus`
    instead of `EventThread` — runs alongside it, never instead of it (disjoint channel sets)."""
    thread = threading.Thread(target=bus.run_forever, args=(poll_timeout_s,), daemon=True)
    thread.start()
    try:
        yield bus
    finally:
        bus.stop()
        await anyio.to_thread.run_sync(thread.join)
