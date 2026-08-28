"""`Trace`/`Span` -- real, persisted SQLAlchemy ORM rows for trace/span capture, replacing
`nanobar_api/eventbus/store.py`'s raw-`sqlite3` `events` table plus its `GROUP BY trace_id`
computed aggregates (`list_trace_ids`/`get_trace_facets`/`TraceSummary`). See
`.focusari/telemetry-domain-refactor-plan-with-tasks.md` Decision 2.

**`Trace` is created once, when the first `Span` for a new `trace_id` is written** -- a trace "is
created from an entry_point" in the sense that the entry_point's first observed span brings the
row into existence, not a deterministic function of the entry_point string itself. Filtering by
`entry_point`/`app_box` is the whole reason `Trace` is a real table rather than a `GROUP BY` view
(Decision 2's confirmed reasoning: these tables "grow quickly"), so both columns are indexed.

`span_count`/`any_error`/`first_recorded_at_ns`/`last_recorded_at_ns` (today's `TraceSummary`
fields) are deliberately **not** stored here -- once a query has already narrowed to one known
`trace_id` via this table (the actual performance problem being solved), a
`SELECT ... WHERE trace_id = ?` aggregate over `Span` is cheap; upserting these on every single
span write would be needless write-path complexity for a problem the `Trace` row alone already
solves. Computing them is `TraceRepository`'s job (Phase 3), not this module's.

`SourceActivityInfo` stays a plain frozen dataclass, not its own table -- same
JSON-column-plus-property-wrapper pattern as `nanobar/model.py`'s `MonitorTargetRef`:
`source_activity_info_json` is the real, nullable `JSON` column ("we won't always get
`source_activity_info`"), `source_activity_info` a Python-level convenience property. Its
population mechanism ("webhook wrappers with proper annotations for keeping track of exposed
operating surfaces") is explicitly future work, not built this pass -- the column exists and is
nullable so nothing downstream breaks before that mechanism exists.

`app_box` is nullable, independent of whether `.focusari/appbox-plan-with-tasks.md` has landed --
that plan is **additive** (a new `app_box` field alongside `Nanobar.domain`, not a rename of it;
nothing built yet as of 2026-08-27), and populating `Trace.app_box` the same way is this plan's
own, not-yet-built ingestion-time work (Phase 4/5), not blocked by that plan's own timeline.

`Span` mirrors today's `events` table, minus the claim-lease columns (`attempt_count`/
`claimed_by`/`lease_expires_at`/`last_error`) -- those served `NanobarWorker`'s concurrent-claim
pattern, which nothing in this design needs (the single `telemetry_drain_worker.py` is the only
writer; `TelemetryScannerService` reads, it doesn't compete to claim). `processed_at` is kept as
a simple "already turned into a brick" marker.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from nanobar_api.telemetry.persistence import Base


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class SourceActivityInfo:
    """Maps a `Trace` back to whatever externally triggered it -- a UI element, cron job, stored
    procedure, or external service -- when that's known. `security_info` is a free-form list of
    the security mechanisms observed on the triggering surface (e.g. `["jwt", "csrf", "ssh"]`),
    not a fixed enum -- new mechanisms shouldn't require a schema change.
    """

    source_name: str
    source_url: str
    created_at: str
    created_by: str
    security_info: list[str] = field(default_factory=list)
    security_checked: bool = False


class Trace(Base):
    __tablename__ = "traces"

    trace_id: Mapped[str] = mapped_column(String, primary_key=True)
    entry_point: Mapped[str] = mapped_column(String, nullable=False, index=True)
    app_box: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    source_activity_info_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)

    spans: Mapped[list[Span]] = relationship(back_populates="trace", cascade="all, delete-orphan")

    @property
    def source_activity_info(self) -> SourceActivityInfo | None:
        if self.source_activity_info_json is None:
            return None
        return SourceActivityInfo(**self.source_activity_info_json)

    @source_activity_info.setter
    def source_activity_info(self, value: SourceActivityInfo | None) -> None:
        self.source_activity_info_json = dataclasses.asdict(value) if value is not None else None


class Span(Base):
    """**`event_id` is the primary key, not `span_id`.** Caught live (a real, integration-level
    bug, not a hypothetical): `EventBusTraceMiddleware` and `SnapshotMiddleware` both capture the
    *same* HTTP request's *same* OTel span, from two different observability layers -- a
    trace-completion event on the `"trace"` channel and a request/response snapshot on the
    `"snapshot"` channel -- and both carry the *identical* `span_id` (they're describing the same
    span), but genuinely different payloads. Making `span_id` the primary key meant the second of
    the two to be ingested silently no-opped as "already ingested" (`IngestSpanService`'s own
    idempotency check), permanently losing whichever payload lost the race -- confirmed live via
    `tests/test_thin_slice_proof.py` failing to find a brick even though ingestion reported zero
    failures. `event_id` (mirroring the old `events` table's own primary key) uniquely identifies
    *one captured observation*; `span_id` stays a real, indexed, but **not unique** correlation
    column -- exactly the old schema's actual shape, which this table's first version collapsed
    into one column without recognizing the semantic difference.
    """

    __tablename__ = "spans"

    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    trace_id: Mapped[str] = mapped_column(ForeignKey("traces.trace_id"), nullable=False, index=True)
    span_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String, nullable=False)
    recorded_at_ns: Mapped[int] = mapped_column(nullable=False)
    monotonic_ns: Mapped[int] = mapped_column(nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    #: `None` means "not yet turned into a `RegressionBrick`" -- set by whatever scans this
    #: table (`TelemetryScannerService`, Phase 4/7), mirroring the old `events.processed_at`.
    processed_at: Mapped[str | None] = mapped_column(String, nullable=True, default=None)

    trace: Mapped[Trace] = relationship(back_populates="spans")
