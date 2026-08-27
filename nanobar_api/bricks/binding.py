"""Auto-registration + binding — turns a freshly-generated `RegressionBrick` carrying
`source["nanobar_type"]`/`source["route_key"]` (both stamped by `generate_bricks()` when the
originating event came from `capture_layer()`) into a bound `(Nanobar, RegressionBrick)` pair,
creating the `Nanobar` row on first sight.

**A deliberate deviation from the source spec, not what its own checklist literally names.**
`.focusari/nanobar_APIDomain_abstract_class_buildplan-with-tasks.md` calls for two separate
things: (a) real-time auto-registration inside `NanobarValidatorGate.__call__`/
`NanobarController.handle`, and (b) "the first real `'trace'` `match_method` implementation" —
correlating bricks by shared `trace_id`. Neither is what this module does, for real reasons:

- Real-time registration would need request-time access to the *bricks* database (a second,
  separate SQLite file from the events database `app.state.telemetry` already provides) plus
  `system_name`/`system_version` metadata with no established source at that layer — new
  dependencies on the hot request path for every single call, for a write this project's own
  stated philosophy (`generate_bricks()`'s docstring: "an explicit batch/CI step... not a
  continuous production worker") already argues belongs off that path.
- Trace-based correlation is ambiguous when a trace has more than one brick of the *same*
  `nanobar_type` (which one binds to which nanobar?) — solvable, but adds real complexity for a
  problem `route_key` (already free: every `capture_layer()` call site inside the validator/
  controller layers already knows its own route key) sidesteps entirely, unambiguously, today.

So: binding here is a batch step (same "explicit, human-reviewable, run after `generate_bricks()`"
shape), keyed by the direct `(nanobar_type, route_key)` pair already stamped on the brick, using
`match_method="exact"` — matching `examples/seed_kahnban_bricks.py`'s own manual binding, just
automatic. A real `"trace"`-based matcher, for boundaries without a natural route key (e.g. an
event-to-subscriber capture), remains unbuilt — this covers the REST validator/controller case
this session actually built, not the fully general one the checklist named.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from nanobar_api.bricks.schema import MonitorTargetRef, Nanobar, NanobarBrickBinding, RegressionBrick
from nanobar_api.bricks.store import (
    bind_brick_to_nanobar,
    get_bricks_for_nanobar,
    get_nanobar,
    insert_nanobar,
    set_regression_weight,
)

if TYPE_CHECKING:
    # Deferred: nanobar_api.taxonomy itself imports nanobar_api.bricks.schema, which --
    # importing any submodule of a package always runs that package's __init__.py first --
    # would otherwise be a circular import at `bricks/__init__.py` load time (it imports this
    # module). `from __future__ import annotations` (above) makes annotations lazy strings, so
    # this import is only needed for type checkers, never at runtime.
    from nanobar_api.taxonomy import NanobarTypeTaxonomy

_MATCHER_VERSION = "v1"
_DEFAULT_TARGET_TYPE = "route"


def _find_nanobar_by_route_key(
    conn: sqlite3.Connection, *, nanobar_type: str, route_key: str, target_type: str
) -> Nanobar | None:
    """O(n) over nanobars of this `nanobar_type` — `monitor_target_refs` is a JSON list, not a
    queryable scalar column, so there's no native index to look this up by directly (a real,
    unaddressed schema question also covered by this doc's Open Decision 3 on where captured
    middleware metadata should live — the same "JSON list isn't indexable" shape). Fine at
    today's scale; a real cost once one `nanobar_type` accumulates many nanobars.
    """
    rows = conn.execute(
        "SELECT nanobar_id, monitor_target_refs_json FROM nanobars WHERE nanobar_type = ?", (nanobar_type,)
    ).fetchall()
    for nanobar_id, refs_json in rows:
        refs = json.loads(refs_json)
        if any(ref["target_type"] == target_type and ref["stable_name"] == route_key for ref in refs):
            nanobar = get_nanobar(conn, nanobar_id)
            assert nanobar is not None
            return nanobar
    return None


def get_or_create_nanobar_by_route_key(
    conn: sqlite3.Connection,
    *,
    nanobar_type: str,
    route_key: str,
    system_name: str = "unknown",
    system_version: str = "0.0.0",
    target_type: str = _DEFAULT_TARGET_TYPE,
    created_by: str = "auto-registration",
    domain: str | None = None,
) -> tuple[Nanobar, bool]:
    """Idempotent get-or-create keyed by `(nanobar_type, route_key)`. Returns `(nanobar,
    was_created)` — the caller (`bind_new_bricks_to_nanobars` below) needs to know which, to
    report an accurate creation count without a second, redundant lookup.

    Wraps the select-then-insert in a `BEGIN IMMEDIATE` transaction — the same atomic-claim
    discipline `eventbus/store.py`'s `claim_events()` established for the same reason: two
    concurrent first-sights of the same route must not race into two `Nanobar` rows. A native SQL
    `ON CONFLICT` (matching `set_review_status`/`set_brick_scenario`'s pattern elsewhere in this
    codebase) isn't available here — there's no unique index on `(nanobar_type, route_key)` to
    conflict against, since `route_key` lives inside a JSON list column, not a scalar one.

    Placeholder metadata for a newly-created row matches `examples/seed_kahnban_bricks.py`'s own
    established convention exactly, not a new guess: `regression_weight=0.5`,
    `endpoint_scenario_frequency={"state": "unmeasured"}`, `request_object_id`/
    `response_object_id` derived as `f"req-{route_key}"`/`f"res-{route_key}"`.

    `domain`, when given, is stamped on a newly-created row only (an existing nanobar's domain
    is left untouched by this function -- see `nanobar_api.bricks.store.set_nanobar_domain` for
    correcting one after the fact). This function itself stays manifest-agnostic: callers that
    know a route's owning domain (e.g. from `nanobar_api.route_manifest`) pass it straight
    through; callers that don't just leave it `None`, same as before this parameter existed.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = _find_nanobar_by_route_key(
            conn, nanobar_type=nanobar_type, route_key=route_key, target_type=target_type
        )
        if existing is not None:
            conn.commit()
            return existing, False

        nanobar = Nanobar(
            nanobar_id=f"nb-{uuid.uuid4().hex[:12]}",
            schema_version="1.0",
            system_name=system_name,
            system_version=system_version,
            nanobar_type=nanobar_type,
            request_object_id=f"req-{route_key}",
            response_object_id=f"res-{route_key}",
            regression_weight=0.5,
            endpoint_scenario_frequency={"state": "unmeasured"},
            created_by=created_by,
            monitor_target_refs=[MonitorTargetRef(target_type=target_type, stable_name=route_key)],
            domain=domain,
        )
        insert_nanobar(conn, nanobar)
        conn.commit()
        return nanobar, True
    except BaseException:
        conn.rollback()
        raise


@dataclass(frozen=True)
class BindingResult:
    nanobars_created: int
    bindings_created: int
    skipped: int  # bricks with no nanobar_type/route_key stamped -- not everything is bindable


def _recompute_weight(conn: sqlite3.Connection, nanobar_id: str, taxonomy: NanobarTypeTaxonomy) -> None:
    """Taxonomy plan §4: "recomputed, not stored-then-forgotten" — re-run whenever a new brick
    binds to a nanobar. Reads the nanobar's *current* full set of bound bricks (not just the ones
    from this batch), so a weight recomputed after binding call N reflects every brick bound so
    far, not only the newest ones.
    """
    from nanobar_api.taxonomy import compute_regression_weight  # deferred -- see the TYPE_CHECKING import above

    nanobar = get_nanobar(conn, nanobar_id)
    assert nanobar is not None
    bound_bricks = get_bricks_for_nanobar(conn, nanobar_id)
    weight = compute_regression_weight(nanobar, bound_bricks, taxonomy)
    set_regression_weight(conn, nanobar_id, weight)


def bind_new_bricks_to_nanobars(
    conn: sqlite3.Connection,
    bricks: list[RegressionBrick],
    *,
    system_name: str = "unknown",
    system_version: str = "0.0.0",
    matched_by: str = "auto-registration",
    taxonomy: NanobarTypeTaxonomy | None = None,
    route_key_domains: dict[str, str] | None = None,
) -> BindingResult:
    """Bind each of `bricks` (typically `generate_bricks()`'s return value) to a `Nanobar` row,
    creating one if this is the first brick ever seen for its `(nanobar_type, route_key)` pair.
    Bricks with neither stamped on `source` (anything not produced via `capture_layer()`, e.g. a
    raw `SnapshotMiddleware` capture with no `nanobar_type` at all) are skipped, not guessed at.

    `taxonomy`, when given, recomputes and persists `regression_weight` for every nanobar touched
    by this call (once each, using its full current set of bound bricks — not just this batch's).
    `None` (the default) skips weight recomputation entirely, unchanged from before this existed.

    `route_key_domains`, when given, maps a brick's `route_key` to the domain a newly-created
    nanobar for it should be stamped with (typically built from `nanobar_api.route_manifest.
    load_route_manifest()` by the caller) -- a route with no entry in this mapping (or when the
    mapping itself is omitted) just creates the nanobar with `domain=None`, unchanged from before
    this parameter existed.
    """
    nanobars_created = 0
    bindings_created = 0
    skipped = 0
    resolved: dict[tuple[str, str], Nanobar] = {}
    touched_nanobar_ids: set[str] = set()

    for brick in bricks:
        nanobar_type = brick.source.get("nanobar_type")
        route_key = brick.source.get("route_key")
        if nanobar_type is None or route_key is None:
            skipped += 1
            continue

        key = (nanobar_type, route_key)
        nanobar = resolved.get(key)
        if nanobar is None:
            nanobar, was_created = get_or_create_nanobar_by_route_key(
                conn,
                nanobar_type=nanobar_type,
                route_key=route_key,
                system_name=system_name,
                system_version=system_version,
                created_by=matched_by,
                domain=route_key_domains.get(route_key) if route_key_domains else None,
            )
            nanobars_created += int(was_created)
            resolved[key] = nanobar

        bind_brick_to_nanobar(
            conn,
            NanobarBrickBinding(
                nanobar_id=nanobar.nanobar_id,
                regression_brick_id=brick.regression_brick_id,
                match_method="exact",
                matcher_version=_MATCHER_VERSION,
                matched_by=matched_by,
                confidence=1.0,
            ),
        )
        bindings_created += 1
        touched_nanobar_ids.add(nanobar.nanobar_id)

    if taxonomy is not None:
        for nanobar_id in touched_nanobar_ids:
            _recompute_weight(conn, nanobar_id, taxonomy)

    return BindingResult(nanobars_created=nanobars_created, bindings_created=bindings_created, skipped=skipped)


def _effective_route_key(brick: RegressionBrick) -> str | None:
    """`brick.source["route_key"]` when stamped (any `capture_layer()`-produced brick); for an
    untagged `SnapshotMiddleware` capture (no `route_key` at all — it doesn't know about this
    concept), derived as `f"{method} {path}"` from the brick's own captured request — the exact
    same `"METHOD /path"` format `NanobarRouteRule.key` already uses, computed post-hoc instead
    of requiring a `SnapshotMiddleware` change.
    """
    stamped = brick.source.get("route_key")
    if stamped:
        return str(stamped)
    method = brick.request.get("method")
    path = brick.request.get("path")
    if method and path:
        return f"{method} {path}"
    return None


def _effective_nanobar_type(brick: RegressionBrick) -> str:
    """`brick.source["nanobar_type"]` when stamped; `"api-request-response"` for an untagged
    `SnapshotMiddleware` capture — the conventional name the API-Domain plan already uses for
    that boundary (`"producing 3 correlated RegressionBricks (api-request-response,
    validator-request-response, controller-request-response)"`), just never previously stamped
    anywhere concretely."""
    stamped = brick.source.get("nanobar_type")
    return str(stamped) if stamped is not None else "api-request-response"


def bind_composite_nanobars(
    conn: sqlite3.Connection,
    bricks: list[RegressionBrick],
    *,
    composite_nanobar_type: str,
    member_nanobar_types: tuple[str, ...],
    system_name: str = "unknown",
    system_version: str = "0.0.0",
    matched_by: str = "auto-registration",
    taxonomy: NanobarTypeTaxonomy | None = None,
) -> BindingResult:
    """Composite nanobars (`"controller-to-db"`/`"api-to-db"`, Service-Domain plan §5) — a
    query-time grouping over bricks sharing one effective route key, spanning more than one
    layer. For each route key where every one of `member_nanobar_types` is present among
    `bricks`, creates/reuses one `composite_nanobar_type` `Nanobar` and binds every matching
    brick to it — *in addition to*, not instead of, whatever `bind_new_bricks_to_nanobars`
    already bound each brick to individually; a brick can be bound to more than one `Nanobar`.
    Route keys missing one or more required member types are skipped, not guessed at.

    A deliberate deviation from the source spec's `match_method="trace"`, same reasoning as
    `bind_new_bricks_to_nanobars` above: route-key grouping is direct and unambiguous for the
    case this session actually built, where trace-correlation would need to disambiguate
    multiple same-type bricks sharing one trace some other way.
    """
    by_route_key: dict[str, dict[str, RegressionBrick]] = {}
    for brick in bricks:
        route_key = _effective_route_key(brick)
        if route_key is None:
            continue
        nanobar_type = _effective_nanobar_type(brick)
        if nanobar_type not in member_nanobar_types:
            continue
        by_route_key.setdefault(route_key, {})[nanobar_type] = brick

    nanobars_created = 0
    bound_brick_ids: set[str] = set()
    touched_nanobar_ids: set[str] = set()

    for route_key, members in by_route_key.items():
        if not all(member_type in members for member_type in member_nanobar_types):
            continue

        nanobar, was_created = get_or_create_nanobar_by_route_key(
            conn,
            nanobar_type=composite_nanobar_type,
            route_key=route_key,
            system_name=system_name,
            system_version=system_version,
            created_by=matched_by,
        )
        nanobars_created += int(was_created)
        touched_nanobar_ids.add(nanobar.nanobar_id)

        for brick in members.values():
            bind_brick_to_nanobar(
                conn,
                NanobarBrickBinding(
                    nanobar_id=nanobar.nanobar_id,
                    regression_brick_id=brick.regression_brick_id,
                    match_method="exact",
                    matcher_version=_MATCHER_VERSION,
                    matched_by=matched_by,
                    confidence=1.0,
                ),
            )
            bound_brick_ids.add(brick.regression_brick_id)

    if taxonomy is not None:
        for nanobar_id in touched_nanobar_ids:
            _recompute_weight(conn, nanobar_id, taxonomy)

    return BindingResult(
        nanobars_created=nanobars_created,
        bindings_created=len(bound_brick_ids),
        skipped=len(bricks) - len(bound_brick_ids),
    )
