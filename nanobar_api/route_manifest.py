"""Static route-tree scanner: enumerates every HTTP route an app declares into a
`RouteManifestEntry` list, independent of whether any traffic has ever hit it.

This is the mechanism behind `nanobar.api-routes.json` (see `nanobar_api/cli.py`'s `routes`
subcommand and `app/main.py`'s on-launch call) -- a ground-truth inventory of an
app's surface area, used to (a) let a nanobar-refresh step create a placeholder `Nanobar` for
a route with zero captured traffic yet, so 100% of the app is reviewable, not just whatever's
been exercised, and (b) let every such nanobar carry the correct `domain` (the route's owning
Mount prefix), which `nanobar_api.bricks.binding.get_or_create_nanobar_by_route_key` doesn't
derive on its own.

Deliberately separate from `nanobar_api.middleware.trace`'s `_match_routes`/
`_resolve_route_path`: those walk a route tree to resolve *one* request's matched leaf,
scope-bound and stopping at the first match. This module unconditionally enumerates every
leaf, with no live request in play.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from starlette.routing import Mount, Route

if TYPE_CHECKING:
    from starlette.applications import Starlette
    from starlette.routing import BaseRoute


@dataclass(frozen=True)
class RouteManifestEntry:
    #: The outermost `Mount` prefix this route lives under (its leading/trailing slashes
    #: stripped), e.g. `"admin/nanobar"`; `""` for a route registered directly on the app with
    #: no enclosing `Mount` at all.
    domain: str
    method: str
    #: Full path, including the domain prefix, e.g. `"/admin/nanobar/dashboard"`.
    path: str
    #: `f"{method} {path}"` -- the same convention `MonitorTargetRef.stable_name` already uses,
    #: so a manifest entry joins directly against existing nanobar/brick data.
    route_key: str


def _walk(routes: list[BaseRoute], prefix: str, domain: str) -> list[RouteManifestEntry]:
    entries: list[RouteManifestEntry] = []
    for route in routes:
        if isinstance(route, Mount):
            child_prefix = f"{prefix}{route.path}"
            # The first Mount encountered while descending from the app's top-level routes
            # fixes `domain` for everything nested under it -- a Mount within a Mount (none
            # exist in this codebase today, but nothing prevents it) doesn't start a new domain.
            child_domain = domain if domain else route.path.strip("/")
            entries.extend(_walk(list(route.routes), child_prefix, child_domain))
        elif isinstance(route, Route):
            path_format = getattr(route, "path_format", None)
            if not isinstance(path_format, str):
                continue  # pragma: no cover - defensive: only exotic BaseRoute subclasses lack this
            full_path = f"{prefix}{path_format}" or "/"
            for method in sorted(route.methods or ()):
                if method == "HEAD":
                    continue  # implicitly added by Starlette alongside GET, not a real distinct route
                entries.append(
                    RouteManifestEntry(domain=domain, method=method, path=full_path, route_key=f"{method} {full_path}")
                )
        else:  # pragma: no cover - defensive: only exotic BaseRoute subclasses (e.g. Host) reach here
            continue
        # An opaque Mount (route.routes == [] for a non-Router sub-application like StaticFiles)
        # simply contributes nothing further.
    return entries


def build_route_manifest(app: Starlette) -> list[RouteManifestEntry]:
    """Enumerates every `(method, path)` this app's router declares, recursively through
    `Mount`s. An opaque `Mount` (its sub-app isn't a `Router`, e.g. `StaticFiles`) contributes
    nothing -- `Mount.routes` is `[]` in that case, so it's skipped with no special-casing."""
    return _walk(list(app.router.routes), "", "")


def write_route_manifest(app: Starlette, path: str | Path) -> list[RouteManifestEntry]:
    """Builds the manifest and writes it to `path` as `{"generated_at": <iso8601 UTC>,
    "routes": [...]}`, creating parent directories if needed. Returns the entries."""
    entries = build_route_manifest(app)
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "generated_at": datetime.now(UTC).isoformat(),
        "routes": [asdict(entry) for entry in entries],
    }
    resolved.write_text(json.dumps(document, indent=2) + "\n")
    return entries


def load_route_manifest(path: str | Path) -> list[RouteManifestEntry]:
    """Reads back a manifest written by `write_route_manifest`."""
    document = json.loads(Path(path).read_text())
    return [RouteManifestEntry(**entry) for entry in document["routes"]]
