from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence

from nanobar_api.eventbus.events import Event, TraceSummary

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

CREATE TABLE IF NOT EXISTS worker_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_id TEXT NOT NULL,
    event_id TEXT,
    error TEXT NOT NULL,
    logged_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_worker_log_worker_id ON worker_log(worker_id, logged_at);
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


def list_trace_ids(conn: sqlite3.Connection, channel: str, limit: int = 100) -> list[TraceSummary]:
    rows = conn.execute(
        """
        SELECT
            trace_id,
            COUNT(*) AS span_count,
            MIN(recorded_at_ns) AS first_recorded_at_ns,
            MAX(recorded_at_ns) AS last_recorded_at_ns,
            MAX(CASE WHEN json_extract(payload_json, '$.error') THEN 1 ELSE 0 END) AS any_error
        FROM events
        WHERE channel = ? AND trace_id IS NOT NULL
        GROUP BY trace_id
        ORDER BY last_recorded_at_ns DESC
        LIMIT ?
        """,
        (channel, limit),
    ).fetchall()
    return [
        TraceSummary(
            trace_id=row[0],
            span_count=row[1],
            first_recorded_at_ns=row[2],
            last_recorded_at_ns=row[3],
            any_error=bool(row[4]),
        )
        for row in rows
    ]


def claim_events(
    conn: sqlite3.Connection, channel: str, worker_id: str, limit: int, lease_seconds: float
) -> list[Event]:
    """Atomically claim up to `limit` free (or lease-expired) events on `channel`.

    Wraps a `BEGIN IMMEDIATE` transaction around select-then-update, per
    `regression-brick-system-plan.md` §9: the same read-then-write race
    `focusari_kahnban` hit with its position-shift `UPDATE`, fixed the same way — an explicit
    atomic claim step, not a bare read, so two workers polling the same channel never both
    select and act on the same row.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = conn.execute(
            """
            SELECT event_id, channel, trace_id, span_id, recorded_at_ns, monotonic_ns, payload_json
            FROM events
            WHERE channel = ? AND processed_at IS NULL AND (claimed_by IS NULL OR lease_expires_at < datetime('now'))
            ORDER BY recorded_at_ns
            LIMIT ?
            """,
            (channel, limit),
        ).fetchall()
        event_ids = [row[0] for row in rows]
        if event_ids:
            placeholders = ",".join("?" for _ in event_ids)
            conn.execute(
                f"""
                UPDATE events SET claimed_by = ?, lease_expires_at = datetime('now', ?)
                WHERE event_id IN ({placeholders})
                """,
                (worker_id, f"{lease_seconds} seconds", *event_ids),
            )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise

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


def ack_event(conn: sqlite3.Connection, event_id: str) -> None:
    """Mark a claimed event processed and release its claim."""
    with conn:
        conn.execute(
            """
            UPDATE events SET processed_at = datetime('now'), claimed_by = NULL, lease_expires_at = NULL
            WHERE event_id = ?
            """,
            (event_id,),
        )


def fail_event(conn: sqlite3.Connection, event_id: str, error: str) -> None:
    """Record a failed processing attempt and release the claim early, rather than waiting out
    the lease — so another worker can retry it on its very next poll instead of idling until
    `lease_expires_at` passes on its own.
    """
    with conn:
        conn.execute(
            """
            UPDATE events
            SET attempt_count = attempt_count + 1, last_error = ?, claimed_by = NULL, lease_expires_at = NULL
            WHERE event_id = ?
            """,
            (error, event_id),
        )


def register_worker(conn: sqlite3.Connection, worker_id: str, channels: Sequence[str]) -> None:
    """Upsert a worker's liveness row — registers it if new, refreshes its channel list and
    heartbeat if it already exists (a worker restarting under the same id re-registers cleanly).
    """
    with conn:
        conn.execute(
            """
            INSERT INTO workers (worker_id, channels, started_at, last_heartbeat_at)
            VALUES (?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(worker_id) DO UPDATE SET
                channels = excluded.channels,
                last_heartbeat_at = excluded.last_heartbeat_at
            """,
            (worker_id, json.dumps(list(channels))),
        )


def heartbeat(conn: sqlite3.Connection, worker_id: str) -> None:
    with conn:
        conn.execute(
            "UPDATE workers SET last_heartbeat_at = datetime('now') WHERE worker_id = ?",
            (worker_id,),
        )


def list_stale_workers(conn: sqlite3.Connection, staleness_seconds: float) -> list[str]:
    """Return worker_ids whose last heartbeat is older than `staleness_seconds`.

    A liveness ledger, not a partition-assignment protocol: row-level lease claiming already
    prevents double-processing on its own, so this exists only for something external — a
    supervisor, an ops dashboard, an alert rule — to answer "which workers are actually alive."
    """
    rows = conn.execute(
        "SELECT worker_id FROM workers WHERE last_heartbeat_at < datetime('now', ?)",
        (f"-{staleness_seconds} seconds",),
    ).fetchall()
    return [row[0] for row in rows]


def insert_worker_log(
    conn: sqlite3.Connection, *, worker_id: str, event_id: str | None, error: str, logged_at: str
) -> None:
    with conn:
        conn.execute(
            "INSERT INTO worker_log (worker_id, event_id, error, logged_at) VALUES (?, ?, ?, ?)",
            (worker_id, event_id, error, logged_at),
        )


def list_worker_log(
    conn: sqlite3.Connection, worker_id: str | None = None, limit: int = 100
) -> list[tuple[str, str | None, str, str]]:
    """Returns raw `(worker_id, event_id, error, logged_at)` tuples — `nanobar_api.worker_utils`
    is what wraps these into `WorkerLogEntry`, the same layering `eventbus/store.py` already
    keeps from `bricks/store.py`'s dataclass-typed functions."""
    if worker_id is None:
        rows = conn.execute(
            "SELECT worker_id, event_id, error, logged_at FROM worker_log ORDER BY logged_at DESC LIMIT ?", (limit,)
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT worker_id, event_id, error, logged_at FROM worker_log
            WHERE worker_id = ? ORDER BY logged_at DESC LIMIT ?
            """,
            (worker_id, limit),
        ).fetchall()
    return [(row[0], row[1], row[2], row[3]) for row in rows]


def get_events_by_trace_id(conn: sqlite3.Connection, trace_id: str, channel: str | None = None) -> list[Event]:
    query = (
        "SELECT event_id, channel, trace_id, span_id, recorded_at_ns, monotonic_ns, payload_json "
        "FROM events WHERE trace_id = ?"
    )
    params: list[str] = [trace_id]
    if channel is not None:
        query += " AND channel = ?"
        params.append(channel)
    query += " ORDER BY monotonic_ns"

    rows = conn.execute(query, params).fetchall()
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
