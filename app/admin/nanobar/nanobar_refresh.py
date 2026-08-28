"""Reconciles `Nanobar` rows against the current route manifest (`nanobar.api-routes.json`,
see `nanobar_api/route_manifest.py`).

Two things this does that real captured traffic alone never does: (1) creates a placeholder
nanobar for a declared route that's never been exercised, so the dashboard's list/coverage
views reflect 100% of the app's surface, not just whatever's been hit so far -- the placeholder
uses `nanobar_type="unclassified"`, which the taxonomy already resolves to a "needs
classification" state (see `admin/nanobar/api.py`'s `nanobar_coverage_gaps`) with no further
plumbing; and (2) backfills/corrects `domain`/`app_box` on an existing route-keyed nanobar, since
real-traffic nanobars only started carrying correct values once `generate_dashboard_bricks` was
given `route_key_domains`/`route_key_app_boxes` lookups -- anything created before that still
needs a pass.

**`effective_app_box()` is this app's own AppBox classification** (per `.focusari/appbox-plan-
with-tasks.md`) -- `RouteManifestEntry.app_box`'s framework-level default (`domain or "api"`) is
deliberately generic (no URL-prefix guessing, since a URL scheme is app-specific), so this app
layers its own refinement on top: `/admin/app/*`/`/admin/nanobar/*` routes registered *without*
an enclosing `Mount` (the two login routes, today) still classify as `"admin/app"`/
`"admin/nanobar"` rather than falling into the generic `"api"` bucket, by recognizing the URL
prefix directly -- exactly the app-specific knowledge `nanobar_api/route_manifest.py` itself
can't have. `domain` for those same two routes is untouched (still `""`, matching
`RouteManifestEntry.domain`'s own unchanged Mount-derived value) -- this refinement is `app_box`
-only, on purpose: rewrapping the login routes in their own `Mount` to fix `domain` too would
create a second `Mount("/admin/app", ...)`/`Mount("/admin/nanobar", ...)` alongside the existing
protected one at the same prefix, and Starlette's own `Mount.matches()` matches by prefix alone
(confirmed live, not assumed) -- it never delegates to the inner router to check whether a
sub-route actually exists, so the *first* registered Mount at a given prefix silently swallows
every request under it, successful match or not. Two separate Mounts sharing one prefix would
make every protected `/admin/app/*`/`/admin/nanobar/*` route 404 the moment the *login* Mount
(registered first) claims the request and finds no matching route inside itself. Not a safe
restructuring to do as a side effect of this feature -- `app_box` gets its correct value one
layer up, in application code that already carries this URL-scheme knowledge, instead.

Used by both a CLI wrapper and the dashboard's "Nanobar refresh" button, same
one-shared-implementation shape as `generate_bricks.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

from nanobar_api.nanobar.repository import NanobarRepository
from nanobar_api.route_manifest import RouteManifestEntry

SYSTEM_NAME = "nanobar-dashboard"
SYSTEM_VERSION = "0.1.0"
CREATED_BY = "api-routes-manifest"

#: `(url_prefix, app_box)` pairs, checked in order -- see this module's own docstring for why
#: this app-specific URL-prefix recognition lives here, not in the generic framework.
_APP_BOX_URL_PREFIXES: tuple[tuple[str, str], ...] = (
    ("/admin/app/", "admin/app"),
    ("/admin/nanobar/", "admin/nanobar"),
)


def effective_app_box(entry: RouteManifestEntry) -> str:
    for prefix, app_box in _APP_BOX_URL_PREFIXES:
        if entry.path.startswith(prefix):
            return app_box
    return entry.app_box


#: A deliberately unresolvable `nanobar_type` -- `nanobar_api.taxonomy.resolve_taxonomy_entry`
#: returns `None` for it (no exact match, no recognized dynamic prefix), which
#: `admin/nanobar/api.py`'s `nanobar_coverage_gaps` already renders as a "needs
#: classification" state. A route with zero real traffic yet genuinely doesn't know what layer
#: type it is; this is that state, not a guess.
UNCLASSIFIED_NANOBAR_TYPE = "unclassified"

#: `list_nanobars`/`count_nanobars` page by default (50); a real app's route count comfortably
#: fits in one page-sized-up call, so this reconciliation doesn't need to paginate through them.
_MAX_NANOBARS_SCANNED = 10_000


@dataclass(frozen=True)
class NanobarRefreshResult:
    routes_scanned: int
    nanobars_created: int
    domains_updated: int
    app_boxes_updated: int


def refresh_nanobars_from_manifest(
    nanobar_repository: NanobarRepository, manifest_entries: list[RouteManifestEntry]
) -> NanobarRefreshResult:
    """The caller owns `nanobar_repository`'s session (opens and closes it), same convention as
    `generate_dashboard_bricks`. Safe to call repeatedly: a route already covered by an
    existing nanobar is never re-created, and a domain/app_box that already matches is never
    rewritten.
    """
    known_route_keys = nanobar_repository.list_known_route_keys()

    nanobars_created = 0
    for entry in manifest_entries:
        if entry.route_key in known_route_keys:
            continue
        nanobar_repository.get_or_create_by_route_key(
            nanobar_type=UNCLASSIFIED_NANOBAR_TYPE,
            route_key=entry.route_key,
            system_name=SYSTEM_NAME,
            system_version=SYSTEM_VERSION,
            created_by=CREATED_BY,
            domain=entry.domain,
            app_box=effective_app_box(entry),
        )
        nanobars_created += 1
        known_route_keys.add(entry.route_key)

    manifest_domains_by_route_key = {entry.route_key: entry.domain for entry in manifest_entries}
    manifest_app_boxes_by_route_key = {entry.route_key: effective_app_box(entry) for entry in manifest_entries}
    domains_updated = 0
    app_boxes_updated = 0
    for nanobar in nanobar_repository.list_nanobars(page=1, page_size=_MAX_NANOBARS_SCANNED):
        for ref in nanobar.monitor_target_refs:
            expected_domain = manifest_domains_by_route_key.get(ref.stable_name)
            if expected_domain is not None and expected_domain != nanobar.domain:
                nanobar_repository.set_domain(nanobar.nanobar_id, expected_domain)
                domains_updated += 1
            expected_app_box = manifest_app_boxes_by_route_key.get(ref.stable_name)
            if expected_app_box is not None and expected_app_box != nanobar.app_box:
                nanobar_repository.set_app_box(nanobar.nanobar_id, expected_app_box)
                app_boxes_updated += 1
            break  # one route-key ref is enough to resolve this nanobar's domain/app_box

    return NanobarRefreshResult(
        routes_scanned=len(manifest_entries),
        nanobars_created=nanobars_created,
        domains_updated=domains_updated,
        app_boxes_updated=app_boxes_updated,
    )
