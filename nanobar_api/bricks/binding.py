"""Auto-registration + binding — turns a freshly-generated `RegressionBrick` carrying
`source["nanobar_type"]`/`source["route_key"]` (both stamped by `generate_bricks()` when the
originating event came from `capture_layer()`) into a bound `(Nanobar, RegressionBrick)` pair,
creating the `Nanobar` row on first sight.

**A deliberate deviation from the source spec, not what its own checklist literally names.**
`.focusari/nanobar_APIDomain_abstract_class_buildplan-with-tasks.md` calls for two separate
things: (a) real-time auto-registration inside `NanobarAPIValidatorGate.__call__`/
`NanobarAPIController.handle`, and (b) "the first real `'trace'` `match_method` implementation" —
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

**Atomic get-or-create + binding now live on `NanobarRepository`/`RegressionBrickRepository`
directly** (`nanobar_api/nanobar/repository.py`'s `get_or_create_by_route_key`/`bind_brick`,
`nanobar_api/regression_brick/repository.py`) -- this module is now a thin orchestration layer
over those two repositories' `Session`, not its own transaction-control/raw-SQL implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from nanobar_api.nanobar.model import Nanobar, NanobarBrickBinding
from nanobar_api.nanobar.repository import NanobarRepository
from nanobar_api.regression_brick.model import RegressionBrick

if TYPE_CHECKING:
    from nanobar_api.taxonomy import NanobarTypeTaxonomy

_MATCHER_VERSION = "v1"


@dataclass(frozen=True)
class BindingResult:
    nanobars_created: int
    bindings_created: int
    skipped: int  # bricks with no nanobar_type/route_key stamped -- not everything is bindable


def _recompute_weight(nanobar_repository: NanobarRepository, nanobar_id: str, taxonomy: NanobarTypeTaxonomy) -> None:
    """Taxonomy plan §4: "recomputed, not stored-then-forgotten" — re-run whenever a new brick
    binds to a nanobar. Reads the nanobar's *current* full set of bound bricks (not just the ones
    from this batch), so a weight recomputed after binding call N reflects every brick bound so
    far, not only the newest ones.
    """
    from nanobar_api.taxonomy import compute_regression_weight  # deferred -- avoids a taxonomy<->binding cycle

    nanobar = nanobar_repository.get(nanobar_id)
    assert nanobar is not None
    bound_bricks = nanobar_repository.bricks_for(nanobar_id)
    weight = compute_regression_weight(nanobar, bound_bricks, taxonomy)
    nanobar_repository.set_regression_weight(nanobar_id, weight)


def bind_new_bricks_to_nanobars(
    nanobar_repository: NanobarRepository,
    bricks: list[RegressionBrick],
    *,
    system_name: str = "unknown",
    system_version: str = "0.0.0",
    matched_by: str = "auto-registration",
    taxonomy: NanobarTypeTaxonomy | None = None,
    route_key_domains: dict[str, str] | None = None,
    route_key_app_boxes: dict[str, str] | None = None,
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
    this parameter existed. `route_key_app_boxes` is `route_key_domains`'s exact counterpart for
    `app_box` -- independent mapping, independent default (`None`), same "newly-created row only"
    stamping rule.
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
            nanobar, was_created = nanobar_repository.get_or_create_by_route_key(
                nanobar_type=nanobar_type,
                route_key=route_key,
                system_name=system_name,
                system_version=system_version,
                created_by=matched_by,
                domain=route_key_domains.get(route_key) if route_key_domains else None,
                app_box=route_key_app_boxes.get(route_key) if route_key_app_boxes else None,
            )
            nanobars_created += int(was_created)
            resolved[key] = nanobar

        nanobar_repository.bind_brick(
            NanobarBrickBinding(
                nanobar_id=nanobar.nanobar_id,
                regression_brick_id=brick.regression_brick_id,
                match_method="exact",
                matcher_version=_MATCHER_VERSION,
                matched_by=matched_by,
                confidence=1.0,
            )
        )
        bindings_created += 1
        touched_nanobar_ids.add(nanobar.nanobar_id)

    if taxonomy is not None:
        for nanobar_id in touched_nanobar_ids:
            _recompute_weight(nanobar_repository, nanobar_id, taxonomy)

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
    nanobar_repository: NanobarRepository,
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

        nanobar, was_created = nanobar_repository.get_or_create_by_route_key(
            nanobar_type=composite_nanobar_type,
            route_key=route_key,
            system_name=system_name,
            system_version=system_version,
            created_by=matched_by,
        )
        nanobars_created += int(was_created)
        touched_nanobar_ids.add(nanobar.nanobar_id)

        for brick in members.values():
            nanobar_repository.bind_brick(
                NanobarBrickBinding(
                    nanobar_id=nanobar.nanobar_id,
                    regression_brick_id=brick.regression_brick_id,
                    match_method="exact",
                    matcher_version=_MATCHER_VERSION,
                    matched_by=matched_by,
                    confidence=1.0,
                )
            )
            bound_brick_ids.add(brick.regression_brick_id)

    if taxonomy is not None:
        for nanobar_id in touched_nanobar_ids:
            _recompute_weight(nanobar_repository, nanobar_id, taxonomy)

    return BindingResult(
        nanobars_created=nanobars_created,
        bindings_created=len(bound_brick_ids),
        skipped=len(bricks) - len(bound_brick_ids),
    )
