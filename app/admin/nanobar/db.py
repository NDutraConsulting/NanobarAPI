"""Resolves the regression-bricks SQLite database path for the dashboard app -- session-factory
construction itself lives in `nanobar_api.persistence.build_session_factory` (shared by
`NanobarRepository`/`RegressionBrickRepository`), built once at app startup and stored on
`app.state.bricks_session_factory` (see `app/main.py`), same convention as
`app/db/blog_session.py`'s `blog_session_factory`.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Default location: `app/admin/nanobar/data/regression_bricks.db`, alongside the code that
#: owns it -- gitignored and also populated by a separate seed script; it may not exist yet or
#: may be empty, and that's fine.
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "regression_bricks.db"

#: Environment variable used to override DEFAULT_DB_PATH (e.g. to point at a fixture in
#: tests, or a different environment's database).
DB_PATH_ENV_VAR = "NANOBAR_REGRESSION_BRICKS_DB"


def resolve_db_path() -> str:
    """Returns the configured regression-bricks db path: env var if set, else the default. The
    parent directory is created first, matching every other `resolve_db_path()` in this package
    -- `build_session_factory()`'s underlying `create_engine()` creates the file but not its
    containing directory.
    """
    db_path = os.environ.get(DB_PATH_ENV_VAR, str(DEFAULT_DB_PATH))
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return db_path
