"""Durable "when did each refresh cycle last run, and what happened" record, backing the
`/admin/nanobar/dashboard/settings` page's "Refresh cycles" section.

Same "own schema, fresh connection per call" shape as `nanobar_api.middleware.trace`'s
`SQLiteTraceCaptureToggle` -- one row per refresh *kind* (`"api"`/`"nanobars"`/`"bricks"`)
instead of a single row, since these are three independent cycles, not one on/off flag.
"""

from __future__ import annotations

import sqlite3

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS refresh_log (
    kind TEXT PRIMARY KEY,
    last_run_at TEXT NOT NULL,
    summary TEXT NOT NULL
);
"""


class SQLiteRefreshLog:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        conn = sqlite3.connect(db_path, timeout=5.0)
        try:
            conn.executescript(_SCHEMA_SQL)
            conn.commit()
        finally:
            conn.close()

    def record(self, kind: str, *, last_run_at: str, summary: str) -> None:
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO refresh_log (kind, last_run_at, summary) VALUES (?, ?, ?)
                    ON CONFLICT (kind) DO UPDATE SET last_run_at = excluded.last_run_at, summary = excluded.summary
                    """,
                    (kind, last_run_at, summary),
                )
        finally:
            conn.close()

    def get_all(self) -> dict[str, dict[str, str]]:
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        try:
            rows = conn.execute("SELECT kind, last_run_at, summary FROM refresh_log").fetchall()
        finally:
            conn.close()
        return {kind: {"last_run_at": last_run_at, "summary": summary} for kind, last_run_at, summary in rows}
