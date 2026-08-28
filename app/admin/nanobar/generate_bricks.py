"""Shared "identify captured spans on the telemetry db's snapshot channel, generate their
regression-bricks, bind to nanobars" logic.

Used by both `examples/generate_dashboard_bricks.py` (terminal/CI entry point) and the dashboard's
own "Generate bricks" button (`admin/nanobar/api.py`'s `generate_bricks_action`) -- exactly one
implementation, not a duplicate, of what used to live only in the script.

**This is the "controller-level orchestration" the regression-brick refactor plan's Decision 4
calls for** ("services never call other services or controllers" -- cross-entity coordination
belongs in the controller) -- composes `nanobar_api.telemetry.telemetry_scanner_service.
TelemetryScannerService` (real `RegressionBrick`-side work, reading via `TraceRepository`/
`SpanRepository` against `nanobar_api_telemetry.db` -- see
`.focusari/telemetry-domain-refactor-plan-with-tasks.md` Decision 5/6, superseding this module's
own earlier `TraceScannerService`/`events_conn` wiring) with `bricks/binding.py`'s
`bind_new_bricks_to_nanobars` (plain orchestration over `NanobarRepository`, not a second
service) to bind each new brick to a `Nanobar`. Not itself a `NanobarAPIController` -- this route
takes no request body to validate (a bare `POST` triggering a scan sweep), so the full
validator_gate/controller ceremony isn't warranted; this plain function already *is* "where the
app composes both entities together," per the plan's own "Cross-cutting, stays framework-level"
section.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.admin.nanobar.nanobar_refresh import effective_app_box
from nanobar_api.bricks import bind_new_bricks_to_nanobars
from nanobar_api.nanobar.repository import NanobarRepository
from nanobar_api.regression_brick.repository import RegressionBrickRepository
from nanobar_api.route_manifest import RouteManifestEntry
from nanobar_api.taxonomy import load_taxonomy
from nanobar_api.telemetry.span_repository import SpanRepository
from nanobar_api.telemetry.telemetry_scanner_service import ScanTracesRequest, TelemetryScannerService
from nanobar_api.telemetry.trace_repository import TraceRepository

SYSTEM_NAME = "nanobar-dashboard"
SYSTEM_VERSION = "0.1.0"
CREATED_BY = "generate_dashboard_bricks"

#: `TelemetryScannerService`'s own single-call default (100) processes one batch per call -- a
#: live dashboard can accumulate thousands of unprocessed spans between runs, so a smaller batch
#: would mean more round trips draining the backlog. Matches this module's own previous
#: `_BATCH_SIZE` (the service now owns the "loop until drained" behavior).
_BATCH_SIZE = 500


@dataclass(frozen=True)
class GenerateBricksResult:
    new_bricks: int
    nanobars_created: int
    bindings_created: int
    skipped: int
    total_nanobars: int


def generate_dashboard_bricks(
    telemetry_session: Session,
    bricks_session: Session,
    *,
    route_manifest_entries: list[RouteManifestEntry] | None = None,
) -> GenerateBricksResult:
    """Identifies captured spans on `telemetry_session`'s (`nanobar_api_telemetry.db`)
    `"snapshot"` channel, generates their `bricks_session` regression-bricks, and binds new
    bricks to nanobars. The caller owns both sessions (opens and closes them) -- this never opens
    either itself, so it works identically called from a one-off script's short-lived sessions or
    a request handler's already-open per-request ones.

    Safe to call repeatedly: `TelemetryScannerService` dedupes already-processed spans and
    already-seen content-hashes; `bind_new_bricks_to_nanobars()` reuses an existing Nanobar for
    a `(nanobar_type, route_key)` pair it's already seen rather than creating a duplicate.

    `route_manifest_entries` (typically `nanobar_api.route_manifest.load_route_manifest()`'s
    return value), when given, lets a newly-created nanobar carry the correct `domain`/`app_box`
    from the start, instead of relying entirely on a later "Nanobar refresh" pass
    (`admin/nanobar/nanobar_refresh.py`) to backfill it. `app_box` values use this app's own
    `effective_app_box()` (`nanobar_refresh.py`'s own URL-prefix refinement), same as that pass.
    """
    brick_repository = RegressionBrickRepository(bricks_session)
    nanobar_repository = NanobarRepository(bricks_session)

    scanner = TelemetryScannerService(
        TraceRepository(telemetry_session), SpanRepository(telemetry_session), brick_repository
    )
    scan_result = scanner(ScanTracesRequest(channel="snapshot", created_by=CREATED_BY, limit=_BATCH_SIZE))
    new_bricks = brick_repository.get_many(scan_result.result.data)

    route_key_domains = (
        {entry.route_key: entry.domain for entry in route_manifest_entries} if route_manifest_entries else None
    )
    route_key_app_boxes = (
        {entry.route_key: effective_app_box(entry) for entry in route_manifest_entries}
        if route_manifest_entries
        else None
    )
    binding_result = bind_new_bricks_to_nanobars(
        nanobar_repository,
        new_bricks,
        system_name=SYSTEM_NAME,
        system_version=SYSTEM_VERSION,
        matched_by=CREATED_BY,
        taxonomy=load_taxonomy(),
        route_key_domains=route_key_domains,
        route_key_app_boxes=route_key_app_boxes,
    )
    return GenerateBricksResult(
        new_bricks=len(new_bricks),
        nanobars_created=binding_result.nanobars_created,
        bindings_created=binding_result.bindings_created,
        skipped=binding_result.skipped,
        total_nanobars=nanobar_repository.count_nanobars(),
    )
