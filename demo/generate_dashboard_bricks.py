"""Turns the live dashboard app's own already-captured traffic into RegressionBricks/Nanobars.

`NanobarValidatorGate`/`NanobarController.handle()` already call `capture_layer()` for every
`/admin/app/*` mutating request (create/edit a post, book an appointment, mark a notification
read) -- see `demo/dashboard/blog_controllers.py` -- writing onto the `"snapshot"` channel of
the same `EventQueueRepository` `demo/dashboard/app.py`'s `eventbus_lifespan` drains into
`demo/data/events.db`. But `generate_bricks()` is deliberately an *explicit batch step*, not a
continuous production worker (see its own docstring: continuous re-inference "would just bless
whatever the app currently does, bugs included") -- nothing in the live app itself ever turns
those captured events into bricks or binds them to nanobars. That's what this script does,
on demand, after you've clicked around `/admin/app/*` (or any other capture_layer()-instrumented
route) in a real browser.

Unlike `seed_kahnban_bricks.py` (which drives synthetic traffic through a *different* demo app
and generates the events from scratch), this script generates no traffic of its own -- it only
processes capture events the live dashboard app already wrote. Reads/writes the exact same
`demo/data/events.db` / `demo/data/regression_bricks.db` files `demo/dashboard/app.py` uses by
default (respects `NANOBAR_EVENTS_DB`/`NANOBAR_REGRESSION_BRICKS_DB` overrides the same way).

Binding uses `nanobar_api.bricks.bind_new_bricks_to_nanobars()` -- keyed by each brick's stamped
`(nanobar_type, route_key)` pair, which every `capture_layer()` call site here already provides
(unlike `seed_kahnban_bricks.py`'s bricks, which come from `SnapshotMiddleware` and carry no
`route_key`, requiring that script's own manual `(method, path-template)` naming instead).

Safe to re-run: `generate_bricks()` dedupes already-processed events and already-seen
content-hashes; `bind_new_bricks_to_nanobars()` reuses an existing Nanobar for a
`(nanobar_type, route_key)` pair it's already seen rather than creating a duplicate.

Run from this repo's root:

    uv run python demo/generate_dashboard_bricks.py
"""

from __future__ import annotations

import sqlite3

from demo.dashboard.db import get_connection as get_bricks_connection
from demo.dashboard.db import resolve_db_path as resolve_bricks_db_path
from demo.dashboard.events_db import get_connection as get_events_connection
from demo.dashboard.events_db import resolve_db_path as resolve_events_db_path
from nanobar_api.bricks import bind_new_bricks_to_nanobars, generate_bricks
from nanobar_api.bricks.schema import RegressionBrick
from nanobar_api.bricks.store import list_nanobars
from nanobar_api.eventbus.store import get_unprocessed
from nanobar_api.taxonomy import load_taxonomy

SYSTEM_NAME = "nanobar-dashboard"
SYSTEM_VERSION = "0.1.0"
CREATED_BY = "generate_dashboard_bricks"

#: generate_bricks()'s own default (100) processes one batch per call -- a live dashboard can
#: accumulate thousands of unprocessed snapshot events (mostly route_key-less ORM captures from
#: plain read handlers) between runs of this script, so draining in a single call would silently
#: leave most of the backlog unprocessed. _drain_all_snapshot_events loops batches instead.
_BATCH_SIZE = 500


def _drain_all_snapshot_events(events_conn: sqlite3.Connection, bricks_conn: sqlite3.Connection) -> list[RegressionBrick]:
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


def main() -> None:
    events_db_path = resolve_events_db_path()
    bricks_db_path = resolve_bricks_db_path()

    events_conn = get_events_connection(events_db_path)
    bricks_conn = get_bricks_connection(bricks_db_path)
    try:
        new_bricks = _drain_all_snapshot_events(events_conn, bricks_conn)
        result = bind_new_bricks_to_nanobars(
            bricks_conn,
            new_bricks,
            system_name=SYSTEM_NAME,
            system_version=SYSTEM_VERSION,
            matched_by=CREATED_BY,
            taxonomy=load_taxonomy(),
        )
        total_nanobars = len(list_nanobars(bricks_conn))
    finally:
        events_conn.close()
        bricks_conn.close()

    print(f"events.db           : {events_db_path}")
    print(f"regression_bricks.db: {bricks_db_path}")
    print(f"New bricks generated : {len(new_bricks)}")
    print(f"Nanobars created     : {result.nanobars_created}")
    print(f"Bindings created     : {result.bindings_created}")
    if result.skipped:
        print(f"Skipped (no route_key/nanobar_type -- not from capture_layer()): {result.skipped}")
    print(f"Total nanobars now   : {total_nanobars}")


if __name__ == "__main__":
    main()
