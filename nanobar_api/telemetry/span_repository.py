"""`SpanRepository` -- `Span`'s own `NanobarAPIRepository` subclass, replacing
`nanobar_api/eventbus/store.py`'s `get_unprocessed`/`mark_processed`/`get_events_by_trace_id`/
`find_latest_span_by_nanobar_type`/`get_trace_facets` raw-SQL functions (see
`.focusari/telemetry-domain-refactor-plan-with-tasks.md` Decision 2).

**Keyed by `event_id`, not `span_id`** -- `Span.span_id` is not unique (see `model.py`'s own
docstring for the real collision this caught live: `EventBusTraceMiddleware`/`SnapshotMiddleware`
both capture the same span with different payloads under the same `span_id`), so `get()`/
`mark_processed()` operate on the real primary key.

`distinct_facets` reuses `nanobar_api.eventbus.store.derive_component` (the classification logic
itself is untouched -- span-name conventions don't change with this migration) rather than
duplicating it, and queries the same way the old `get_trace_facets` did (`SELECT DISTINCT
json_extract(...)`, not loading every matching span into Python) since this table is expected to
"grow quickly" per the user's own stated reasoning for this migration.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from nanobar_api.eventbus.store import derive_component
from nanobar_api.framework.nanobar_api_repository import NanobarAPIRepository
from nanobar_api.telemetry.model import Span


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


class SpanRepository(NanobarAPIRepository):
    def cache_key(self, *args: Any, **kwargs: Any) -> str:
        return f"span:{args[0]}" if args else "span:all"

    def create(self, span: Span) -> Span:
        self.session.add(span)
        self.session.commit()
        self.session.refresh(span)
        return span

    def create_many(self, spans: Sequence[Span]) -> list[Span]:
        """One batch insert, not `len(spans)` individual `create()` calls -- the drain worker
        (Phase 5) writes in batches, same shape as `EventThread`'s own `insert_events`."""
        if not spans:
            return []
        self.session.add_all(spans)
        self.session.commit()
        for span in spans:
            self.session.refresh(span)
        return list(spans)

    def get(self, event_id: str) -> Span | None:
        cached = self.get_cached(event_id)
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        span = self.session.get(Span, event_id)
        if span is not None:
            self.set_cached(span, event_id)
        return span

    def list_by_trace_id(self, trace_id: str, channel: str | None = None) -> list[Span]:
        query = self.session.query(Span).filter(Span.trace_id == trace_id)
        if channel is not None:
            query = query.filter(Span.channel == channel)
        return list(query.order_by(Span.monotonic_ns).all())

    def list_unprocessed_for_trace(self, trace_id: str, channel: str, limit: int = 100) -> list[Span]:
        """A `TraceRepository.list_with_unprocessed_spans()` match's own unprocessed spans on
        `channel` -- the second half of Decision 5's two-step scan (traces first, then each
        matching trace's spans)."""
        return list(
            self.session.query(Span)
            .filter(Span.trace_id == trace_id, Span.channel == channel, Span.processed_at.is_(None))
            .order_by(Span.recorded_at_ns)
            .limit(limit)
            .all()
        )

    def mark_processed(self, event_ids: Sequence[str]) -> None:
        if not event_ids:
            return
        self.session.query(Span).filter(Span.event_id.in_(event_ids)).update(
            {"processed_at": _utcnow_iso()}, synchronize_session=False
        )
        self.session.commit()

    def find_latest_by_nanobar_type(self, channel: str, nanobar_type: str) -> Span | None:
        """The most recently recorded span tagged `nanobar_type` on `channel` -- replaces
        `find_latest_span_by_nanobar_type`, same "evidence for a type that can't otherwise be
        classified" use (`admin/nanobar/api.py`'s `nanobar_coverage_gaps`, Phase 6)."""
        return (
            self.session.query(Span)
            .filter(Span.channel == channel)
            .filter(
                text("json_extract(payload_json, '$.nanobar_type') = :nanobar_type").bindparams(
                    nanobar_type=nanobar_type
                )
            )
            .order_by(Span.recorded_at_ns.desc())
            .first()
        )

    def distinct_facets(
        self, channel: str, *, created_after_ns: int | None = None, created_before_ns: int | None = None
    ) -> tuple[list[str], list[str]]:
        """Distinct `nanobar_type` values and distinct `"kind:name"` component tags actually
        present on `channel` (optionally bounded by a recorded-at window) -- same computation
        `get_trace_facets` did over the old raw `events` table, now over `spans`."""
        clauses = ["channel = :channel"]
        params: dict[str, Any] = {"channel": channel}
        if created_after_ns is not None:
            clauses.append("recorded_at_ns >= :created_after_ns")
            params["created_after_ns"] = created_after_ns
        if created_before_ns is not None:
            clauses.append("recorded_at_ns <= :created_before_ns")
            params["created_before_ns"] = created_before_ns
        where = " AND ".join(clauses)

        type_expr = "json_extract(payload_json, '$.nanobar_type')"
        type_rows = self.session.execute(
            text(f"SELECT DISTINCT {type_expr} FROM spans WHERE {where} AND {type_expr} IS NOT NULL"), params
        ).all()
        nanobar_types = sorted(row[0] for row in type_rows)

        name_expr = "json_extract(payload_json, '$.name')"
        name_rows = self.session.execute(
            text(f"SELECT DISTINCT {name_expr} FROM spans WHERE {where} AND {name_expr} IS NOT NULL"), params
        ).all()
        components = sorted({f"{kind}:{name}" for kind, name in (derive_component(row[0]) for row in name_rows)})

        return nanobar_types, components
