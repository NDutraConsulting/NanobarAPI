from __future__ import annotations

import queue
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from nanobar_api.eventbus.events import Event

_POLL_INTERVAL_SECONDS = 0.01


@dataclass(frozen=True)
class ChannelConfig:
    name: str
    thread: Literal["shared", "dedicated"] = "shared"
    priority: int = 0


class EventQueueRepository:
    """Durable, in-memory front door for per-channel event queues.

    `put()` is called from request-handling code (many concurrent callers,
    possibly across a threadpool) and must never block or raise on backpressure.
    `get_any()` is called from a single background draining thread and is
    allowed to block briefly while polling.

    Thread-safety note on the dropped-event counters: each channel's counter
    is only ever incremented by `put()`, and only after a `queue.Full` is
    caught for that specific channel's `queue.Queue`. Multiple threads can
    call `put()` for the *same* channel concurrently, so `self._dropped[name]
    += 1` is a genuine multi-writer increment. In CPython, `dict.__getitem__`
    followed by `int.__add__` and `dict.__setitem__` is not a single bytecode
    and is not guaranteed atomic in general — under free-threaded (no-GIL)
    Python in particular, this could lose increments. We therefore guard the
    counter dict with a `threading.Lock` rather than relying on GIL
    incidental safety, since the cost (an uncontended lock acquisition on the
    rare `Full` path) is negligible and the correctness is then guaranteed
    regardless of interpreter build.
    """

    def __init__(self, configs: Sequence[ChannelConfig], maxsize: int = 1000) -> None:
        self._configs: dict[str, ChannelConfig] = {config.name: config for config in configs}
        self._channel_names: tuple[str, ...] = tuple(config.name for config in configs)
        self._queues: dict[str, queue.Queue[Event]] = {config.name: queue.Queue(maxsize=maxsize) for config in configs}
        self._dropped: dict[str, int] = dict.fromkeys(self._channel_names, 0)
        self._dropped_lock = threading.Lock()

    def put(self, channel: str, event: Event) -> None:
        try:
            target_queue = self._queues[channel]
        except KeyError:
            raise KeyError(f"unknown channel {channel!r}; configured channels: {self._channel_names}") from None

        try:
            target_queue.put_nowait(event)
        except queue.Full:
            with self._dropped_lock:
                self._dropped[channel] += 1

    def get_any(self, channels: Sequence[str], timeout: float) -> Event | None:
        for name in channels:
            if name not in self._queues:
                raise KeyError(f"unknown channel {name!r}; configured channels: {self._channel_names}")

        deadline = time.monotonic() + timeout
        while True:
            for name in channels:
                try:
                    return self._queues[name].get_nowait()
                except queue.Empty:
                    continue

            if time.monotonic() >= deadline:
                return None

            time.sleep(_POLL_INTERVAL_SECONDS)

    @property
    def dropped_counts(self) -> Mapping[str, int]:
        with self._dropped_lock:
            return MappingProxyType(dict(self._dropped))

    @property
    def channel_names(self) -> Sequence[str]:
        return self._channel_names
