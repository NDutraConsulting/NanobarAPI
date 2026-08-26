"""Resolves the path to the admin-auth SQLite database ("adminDB") for the dashboard app."""

from __future__ import annotations

import os
from pathlib import Path

#: Default location, relative to this file, matching db.py/events_db.py's own convention
#: (``demo/data/*.db``). ``demo/data/`` is gitignored.
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "admin.db"

#: Environment variable used to override DEFAULT_DB_PATH.
DB_PATH_ENV_VAR = "NANOBAR_ADMIN_DB"


def resolve_db_path() -> str:
    """Returns the configured admin db path: env var if set, else the default. The parent
    directory is created first in case it doesn't exist yet -- `SQLiteSessionBackend` itself
    creates the file and schema, but not its containing directory.
    """
    db_path = os.environ.get(DB_PATH_ENV_VAR, str(DEFAULT_DB_PATH))
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return db_path
