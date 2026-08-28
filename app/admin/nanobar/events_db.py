"""Resolves and opens the events SQLite database for the dashboard app.

**Worker-registry bookkeeping only, as of `.focusari/telemetry-domain-refactor-plan-with-tasks.md`
Decision 6** -- trace/span capture moved to its own database (`nanobar_api_telemetry.db`, see
`telemetry_db.py`). This file's `workers`/`worker_log` tables (`NanobarWorker`'s own claim-lease
liveness mechanism) stay here, unmigrated -- a deliberately separate concern from trace/span
storage that happened to share a physical file before this split, not after.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from nanobar_api.eventbus.store import connect

#: Default location: `app/db/events.db` -- worker-registry bookkeeping only now (see module
#: docstring), not nested under this package's own `data/` directory. Gitignored; may not exist
#: yet or may be empty.
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "db" / "events.db"

#: Environment variable used to override DEFAULT_DB_PATH (e.g. to point at a fixture in
#: tests, or a different environment's database).
DB_PATH_ENV_VAR = "NANOBAR_EVENTS_DB"


def resolve_db_path() -> str:
    """Returns the configured events db path: env var if set, else the default."""
    return os.environ.get(DB_PATH_ENV_VAR, str(DEFAULT_DB_PATH))


def get_connection(db_path: str) -> sqlite3.Connection:
    """Opens a fresh connection to the events database at `db_path`.

    `store.connect()` creates the schema idempotently whenever the database file or its
    tables don't exist yet, so a not-yet-seeded (or entirely missing) database still opens
    cleanly and simply shows up as "no traces", rather than crashing.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return connect(db_path)
