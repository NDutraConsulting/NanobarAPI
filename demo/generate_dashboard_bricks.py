"""Turns the live dashboard app's own already-captured traffic into RegressionBricks/Nanobars.

`NanobarValidatorGate`/`NanobarController.handle()` already call `capture_layer()` for every
`/admin/app/*` mutating request (create/edit a post, book an appointment, mark a notification
read) -- see `demo/dashboard/blog_controllers.py` -- writing onto the `"snapshot"` channel of
the same `EventQueueRepository` `demo/dashboard/app.py`'s `eventbus_lifespan` drains into
`demo/data/events.db`. `demo/dashboard/generate_bricks.py`'s `generate_dashboard_bricks()`
(the actual implementation, shared with the dashboard's own "Generate bricks" button) turns
those captured events into bricks and binds them to nanobars, on demand.

Unlike `seed_kahnban_bricks.py` (which drives synthetic traffic through a *different* demo app
and generates the events from scratch), this script generates no traffic of its own -- it only
processes capture events the live dashboard app already wrote. Reads/writes the exact same
`demo/data/events.db` / `demo/data/regression_bricks.db` files `demo/dashboard/app.py` uses by
default (respects `NANOBAR_EVENTS_DB`/`NANOBAR_REGRESSION_BRICKS_DB` overrides the same way).

Run from this repo's root:

    uv run python demo/generate_dashboard_bricks.py

Or, equivalently, click "Generate bricks" on the nanobars list page in a running dashboard.
"""

from __future__ import annotations

from demo.dashboard.db import get_connection as get_bricks_connection, resolve_db_path as resolve_bricks_db_path
from demo.dashboard.events_db import get_connection as get_events_connection, resolve_db_path as resolve_events_db_path
from demo.dashboard.generate_bricks import generate_dashboard_bricks
from demo.dashboard.route_manifest_path import resolve_path as resolve_route_manifest_path
from nanobar_api.route_manifest import load_route_manifest


def main() -> None:
    events_db_path = resolve_events_db_path()
    bricks_db_path = resolve_bricks_db_path()

    try:
        manifest_entries = load_route_manifest(resolve_route_manifest_path())
    except FileNotFoundError:
        # The dashboard app writes this on every launch ("built on launch") -- it just hasn't
        # been started yet. New nanobars still get created, just without a domain until a
        # later "Nanobar refresh" backfills it.
        manifest_entries = None

    events_conn = get_events_connection(events_db_path)
    bricks_conn = get_bricks_connection(bricks_db_path)
    try:
        result = generate_dashboard_bricks(events_conn, bricks_conn, route_manifest_entries=manifest_entries)
    finally:
        events_conn.close()
        bricks_conn.close()

    print(f"events.db           : {events_db_path}")
    print(f"regression_bricks.db: {bricks_db_path}")
    print(f"New bricks generated : {result.new_bricks}")
    print(f"Nanobars created     : {result.nanobars_created}")
    print(f"Bindings created     : {result.bindings_created}")
    if result.skipped:
        print(f"Skipped (no route_key/nanobar_type -- not from capture_layer()): {result.skipped}")
    print(f"Total nanobars now   : {result.total_nanobars}")


if __name__ == "__main__":
    main()
