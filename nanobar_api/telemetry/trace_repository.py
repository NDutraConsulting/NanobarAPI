"""`TraceRepository` -- `Trace`'s own `NanobarAPIRepository` subclass, replacing
`nanobar_api/eventbus/store.py`'s `list_trace_ids`/`count_trace_ids` raw-SQL `GROUP BY trace_id`
aggregates with real, indexed queries against the `Trace` table (see
`.focusari/telemetry-domain-refactor-plan-with-tasks.md` Decision 2).

**`list_with_unprocessed_spans` is the load-bearing method for Decision 5** ("`TelemetryScannerService`
reads via `Trace` first, to keep the number of rows being queried at a minimum"): it finds which
*traces* have at least one unprocessed `Span` on a given channel -- a small, indexed lookup -- so
the caller only then asks `SpanRepository` for each matching trace's actual spans, rather than
scanning every span up front the way `nanobar_api.eventbus.store.get_unprocessed` did.

**`list_trace_summaries`/`count_trace_summaries`** replace `list_trace_ids`/`count_trace_ids`
for `app/admin/nanobar/api.py`'s `list_traces` route -- Decision 2's own note that
`span_count`/`any_error`/`first_recorded_at_ns`/`last_recorded_at_ns` stay computed, not stored,
and that computing them is "`TraceRepository`'s job" (not built until this route needed it).
Ported near-verbatim from the old `_trace_where`/`list_trace_ids`, querying the joined `spans`
table (still the source of `channel`/`nanobar_type`/component-name filtering -- a `Trace` row
itself carries none of those) instead of the old flat `events` table.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.orm import Query

from nanobar_api.eventbus.events import TraceSummary
from nanobar_api.eventbus.store import component_span_name
from nanobar_api.framework.nanobar_api_repository import NanobarAPIRepository
from nanobar_api.telemetry.model import SourceActivityInfo, Span, Trace


def _component_span_name(component: str) -> str | None:
    """`"kind:name"` -> the exact span name that would classify as it -- inverse of
    `nanobar_api.eventbus.store.derive_component`, via that module's own
    `component_span_name(kind, name)`. Splits `component` the same way that module's private
    `_split_component` does, without importing a `_`-prefixed cross-module function."""
    kind, _, name = component.partition(":")
    return component_span_name(kind, name)


class TraceRepository(NanobarAPIRepository):
    def cache_key(self, *args: Any, **kwargs: Any) -> str:
        return f"trace:{args[0]}" if args else "trace:all"

    def create(self, trace: Trace) -> Trace:
        self.session.add(trace)
        self.session.commit()
        self.session.refresh(trace)
        return trace

    def get(self, trace_id: str) -> Trace | None:
        cached = self.get_cached(trace_id)
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        trace = self.session.get(Trace, trace_id)
        if trace is not None:
            self.set_cached(trace, trace_id)
        return trace

    def get_or_create(
        self,
        trace_id: str,
        *,
        entry_point: str,
        app_box: str | None = None,
        source_activity_info: SourceActivityInfo | None = None,
    ) -> tuple[Trace, bool]:
        """Idempotent get-or-create keyed by `trace_id` -- a `Trace` row is created once, on the
        first `Span` ever seen for a new `trace_id` (Decision 2's "a trace is created from an
        entry_point": the entry_point's first observed span is what brings the row into
        existence). **Not** protected by an atomic-claim transaction the way
        `NanobarRepository.get_or_create_by_route_key` is -- unlike that method (called from many
        concurrent HTTP requests), this is only ever called from the single, sequential
        `telemetry_drain_worker.py` (Decision 4's "one designated worker"), so no concurrent call
        on this engine can ever interleave with this select-then-insert in the first place; see
        `nanobar_api/telemetry/persistence.py`'s own module docstring for why installing that
        protection here anyway would cost more than it buys.

        `app_box`/`source_activity_info`, when given, are stamped on a newly-created row only --
        an existing trace's values are left untouched, matching
        `get_or_create_by_route_key`'s own "placeholder metadata only applies at creation" rule.
        """
        existing = self.session.get(Trace, trace_id)
        if existing is not None:
            return existing, False

        trace = Trace(trace_id=trace_id, entry_point=entry_point, app_box=app_box)
        if source_activity_info is not None:
            trace.source_activity_info = source_activity_info
        self.session.add(trace)
        self.session.commit()
        self.session.refresh(trace)
        return trace, True

    def _filtered(
        self,
        *,
        entry_point: str | None,
        app_box: str | None,
        created_after: str | None,
        created_before: str | None,
    ) -> Query[Trace]:
        query = self.session.query(Trace)
        if entry_point is not None:
            query = query.filter(Trace.entry_point == entry_point)
        if app_box is not None:
            query = query.filter(Trace.app_box == app_box)
        if created_after is not None:
            query = query.filter(Trace.created_at >= created_after)
        if created_before is not None:
            query = query.filter(Trace.created_at <= created_before)
        return query

    def list_traces(
        self,
        *,
        entry_point: str | None = None,
        app_box: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> list[Trace]:
        query = self._filtered(
            entry_point=entry_point, app_box=app_box, created_after=created_after, created_before=created_before
        )
        return list(query.order_by(Trace.created_at.desc()).limit(page_size).offset((page - 1) * page_size).all())

    def count_traces(
        self,
        *,
        entry_point: str | None = None,
        app_box: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
    ) -> int:
        return self._filtered(
            entry_point=entry_point, app_box=app_box, created_after=created_after, created_before=created_before
        ).count()

    def list_with_unprocessed_spans(self, channel: str, limit: int = 100) -> list[Trace]:
        """Traces with at least one unprocessed (`processed_at IS NULL`) `Span` on `channel`,
        oldest-unprocessed-span first -- mirrors `get_unprocessed`'s own `ORDER BY
        recorded_at_ns`, just grouped up to the trace level first."""
        unprocessed = (
            self.session.query(Span.trace_id, func.min(Span.recorded_at_ns).label("first_unprocessed_ns"))
            .filter(Span.channel == channel, Span.processed_at.is_(None))
            .group_by(Span.trace_id)
            .order_by(func.min(Span.recorded_at_ns))
            .limit(limit)
            .subquery()
        )
        return list(
            self.session.query(Trace)
            .join(unprocessed, Trace.trace_id == unprocessed.c.trace_id)
            .order_by(unprocessed.c.first_unprocessed_ns)
            .all()
        )

    def _trace_summary_where(
        self,
        channel: str,
        created_after_ns: int | None,
        created_before_ns: int | None,
        nanobar_types: Sequence[str] | None,
        components: Sequence[str] | None,
    ) -> tuple[str, dict[str, Any]]:
        date_clauses: list[str] = []
        params: dict[str, Any] = {"channel": channel}
        if created_after_ns is not None:
            date_clauses.append("recorded_at_ns >= :created_after_ns")
            params["created_after_ns"] = created_after_ns
        if created_before_ns is not None:
            date_clauses.append("recorded_at_ns <= :created_before_ns")
            params["created_before_ns"] = created_before_ns
        date_sql = "".join(f" AND {clause}" for clause in date_clauses)

        clauses = ["channel = :channel", *date_clauses]

        if nanobar_types:
            placeholders = ", ".join(f":nanobar_type_{i}" for i in range(len(nanobar_types)))
            clauses.append(
                f"trace_id IN (SELECT trace_id FROM spans WHERE channel = :channel{date_sql} "
                f"AND json_extract(payload_json, '$.nanobar_type') IN ({placeholders}))"
            )
            for i, value in enumerate(nanobar_types):
                params[f"nanobar_type_{i}"] = value

        if components:
            span_names = [span_name for value in components if (span_name := _component_span_name(value)) is not None]
            if span_names:
                placeholders = ", ".join(f":component_name_{i}" for i in range(len(span_names)))
                clauses.append(
                    f"trace_id IN (SELECT trace_id FROM spans WHERE channel = :channel{date_sql} "
                    f"AND json_extract(payload_json, '$.name') IN ({placeholders}))"
                )
                for i, value in enumerate(span_names):
                    params[f"component_name_{i}"] = value

        return "WHERE " + " AND ".join(clauses), params

    def list_trace_summaries(
        self,
        channel: str,
        *,
        page: int = 1,
        page_size: int = 100,
        created_after_ns: int | None = None,
        created_before_ns: int | None = None,
        nanobar_types: Sequence[str] | None = None,
        components: Sequence[str] | None = None,
    ) -> list[TraceSummary]:
        where, params = self._trace_summary_where(
            channel, created_after_ns, created_before_ns, nanobar_types, components
        )
        params["limit"] = page_size
        params["offset"] = (page - 1) * page_size
        rows = self.session.execute(
            text(
                f"""
                SELECT
                    trace_id,
                    COUNT(*) AS span_count,
                    MIN(recorded_at_ns) AS first_recorded_at_ns,
                    MAX(recorded_at_ns) AS last_recorded_at_ns,
                    MAX(CASE WHEN json_extract(payload_json, '$.error') THEN 1 ELSE 0 END) AS any_error
                FROM spans
                {where}
                GROUP BY trace_id
                ORDER BY last_recorded_at_ns DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        ).all()
        return [
            TraceSummary(
                trace_id=row.trace_id,
                span_count=row.span_count,
                first_recorded_at_ns=row.first_recorded_at_ns,
                last_recorded_at_ns=row.last_recorded_at_ns,
                any_error=bool(row.any_error),
            )
            for row in rows
        ]

    def count_trace_summaries(
        self,
        channel: str,
        *,
        created_after_ns: int | None = None,
        created_before_ns: int | None = None,
        nanobar_types: Sequence[str] | None = None,
        components: Sequence[str] | None = None,
    ) -> int:
        where, params = self._trace_summary_where(
            channel, created_after_ns, created_before_ns, nanobar_types, components
        )
        row = self.session.execute(text(f"SELECT COUNT(DISTINCT trace_id) FROM spans {where}"), params).one()
        return int(row[0])
