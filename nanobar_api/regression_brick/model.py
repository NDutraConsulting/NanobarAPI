"""`RegressionBrick` -- an immutable, versioned example of a `Nanobar`'s class of tests (see
this repo's own `README.md`, "Core concepts"). Real SQLAlchemy ORM model, replacing
`nanobar_api/bricks/schema.py`'s old `RegressionBrick` frozen dataclass + raw
`CREATE TABLE regression_bricks` DDL string -- see
`.focusari/regression-brick-refactor-plan-with-tasks.md` Design Decision 1/2.

**Immutability enforcement**: the DB-level `regression_bricks_are_immutable` trigger lives in
`nanobar_api/persistence.py` (shared setup, installed once per engine) -- SQLAlchemy has no
declarative trigger API, so raw DDL is still how this is enforced, exactly as it was before this
migration. A brick that needs new evidence forks (`forked_from_regression_brick_id`), never
updates in place.

**Composition, not inheritance, for `RegressionBrickStateFields`** -- `NanobarAPIModel(ABC)`
uses `ABCMeta`, which conflicts with SQLAlchemy's `DeclarativeBase` metaclass (same conflict
`app/models/blog_model.py`'s `Post`/`PostStateFields` already documents and works around). This
declares `regression_brick_review_status`'s `new -> reviewed/flagged/promoted` transitions as a
real state-machine contract (today enforced only as a `CHECK` allowing any-to-any, no real
transition-order rule), consulted by the service layer at the point of a status change -- not
mixed into the `BrickReviewStatus` ORM row.

**Current state (including soft delete) lives in its own side-table (`BrickState`), not columns
on `RegressionBrick` itself** -- per `.focusari/complete/adr/data-retention-adr.md` §4 (its
soft-delete lifecycle, originally "deferred beyond beta," pulled forward), replacing the old
schema's `ON DELETE RESTRICT` foreign keys, which "create problems": a restrictive FK blocking
an unrelated, legitimate deletion elsewhere just because a binding still exists is the wrong
failure mode for evidentiary data that should be reviewable, not silently unreachable. A column
can't work here directly -- the immutability trigger above blocks *any* `UPDATE` on
`regression_bricks`, including one that would only touch a `deleted_at` column -- so this state
needs the exact same side-table treatment `BrickReviewStatus`/`BrickScenario` already use for
mutable annotations on an otherwise-immutable row. `BrickState` is one-to-one with
`RegressionBrick` (no row means active/no recorded state yet) and, beyond the deletion audit
fields the ADR's §3/§4 call for, carries a `data_json` bag for whatever other per-brick state a
testing flow needs to track (e.g. inclusion in a replay suite, last replay outcome) -- deferred
to a flexible column rather than guessed-at named ones, since the exact fields aren't pinned down
yet and a JSON bag avoids a schema migration every time that list grows.

`BrickState.regression_brick_id` is **deliberately not an enforced foreign key**, for the same
reason `BrickDeletion` (its predecessor) wasn't: the ADR requires the deletion audit fields to
"survive the deletion [they're] documenting" (§3 step 3) past the brick's eventual hard delete.
Since deletion is now folded into the same general-state row as the other `data_json` state,
that same no-enforced-FK choice covers the whole row -- an `ON DELETE CASCADE` FK would destroy
the audit proof at the exact moment it matters most; `ON DELETE RESTRICT`/no-action would block
the hard delete this lifecycle exists to eventually allow. None of the other `data_json` state
needs FK enforcement to remain useful, so nothing is lost by this row sharing that property.

**`BrickLog` is a separate, one-to-many table** -- append-only notes/events about a brick over
time (`created_at` + a `data_json` blob per entry), meant for building graphs/charts of
per-brick activity. Unlike `BrickState`'s audit fields, log entries are derived/dependent data
about a brick, not independent evidence that must outlive it (same distinction the ADR's §2 draws
for shadow-store fixtures) -- so this FK *is* enforced, with `ON DELETE CASCADE`: a brick's log
entries are cleaned up with it on hard delete, which is cleanup, not the blocking failure mode
the ADR's soft-delete redesign was built to avoid.

**`span_id` (added 2026-08-27, `.focusari/telemetry-domain-refactor-plan-with-tasks.md` Decision
6)** -- a real, first-class, indexable column promoted out of `source_json`'s buried
`source["span_id"]`, **deliberately not a foreign key**: the `Span` row it names lives in a
different physical database (`nanobar_api_telemetry.db`, see `nanobar_api/telemetry/model.py`),
and SQLite has no meaningful cross-database FK enforcement anyway. Resolving a brick's `span_id`
to its actual `Span` row is always a second, separate query against the telemetry database, never
a SQL join. Nullable -- older bricks (created before this column existed) and any future brick
source that isn't span-derived have nothing to put here.

**`entry_point`/`app_box`/`nanobar_type` (added 2026-08-27, second pass -- see
`.focusari/2026-08-27-regression-brick-clarification.md` Part 2 and
`.focusari/2026-08-27.1800.build_and_update_plan_with_tasks.md` Phase 1)** -- promoted to
first-class columns so a brick is **self-contained at replay time**: no query against the
telemetry database is needed to know where/how to replay a brick. `nanobar_api/bricks/
generate.py` (the one and only brick-creation call site) already holds the owning `Trace` row in
scope when it builds a brick (it scans trace-by-trace), so populating `entry_point`/`app_box`
here is reading two fields off an object already in hand, not an extra query -- the
`brick.span_id -> Span.trace_id -> Trace.entry_point` join this replaces was only ever going to
be a *replay-time* cost, never a creation-time one. `nanobar_type` was previously buried in
`source_json["nanobar_type"]`; promoted alongside these two since it's what replay dispatch keys
off and deserves the same "no digging into a JSON blob" treatment. All three nullable -- older
bricks predating this column and any capture path that doesn't yet supply them (e.g. no
`nanobar_type` on a `SnapshotMiddleware`-sourced brick) have nothing to put here; see Phase 7 of
the plan doc for the one-time backfill.

**`source_info_json`/`source_info`** -- a new, narrower traceability-only bag
(`{trace_id, span_id, channel}`), **never read at replay time** (that's the whole point of the
three columns above). `source_json` (the older, broader field -- also carries `nanobar_type`/
`route_key`) is left untouched for backward compatibility with existing readers; this is
deliberately additive, not a replacement, same pattern this codebase already uses for `app_box`
alongside `domain`. `span_id`'s own top-level column above is unchanged and still the one real,
indexed column for "which bricks came from this span" lookups -- `source_info_json` duplicating
it is accepted, not something to reconcile away.

**`regression_scenario_description`** -- a new, optional free-text companion to
`regression_scenario_type` (which stays a coarse classification key, e.g. `"success"`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from nanobar_api.framework.nanobar_api_model import NanobarAPIModel
from nanobar_api.persistence import Base


def _new_brick_id() -> str:
    return f"rbrick-{uuid.uuid4().hex[:12]}"


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


REVIEW_STATUSES = ("new", "reviewed", "flagged", "promoted")


class RegressionBrickStateFields(NanobarAPIModel):
    """`regression_brick_review_status.status`'s declared transitions -- see module docstring
    for why this is composed alongside `BrickReviewStatus`, not mixed into it."""

    monitored_state_fields = {"status": REVIEW_STATUSES}
    idempotent_fields = ("regression_brick_id",)


class RegressionBrick(Base):
    __tablename__ = "regression_bricks"

    regression_brick_id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_brick_id)
    schema_version: Mapped[str] = mapped_column(String, nullable=False)
    brick_version: Mapped[int] = mapped_column(nullable=False, default=1)
    forked_from_regression_brick_id: Mapped[str | None] = mapped_column(
        ForeignKey("regression_bricks.regression_brick_id"), nullable=True
    )
    source_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    trace_refs_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    #: Not a foreign key -- see module docstring. Indexed since a real caller (a future "which
    #: bricks came from this span" lookup) would filter by it.
    span_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    capture_policy_id: Mapped[str | None] = mapped_column(String, nullable=True)
    content_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    regression_scenario_type: Mapped[str | None] = mapped_column(String, nullable=True)
    regression_scenario_description: Mapped[str | None] = mapped_column(String, nullable=True)
    #: Self-contained replay target -- see module docstring. Populated once at creation time,
    #: never re-derived at replay time.
    entry_point: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    app_box: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    nanobar_type: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    source_info_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)
    created_by: Mapped[str] = mapped_column(String, nullable=False)

    @property
    def source(self) -> dict[str, Any]:
        return self.source_json

    @source.setter
    def source(self, value: dict[str, Any]) -> None:
        self.source_json = value

    @property
    def request(self) -> dict[str, Any]:
        return self.request_json

    @request.setter
    def request(self, value: dict[str, Any]) -> None:
        self.request_json = value

    @property
    def response(self) -> dict[str, Any]:
        return self.response_json

    @response.setter
    def response(self, value: dict[str, Any]) -> None:
        self.response_json = value

    @property
    def trace_refs(self) -> list[dict[str, Any]]:
        return self.trace_refs_json

    @trace_refs.setter
    def trace_refs(self, value: list[dict[str, Any]]) -> None:
        self.trace_refs_json = value

    @property
    def source_info(self) -> dict[str, Any] | None:
        return self.source_info_json

    @source_info.setter
    def source_info(self, value: dict[str, Any] | None) -> None:
        self.source_info_json = value


class BrickReviewStatus(Base):
    __tablename__ = "regression_brick_review_status"
    __table_args__ = (CheckConstraint(f"status IN {REVIEW_STATUSES!r}"),)

    regression_brick_id: Mapped[str] = mapped_column(
        ForeignKey("regression_bricks.regression_brick_id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="new")
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)
    updated_by: Mapped[str] = mapped_column(String, nullable=False)


class BrickScenario(Base):
    __tablename__ = "regression_brick_scenario"

    regression_brick_id: Mapped[str] = mapped_column(
        ForeignKey("regression_bricks.regression_brick_id", ondelete="CASCADE"), primary_key=True
    )
    regression_scenario_label: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)
    updated_by: Mapped[str] = mapped_column(String, nullable=False)


class BrickTag(Base):
    __tablename__ = "regression_brick_tags"
    __table_args__ = (UniqueConstraint("regression_brick_id", "tag"),)

    regression_brick_id: Mapped[str] = mapped_column(
        ForeignKey("regression_bricks.regression_brick_id", ondelete="CASCADE"), primary_key=True
    )
    tag: Mapped[str] = mapped_column(String, primary_key=True)


class BrickState(Base):
    """One-to-one current-state record for a `RegressionBrick` -- no row means active/no state
    recorded yet. See module docstring for the FK-enforcement and `data_json` design reasoning.
    """

    __tablename__ = "regression_brick_state"

    regression_brick_id: Mapped[str] = mapped_column(String, primary_key=True)
    deleted_at: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    deleted_by: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    deletion_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    data_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)
    updated_by: Mapped[str] = mapped_column(String, nullable=False)

    @property
    def data(self) -> dict[str, Any]:
        return self.data_json

    @data.setter
    def data(self, value: dict[str, Any]) -> None:
        self.data_json = value


class BrickLog(Base):
    """Append-only, one-to-many log of notes/events about a `RegressionBrick` over time -- see
    module docstring for why its FK is enforced with `ON DELETE CASCADE`, unlike `BrickState`."""

    __tablename__ = "regression_brick_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    regression_brick_id: Mapped[str] = mapped_column(
        ForeignKey("regression_bricks.regression_brick_id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)
    data_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    @property
    def data(self) -> dict[str, Any]:
        return self.data_json

    @data.setter
    def data(self, value: dict[str, Any]) -> None:
        self.data_json = value


@dataclass(frozen=True)
class BrickReviewStatusValue:
    """Plain-data return shape for `RegressionBrickRepository.get_review_status()` -- mirrors
    the old `bricks.schema.BrickReviewStatus` dataclass, kept distinct from the `BrickReviewStatus`
    ORM row above so callers reading a status get a detached value, not a live-session-bound ORM
    instance they could accidentally mutate outside a transaction."""

    regression_brick_id: str
    status: str
    updated_by: str


@dataclass(frozen=True)
class BrickScenarioValue:
    """Plain-data return shape for `RegressionBrickRepository.get_scenario()` -- same reasoning
    as `BrickReviewStatusValue` above."""

    regression_brick_id: str
    regression_scenario_label: str | None
    description: str | None
    updated_by: str
