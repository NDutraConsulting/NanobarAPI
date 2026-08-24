from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import anyio.to_thread

from nanobar_api.eventbus.event_thread import EventThread
from nanobar_api.eventbus.queue_repository import EventQueueRepository


@asynccontextmanager
async def eventbus_lifespan(
    repository: EventQueueRepository,
    db_path: str,
    channels: Sequence[str] | None = None,
) -> AsyncIterator[EventThread]:
    thread = EventThread(
        channels=list(channels) if channels is not None else list(repository.channel_names),
        repository=repository,
        db_path=db_path,
    )
    thread.start()
    try:
        yield thread
    finally:
        thread.stop()
        await anyio.to_thread.run_sync(thread.join)
