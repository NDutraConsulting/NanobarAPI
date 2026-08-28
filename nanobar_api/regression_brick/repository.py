"""`RegressionBrickRepository` -- `RegressionBrick`'s own `NanobarAPIRepository` subclass,
replacing `nanobar_api/bricks/store.py`'s `RegressionBrick`/review-status/scenario/tag-related
raw-`sqlite3` functions (see `.focusari/regression-brick-refactor-plan-with-tasks.md` Phase 1b).

**Owns `nanobars_for()`** (the reverse of `NanobarRepository.bricks_for()`) -- both repositories
read the shared `NanobarBrickBinding` join table from their own entity's side, rather than one
repository owning the table exclusively; see `nanobar/repository.py`'s module docstring for the
symmetric reasoning.

**`BrickState`/`BrickLog` methods** are new -- `bricks/store.py` never had them, since soft
delete and per-brick logging didn't exist before this refactor (see `regression_brick/model.py`'s
module docstring). Included here so those two tables aren't dead code with no way to populate
them yet; kept deliberately minimal (soft delete/restore, a flexible state-data setter, append
+list for the log) rather than guessing at more specialized operations a later phase's service
layer doesn't need yet.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from nanobar_api.framework.nanobar_api_repository import NanobarAPIRepository
from nanobar_api.nanobar.model import Nanobar, NanobarBrickBinding
from nanobar_api.regression_brick.model import (
    REVIEW_STATUSES,
    BrickLog,
    BrickReviewStatus,
    BrickReviewStatusValue,
    BrickScenario,
    BrickScenarioValue,
    BrickState,
    BrickTag,
    RegressionBrick,
)


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


class RegressionBrickRepository(NanobarAPIRepository):
    def cache_key(self, *args: Any, **kwargs: Any) -> str:
        return f"regression_brick:{args[0]}" if args else "regression_brick:all"

    def create(self, brick: RegressionBrick) -> RegressionBrick:
        self.session.add(brick)
        self.session.commit()
        self.session.refresh(brick)
        return brick

    def get(self, regression_brick_id: str) -> RegressionBrick | None:
        cached = self.get_cached(regression_brick_id)
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        brick = self.session.get(RegressionBrick, regression_brick_id)
        if brick is not None:
            self.set_cached(brick, regression_brick_id)
        return brick

    def get_by_content_hash(self, content_hash: str) -> RegressionBrick | None:
        return self.session.query(RegressionBrick).filter(RegressionBrick.content_hash == content_hash).one_or_none()

    def get_many(self, regression_brick_ids: Sequence[str]) -> list[RegressionBrick]:
        """One bulk `WHERE ... IN (...)` query, not `regression_brick_ids` individual `.get()`
        calls -- introduced for `TelemetryScannerService`'s callers, which get back only brick ids
        from its `ServiceResult` (a raw ORM row isn't JSON-safe for `capture_layer()`'s own
        serialization) but need the full rows for `bind_new_bricks_to_nanobars`. Confirmed live:
        looping individual `.get()` calls here produced one `orm-request-response` capture event
        per brick with no `route_key` (this method runs outside any `NanobarAPIController`,
        so `current_route_key` is never set) -- at real-world batch sizes, dozens of extra
        no-route-key captures were enough to measurably shift the background `EventThread`'s
        flush timing and make `tests/test_demo_dashboard.py`'s replay-flow tests flaky. Order is
        not guaranteed to match `regression_brick_ids`' order -- no caller needs it to.
        """
        if not regression_brick_ids:
            return []
        return list(
            self.session.query(RegressionBrick)
            .filter(RegressionBrick.regression_brick_id.in_(regression_brick_ids))
            .all()
        )

    def set_review_status(self, regression_brick_id: str, status: str, updated_by: str) -> BrickReviewStatus:
        if status not in REVIEW_STATUSES:
            raise ValueError(f"invalid review status {status!r}, must be one of {REVIEW_STATUSES}")
        row = self.session.get(BrickReviewStatus, regression_brick_id)
        if row is None:
            row = BrickReviewStatus(regression_brick_id=regression_brick_id, status=status, updated_by=updated_by)
            self.session.add(row)
        else:
            row.status = status
            row.updated_at = _utcnow_iso()
            row.updated_by = updated_by
        self.session.commit()
        return row

    def get_review_status(self, regression_brick_id: str) -> BrickReviewStatusValue:
        row = self.session.get(BrickReviewStatus, regression_brick_id)
        if row is None:
            return BrickReviewStatusValue(regression_brick_id=regression_brick_id, status="new", updated_by="system")
        return BrickReviewStatusValue(
            regression_brick_id=row.regression_brick_id, status=row.status, updated_by=row.updated_by
        )

    def list_by_review_status(self, status: str | None = None) -> list[RegressionBrick]:
        if status is not None and status not in REVIEW_STATUSES:
            raise ValueError(f"invalid review status {status!r}, must be one of {REVIEW_STATUSES}")
        rows = (
            self.session.query(RegressionBrick, BrickReviewStatus)
            .outerjoin(BrickReviewStatus, BrickReviewStatus.regression_brick_id == RegressionBrick.regression_brick_id)
            .order_by(RegressionBrick.created_at)
            .all()
        )
        return [
            brick
            for brick, review_status in rows
            if status is None or (review_status.status if review_status is not None else "new") == status
        ]

    def nanobars_for(self, regression_brick_id: str) -> list[Nanobar]:
        return list(
            self.session.query(Nanobar)
            .join(NanobarBrickBinding, NanobarBrickBinding.nanobar_id == Nanobar.nanobar_id)
            .filter(NanobarBrickBinding.regression_brick_id == regression_brick_id)
            .order_by(Nanobar.created_at)
            .all()
        )

    def set_scenario(
        self,
        regression_brick_id: str,
        *,
        regression_scenario_label: str | None = None,
        description: str | None = None,
        updated_by: str,
    ) -> BrickScenario:
        row = self.session.get(BrickScenario, regression_brick_id)
        if row is None:
            row = BrickScenario(
                regression_brick_id=regression_brick_id,
                regression_scenario_label=regression_scenario_label,
                description=description,
                updated_by=updated_by,
            )
            self.session.add(row)
        else:
            row.regression_scenario_label = regression_scenario_label
            row.description = description
            row.updated_at = _utcnow_iso()
            row.updated_by = updated_by
        self.session.commit()
        return row

    def get_scenario(self, regression_brick_id: str) -> BrickScenarioValue:
        row = self.session.get(BrickScenario, regression_brick_id)
        if row is None:
            return BrickScenarioValue(
                regression_brick_id=regression_brick_id,
                regression_scenario_label=None,
                description=None,
                updated_by="system",
            )
        return BrickScenarioValue(
            regression_brick_id=row.regression_brick_id,
            regression_scenario_label=row.regression_scenario_label,
            description=row.description,
            updated_by=row.updated_by,
        )

    def add_tag(self, regression_brick_id: str, tag: str) -> None:
        exists = (
            self.session.query(BrickTag)
            .filter(BrickTag.regression_brick_id == regression_brick_id, BrickTag.tag == tag)
            .one_or_none()
        )
        if exists is None:
            self.session.add(BrickTag(regression_brick_id=regression_brick_id, tag=tag))
            self.session.commit()

    def remove_tag(self, regression_brick_id: str, tag: str) -> None:
        self.session.query(BrickTag).filter(
            BrickTag.regression_brick_id == regression_brick_id, BrickTag.tag == tag
        ).delete()
        self.session.commit()

    def tags_for(self, regression_brick_id: str) -> list[str]:
        rows = (
            self.session.query(BrickTag.tag)
            .filter(BrickTag.regression_brick_id == regression_brick_id)
            .order_by(BrickTag.tag)
            .all()
        )
        return [row[0] for row in rows]

    def list_by_tag(self, tag: str) -> list[RegressionBrick]:
        return list(
            self.session.query(RegressionBrick)
            .join(BrickTag, BrickTag.regression_brick_id == RegressionBrick.regression_brick_id)
            .filter(BrickTag.tag == tag)
            .order_by(RegressionBrick.created_at)
            .all()
        )

    def get_state(self, regression_brick_id: str) -> BrickState | None:
        return self.session.get(BrickState, regression_brick_id)

    def soft_delete(self, regression_brick_id: str, *, deleted_by: str, reason: str | None = None) -> BrickState:
        state = self.session.get(BrickState, regression_brick_id)
        now = _utcnow_iso()
        if state is None:
            state = BrickState(
                regression_brick_id=regression_brick_id,
                deleted_at=now,
                deleted_by=deleted_by,
                deletion_reason=reason,
                updated_by=deleted_by,
            )
            self.session.add(state)
        else:
            state.deleted_at = now
            state.deleted_by = deleted_by
            state.deletion_reason = reason
            state.updated_at = now
            state.updated_by = deleted_by
        self.session.commit()
        return state

    def restore(self, regression_brick_id: str, *, updated_by: str) -> BrickState | None:
        state = self.session.get(BrickState, regression_brick_id)
        if state is None:
            return None
        state.deleted_at = None
        state.deleted_by = None
        state.deletion_reason = None
        state.updated_at = _utcnow_iso()
        state.updated_by = updated_by
        self.session.commit()
        return state

    def set_state_data(self, regression_brick_id: str, *, updated_by: str, data: dict[str, Any]) -> BrickState:
        """Replaces (not merges) `data_json`, matching `BrickState.data`'s own setter semantics --
        the caller reads the current value first if it needs to merge, same partial-update
        contract `NanobarRepository.update_fields` already establishes for its own fields."""
        state = self.session.get(BrickState, regression_brick_id)
        if state is None:
            state = BrickState(regression_brick_id=regression_brick_id, data=data, updated_by=updated_by)
            self.session.add(state)
        else:
            state.data = data
            state.updated_at = _utcnow_iso()
            state.updated_by = updated_by
        self.session.commit()
        return state

    def add_log(self, regression_brick_id: str, data: dict[str, Any]) -> BrickLog:
        entry = BrickLog(regression_brick_id=regression_brick_id, data=data)
        self.session.add(entry)
        self.session.commit()
        self.session.refresh(entry)
        return entry

    def list_logs(self, regression_brick_id: str) -> list[BrickLog]:
        return list(
            self.session.query(BrickLog)
            .filter(BrickLog.regression_brick_id == regression_brick_id)
            .order_by(BrickLog.id)
            .all()
        )
