"""Resolves the telemetry (trace/span) SQLite database path for the dashboard app --
session-factory construction itself lives in `nanobar_api.telemetry.persistence.
build_session_factory`, built once at app startup and stored on
`app.state.telemetry_session_factory` (see `app/main.py`), same convention as
`app/admin/nanobar/db.py`'s `bricks_session_factory`.

Own file, own database (`nanobar_api_telemetry.db`), separate from `events.db` --
`.focusari/telemetry-domain-refactor-plan-with-tasks.md` Decision 6: trace/span capture is no
longer part of `events.db` (which now holds only worker-registry bookkeeping, see
`events_db.py`'s own updated docstring), and no longer shares `regression_bricks.db` with
`RegressionBrick`/`Nanobar` either.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Default location: `app/db/nanobar_api_telemetry.db` -- alongside `events.db`, cross-domain
#: telemetry data, not nested under this package's own `data/` directory. Gitignored; may not
#: exist yet or may be empty.
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "db" / "nanobar_api_telemetry.db"

#: Environment variable used to override DEFAULT_DB_PATH (e.g. to point at a fixture in
#: tests, or a different environment's database).
DB_PATH_ENV_VAR = "NANOBAR_TELEMETRY_DB"


def resolve_db_path() -> str:
    """Returns the configured telemetry db path: env var if set, else the default. The parent
    directory is created first, matching `app/admin/nanobar/db.py`'s identical convention --
    `build_session_factory()`'s underlying `create_engine()` creates the file but not its
    containing directory.
    """
    db_path = os.environ.get(DB_PATH_ENV_VAR, str(DEFAULT_DB_PATH))
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return db_path
