"""Resolves and opens the dynamic (runtime-writable) nanobar-type-system SQLite database.

See `nanobar_api/dynamic_taxonomy.py`'s own module docstring for what this database is for --
per-`(key, key_name)` `nanobar_type` coverage rules (e.g. one entry per worker channel) that a
static, checked-in `nanobar.types.lock` file can't hold without a code change and a release.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from app.core.config import DATA_DIR
from nanobar_api.dynamic_taxonomy import connect

#: Default location, matching every other per-app database's own convention
#: (``demo/data/regression_bricks.db``, ``demo/data/events.db``, ...). ``demo/data/`` is
#: gitignored; it may not exist yet or may be empty, and that's fine.
DEFAULT_DB_PATH = DATA_DIR / "nanobar_type_system.db"

#: Environment variable used to override DEFAULT_DB_PATH (e.g. to point at a fixture in
#: tests, or a different environment's database).
DB_PATH_ENV_VAR = "NANOBAR_TYPE_SYSTEM_DB"


def resolve_db_path() -> str:
    """Returns the configured nanobar-type-system db path: env var if set, else the default."""
    return os.environ.get(DB_PATH_ENV_VAR, str(DEFAULT_DB_PATH))


def get_connection(db_path: str) -> sqlite3.Connection:
    """Opens a fresh connection to the nanobar-type-system database at `db_path`.

    `dynamic_taxonomy.connect()` creates the schema idempotently, so a not-yet-seeded (or
    entirely missing) database still opens cleanly and simply shows up empty. The parent
    directory is created first in case it doesn't exist either — `sqlite3.connect()` creates
    the file but not its containing directory.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return connect(db_path)
