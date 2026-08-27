"""Resolves the path to the nanobar-admin SQLite database ("nanobar_admin.db") -- this admin
surface's own sessions/CSRF/user-store storage, independent of `admin/app`'s own
`app_admin.db`. Also holds `SQLiteTraceCaptureToggle`/`SQLiteRefreshLog` -- both are managed
exclusively from this admin surface's own Settings page, so their storage lives alongside its
own session store rather than in a database shared with the other admin surface.

Named `auth_db.py`, not `db.py` -- `db.py` in this same package already resolves
`regression_bricks.db`, a wholly different database.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Default location: `app/admin/nanobar/data/nanobar_admin.db`, alongside the code that owns
#: it -- gitignored.
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "nanobar_admin.db"

#: Environment variable used to override DEFAULT_DB_PATH.
DB_PATH_ENV_VAR = "NANOBAR_ADMIN_DB"


def resolve_db_path() -> str:
    """Returns the configured nanobar-admin db path: env var if set, else the default. The
    parent directory is created first in case it doesn't exist yet -- `SQLiteSessionBackend`
    itself creates the file and schema, but not its containing directory.
    """
    db_path = os.environ.get(DB_PATH_ENV_VAR, str(DEFAULT_DB_PATH))
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return db_path
