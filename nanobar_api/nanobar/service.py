"""Service layer for `Nanobar`'s own mutating route -- `update_nanobar` (PATCH), the one
`Nanobar`-owned operation currently going through the real pipeline. Matches
`app/services/blog_service.py`'s shape.
"""

from __future__ import annotations

from dataclasses import dataclass

from nanobar_api.dynamic_taxonomy import (
    connect as connect_dynamic_taxonomy,
    get_or_create_entry,
    split_dynamic_nanobar_type,
)
from nanobar_api.framework.nanobar_api_service import NanobarAPIService, ServiceResult, ServiceResultBody
from nanobar_api.nanobar.model import nanobar_to_dict
from nanobar_api.nanobar.repository import NanobarRepository
from nanobar_api.taxonomy import NanobarTypeTaxonomy, compute_regression_weight
from nanobar_api.telemetry import NanobarTelemetry

#: Fixed prefixes this project's own runtime actually produces as dynamically-suffixed
#: nanobar_type values -- same list `app/admin/nanobar/api.py`'s own (still-live, for the
#: not-yet-migrated `nanobar_coverage_gaps` route) `_effective_taxonomy` uses. "worker-*" is the
#: one that genuinely needs its own per-(key, key_name) entry, since a worker's expected failure
#: modes are channel-specific.
_DYNAMIC_TAXONOMY_KEYS = ("worker",)


@dataclass
class UpdateNanobarRequest:
    """`None` on any field but `nanobar_id` means "keep the current stored value" (partial
    update) -- merged against the current row inside `handle()`, since the current value is a
    DB read the validator gate has no business making."""

    nanobar_id: str
    label: str | None
    scenario_description: str | None
    component_source_description: str | None
    domain: str | None
    app_box: str | None
    criticality: float | None


class NanobarService(NanobarAPIService):
    def __init__(
        self,
        telemetry: NanobarTelemetry,
        repository: NanobarRepository,
        *,
        static_taxonomy: NanobarTypeTaxonomy,
        dynamic_taxonomy_db_path: str,
    ) -> None:
        super().__init__(telemetry)
        self.repository = repository
        self.static_taxonomy = static_taxonomy
        self.dynamic_taxonomy_db_path = dynamic_taxonomy_db_path

    def _effective_taxonomy(self, nanobar_type: str) -> NanobarTypeTaxonomy:
        """Same resolution `app/admin/nanobar/api.py`'s own `_effective_taxonomy` performs --
        duplicated once here (not shared, not imported across the app/framework boundary a
        framework-level service can't cross) rather than factored out for a single caller;
        revisit if/when `nanobar_coverage_gaps` also migrates onto this service."""
        if nanobar_type in self.static_taxonomy:
            return self.static_taxonomy

        split = split_dynamic_nanobar_type(nanobar_type, known_keys=_DYNAMIC_TAXONOMY_KEYS)
        if split is None:
            return self.static_taxonomy
        key, key_name = split

        default_entry = self.static_taxonomy.get(key)
        if default_entry is None:
            return self.static_taxonomy

        conn = connect_dynamic_taxonomy(self.dynamic_taxonomy_db_path)
        try:
            entry, _created = get_or_create_entry(
                conn, key, key_name, default_entry=default_entry, created_by="dashboard"
            )
        finally:
            conn.close()
        return {**self.static_taxonomy, nanobar_type: entry}

    def handle(self, request: UpdateNanobarRequest) -> ServiceResult:
        current = self.repository.get(request.nanobar_id)
        if current is None:
            return ServiceResult(
                status="error",
                result=ServiceResultBody(
                    type="object", data=None, msg_summary=f"nanobar {request.nanobar_id!r} not found"
                ),
            )

        label = request.label if request.label is not None else current.label
        scenario_description = (
            request.scenario_description if request.scenario_description is not None else current.scenario_description
        )
        component_source_description = (
            request.component_source_description
            if request.component_source_description is not None
            else current.component_source_description
        )
        domain = request.domain if request.domain is not None else current.domain
        app_box = request.app_box if request.app_box is not None else current.app_box
        criticality = request.criticality if request.criticality is not None else current.criticality
        previous_criticality = current.criticality

        self.repository.update_fields(
            request.nanobar_id,
            label=label,
            scenario_description=scenario_description,
            component_source_description=component_source_description,
            domain=domain,
            app_box=app_box,
            criticality=criticality,
        )

        if criticality != previous_criticality:
            # regression_weight depends on criticality (nanobar_api.taxonomy.
            # compute_regression_weight) -- recompute it too, same "recompute on criticality
            # change" trigger the taxonomy plan's own Phase B calls for.
            bound_bricks = self.repository.bricks_for(request.nanobar_id)
            refreshed = self.repository.get(request.nanobar_id)
            assert refreshed is not None
            weight = compute_regression_weight(
                refreshed, bound_bricks, self._effective_taxonomy(refreshed.nanobar_type)
            )
            self.repository.set_regression_weight(request.nanobar_id, weight)

        updated = self.repository.get(request.nanobar_id)
        assert updated is not None
        return ServiceResult(
            status="success",
            result=ServiceResultBody(type="object", data=nanobar_to_dict(updated), msg_summary="nanobar updated"),
        )
