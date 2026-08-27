"""Reconciles `Nanobar` rows against the current route manifest (`nanobar.api-routes.json`,
see `nanobar_api/route_manifest.py`).

Two things this does that real captured traffic alone never does: (1) creates a placeholder
nanobar for a declared route that's never been exercised, so the dashboard's list/coverage
views reflect 100% of the app's surface, not just whatever's been hit so far -- the placeholder
uses `nanobar_type="unclassified"`, which the taxonomy already resolves to a "needs
classification" state (see `admin/nanobar/api.py`'s `nanobar_coverage_gaps`) with no further
plumbing; and (2) backfills/corrects `domain` on an existing route-keyed nanobar, since
real-traffic nanobars only started carrying a correct `domain` once `generate_dashboard_bricks`
was given a `route_key_domains` lookup -- anything created before that still needs a pass.

Used by both a CLI wrapper and the dashboard's "Nanobar refresh" button, same
one-shared-implementation shape as `generate_bricks.py`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from nanobar_api.bricks.binding import get_or_create_nanobar_by_route_key
from nanobar_api.bricks.store import list_known_route_keys, list_nanobars, set_nanobar_domain
from nanobar_api.route_manifest import RouteManifestEntry

SYSTEM_NAME = "nanobar-dashboard"
SYSTEM_VERSION = "0.1.0"
CREATED_BY = "api-routes-manifest"

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


def refresh_nanobars_from_manifest(
    bricks_conn: sqlite3.Connection, manifest_entries: list[RouteManifestEntry]
) -> NanobarRefreshResult:
    """The caller owns `bricks_conn` (opens and closes it), same convention as
    `generate_dashboard_bricks`. Safe to call repeatedly: a route already covered by an
    existing nanobar is never re-created, and a domain that already matches is never rewritten.
    """
    known_route_keys = list_known_route_keys(bricks_conn)

    nanobars_created = 0
    for entry in manifest_entries:
        if entry.route_key in known_route_keys:
            continue
        get_or_create_nanobar_by_route_key(
            bricks_conn,
            nanobar_type=UNCLASSIFIED_NANOBAR_TYPE,
            route_key=entry.route_key,
            system_name=SYSTEM_NAME,
            system_version=SYSTEM_VERSION,
            created_by=CREATED_BY,
            domain=entry.domain,
        )
        nanobars_created += 1
        known_route_keys.add(entry.route_key)

    manifest_domains_by_route_key = {entry.route_key: entry.domain for entry in manifest_entries}
    domains_updated = 0
    for nanobar in list_nanobars(bricks_conn, page=1, page_size=_MAX_NANOBARS_SCANNED):
        for ref in nanobar.monitor_target_refs:
            expected_domain = manifest_domains_by_route_key.get(ref.stable_name)
            if expected_domain is not None and expected_domain != nanobar.domain:
                set_nanobar_domain(bricks_conn, nanobar.nanobar_id, expected_domain)
                domains_updated += 1
            break  # one route-key ref is enough to resolve this nanobar's domain

    return NanobarRefreshResult(
        routes_scanned=len(manifest_entries), nanobars_created=nanobars_created, domains_updated=domains_updated
    )
