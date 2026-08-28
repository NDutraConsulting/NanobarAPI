from __future__ import annotations

from pathlib import Path

from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from nanobar_api.eventbus.events import Event
from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.middleware.snapshot import SnapshotMiddleware
from nanobar_api.middleware.trace import EventBusTraceMiddleware
from nanobar_api.persistence import build_session_factory
from nanobar_api.regression_brick.model import RegressionBrick
from nanobar_api.regression_brick.repository import RegressionBrickRepository
from nanobar_api.telemetry.model import Span
from nanobar_api.telemetry.persistence import build_session_factory as build_telemetry_session_factory
from nanobar_api.telemetry.span_repository import SpanRepository
from nanobar_api.telemetry.telemetry_scanner_service import ScanTracesRequest, TelemetryScannerService
from nanobar_api.telemetry.trace_repository import TraceRepository

if isinstance(otel_trace.get_tracer_provider(), otel_trace.NoOpTracerProvider | otel_trace.ProxyTracerProvider):
    otel_trace.set_tracer_provider(TracerProvider())


async def _get_items(request: Request) -> JSONResponse:
    return JSONResponse({"status": "success", "msg": "", "result": {"type": "array", "data": [{"id": 1}]}})


async def _get_item(request: Request) -> JSONResponse:
    # Distinct response body per call (via the path param) -- generate_bricks() dedupes by
    # content-hash, so identical repeated captures would collapse into a single brick.
    item_id = request.path_params["item_id"]
    return JSONResponse({"status": "success", "msg": "", "result": {"type": "object", "data": {"id": item_id}}})


def _capture_repository(*channels: str) -> EventQueueRepository:
    return EventQueueRepository([ChannelConfig(name=c) for c in channels or ("snapshot",)])


def _build_app(repository: EventQueueRepository) -> Starlette:
    return Starlette(
        routes=[Route("/items", _get_items), Route("/items/{item_id}", _get_item)],
        middleware=[
            Middleware(EventBusTraceMiddleware, repository=repository, channel="trace"),
            Middleware(SnapshotMiddleware, repository=repository, channel="snapshot"),
        ],
    )


def _capture_one_snapshot_event(client: TestClient, repository: EventQueueRepository) -> Event:
    client.get("/items")
    event = repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None
    return event


def _ingest_event_as_span(event: Event, trace_repository: TraceRepository, span_repository: SpanRepository) -> None:
    """Test-only stand-in for what `TelemetryDrainWorker` does for real in production -- writes
    a captured `Event` straight into `Trace`/`Span` rows, skipping the
    validator_gate/controller/service ceremony these tests aren't exercising."""
    assert event.trace_id is not None
    assert event.span_id is not None
    trace_repository.get_or_create(event.trace_id, entry_point=event.payload.get("name") or "unknown")
    span_repository.create(
        Span(
            event_id=event.event_id,
            span_id=event.span_id,
            trace_id=event.trace_id,
            channel=event.channel,
            recorded_at_ns=event.recorded_at_ns,
            monotonic_ns=event.monotonic_ns,
            payload_json=event.payload,
        )
    )


def _brick_repository(tmp_path: Path) -> RegressionBrickRepository:
    session = build_session_factory(
        str(tmp_path / "bricks.db"), repository=EventQueueRepository([ChannelConfig(name="snapshot")])
    )()
    return RegressionBrickRepository(session)


def _telemetry_repositories(tmp_path: Path, name: str = "telemetry.db") -> tuple[TraceRepository, SpanRepository]:
    session = build_telemetry_session_factory(str(tmp_path / name))()
    return TraceRepository(session), SpanRepository(session)


def test_scan_traces_creates_a_brick_from_a_real_snapshot_event(tmp_path: Path) -> None:
    capture_repository = _capture_repository("snapshot", "trace")
    client = TestClient(_build_app(capture_repository))
    event = _capture_one_snapshot_event(client, capture_repository)

    trace_repository, span_repository = _telemetry_repositories(tmp_path)
    _ingest_event_as_span(event, trace_repository, span_repository)
    brick_repository = _brick_repository(tmp_path)
    service = TelemetryScannerService(trace_repository, span_repository, brick_repository)

    result = service(ScanTracesRequest(channel="snapshot"))

    assert result.status == "success"
    assert len(result.result.data) == 1
    brick = brick_repository.get(result.result.data[0])
    assert brick is not None
    assert brick.content_hash == f"sha256:{event.payload['content_hash']}"
    assert brick.span_id == event.span_id


def test_scan_traces_with_nothing_pending_creates_no_bricks(tmp_path: Path) -> None:
    trace_repository, span_repository = _telemetry_repositories(tmp_path)
    brick_repository = _brick_repository(tmp_path)
    service = TelemetryScannerService(trace_repository, span_repository, brick_repository)

    result = service(ScanTracesRequest(channel="snapshot"))

    assert result.status == "success"
    assert result.result.data == []
    assert "0 new brick" in result.result.msg_summary


def test_scan_traces_respects_channel_and_limit(tmp_path: Path) -> None:
    capture_repository = _capture_repository("custom", "trace")
    app = Starlette(
        routes=[Route("/items", _get_items)],
        middleware=[
            Middleware(EventBusTraceMiddleware, repository=capture_repository, channel="trace"),
            Middleware(SnapshotMiddleware, repository=capture_repository, channel="custom"),
        ],
    )
    client = TestClient(app)
    client.get("/items")
    event = capture_repository.get_any(["custom"], timeout=1.0)
    assert event is not None

    trace_repository, span_repository = _telemetry_repositories(tmp_path)
    _ingest_event_as_span(event, trace_repository, span_repository)
    brick_repository = _brick_repository(tmp_path)
    service = TelemetryScannerService(trace_repository, span_repository, brick_repository)

    # Default channel ("snapshot") sees nothing on the "custom" channel.
    default_result = service(ScanTracesRequest())
    assert default_result.result.data == []

    custom_result = service(ScanTracesRequest(channel="custom", limit=1))
    assert len(custom_result.result.data) == 1


def test_scan_traces_drains_more_than_one_batch(tmp_path: Path) -> None:
    """`limit` is a per-batch size, not a total cap -- with 5 distinct pending events and a
    batch size of 2, `__call__()` must loop internally (3 batches: 2 + 2 + 1) rather than
    stopping after the first batch."""
    capture_repository = _capture_repository("snapshot", "trace")
    client = TestClient(_build_app(capture_repository))
    trace_repository, span_repository = _telemetry_repositories(tmp_path)
    for item_id in range(5):
        client.get(f"/items/{item_id}")
        event = capture_repository.get_any(["snapshot"], timeout=1.0)
        assert event is not None
        _ingest_event_as_span(event, trace_repository, span_repository)

    brick_repository = _brick_repository(tmp_path)
    service = TelemetryScannerService(trace_repository, span_repository, brick_repository)

    result = service(ScanTracesRequest(channel="snapshot", limit=2))

    assert len(result.result.data) == 5


def test_scan_traces_does_not_capture_itself(tmp_path: Path) -> None:
    """Regression test for the self-capture-loop bug class `IngestSpanService` already avoided
    (see `telemetry_service.py`'s own docstring) -- `TelemetryScannerService` is a plain class,
    not a `NanobarAPIService` subclass, specifically so running a scan never produces a new
    "service-request-response" event of its own that a later scan would re-discover and turn
    into a permanent, ever-growing "meta-brick" about its own prior invocation. Two scans in a
    row against an empty backlog must still create exactly zero bricks."""
    trace_repository, span_repository = _telemetry_repositories(tmp_path)
    brick_repository = _brick_repository(tmp_path)
    service = TelemetryScannerService(trace_repository, span_repository, brick_repository)

    service(ScanTracesRequest())
    service(ScanTracesRequest())

    assert brick_repository.session.query(RegressionBrick).count() == 0
