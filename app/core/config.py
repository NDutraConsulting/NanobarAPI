"""Cross-cutting path configuration -- `WEB_DIR` (the static page-bundle root every
page-serving route file needs) and the route-manifest JSON file's own path resolver.

Per-domain SQLite database paths are **not** resolved here -- each domain's data now lives
alongside the code that owns it (`app/db/blog.db`, `app/admin/app/data/app_admin.db`,
`app/admin/nanobar/data/nanobar_admin.db`, ...), so each resolver computes its own
`Path(__file__).resolve().parent...`-relative default directly, rather than sharing one flat
`DATA_DIR`. `WEB_DIR` stays centralized here since `app/pages/` genuinely is one shared
directory every domain's page-serving code reads from.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Repo root -- three `.parent`s up from `app/core/config.py` (config.py -> core/ -> app/ -> repo root).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: Path to `app/pages/`, the static per-page bundle root every page-serving route file needs.
WEB_DIR = _REPO_ROOT / "app" / "pages"

#: Default location for the route manifest JSON -- lives directly under `app/`, gitignored.
ROUTE_MANIFEST_DEFAULT_PATH = _REPO_ROOT / "app" / "nanobar.api-routes.json"

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
