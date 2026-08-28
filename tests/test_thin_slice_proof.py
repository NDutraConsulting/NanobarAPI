from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import anyio
import httpx2
import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from sqlalchemy.orm import Session
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

from nanobar_api import NanobarAPI, endpoint_schema, success
from nanobar_api.bricks import RegressionBrick, generate_bricks
from nanobar_api.eventbus import ChannelConfig, EventQueueRepository
from nanobar_api.middleware.snapshot import SnapshotMiddleware
from nanobar_api.middleware.trace import EventBusTraceMiddleware
from nanobar_api.persistence import build_session_factory
from nanobar_api.regression_brick.regression_brick_analysis_service import ReplayBrickRequest, ReplayBrickService
from nanobar_api.regression_brick.repository import RegressionBrickRepository
from nanobar_api.telemetry import NanobarTelemetry
from nanobar_api.telemetry.persistence import build_session_factory as build_telemetry_session_factory
from nanobar_api.telemetry.span_repository import SpanRepository
from nanobar_api.telemetry.telemetry_drain_worker import telemetry_drain_worker_lifespan
from nanobar_api.telemetry.trace_repository import TraceRepository

# A real SDK TracerProvider so EventBusTraceMiddleware produces real (non-NoOp) trace/span ids --
# without one, the captured event carries no trace context at all, and TelemetryDrainWorker would
# drop it (see telemetry_drain_worker.py's skipped_no_trace_context) rather than ingest it.
if isinstance(otel_trace.get_tracer_provider(), otel_trace.NoOpTracerProvider | otel_trace.ProxyTracerProvider):
    otel_trace.set_tracer_provider(TracerProvider())


@dataclass
class Pong:
    message: str


def _inner_app(message: str) -> NanobarAPI:
    @endpoint_schema(response=Pong, summary="Ping")
    async def ping(request: Request) -> Response:
        return JSONResponse(success({"message": message}))

    return NanobarAPI(routes=[Route("/ping", ping, methods=["GET"])], openapi_url=None, docs_url=None)


def _client(app: Starlette) -> httpx2.Client:
    """`TestClient` is itself a real `httpx2.Client` subclass -- see
    `test_regression_brick_analysis_service.py`'s own `_client()` for the full reasoning on why
    that (not `httpx2.ASGITransport`, which is async-only) is the correct in-process stand-in."""
    return TestClient(app)


def _telemetry() -> NanobarTelemetry:
    return NanobarTelemetry(
        EventQueueRepository([ChannelConfig(name="trace"), ChannelConfig(name="snapshot")]), channel="trace"
    )


async def _capture_one_brick(tmp_path: Path) -> tuple[Session, RegressionBrickRepository, str]:
    """Records one real request end-to-end (EventBusTraceMiddleware + SnapshotMiddleware ->
    eventbus -> TelemetryDrainWorker -> nanobar_api_telemetry.db), generates a brick from it, and
    returns (bricks_session, brick_repository, regression_brick_id).
    """
    telemetry_db_path = str(tmp_path / "nanobar_api_telemetry.db")
    bricks_db_path = str(tmp_path / "regression_bricks.db")

    repository = EventQueueRepository([ChannelConfig(name="trace"), ChannelConfig(name="snapshot")])
    inner = _inner_app("pong")
    recorder = EventBusTraceMiddleware(SnapshotMiddleware(inner, repository, channel="snapshot"), repository)

    bricks_session = build_session_factory(
        bricks_db_path, repository=EventQueueRepository([ChannelConfig(name="snapshot")])
    )()
    brick_repository = RegressionBrickRepository(bricks_session)
    # A separate session from the worker's own -- SQLAlchemy `Session`s aren't safe to share
    # across threads, and `TelemetryDrainWorker` runs in its own background thread.
    telemetry_session_factory = build_telemetry_session_factory(telemetry_db_path)
    telemetry_session = telemetry_session_factory()
    trace_repository = TraceRepository(telemetry_session)
    span_repository = SpanRepository(telemetry_session)

    async with telemetry_drain_worker_lifespan(["trace", "snapshot"], repository, telemetry_session_factory):
        client = TestClient(recorder)
        response = client.get("/ping")
        assert response.status_code == 200

        deadline = time.monotonic() + 5.0
        bricks: list[RegressionBrick] = []
        while time.monotonic() < deadline and not bricks:
            # `telemetry_session` is long-lived across this whole loop; without ending its
            # current transaction, it never observes the drain worker's own (separate-session,
            # separate-thread) commits -- a stale read snapshot, not a real absence of data.
            telemetry_session.rollback()
            bricks = generate_bricks(trace_repository, span_repository, brick_repository)
            if not bricks:
                await anyio.sleep(0.05)

    assert len(bricks) == 1, "expected exactly one brick generated from the one captured request"
    return bricks_session, brick_repository, bricks[0].regression_brick_id


@pytest.mark.anyio
async def test_thin_slice_replay_against_unchanged_app_passes(tmp_path: Path) -> None:
    """The checkpoint's first half: no false positive on an unchanged endpoint."""
    bricks_session, brick_repository, brick_id = await _capture_one_brick(tmp_path)
    try:
        brick = brick_repository.get(brick_id)
        assert brick is not None

        unchanged_app = _inner_app("pong")
        service = ReplayBrickService(_telemetry(), brick_repository, _client(unchanged_app))
        result = service(ReplayBrickRequest(regression_brick_id=brick_id))
        verdict = result.result.data["verdict"]

        assert verdict["overall_passed"] is True
        assert verdict["diffs"] == []
    finally:
        bricks_session.close()


@pytest.mark.anyio
async def test_thin_slice_replay_against_regressed_app_fails(tmp_path: Path) -> None:
    """The checkpoint's second half: a real regression is actually caught."""
    bricks_session, brick_repository, brick_id = await _capture_one_brick(tmp_path)
    try:
        brick = brick_repository.get(brick_id)
        assert brick is not None

        regressed_app = _inner_app("BROKEN")  # the endpoint now returns something different
        service = ReplayBrickService(_telemetry(), brick_repository, _client(regressed_app))
        result = service(ReplayBrickRequest(regression_brick_id=brick_id))
        verdict = result.result.data["verdict"]

        assert verdict["overall_passed"] is False
        assert any("message" in d for d in verdict["diffs"])
    finally:
        bricks_session.close()


@pytest.mark.anyio
async def test_thin_slice_replay_against_status_regression_reports_the_status_code_diff(tmp_path: Path) -> None:
    """A regression that changes the status code is caught -- reported as one more diff entry,
    not a separate gated layer."""
    bricks_session, brick_repository, brick_id = await _capture_one_brick(tmp_path)
    try:
        brick = brick_repository.get(brick_id)
        assert brick is not None

        async def broken_ping(request: Request) -> Response:
            return JSONResponse({"error": "boom"}, status_code=500)

        broken_app = NanobarAPI(routes=[Route("/ping", broken_ping, methods=["GET"])], openapi_url=None, docs_url=None)
        service = ReplayBrickService(_telemetry(), brick_repository, _client(broken_app))
        result = service(ReplayBrickRequest(regression_brick_id=brick_id))
        verdict = result.result.data["verdict"]

        assert verdict["overall_passed"] is False
        assert any("status_code" in d and "200" in d and "500" in d for d in verdict["diffs"])
    finally:
        bricks_session.close()
