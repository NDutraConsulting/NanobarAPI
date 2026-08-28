"""`NanobarRepository` -- `Nanobar`'s own `NanobarAPIRepository` subclass, replacing
`nanobar_api/bricks/store.py`'s `Nanobar`/`NanobarBrickBinding`-related raw-`sqlite3` functions
(see `.focusari/regression-brick-refactor-plan-with-tasks.md` Phase 1b).

**Owns `NanobarBrickBinding` reads/writes** -- the join table lives in `nanobar/model.py` (that
module's own docstring explains why: keyed by `nanobar_id` first), so binding creation and
"which bricks are bound to this nanobar" both belong here, symmetric with
`RegressionBrickRepository.nanobars_for()` owning the reverse direction. Reading `RegressionBrick`
rows here is a real cross-entity import, not a layering violation -- `nanobar_api/persistence.py`
already establishes both entities share one database specifically because of this join table.

**`get_or_create_by_route_key`'s atomicity** comes from the engine-wide `BEGIN IMMEDIATE` event
listener in `nanobar_api/persistence.py`, not a manual transaction call here -- see that
listener's own docstring. This method just needs to do its select-then-insert as ordinary ORM
calls within the session's current transaction; the engine guarantees no concurrent transaction
can interleave a conflicting write.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, text

from nanobar_api.framework.nanobar_api_repository import NanobarAPIRepository
from nanobar_api.nanobar.model import MonitorTargetRef, Nanobar, NanobarBrickBinding
from nanobar_api.regression_brick.model import RegressionBrick

_DEFAULT_TARGET_TYPE = "route"

#: Text fields searched by `list_nanobars(q=...)` -- exactly the fields already rendered on the
#: nanobars list/detail pages, so "search" means "search what you can already see."
_SEARCHABLE_NANOBAR_COLUMNS = (
    "label",
    "scenario_description",
    "component_source_description",
    "domain",
    "app_box",
    "nanobar_type",
    "nanobar_id",
)

#: Sentinel `domain` filter value meaning "nanobars with no domain at all" (`domain IS NULL`) --
#: see `nanobar_api/bricks/store.py`'s original `UNMAPPED_DOMAIN` for why this can't collide with
#: a real domain name (domain names come from `Mount` path segments, which can't contain
#: parentheses).
UNMAPPED_DOMAIN = "(unmapped)"

#: Same reasoning as `UNMAPPED_DOMAIN`, for `app_box` -- a nanobar created before this field
#: existed (or never refreshed since) has no `app_box` at all yet.
UNMAPPED_APP_BOX = "(unmapped)"


class NanobarRepository(NanobarAPIRepository):
    def cache_key(self, *args: Any, **kwargs: Any) -> str:
        return f"nanobar:{args[0]}" if args else "nanobar:all"

    def create(self, nanobar: Nanobar) -> Nanobar:
        self.session.add(nanobar)
        self.session.commit()
        self.session.refresh(nanobar)
        return nanobar

    def get(self, nanobar_id: str) -> Nanobar | None:
        cached = self.get_cached(nanobar_id)
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        nanobar = self.session.get(Nanobar, nanobar_id)
        if nanobar is not None:
            self.set_cached(nanobar, nanobar_id)
        return nanobar

    def update_fields(
        self,
        nanobar_id: str,
        *,
        label: str | None = None,
        scenario_description: str | None = None,
        component_source_description: str | None = None,
        domain: str | None = None,
        app_box: str | None = None,
        criticality: float,
    ) -> Nanobar | None:
        """Overwrites the human-navigation fields with exactly the values given -- partial-update
        ("keep unspecified fields as-is") semantics belong to the caller, matching
        `bricks/store.py`'s original `update_nanobar` contract exactly, including `criticality`
        having no default (a silent numeric default would quietly overwrite a real value the
        caller never meant to touch)."""
        nanobar = self.session.get(Nanobar, nanobar_id)
        if nanobar is None:
            return None
        nanobar.label = label
        nanobar.scenario_description = scenario_description
        nanobar.component_source_description = component_source_description
        nanobar.domain = domain
        nanobar.app_box = app_box
        nanobar.criticality = criticality
        self.session.commit()
        self.session.refresh(nanobar)
        self.invalidate(nanobar_id)
        return nanobar

    def set_regression_weight(self, nanobar_id: str, regression_weight: float) -> Nanobar | None:
        """A dedicated setter, not folded into `update_fields` above -- `regression_weight` is a
        derived/materialized value (`nanobar_api.taxonomy.compute_regression_weight`), not a
        human-navigation field with partial-update semantics."""
        nanobar = self.session.get(Nanobar, nanobar_id)
        if nanobar is None:
            return None
        nanobar.regression_weight = regression_weight
        self.session.commit()
        self.invalidate(nanobar_id)
        return nanobar

    def set_domain(self, nanobar_id: str, domain: str | None) -> Nanobar | None:
        """A dedicated setter, same category as `set_regression_weight` above -- for a
        system-managed correction (backfilling/correcting `domain` against the current route
        manifest), without touching any human-edited fields."""
        nanobar = self.session.get(Nanobar, nanobar_id)
        if nanobar is None:
            return None
        nanobar.domain = domain
        self.session.commit()
        self.invalidate(nanobar_id)
        return nanobar

    def set_app_box(self, nanobar_id: str, app_box: str | None) -> Nanobar | None:
        """`set_domain`'s exact counterpart for `app_box` -- backfilling/correcting it against
        the current route manifest, independent of `domain`."""
        nanobar = self.session.get(Nanobar, nanobar_id)
        if nanobar is None:
            return None
        nanobar.app_box = app_box
        self.session.commit()
        self.invalidate(nanobar_id)
        return nanobar

    def soft_delete(self, nanobar_id: str, *, deleted_at: str) -> Nanobar | None:
        nanobar = self.session.get(Nanobar, nanobar_id)
        if nanobar is None:
            return None
        nanobar.deleted_at = deleted_at
        self.session.commit()
        self.invalidate(nanobar_id)
        return nanobar

    def restore(self, nanobar_id: str) -> Nanobar | None:
        nanobar = self.session.get(Nanobar, nanobar_id)
        if nanobar is None:
            return None
        nanobar.deleted_at = None
        self.session.commit()
        self.invalidate(nanobar_id)
        return nanobar

    def list_known_route_keys(self) -> set[str]:
        """Every distinct `monitor_target_refs[].stable_name` (route key) any existing nanobar
        already carries -- used by `admin/nanobar/nanobar_refresh.py` to find which manifest
        routes have no nanobar yet, without creating a duplicate for one that does."""
        rows = self.session.execute(
            text(
                "SELECT DISTINCT json_extract(value, '$.stable_name') "
                "FROM nanobars, json_each(monitor_target_refs_json)"
            )
        ).all()
        return {row[0] for row in rows if row[0] is not None}

    def _where_clauses(
        self,
        target_type: str | None,
        nanobar_type: str | None,
        q: str | None,
        domain: str | None,
        app_box: str | None = None,
    ) -> list[Any]:
        clauses: list[Any] = []
        if target_type is not None:
            clauses.append(
                text(
                    "EXISTS (SELECT 1 FROM json_each(nanobars.monitor_target_refs_json) "
                    "WHERE json_extract(value, '$.target_type') = :target_type)"
                ).bindparams(target_type=target_type)
            )
        if nanobar_type is not None:
            clauses.append(Nanobar.nanobar_type == nanobar_type)
        if domain is not None:
            clauses.append(Nanobar.domain.is_(None) if domain == UNMAPPED_DOMAIN else Nanobar.domain == domain)
        if app_box is not None:
            clauses.append(Nanobar.app_box.is_(None) if app_box == UNMAPPED_APP_BOX else Nanobar.app_box == app_box)
        if q:
            needle = f"%{q.lower()}%"
            clauses.append(
                or_(
                    *(
                        func.lower(func.coalesce(getattr(Nanobar, column), "")).like(needle)
                        for column in _SEARCHABLE_NANOBAR_COLUMNS
                    )
                )
            )
        return clauses

    def list_nanobars(
        self,
        target_type: str | None = None,
        *,
        nanobar_type: str | None = None,
        domain: str | None = None,
        app_box: str | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> list[Nanobar]:
        query = self.session.query(Nanobar)
        for clause in self._where_clauses(target_type, nanobar_type, q, domain, app_box):
            query = query.filter(clause)
        return list(query.order_by(Nanobar.created_at.desc()).limit(page_size).offset((page - 1) * page_size).all())

    def count_nanobars(
        self,
        target_type: str | None = None,
        *,
        nanobar_type: str | None = None,
        domain: str | None = None,
        app_box: str | None = None,
        q: str | None = None,
    ) -> int:
        query = self.session.query(Nanobar)
        for clause in self._where_clauses(target_type, nanobar_type, q, domain, app_box):
            query = query.filter(clause)
        return query.count()

    def _find_by_route_key(self, *, nanobar_type: str, route_key: str, target_type: str) -> Nanobar | None:
        """O(n) over nanobars of this `nanobar_type` -- `monitor_target_refs` is a JSON list, not
        a queryable scalar column, so there's no native index to look this up by directly. Fine
        at today's scale; a real cost once one `nanobar_type` accumulates many nanobars."""
        candidates = self.session.query(Nanobar).filter(Nanobar.nanobar_type == nanobar_type).all()
        for nanobar in candidates:
            if any(
                ref.target_type == target_type and ref.stable_name == route_key for ref in nanobar.monitor_target_refs
            ):
                return nanobar
        return None

    def get_or_create_by_route_key(
        self,
        *,
        nanobar_type: str,
        route_key: str,
        system_name: str = "unknown",
        system_version: str = "0.0.0",
        target_type: str = _DEFAULT_TARGET_TYPE,
        created_by: str = "auto-registration",
        domain: str | None = None,
        app_box: str | None = None,
    ) -> tuple[Nanobar, bool]:
        """Idempotent get-or-create keyed by `(nanobar_type, route_key)`. Returns `(nanobar,
        was_created)` -- the caller needs to know which, to report an accurate creation count
        without a second, redundant lookup. Race-free against a concurrent call on this engine
        via the `BEGIN IMMEDIATE` event listener installed in `nanobar_api/persistence.py` (see
        module docstring).

        Placeholder metadata for a newly-created row matches `examples/seed_kahnban_bricks.py`'s
        own established convention: `regression_weight=0.5`,
        `endpoint_scenario_frequency={"state": "unmeasured"}`, `request_object_id`/
        `response_object_id` derived as `f"req-{route_key}"`/`f"res-{route_key}"`.

        `domain`/`app_box`, when given, are stamped on a newly-created row only -- an existing
        nanobar's values are left untouched (see `set_domain`/`set_app_box` for correcting one
        after the fact).
        """
        existing = self._find_by_route_key(nanobar_type=nanobar_type, route_key=route_key, target_type=target_type)
        if existing is not None:
            return existing, False

        nanobar = Nanobar(
            schema_version="1.0",
            system_name=system_name,
            system_version=system_version,
            nanobar_type=nanobar_type,
            request_object_id=f"req-{route_key}",
            response_object_id=f"res-{route_key}",
            regression_weight=0.5,
            endpoint_scenario_frequency={"state": "unmeasured"},
            created_by=created_by,
            domain=domain,
            app_box=app_box,
        )
        nanobar.monitor_target_refs = [MonitorTargetRef(target_type=target_type, stable_name=route_key)]
        self.session.add(nanobar)
        self.session.commit()
        self.session.refresh(nanobar)
        return nanobar, True

    def bind_brick(self, binding: NanobarBrickBinding) -> NanobarBrickBinding:
        self.session.add(binding)
        self.session.commit()
        self.invalidate(binding.nanobar_id)
        return binding

    def bricks_for(self, nanobar_id: str) -> list[RegressionBrick]:
        return list(
            self.session.query(RegressionBrick)
            .join(NanobarBrickBinding, NanobarBrickBinding.regression_brick_id == RegressionBrick.regression_brick_id)
            .filter(NanobarBrickBinding.nanobar_id == nanobar_id)
            .order_by(RegressionBrick.created_at)
            .all()
        )
