"""`TelemetryService` -- the telemetry domain's own service layer, per
`.focusari/telemetry-domain-refactor-plan-with-tasks.md` Decisions 2/3.

**`IngestSpanService` deliberately does NOT extend `NanobarAPIService`.** Every other real
service in this codebase inherits `NanobarAPIService.__call__`'s unconditional self-
instrumentation -- a `telemetry.span("service", ...)` + `capture_layer(..., channel="snapshot")`
on every call -- correct there, wrong here: this service is invoked *by* the telemetry drain
worker (`telemetry_drain_worker.py`, Phase 5) as it drains the very channels
(`"trace"`/`"snapshot"`) those two calls would themselves write to. Extending the base class
would mean every ingested span recursively produces two more events destined to be ingested as
two more spans each -- unbounded, compounding growth, confirmed live before this design existed
(constructing the earlier, `NanobarAPIService`-extending version with a `NanobarTelemetry` too
narrow to swallow its own self-capture raised `KeyError` on the very first real call). "It should
not capture itself" (the user's own words) -- resolved here by simply not participating in the
capture-producing base class at all, rather than routing its self-instrumentation to a no-op
sink. `ServiceResult`/`ServiceResultBody` (from `nanobar_api.framework.nanobar_api_service`) are
still reused as the return shape -- they're plain dataclasses, not coupled to the ABC -- so
`TelemetryController` doesn't need two different result shapes for this one domain.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from nanobar_api.framework.nanobar_api_service import ServiceResult, ServiceResultBody
from nanobar_api.telemetry.model import SourceActivityInfo, Span
from nanobar_api.telemetry.span_repository import SpanRepository
from nanobar_api.telemetry.trace_repository import TraceRepository


@dataclass(frozen=True)
class IngestSpanRequest:
    """Already-known, already-derived fields -- `entry_point`/`app_box` derivation (Decision 2's
    `f"{method} {path}"`/`f"worker-{channel}"` conventions) is the drain worker's own job (Phase
    5), not this service's: this dataclass is the *shape* a caller must hand over, not a
    computation this layer performs on a raw `Event`.

    `event_id` identifies *this one captured observation* and is `Span`'s real primary key;
    `span_id` is a real, indexed, but **not unique** correlation column -- caught live via
    `tests/test_thin_slice_proof.py`: `EventBusTraceMiddleware` and `SnapshotMiddleware` both
    capture the *same* span under the *same* `span_id` with different payloads, so keying
    ingestion off `span_id` alone silently dropped whichever payload lost the race. See
    `nanobar_api/telemetry/model.py`'s `Span` docstring for the full story.
    """

    event_id: str
    trace_id: str
    span_id: str
    channel: str
    recorded_at_ns: int
    monotonic_ns: int
    payload: dict[str, Any]
    entry_point: str
    app_box: str | None = None
    source_activity_info: SourceActivityInfo | None = None


class IngestSpanService:
    def __init__(self, session: Session) -> None:
        self.traces = TraceRepository(session)
        self.spans = SpanRepository(session)

    def __call__(self, request: IngestSpanRequest) -> ServiceResult:
        """Idempotent on `event_id` -- a re-delivered event (the drain worker retrying after a
        crash between claiming and acking) is a no-op, not a duplicate-key error."""
        existing = self.spans.get(request.event_id)
        if existing is not None:
            return ServiceResult(
                status="success",
                result=ServiceResultBody(
                    type="object",
                    data={"trace_id": existing.trace_id, "event_id": existing.event_id, "trace_created": False},
                    msg_summary="span already ingested (idempotent no-op)",
                ),
            )

        trace, trace_created = self.traces.get_or_create(
            request.trace_id,
            entry_point=request.entry_point,
            app_box=request.app_box,
            source_activity_info=request.source_activity_info,
        )
        span = self.spans.create(
            Span(
                event_id=request.event_id,
                span_id=request.span_id,
                trace_id=request.trace_id,
                channel=request.channel,
                recorded_at_ns=request.recorded_at_ns,
                monotonic_ns=request.monotonic_ns,
                payload_json=request.payload,
            )
        )
        return ServiceResult(
            status="success",
            result=ServiceResultBody(
                type="object",
                data={"trace_id": trace.trace_id, "event_id": span.event_id, "trace_created": trace_created},
                msg_summary="span ingested",
            ),
        )
