"""Worker failure logging — per the source spec: "If a worker fails that must be logged into
an append only file and a worker sqliteDB log with a worker_log class defined in
worker_utils.py."

Two sinks on every failure: an append-only JSON-line file
(`{log_dir}/{date}-worker-failures.log`, the same path convention `NanobarSupervisor`'s
`SupervisorConfig.log_dir` reuses for its own process-level escalations) and a `worker_log` row
in `events.db` — not a third SQLite file, since these are small-volume and tightly coupled to
the `events`/`workers` tables already there.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from nanobar_api.eventbus.store import insert_worker_log, list_worker_log


@dataclass(frozen=True)
class WorkerLogEntry:
    worker_id: str
    event_id: str | None
    error: str
    logged_at: str


def log_worker_failure(conn: sqlite3.Connection, entry: WorkerLogEntry, *, log_dir: str = "logs") -> None:
    _append_to_log_file(entry, log_dir)
    insert_worker_log(
        conn, worker_id=entry.worker_id, event_id=entry.event_id, error=entry.error, logged_at=entry.logged_at
    )


def _append_to_log_file(entry: WorkerLogEntry, log_dir: str) -> None:
    date = entry.logged_at[:10]  # logged_at is "YYYY-MM-DD HH:MM:SS" -- date is its first 10 chars
    path = Path(log_dir) / f"{date}-worker-failures.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {"worker_id": entry.worker_id, "event_id": entry.event_id, "error": entry.error, "logged_at": entry.logged_at}
    )
    with path.open("a") as f:
        f.write(line + "\n")


def get_worker_log(conn: sqlite3.Connection, worker_id: str | None = None, limit: int = 100) -> list[WorkerLogEntry]:
    return [
        WorkerLogEntry(worker_id=row[0], event_id=row[1], error=row[2], logged_at=row[3])
        for row in list_worker_log(conn, worker_id, limit)
    ]
