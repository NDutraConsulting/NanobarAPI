"""Shared "identify captured event-spans on events.db's snapshot channel, generate their
regression-bricks, bind to nanobars" logic.

Used by both `examples/generate_dashboard_bricks.py` (terminal/CI entry point) and the dashboard's
own "Generate bricks" button (`admin/nanobar/api.py`'s `generate_bricks_action`) -- exactly one
implementation, not a duplicate, of what used to live only in the script.

`generate_bricks()` is deliberately an *explicit batch step*, not a continuous production worker
(see its own docstring: continuous re-inference "would just bless whatever the app currently
does, bugs included") -- nothing in the live app itself ever turns captured `"snapshot"`-channel
events into bricks or binds them to nanobars on its own. This module is that step, callable
either from a script or from a request handler.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from nanobar_api.bricks import bind_new_bricks_to_nanobars, generate_bricks
from nanobar_api.bricks.schema import RegressionBrick
from nanobar_api.bricks.store import count_nanobars
from nanobar_api.eventbus.store import get_unprocessed
from nanobar_api.route_manifest import RouteManifestEntry
from nanobar_api.taxonomy import load_taxonomy

SYSTEM_NAME = "nanobar-dashboard"
SYSTEM_VERSION = "0.1.0"
CREATED_BY = "generate_dashboard_bricks"

#: generate_bricks()'s own default (100) processes one batch per call -- a live dashboard can
#: accumulate thousands of unprocessed snapshot event-spans (mostly route_key-less ORM captures
#: from plain read handlers) between runs, so processing a single batch would silently leave most
#: of the backlog unidentified. _generate_bricks_for_all_captured_spans loops batches instead.
_BATCH_SIZE = 500


def _generate_bricks_for_all_captured_spans(
    events_conn: sqlite3.Connection, bricks_conn: sqlite3.Connection
) -> list[RegressionBrick]:
    all_new_bricks: list[RegressionBrick] = []
    while True:
        pending = get_unprocessed(events_conn, "snapshot", _BATCH_SIZE)
        if not pending:
            break
        all_new_bricks.extend(
            generate_bricks(events_conn, bricks_conn, channel="snapshot", created_by=CREATED_BY, limit=_BATCH_SIZE)
        )
        if len(pending) < _BATCH_SIZE:
            break
    return all_new_bricks


@dataclass(frozen=True)
class GenerateBricksResult:
    new_bricks: int
    nanobars_created: int
    bindings_created: int
    skipped: int
    total_nanobars: int


def generate_dashboard_bricks(
    events_conn: sqlite3.Connection,
    bricks_conn: sqlite3.Connection,
    *,
    route_manifest_entries: list[RouteManifestEntry] | None = None,
) -> GenerateBricksResult:
    """Identifies captured event-spans on `events_conn`'s `"snapshot"` channel, generates their
    `bricks_conn` regression-bricks, and binds new bricks to nanobars. The caller owns both
    connections (opens and closes them) -- this never opens one itself, so it works identically
    called from a one-off script's short-lived connections or a request handler's already-open
    per-request ones.

    Safe to call repeatedly: `generate_bricks()` dedupes already-processed events and
    already-seen content-hashes; `bind_new_bricks_to_nanobars()` reuses an existing Nanobar for
    a `(nanobar_type, route_key)` pair it's already seen rather than creating a duplicate.

    `route_manifest_entries` (typically `nanobar_api.route_manifest.load_route_manifest()`'s
    return value), when given, lets a newly-created nanobar carry the correct `domain` from the
    start, instead of relying entirely on a later "Nanobar refresh" pass
    (`admin/nanobar/nanobar_refresh.py`) to backfill it.
    """
    new_bricks = _generate_bricks_for_all_captured_spans(events_conn, bricks_conn)
    route_key_domains = (
        {entry.route_key: entry.domain for entry in route_manifest_entries} if route_manifest_entries else None
    )
    binding_result = bind_new_bricks_to_nanobars(
        bricks_conn,
        new_bricks,
        system_name=SYSTEM_NAME,
        system_version=SYSTEM_VERSION,
        matched_by=CREATED_BY,
        taxonomy=load_taxonomy(),
        route_key_domains=route_key_domains,
    )
    return GenerateBricksResult(
        new_bricks=len(new_bricks),
        nanobars_created=binding_result.nanobars_created,
        bindings_created=binding_result.bindings_created,
        skipped=binding_result.skipped,
        total_nanobars=count_nanobars(bricks_conn),
    )
