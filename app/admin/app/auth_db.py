"""Resolves the path to the app-admin SQLite database ("app_admin.db") -- this admin surface's
own sessions/CSRF/user-store storage, independent of `admin/nanobar`'s own `nanobar_admin.db`.
Two admin surfaces, two logins, two databases -- not one shared admin auth store."""

from __future__ import annotations

import os
from pathlib import Path

from app.core.config import DATA_DIR

#: Default location, matching every other per-app database's own convention
#: (``demo/data/*.db``). ``demo/data/`` is gitignored.
DEFAULT_DB_PATH = DATA_DIR / "app_admin.db"

#: Environment variable used to override DEFAULT_DB_PATH.
DB_PATH_ENV_VAR = "NANOBAR_APP_ADMIN_DB"


def resolve_db_path() -> str:
    """Returns the configured app-admin db path: env var if set, else the default. The parent
    directory is created first in case it doesn't exist yet -- `SQLiteSessionBackend` itself
    creates the file and schema, but not its containing directory.
    """
    db_path = os.environ.get(DB_PATH_ENV_VAR, str(DEFAULT_DB_PATH))
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return db_path
