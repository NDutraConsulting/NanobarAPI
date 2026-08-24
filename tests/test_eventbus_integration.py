import json
import sqlite3
import time
from pathlib import Path

import anyio
import pytest

from nanobar_api.eventbus.events import Event
from nanobar_api.eventbus.lifespan import eventbus_lifespan
from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository


@pytest.mark.anyio
async def test_put_event_reaches_events_db(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    repository = EventQueueRepository([ChannelConfig(name="trace")])

    async with eventbus_lifespan(repository, db_path) as thread:
        repository.put(
            "trace",
            Event(
                event_id="evt-1",
                channel="trace",
                recorded_at_ns=time.time_ns(),
                monotonic_ns=time.monotonic_ns(),
                payload={"method": "GET", "path": "/ping"},
                trace_id="a" * 32,
                span_id="b" * 16,
            ),
        )

        deadline = time.monotonic() + 5.0
        rows: list[tuple[str, str, str, str]] = []
        while time.monotonic() < deadline:
            conn = sqlite3.connect(db_path)
            try:
                rows = conn.execute("SELECT event_id, channel, trace_id, payload_json FROM events").fetchall()
            except sqlite3.OperationalError:
                rows = []
            finally:
                conn.close()
            if rows:
                break
            await anyio.sleep(0.05)

        assert thread.is_alive()

    assert rows == [("evt-1", "trace", "a" * 32, json.dumps({"method": "GET", "path": "/ping"}))]
    assert not thread.is_alive()


@pytest.mark.anyio
async def test_multiple_events_batch_and_persist(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    repository = EventQueueRepository([ChannelConfig(name="trace")])

    async with eventbus_lifespan(repository, db_path) as thread:
        for i in range(5):
            repository.put(
                "trace",
                Event(
                    event_id=f"evt-{i}",
                    channel="trace",
                    recorded_at_ns=time.time_ns(),
                    monotonic_ns=time.monotonic_ns(),
                    payload={"i": i},
                ),
            )
        await anyio.sleep(0.2)
        assert thread.write_failures == 0

    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        conn.close()
    assert count == 5


@pytest.mark.anyio
async def test_write_failure_is_counted_not_raised(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    repository = EventQueueRepository([ChannelConfig(name="trace")], maxsize=10)

    async with eventbus_lifespan(repository, db_path) as thread:
        duplicate = Event(
            event_id="dup",
            channel="trace",
            recorded_at_ns=time.time_ns(),
            monotonic_ns=time.monotonic_ns(),
            payload={},
        )
        repository.put("trace", duplicate)
        repository.put("trace", duplicate)

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and thread.write_failures == 0:
            await anyio.sleep(0.05)

        assert thread.write_failures >= 1
        assert thread.is_alive()


@pytest.mark.anyio
async def test_shutdown_drains_remaining_queued_events(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    repository = EventQueueRepository([ChannelConfig(name="trace")])

    async with eventbus_lifespan(repository, db_path) as thread:
        # Put right before shutdown, racing the thread's own drain loop —
        # the final drain-on-stop pass must still pick this up.
        repository.put(
            "trace",
            Event(
                event_id="evt-last",
                channel="trace",
                recorded_at_ns=time.time_ns(),
                monotonic_ns=time.monotonic_ns(),
                payload={},
            ),
        )

    assert not thread.is_alive()
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT event_id FROM events WHERE event_id = 'evt-last'").fetchall()
    finally:
        conn.close()
    assert rows == [("evt-last",)]
