from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence

from nanobar_api.eventbus.events import Event, WorkerRecord

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
    last_heartbeat_at TEXT NOT NULL,
    mode TEXT,
    schedule TEXT,
    poll_interval_s REAL,
    claim_limit INTEGER,
    lease_seconds REAL
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


#: Inverse pair: `derive_component()` reads a captured span's `payload["name"]` and classifies
#: it into a `(kind, name)` component tag; `component_span_name()` reconstructs the exact span
#: name a given `(kind, name)` pair would have produced, so the `components` filter below can
#: match with a plain SQL `IN (...)` against `payload_json`'s `$.name` rather than replicating
#: this classification logic in SQL. Every `kind` here corresponds to a real, already-existing
#: `telemetry.span(...)`/`.trace(...)` call site (see the dashboard-search-and-replay plan doc's
#: "What exists today" section for the full inventory) -- this doesn't invent new span shapes.
_WORKER_PREFIX = "worker."
_WORKER_SUFFIX = ".process"
_CONTROLLER_PREFIX = "controller."
_VALIDATOR_PREFIX = "validator."
_EVENT_CALLBACK_PREFIX = "event-callback."


def derive_component(span_name: str) -> tuple[str, str]:
    """Classify a captured span's `payload["name"]` into a `(kind, name)` component tag.

    `"other"` is a real, intentional catch-all (e.g. `admin/nanobar/api.py`'s ad hoc
    `"dashboard.nanobars.list"` span) -- not every span belongs to one of the framework's
    named layers, and guessing at a made-up kind for those would be worse than being honest
    that this is just "some other span," carrying its own name through unchanged.
    """
    if span_name == "service":
        return ("service", "service")
    if span_name.startswith(_CONTROLLER_PREFIX):
        return ("controller", span_name[len(_CONTROLLER_PREFIX) :])
    if span_name.startswith(_VALIDATOR_PREFIX):
        return ("validator", span_name[len(_VALIDATOR_PREFIX) :])
    if span_name.startswith(_WORKER_PREFIX) and span_name.endswith(_WORKER_SUFFIX):
        return ("worker", span_name[len(_WORKER_PREFIX) : -len(_WORKER_SUFFIX)])
    if span_name.startswith(_EVENT_CALLBACK_PREFIX):
        return ("event", span_name[len(_EVENT_CALLBACK_PREFIX) :])
    # EventBusTraceMiddleware's own top-level HTTP span: "{METHOD} {route}" -- a space, and no
    # "."-joined prefix before that space (rules this out from any of the dotted kinds above).
    if " " in span_name and "." not in span_name.split(" ", 1)[0]:
        return ("api", span_name)
    return ("other", span_name)


def component_span_name(kind: str, name: str) -> str | None:
    """Inverse of `derive_component()`. Returns `None` for a `kind` with no reconstructible
    exact span name (there is none today -- every real kind round-trips -- but callers building
    a filter from unvalidated user input should still handle this rather than assume)."""
    if kind == "api" or kind == "other":
        return name
    if kind == "controller":
        return f"{_CONTROLLER_PREFIX}{name}"
    if kind == "validator":
        return f"{_VALIDATOR_PREFIX}{name}"
    if kind == "worker":
        return f"{_WORKER_PREFIX}{name}{_WORKER_SUFFIX}"
    if kind == "event":
        return f"{_EVENT_CALLBACK_PREFIX}{name}"
    if kind == "service":
        return "service"
    return None


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


def register_worker(
    conn: sqlite3.Connection,
    worker_id: str,
    channels: Sequence[str],
    *,
    mode: str | None = None,
    schedule: str | None = None,
    poll_interval_s: float | None = None,
    claim_limit: int | None = None,
    lease_seconds: float | None = None,
) -> None:
    """Upsert a worker's liveness + configuration row — registers it if new, refreshes its
    channel list/config/heartbeat if it already exists (a worker restarting under the same id
    re-registers cleanly). `mode`/`schedule`/`poll_interval_s`/`claim_limit`/`lease_seconds` are
    a snapshot of `WorkerConfig`/constructor values at the moment of this call (see
    `nanobar_api.workers.NanobarWorker.run_once()`, the only real call site) -- all optional so
    a caller with nothing more than `worker_id`/`channels` still registers cleanly, same as
    before these fields existed.
    """
    with conn:
        conn.execute(
            """
            INSERT INTO workers (
                worker_id, channels, started_at, last_heartbeat_at,
                mode, schedule, poll_interval_s, claim_limit, lease_seconds
            )
            VALUES (?, ?, datetime('now'), datetime('now'), ?, ?, ?, ?, ?)
            ON CONFLICT(worker_id) DO UPDATE SET
                channels = excluded.channels,
                last_heartbeat_at = excluded.last_heartbeat_at,
                mode = excluded.mode,
                schedule = excluded.schedule,
                poll_interval_s = excluded.poll_interval_s,
                claim_limit = excluded.claim_limit,
                lease_seconds = excluded.lease_seconds
            """,
            (worker_id, json.dumps(list(channels)), mode, schedule, poll_interval_s, claim_limit, lease_seconds),
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


def list_workers(conn: sqlite3.Connection) -> list[WorkerRecord]:
    """Every registered worker, most-recently-alive first -- "reviewing configurations and
    monitoring lifecycles" (an app/dashboard consumer, e.g. `admin/nanobar/api.py`'s workers
    routes), a plain listing rather than `list_stale_workers()`'s narrower filtered-to-stale
    view. Whether a given row is stale is left for the caller to decide (it knows its own
    staleness threshold; this function doesn't invent one)."""
    rows = conn.execute(
        """
        SELECT worker_id, channels, started_at, last_heartbeat_at, mode, schedule,
               poll_interval_s, claim_limit, lease_seconds
        FROM workers ORDER BY last_heartbeat_at DESC
        """
    ).fetchall()
    return [
        WorkerRecord(
            worker_id=row[0],
            channels=json.loads(row[1]),
            started_at=row[2],
            last_heartbeat_at=row[3],
            mode=row[4],
            schedule=row[5],
            poll_interval_s=row[6],
            claim_limit=row[7],
            lease_seconds=row[8],
        )
        for row in rows
    ]


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
