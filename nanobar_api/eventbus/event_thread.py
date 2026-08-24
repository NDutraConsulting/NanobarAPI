from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Sequence

from nanobar_api.eventbus import store
from nanobar_api.eventbus.events import Event
from nanobar_api.eventbus.queue_repository import EventQueueRepository


class EventThread(threading.Thread):
    def __init__(
        self,
        channels: Sequence[str],
        repository: EventQueueRepository,
        db_path: str,
        batch_size: int = 50,
        batch_window_s: float = 0.5,
        poll_timeout_s: float = 0.1,
    ) -> None:
        super().__init__(daemon=True)
        self._channels = tuple(channels)
        self._repository = repository
        self._db_path = db_path
        self._batch_size = batch_size
        self._batch_window_s = batch_window_s
        self._poll_timeout_s = poll_timeout_s
        self._stop_event = threading.Event()
        self.write_failures = 0

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        conn = store.connect(self._db_path)
        try:
            batch: list[Event] = []
            last_flush = time.monotonic()

            while not self._stop_event.is_set():
                event = self._repository.get_any(self._channels, timeout=self._poll_timeout_s)
                if event is not None:
                    batch.append(event)

                now = time.monotonic()
                should_flush = bool(batch) and (
                    len(batch) >= self._batch_size or now - last_flush >= self._batch_window_s
                )
                if should_flush:
                    self._flush(conn, batch)
                    batch = []
                    last_flush = now

            while True:
                event = self._repository.get_any(self._channels, timeout=0.0)
                if event is None:
                    break
                batch.append(event)

            self._flush(conn, batch)
        finally:
            conn.close()

    def _flush(self, conn: sqlite3.Connection, batch: list[Event]) -> None:
        if not batch:
            return
        try:
            store.insert_events(conn, batch)
        except Exception:
            self.write_failures += 1
