"""Resolves and opens the events SQLite database (trace/span data) for the dashboard app."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from nanobar_api.eventbus.store import connect

#: Default location, relative to this file, matching the seed script's own output path
#: (``demo/data/events.db``). ``demo/data/`` is gitignored and populated by a separate seed
#: script; it may not exist yet or may be empty, and that's fine.
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "events.db"

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
