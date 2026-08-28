"""`TelemetryScannerService` -- `RegressionBrick`-side half of trace scanning, relocated and
renamed from `nanobar_api/regression_brick/trace_scanner_service.py`'s `TraceScannerService` per
`.focusari/telemetry-domain-refactor-plan-with-tasks.md` Decision 5. Drains unprocessed spans off
an eventbus channel (default `"snapshot"`) via `TraceRepository`/`SpanRepository` (not a raw
`sqlite3.Connection` against `events.db` anymore), dedupes by content-hash, creates new
`RegressionBrick` rows -- wraps `nanobar_api/bricks/generate.py`'s existing `generate_bricks()`
pure logic.

**Plain class, not a `NanobarAPIService` subclass.** Same reasoning as `telemetry_service.py`'s
`IngestSpanService` ("it should not capture itself") applies here too, in a slower-growing but
real form: `NanobarAPIService.__call__`'s self-instrumentation would capture this service's own
`ScanTracesRequest`/`ServiceResult` as a `"service-request-response"` event. Because a
`ServiceResult`'s `data` (the list of newly-created brick ids) is different on every real call,
that self-capture would never content-hash-dedupe against a prior run the way a truly repeated
capture would -- every scan invocation would permanently create one more brick *about* its own
previous scan, forever, even once there's genuinely nothing left to find. Not hypothetical, same
category of bug the ingest pipeline already had; avoided the same way, by not extending the
capture-producing base class at all.

**Deliberately does not bind a new brick to a `Nanobar`.** Per the refactor plan's Decision 4
(this project's own architecture rule: "services never call other services or controllers"),
`RegressionBrick`'s own service does only `RegressionBrick`-side work -- creating rows. Binding
each new brick to a `Nanobar` (`bricks/binding.py`) is controller-level orchestration
(`app/admin/nanobar/generate_bricks.py`'s `generate_dashboard_bricks()`), not this service's job.

**Drains the whole backlog, not just one `generate_bricks()` batch.** A live dashboard can
accumulate thousands of unprocessed spans between scans -- processing a single batch would
silently leave most of the backlog unidentified. Loops `generate_bricks()` batch-by-batch until
fewer than `request.limit` candidate traces remain.
"""

from __future__ import annotations

from dataclasses import dataclass

from nanobar_api.bricks.generate import generate_bricks
from nanobar_api.framework.nanobar_api_service import ServiceResult, ServiceResultBody
from nanobar_api.regression_brick.model import RegressionBrick
from nanobar_api.regression_brick.repository import RegressionBrickRepository
from nanobar_api.telemetry.span_repository import SpanRepository
from nanobar_api.telemetry.trace_repository import TraceRepository

#: Larger than `generate_bricks()`'s own single-call default (100) -- fewer round trips while
#: draining a potentially large backlog. Matches the old `app/admin/nanobar/generate_bricks.py`'s
#: own `_BATCH_SIZE`.
_DEFAULT_LIMIT = 500


@dataclass
class ScanTracesRequest:
    channel: str = "snapshot"
    schema_version: str = "1.0"
    created_by: str = "nanobarapi"
    capture_policy_id: str | None = "default-v1"
    limit: int = _DEFAULT_LIMIT


class TelemetryScannerService:
    def __init__(
        self,
        trace_repository: TraceRepository,
        span_repository: SpanRepository,
        brick_repository: RegressionBrickRepository,
    ) -> None:
        self.trace_repository = trace_repository
        self.span_repository = span_repository
        self.brick_repository = brick_repository

    def __call__(self, request: ScanTracesRequest) -> ServiceResult:
        all_new_bricks: list[RegressionBrick] = []
        while True:
            candidate_traces = self.trace_repository.list_with_unprocessed_spans(request.channel, request.limit)
            if not candidate_traces:
                break
            all_new_bricks.extend(
                generate_bricks(
                    self.trace_repository,
                    self.span_repository,
                    self.brick_repository,
                    channel=request.channel,
                    schema_version=request.schema_version,
                    created_by=request.created_by,
                    capture_policy_id=request.capture_policy_id,
                    limit=request.limit,
                )
            )
            if len(candidate_traces) < request.limit:
                break
        return ServiceResult(
            status="success",
            result=ServiceResultBody(
                type="array",
                data=[brick.regression_brick_id for brick in all_new_bricks],
                msg_summary=f"{len(all_new_bricks)} new brick(s)",
            ),
        )
