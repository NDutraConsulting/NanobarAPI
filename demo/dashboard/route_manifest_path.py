"""Resolves the path to the route-manifest JSON file (`nanobar.api-routes.json`) for the
dashboard app."""

from __future__ import annotations

import os
from pathlib import Path

#: Default location, relative to this file, matching db.py/events_db.py/admin_db.py's own
#: convention (``demo/data/*``). ``demo/data/`` is gitignored.
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "nanobar.api-routes.json"

#: Environment variable used to override DEFAULT_PATH.
PATH_ENV_VAR = "NANOBAR_API_ROUTES_MANIFEST"


def resolve_path() -> str:
    """Returns the configured route-manifest path: env var if set, else the default. The
    parent directory is created first in case it doesn't exist yet -- `write_route_manifest`
    itself also creates it, but resolving eagerly matches every other `resolve_*_path`
    function in this package."""
    path = os.environ.get(PATH_ENV_VAR, str(DEFAULT_PATH))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return path
