"""`Nanobar` -- the stable identity of one component's input/output boundary; a class of tests,
not an individual scenario (see this repo's own `README.md`, "Core concepts"). Real SQLAlchemy
ORM model, replacing `nanobar_api/bricks/schema.py`'s old `Nanobar` frozen dataclass + raw
`CREATE TABLE nanobars` DDL string -- see `.focusari/regression-brick-refactor-plan-with-tasks.md`
Design Decision 1/2.

`MonitorTargetRef` stays a plain dataclass (not its own table) -- it's small, embedded, per-row
data (`Nanobar.monitor_target_refs`), the same shape it always was; `monitor_target_refs_json`
is the real SQLAlchemy `JSON` column, `monitor_target_refs` a Python-level convenience property
converting to/from `MonitorTargetRef` instances, matching the old `_row_to_nanobar()`'s exact
behavior.

`NanobarBrickBinding` (the `nanobar_regression_bricks` join table) lives here, not in
`regression_brick/model.py` -- it's keyed by `nanobar_id` first and is `Nanobar`'s own concern to
own, even though it references `RegressionBrick` rows (per the refactor plan's own File-by-file
plan).
"""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from nanobar_api.persistence import Base


def _new_nanobar_id() -> str:
    return f"nb-{uuid.uuid4().hex[:12]}"


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class MonitorTargetRef:
    target_type: str
    stable_name: str


class Nanobar(Base):
    __tablename__ = "nanobars"
    __table_args__ = (CheckConstraint("regression_weight BETWEEN 0.0 AND 1.0"),)

    nanobar_id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_nanobar_id)
    schema_version: Mapped[str] = mapped_column(String, nullable=False)
    system_name: Mapped[str] = mapped_column(String, nullable=False)
    system_version: Mapped[str] = mapped_column(String, nullable=False)
    nanobar_type: Mapped[str] = mapped_column(String, nullable=False)
    request_object_id: Mapped[str] = mapped_column(String, nullable=False)
    response_object_id: Mapped[str] = mapped_column(String, nullable=False)
    regression_weight: Mapped[float] = mapped_column(nullable=False)
    criticality: Mapped[float] = mapped_column(nullable=False, default=0.5)
    endpoint_scenario_frequency_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    monitor_target_refs_json: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False, default=list)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    scenario_description: Mapped[str | None] = mapped_column(String, nullable=True)
    component_source_description: Mapped[str | None] = mapped_column(String, nullable=True)
    domain: Mapped[str | None] = mapped_column(String, nullable=True)
    #: Additive alongside `domain`, per `.focusari/appbox-plan-with-tasks.md` -- a purely
    #: structural/classification field (`"admin/app"`, `"admin/nanobar"`, `"api"`, `"workers"`),
    #: computed and stamped the same way `domain` is (`get_or_create_by_route_key`/
    #: `bind_new_bricks_to_nanobars`/`nanobar_refresh.py`), never a replacement for `domain`
    #: (which stays exactly as it was -- bricks need it, unmodified, to actually run/replay).
    #: `RegressionBrick.app_box` already exists (see that model's own docstring); this is the
    #: matching field on `Nanobar`, the one half of the plan not yet built.
    app_box: Mapped[str | None] = mapped_column(String, nullable=True)
    source_info_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    #: Soft-delete marker (`.focusari/complete/adr/data-retention-adr.md` §4's lifecycle,
    #: pulled forward from its original "deferred beyond beta" scope) -- `None` means active.
    #: `Nanobar` rows are already mutable (unlike `RegressionBrick`, see that model's own
    #: docstring), so this lives directly on the row rather than needing a side-table.
    deleted_at: Mapped[str | None] = mapped_column(String, nullable=True, default=None)

    bindings: Mapped[list[NanobarBrickBinding]] = relationship(back_populates="nanobar", cascade="all, delete-orphan")

    @property
    def monitor_target_refs(self) -> list[MonitorTargetRef]:
        return [
            MonitorTargetRef(target_type=r["target_type"], stable_name=r["stable_name"])
            for r in self.monitor_target_refs_json
        ]

    @monitor_target_refs.setter
    def monitor_target_refs(self, refs: list[MonitorTargetRef]) -> None:
        self.monitor_target_refs_json = [{"target_type": r.target_type, "stable_name": r.stable_name} for r in refs]

    @property
    def endpoint_scenario_frequency(self) -> dict[str, Any]:
        return self.endpoint_scenario_frequency_json

    @endpoint_scenario_frequency.setter
    def endpoint_scenario_frequency(self, value: dict[str, Any]) -> None:
        self.endpoint_scenario_frequency_json = value

    @property
    def source_info(self) -> dict[str, Any] | None:
        return self.source_info_json

    @source_info.setter
    def source_info(self, value: dict[str, Any] | None) -> None:
        self.source_info_json = value


def nanobar_to_dict(nanobar: Nanobar) -> dict[str, Any]:
    """Same field set `dataclasses.asdict()` produced against the old `bricks.schema.Nanobar`
    frozen dataclass -- explicit since the real ORM row isn't a dataclass and carries extra
    ORM-only fields (`created_at`, `deleted_at`) this shape never exposed. Shared by
    `nanobar_api/nanobar/service.py` (framework-level) and `app/admin/nanobar/api.py`'s own
    read-only routes (app-level) -- lives here, not in either caller, since a framework-level
    service can't import from `app/`.
    """
    return {
        "nanobar_id": nanobar.nanobar_id,
        "schema_version": nanobar.schema_version,
        "system_name": nanobar.system_name,
        "system_version": nanobar.system_version,
        "nanobar_type": nanobar.nanobar_type,
        "request_object_id": nanobar.request_object_id,
        "response_object_id": nanobar.response_object_id,
        "regression_weight": nanobar.regression_weight,
        "endpoint_scenario_frequency": nanobar.endpoint_scenario_frequency,
        "created_by": nanobar.created_by,
        "criticality": nanobar.criticality,
        "monitor_target_refs": [dataclasses.asdict(ref) for ref in nanobar.monitor_target_refs],
        "label": nanobar.label,
        "scenario_description": nanobar.scenario_description,
        "component_source_description": nanobar.component_source_description,
        "domain": nanobar.domain,
        "app_box": nanobar.app_box,
        "source_info": nanobar.source_info,
    }


class NanobarBrickBinding(Base):
    """`ON DELETE` FK actions deliberately not set on either foreign key below (contrast the old
    raw-SQL schema's `ON DELETE CASCADE`/`ON DELETE RESTRICT`) -- per
    `.focusari/complete/adr/data-retention-adr.md` §4, deletion of a `Nanobar`/`RegressionBrick`
    is a soft delete (a marker set, no row physically removed) in normal application operation,
    not a hard `DELETE` a database-level cascade/restrict action would ever actually run
    against. A real hard-delete/purge workflow (the ADR's own grace-period-expiry step, not yet
    built) handles its own referential cleanup explicitly rather than relying on an FK action
    that "creates problems" for exactly the reason this ADR flags: an `ON DELETE RESTRICT`
    blocking an unrelated, legitimate deletion elsewhere just because a binding still exists is
    the wrong failure mode for evidentiary data that should be reviewable, not silently
    unreachable.
    """

    __tablename__ = "nanobar_regression_bricks"
    __table_args__ = (CheckConstraint("match_method IN ('exact', 'regex', 'fuzzy', 'trace', 'manual')"),)

    nanobar_id: Mapped[str] = mapped_column(ForeignKey("nanobars.nanobar_id"), primary_key=True)
    regression_brick_id: Mapped[str] = mapped_column(
        ForeignKey("regression_bricks.regression_brick_id"), primary_key=True
    )
    match_method: Mapped[str] = mapped_column(String, nullable=False)
    match_rule: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    matcher_version: Mapped[str] = mapped_column(String, nullable=False)
    matched_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)
    matched_by: Mapped[str] = mapped_column(String, nullable=False)

    nanobar: Mapped[Nanobar] = relationship(back_populates="bindings")
