from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence

from nanobar_api.eventbus.events import Event

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    trace_id TEXT,
    span_id TEXT,
    recorded_at_ns INTEGER NOT NULL,
    monotonic_ns INTEGER NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    processed_at TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    claimed_by TEXT,
    lease_expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_channel_recorded ON events(channel, recorded_at_ns);
CREATE INDEX IF NOT EXISTS idx_events_trace_id ON events(trace_id);
CREATE INDEX IF NOT EXISTS idx_events_unprocessed ON events(channel, processed_at) WHERE processed_at IS NULL;

CREATE TABLE IF NOT EXISTS workers (
    worker_id TEXT PRIMARY KEY,
    channels TEXT NOT NULL,
    started_at TEXT NOT NULL,
    last_heartbeat_at TEXT NOT NULL
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    """Open a new connection to db_path, owned by exactly one thread for its whole lifetime.

    Sets WAL mode so a reader thread and this writer thread do not block each other,
    then ensures the schema exists (idempotent via IF NOT EXISTS). Safe to call
    concurrently from multiple threads pointed at the same db_path: SQLite's own
    file-level locking plus IF NOT EXISTS on the schema statements makes this safe
    without any extra locking here — contending writers block-and-retry against
    each other via the explicit busy timeout below, rather than erroring immediately.
    """
    conn = sqlite3.connect(db_path, check_same_thread=True, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def insert_events(conn: sqlite3.Connection, events: Sequence[Event]) -> None:
    """Insert a batch of events in one transaction.

    No-op if events is empty. Batching is the caller's responsibility (batch
    size/timing); this function's job is just to insert the given batch
    atomically — if any row fails, the whole batch is rolled back rather than
    partially applied or silently swallowed.
    """
    if not events:
        return

    rows = [
        (
            event.event_id,
            event.channel,
            event.trace_id,
            event.span_id,
            event.recorded_at_ns,
            event.monotonic_ns,
            json.dumps(event.payload),
        )
        for event in events
    ]

    with conn:
        conn.executemany(
            """
            INSERT INTO events (event_id, channel, trace_id, span_id, recorded_at_ns, monotonic_ns, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def get_unprocessed(conn: sqlite3.Connection, channel: str, limit: int = 100) -> list[Event]:
    rows = conn.execute(
        """
        SELECT event_id, channel, trace_id, span_id, recorded_at_ns, monotonic_ns, payload_json
        FROM events WHERE channel = ? AND processed_at IS NULL ORDER BY recorded_at_ns LIMIT ?
        """,
        (channel, limit),
    ).fetchall()
    return [
        Event(
            event_id=row[0],
            channel=row[1],
            trace_id=row[2],
            span_id=row[3],
            recorded_at_ns=row[4],
            monotonic_ns=row[5],
            payload=json.loads(row[6]),
        )
        for row in rows
    ]


def mark_processed(conn: sqlite3.Connection, event_ids: Sequence[str]) -> None:
    if not event_ids:
        return
    with conn:
        conn.executemany(
            "UPDATE events SET processed_at = datetime('now') WHERE event_id = ?",
            [(event_id,) for event_id in event_ids],
        )
