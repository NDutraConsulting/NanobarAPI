"""Cross-cutting path configuration -- `DATA_DIR` (every per-domain SQLite/JSON path resolver's
default is relative to it), `WEB_DIR` (the static page-bundle root every page-serving route file
needs), and the route-manifest JSON file's own path resolver.

Kept in one fixed location (`app/core/`, not computed inline inside each individual resolver
file) for the same reason: a `Path(__file__).resolve().parent...` computed inside each resolver
needs a different `.parent` count depending on how deeply nested that resolver's own domain
subpackage is -- fragile, and silently wrong if a file ever moves without recomputing it (as has
happened more than once in this project's own domain refactors).
"""

from __future__ import annotations

import os
from pathlib import Path

#: Repo root -- three `.parent`s up from `app/core/config.py` (config.py -> core/ -> app/ -> repo root).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: Path to `demo/data/`, every per-domain SQLite/JSON path resolver's default parent directory.
DATA_DIR = _REPO_ROOT / "demo" / "data"

#: Path to `app/pages/`, the static per-page bundle root every page-serving route file needs.
WEB_DIR = _REPO_ROOT / "app" / "pages"

#: Default location for the route manifest JSON, matching every other `resolve_*_path`'s own
#: convention (``demo/data/*``). ``demo/data/`` is gitignored.
ROUTE_MANIFEST_DEFAULT_PATH = DATA_DIR / "nanobar.api-routes.json"

#: Environment variable used to override ROUTE_MANIFEST_DEFAULT_PATH.
ROUTE_MANIFEST_PATH_ENV_VAR = "NANOBAR_API_ROUTES_MANIFEST"


def resolve_route_manifest_path() -> str:
    """Returns the configured route-manifest path: env var if set, else the default. The
    parent directory is created first in case it doesn't exist yet -- `write_route_manifest`
    itself also creates it, but resolving eagerly matches every other `resolve_*_path` function
    in this package."""
    path = os.environ.get(ROUTE_MANIFEST_PATH_ENV_VAR, str(ROUTE_MANIFEST_DEFAULT_PATH))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return path
