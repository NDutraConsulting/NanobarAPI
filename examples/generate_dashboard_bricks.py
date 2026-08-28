"""Turns the live dashboard app's own already-captured traffic into RegressionBricks/Nanobars.

`NanobarAPIValidatorGate`/`NanobarAPIController.handle()` already call `capture_layer()` for every
`/admin/app/*` mutating request (create/edit a post, book an appointment, mark a notification
read) -- see `app/controllers/blog_controller.py` -- writing onto the `"snapshot"` channel of
the same `EventQueueRepository` `app/main.py`'s `TelemetryDrainWorker` drains into
`app/db/nanobar_api_telemetry.db`. `app/admin/nanobar/generate_bricks.py`'s
`generate_dashboard_bricks()` (the actual implementation, shared with the dashboard's own
"Generate bricks" button) turns those captured spans into bricks and binds them to nanobars,
on demand.

Unlike `seed_kahnban_bricks.py` (which drives synthetic traffic through a *different* demo app
and generates the events from scratch), this script generates no traffic of its own -- it only
processes captured spans the live dashboard app already wrote. Reads/writes the exact same
`app/db/nanobar_api_telemetry.db` / `app/admin/nanobar/data/regression_bricks.db` files
`app/main.py` uses by default (respects `NANOBAR_TELEMETRY_DB`/`NANOBAR_REGRESSION_BRICKS_DB`
overrides the same way).

Run from this repo's root:

    uv run python examples/generate_dashboard_bricks.py

Or, equivalently, click "Generate bricks" on the nanobars list page in a running dashboard.
"""

from __future__ import annotations

from app.admin.nanobar.db import resolve_db_path as resolve_bricks_db_path
from app.admin.nanobar.generate_bricks import generate_dashboard_bricks
from app.admin.nanobar.telemetry_db import resolve_db_path as resolve_telemetry_db_path
from app.core.config import resolve_route_manifest_path
from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.persistence import build_session_factory as build_bricks_session_factory
from nanobar_api.route_manifest import load_route_manifest
from nanobar_api.telemetry.persistence import build_session_factory as build_telemetry_session_factory


def main() -> None:
    telemetry_db_path = resolve_telemetry_db_path()
    bricks_db_path = resolve_bricks_db_path()

    try:
        manifest_entries = load_route_manifest(resolve_route_manifest_path())
    except FileNotFoundError:
        # The dashboard app writes this on every launch ("built on launch") -- it just hasn't
        # been started yet. New nanobars still get created, just without a domain until a
        # later "Nanobar refresh" backfills it.
        manifest_entries = None

    # A throwaway repository -- this script's own ORM-capture events land nowhere real (the live
    # dashboard's own EventQueueRepository is only reachable from a running process), same as
    # every other one-off script in this codebase that builds its own session factory.
    capture_repository = EventQueueRepository([ChannelConfig(name="snapshot")])
    bricks_session_factory = build_bricks_session_factory(bricks_db_path, repository=capture_repository)
    telemetry_session_factory = build_telemetry_session_factory(telemetry_db_path)

    telemetry_session = telemetry_session_factory()
    bricks_session = bricks_session_factory()
    try:
        result = generate_dashboard_bricks(telemetry_session, bricks_session, route_manifest_entries=manifest_entries)
    finally:
        telemetry_session.close()
        bricks_session.close()

    print(f"nanobar_api_telemetry.db: {telemetry_db_path}")
    print(f"regression_bricks.db: {bricks_db_path}")
    print(f"New bricks generated : {result.new_bricks}")
    print(f"Nanobars created     : {result.nanobars_created}")
    print(f"Bindings created     : {result.bindings_created}")
    if result.skipped:
        print(f"Skipped (no route_key/nanobar_type -- not from capture_layer()): {result.skipped}")
    print(f"Total nanobars now   : {result.total_nanobars}")


if __name__ == "__main__":
    main()
