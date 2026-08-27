"""Flips due `scheduled` posts to `published` -- a periodic time-based sweep.

**Deliberately NOT a `NanobarWorker` subclass, despite the "post scheduler" framing suggesting
one -- found by reading `nanobar_api/workers.py` directly, not assumed.** `NanobarWorker.run_once()`
is built entirely around `eventbus/store.py`'s `claim_events()`/`ack_event()`/`fail_event()` --
SQL-table claim/lease/ack semantics over the `events` table's rows. A "is any post due yet" sweep
has no natural per-post *queued work item* to claim: nothing publishes a discrete event per due
post, and forcing one through (e.g. a synthetic per-tick event) would be machinery built to
satisfy the base class rather than the actual job. `NanobarEventBus`/`NanobarEventBus.publish()`
(used by `app/services/blog_service.py`'s `BookAppointmentService`) is also the wrong fit here for a
different reason -- it's in-memory pub/sub with no independent persistence, not something
`claim_events()` can see either (confirmed: nothing inserts a `NanobarEventBus`-published event
into the `events` SQL table; `EventThread` and `NanobarEventBus` deliberately drain disjoint
channel sets, by the Event-System-Domain plan's own design).

So: a plain periodic sweep, run the same way `EventThread`/`NanobarEventBus` already run their
own background loops -- a daemon thread started/stopped via an async lifespan context manager,
not directly on the ASGI event loop (SQLAlchemy's sync `Session` API would block it).
"""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from app.crud.blog_crud import PostRepository


class PostPublisherThread:
    def __init__(self, session_factory: sessionmaker[Session], *, poll_interval_s: float = 5.0) -> None:
        self.session_factory = session_factory
        self.poll_interval_s = poll_interval_s
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run_once(self) -> int:
        """Publishes every scheduled post whose `scheduled_at` has passed. Returns the count
        published -- lets a caller (tests, `run_forever()`) observe progress without racing the
        poll loop's own sleep.
        """
        session = self.session_factory()
        try:
            repository = PostRepository(session)
            due = repository.list_due_for_publish(now=datetime.now(UTC))
            for post in due:
                repository.update_status(post.id, status="published", published_at=datetime.now(UTC))
            return len(due)
        finally:
            session.close()

    def run_forever(self) -> None:
        while not self._stop_event.is_set():
            self.run_once()
            self._stop_event.wait(self.poll_interval_s)


@asynccontextmanager
async def post_publisher_lifespan(publisher: PostPublisherThread) -> AsyncIterator[PostPublisherThread]:
    """Same shape as `nanobar_api.eventbus.lifespan.eventbus_lifespan`/`event_bus_lifespan`:
    starts the sweep in its own daemon thread, stops it (and joins, so a shutdown never leaves
    the thread running past the app's own lifetime) on exit."""
    thread = threading.Thread(target=publisher.run_forever, daemon=True)
    thread.start()
    try:
        yield publisher
    finally:
        publisher.stop()
        thread.join(timeout=5.0)
