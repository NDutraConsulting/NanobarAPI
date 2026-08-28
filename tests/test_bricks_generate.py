from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from nanobar_api.bricks.generate import _classify_capture_layer_scenario, generate_bricks
from nanobar_api.capture.layer_capture import capture_layer
from nanobar_api.eventbus.events import Event
from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.middleware.snapshot import SnapshotMiddleware
from nanobar_api.middleware.trace import EventBusTraceMiddleware
from nanobar_api.persistence import build_session_factory
from nanobar_api.regression_brick.repository import RegressionBrickRepository
from nanobar_api.telemetry.model import Span
from nanobar_api.telemetry.persistence import build_session_factory as build_telemetry_session_factory
from nanobar_api.telemetry.span_repository import SpanRepository
from nanobar_api.telemetry.trace_repository import TraceRepository

# A real SDK TracerProvider so EventBusTraceMiddleware produces real (non-NoOp) trace/span
# ids, which SnapshotMiddleware's emitted event then carries. The OTel API only allows the
# global provider to be set once per process; guard against a second test module in this
# same run already having set one (set_tracer_provider warns but keeps the first on a
# second call, which is fine for these tests' purposes either way).
if isinstance(otel_trace.get_tracer_provider(), otel_trace.NoOpTracerProvider | otel_trace.ProxyTracerProvider):
    otel_trace.set_tracer_provider(TracerProvider())


async def _get_items(request: Request) -> JSONResponse:
    return JSONResponse(
        {"status": "success", "msg": "", "result": {"type": "array", "data": [{"id": 1, "name": "widget"}]}}
    )


async def _echo_body(request: Request) -> JSONResponse:
    body = await request.body()
    return JSONResponse({"received_bytes": len(body)})


def _repository(*channels: str) -> EventQueueRepository:
    return EventQueueRepository([ChannelConfig(name=c) for c in channels or ("snapshot",)])


def _build_app(repository: EventQueueRepository) -> Starlette:
    return Starlette(
        routes=[
            Route("/items", _get_items),
            Route("/echo", _echo_body, methods=["POST"]),
        ],
        middleware=[
            Middleware(EventBusTraceMiddleware, repository=repository, channel="trace"),
            Middleware(SnapshotMiddleware, repository=repository, channel="snapshot"),
        ],
    )


def _capture_one_snapshot_event(
    client: TestClient, repository: EventQueueRepository, **request_kwargs: object
) -> Event:
    method = str(request_kwargs.pop("method", "GET"))
    path = str(request_kwargs.pop("path", "/items"))
    client.request(method, path, **request_kwargs)  # type: ignore[arg-type]
    event = repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None
    return event


def _dbs(tmp_path: Path) -> tuple[TraceRepository, SpanRepository, RegressionBrickRepository]:
    telemetry_session = build_telemetry_session_factory(str(tmp_path / "telemetry.db"))()
    bricks_session = build_session_factory(
        str(tmp_path / "regression_bricks.db"), repository=EventQueueRepository([ChannelConfig(name="snapshot")])
    )()
    return (
        TraceRepository(telemetry_session),
        SpanRepository(telemetry_session),
        RegressionBrickRepository(bricks_session),
    )


def _ingest_events(events: Sequence[Event], trace_repository: TraceRepository, span_repository: SpanRepository) -> None:
    """Test-only stand-in for what `TelemetryDrainWorker` does for real in production -- writes
    each `Event` straight into `Trace`/`Span` rows. Synthesizes a `trace_id`/`span_id` from
    `event_id` for an event that doesn't already carry one (a hand-built payload-shape test
    fixture, or a `capture_layer()` call made outside any active trace context) -- these tests
    exercise `generate_bricks()`'s own payload-handling logic, not trace-context realism, and
    `Span.trace_id`/`.span_id` are non-nullable regardless.
    """
    for event in events:
        trace_id = event.trace_id or f"synthetic-trace-{event.event_id}"
        span_id = event.span_id or f"synthetic-span-{event.event_id}"
        trace_repository.get_or_create(trace_id, entry_point=event.payload.get("name") or event.channel)
        span_repository.create(
            Span(
                event_id=event.event_id,
                span_id=span_id,
                trace_id=trace_id,
                channel=event.channel,
                recorded_at_ns=event.recorded_at_ns,
                monotonic_ns=event.monotonic_ns,
                payload_json=event.payload,
            )
        )


def _unprocessed_span_count(span_repository: SpanRepository, channel: str) -> int:
    return span_repository.session.query(Span).filter(Span.channel == channel, Span.processed_at.is_(None)).count()


def test_generate_bricks_builds_brick_from_real_snapshot_event(tmp_path: Path) -> None:
    repository = _repository("snapshot", "trace")
    client = TestClient(_build_app(repository))
    event = _capture_one_snapshot_event(client, repository, path="/items")

    trace_repository, span_repository, brick_repository = _dbs(tmp_path)
    _ingest_events([event], trace_repository, span_repository)

    bricks = generate_bricks(trace_repository, span_repository, brick_repository, channel="snapshot")

    assert len(bricks) == 1
    brick = bricks[0]
    assert brick.regression_brick_id.startswith("rbrick-")
    assert brick.schema_version == "1.0"
    assert brick.brick_version == 1
    assert brick.created_by == "nanobarapi"
    assert brick.capture_policy_id == "default-v1"
    assert brick.content_hash == f"sha256:{event.payload['content_hash']}"
    assert brick.span_id == event.span_id

    assert brick.request["method"] == "GET"
    assert brick.request["path"] == "/items"
    assert brick.request["payload"] == {}

    assert brick.response["status_code"] == 200
    assert brick.response["payload"] == {
        "status": "success",
        "msg": "",
        "result": {"type": "array", "data": [{"id": 1, "name": "widget"}]},
    }


def test_source_is_honest_and_minimal_not_fabricated(tmp_path: Path) -> None:
    repository = _repository("snapshot", "trace")
    client = TestClient(_build_app(repository))
    event = _capture_one_snapshot_event(client, repository, path="/items")

    trace_repository, span_repository, brick_repository = _dbs(tmp_path)
    _ingest_events([event], trace_repository, span_repository)

    bricks = generate_bricks(trace_repository, span_repository, brick_repository, channel="snapshot")

    brick = bricks[0]
    # Only real, known-at-this-stage fields — no fabricated host/project/file/class/
    # function fields the codebase doesn't actually know at this stage.
    assert set(brick.source.keys()) == {"trace_id", "span_id", "channel"}
    assert brick.source["channel"] == "snapshot"
    # A real SDK TracerProvider is configured above, so EventBusTraceMiddleware
    # actually produced a real trace/span id for this request.
    assert brick.source["trace_id"] == event.trace_id
    assert brick.source["span_id"] == event.span_id
    assert brick.source["trace_id"] is not None


def test_brick_is_self_contained_with_entry_point_and_app_box_from_its_trace(tmp_path: Path) -> None:
    """`entry_point`/`app_box` come straight off the owning `Trace` row this loop already holds
    in scope -- no query against the telemetry db is needed at replay time. See
    `.focusari/2026-08-27-regression-brick-clarification.md` Part 2."""
    repository = _repository("snapshot", "trace")
    client = TestClient(_build_app(repository))
    event = _capture_one_snapshot_event(client, repository, path="/items")

    trace_repository, span_repository, brick_repository = _dbs(tmp_path)
    trace_id = event.trace_id or f"synthetic-trace-{event.event_id}"
    trace_repository.get_or_create(trace_id, entry_point="GET /items", app_box="api")
    span_repository.create(
        Span(
            event_id=event.event_id,
            span_id=event.span_id or f"synthetic-span-{event.event_id}",
            trace_id=trace_id,
            channel=event.channel,
            recorded_at_ns=event.recorded_at_ns,
            monotonic_ns=event.monotonic_ns,
            payload_json=event.payload,
        )
    )

    bricks = generate_bricks(trace_repository, span_repository, brick_repository, channel="snapshot")

    brick = bricks[0]
    assert brick.entry_point == "GET /items"
    assert brick.app_box == "api"
    assert brick.source_info == {"trace_id": event.trace_id, "span_id": event.span_id, "channel": "snapshot"}


def test_capture_layer_produced_brick_gets_nanobar_type_promoted_to_a_column(tmp_path: Path) -> None:
    trace_repository, span_repository, brick_repository = _dbs(tmp_path)
    trace_repository.get_or_create("tr-1", entry_point="POST /items")
    span_repository.create(
        Span(
            event_id="ev-1",
            span_id="sp-1",
            trace_id="tr-1",
            channel="snapshot",
            recorded_at_ns=1,
            monotonic_ns=1,
            payload_json={
                "request": {"name": "gizmo"},
                "response": {"id": 1, "name": "gizmo"},
                "content_hash": "abc",
                "nanobar_type": "controller-request-response",
            },
        )
    )

    bricks = generate_bricks(trace_repository, span_repository, brick_repository, channel="snapshot")

    brick = bricks[0]
    assert brick.nanobar_type == "controller-request-response"
    assert brick.source.get("nanobar_type") == "controller-request-response"  # source_json untouched


def test_trace_refs_always_populated(tmp_path: Path) -> None:
    """Unlike the old raw-`Event` shape (where `trace_id`/`span_id` were nullable, and
    `trace_refs` conditional on them), `Span.trace_id`/`.span_id` are non-nullable columns --
    `TelemetryDrainWorker` never ingests an event missing either (see
    `telemetry_drain_worker.py`'s `skipped_no_trace_context`), so every brick `generate_bricks()`
    produces now unconditionally carries a `trace_refs` entry."""
    repository = _repository("snapshot", "trace")
    client = TestClient(_build_app(repository))
    event = _capture_one_snapshot_event(client, repository, path="/items")
    assert event.trace_id is not None  # sanity: the real tracer produced one

    trace_repository, span_repository, brick_repository = _dbs(tmp_path)
    _ingest_events([event], trace_repository, span_repository)

    bricks = generate_bricks(trace_repository, span_repository, brick_repository, channel="snapshot")

    brick = bricks[0]
    assert brick.trace_refs == [{"trace_id": event.trace_id, "span_ids": [event.span_id]}]


def test_request_body_decoded_as_json(tmp_path: Path) -> None:
    repository = _repository("snapshot", "trace")
    client = TestClient(_build_app(repository))
    event = _capture_one_snapshot_event(
        client, repository, method="POST", path="/echo", content=json.dumps({"a": 1, "b": [1, 2]})
    )

    trace_repository, span_repository, brick_repository = _dbs(tmp_path)
    _ingest_events([event], trace_repository, span_repository)

    bricks = generate_bricks(trace_repository, span_repository, brick_repository, channel="snapshot")

    assert bricks[0].request["payload"] == {"a": 1, "b": [1, 2]}


def test_non_json_request_body_falls_back_to_empty_dict(tmp_path: Path) -> None:
    repository = _repository("snapshot", "trace")
    client = TestClient(_build_app(repository))
    event = _capture_one_snapshot_event(
        client, repository, method="POST", path="/echo", content=b"\xff\xfe not json at all"
    )

    trace_repository, span_repository, brick_repository = _dbs(tmp_path)
    _ingest_events([event], trace_repository, span_repository)

    bricks = generate_bricks(trace_repository, span_repository, brick_repository, channel="snapshot")

    assert bricks[0].request["payload"] == {}


def test_dedup_repeated_content_hash_does_not_insert_second_brick_row(tmp_path: Path) -> None:
    repository = _repository("snapshot", "trace")
    client = TestClient(_build_app(repository))

    event_1 = _capture_one_snapshot_event(client, repository, path="/items")
    event_2 = _capture_one_snapshot_event(client, repository, path="/items")
    # Same endpoint, same (deterministic) request/response bytes -> identical content_hash,
    # despite being two distinct underlying events (different event_id/trace_id/span_id).
    assert event_1.payload["content_hash"] == event_2.payload["content_hash"]
    assert event_1.event_id != event_2.event_id

    trace_repository, span_repository, brick_repository = _dbs(tmp_path)
    _ingest_events([event_1], trace_repository, span_repository)
    first_batch = generate_bricks(trace_repository, span_repository, brick_repository, channel="snapshot")
    assert len(first_batch) == 1

    _ingest_events([event_2], trace_repository, span_repository)
    second_batch = generate_bricks(trace_repository, span_repository, brick_repository, channel="snapshot")

    # No newly-inserted brick for the duplicate content_hash.
    assert second_batch == []


def test_dedup_still_marks_the_duplicate_event_processed(tmp_path: Path) -> None:
    repository = _repository("snapshot", "trace")
    client = TestClient(_build_app(repository))

    event_1 = _capture_one_snapshot_event(client, repository, path="/items")
    event_2 = _capture_one_snapshot_event(client, repository, path="/items")

    trace_repository, span_repository, brick_repository = _dbs(tmp_path)
    _ingest_events([event_1, event_2], trace_repository, span_repository)

    generate_bricks(trace_repository, span_repository, brick_repository, channel="snapshot")

    # Both events consumed in one call; nothing left unprocessed for a second call.
    assert _unprocessed_span_count(span_repository, "snapshot") == 0


def test_second_call_does_not_reprocess_already_marked_events(tmp_path: Path) -> None:
    repository = _repository("snapshot", "trace")
    client = TestClient(_build_app(repository))
    event = _capture_one_snapshot_event(client, repository, path="/items")

    trace_repository, span_repository, brick_repository = _dbs(tmp_path)
    _ingest_events([event], trace_repository, span_repository)

    first_call = generate_bricks(trace_repository, span_repository, brick_repository, channel="snapshot")
    second_call = generate_bricks(trace_repository, span_repository, brick_repository, channel="snapshot")

    assert len(first_call) == 1
    assert second_call == []


def test_malformed_payload_is_marked_processed_and_skipped_not_crashed(tmp_path: Path) -> None:
    malformed_event = Event(
        event_id="evt-malformed",
        channel="snapshot",
        recorded_at_ns=1,
        monotonic_ns=1,
        payload={"unexpected": "shape"},  # missing request/response/content_hash entirely
    )

    trace_repository, span_repository, brick_repository = _dbs(tmp_path)
    _ingest_events([malformed_event], trace_repository, span_repository)

    bricks = generate_bricks(trace_repository, span_repository, brick_repository, channel="snapshot")

    assert bricks == []
    # Marked processed despite being unusable -- no retry loop at this stage.
    assert _unprocessed_span_count(span_repository, "snapshot") == 0


def test_non_dict_request_payload_is_marked_processed_and_skipped_not_crashed(tmp_path: Path) -> None:
    malformed_event = Event(
        event_id="evt-malformed-request",
        channel="snapshot",
        recorded_at_ns=1,
        monotonic_ns=1,
        # request/response/content_hash are all present, but request isn't an object -- a
        # naive .get() on it downstream would raise AttributeError instead of being skipped.
        payload={"request": "not-an-object", "response": {}, "content_hash": "abc"},
    )

    trace_repository, span_repository, brick_repository = _dbs(tmp_path)
    _ingest_events([malformed_event], trace_repository, span_repository)

    bricks = generate_bricks(trace_repository, span_repository, brick_repository, channel="snapshot")

    assert bricks == []
    assert _unprocessed_span_count(span_repository, "snapshot") == 0


def test_limit_is_respected(tmp_path: Path) -> None:
    repository = _repository("snapshot", "trace")
    client = TestClient(_build_app(repository))
    events = [_capture_one_snapshot_event(client, repository, path="/items") for _ in range(3)]

    trace_repository, span_repository, brick_repository = _dbs(tmp_path)
    _ingest_events(events, trace_repository, span_repository)

    bricks = generate_bricks(trace_repository, span_repository, brick_repository, channel="snapshot", limit=1)

    # Only one trace claimed this call (each capture is its own trace here); the
    # identical-content duplicates would dedup anyway, so assert on the unprocessed count
    # directly instead.
    assert _unprocessed_span_count(span_repository, "snapshot") == 2
    assert len(bricks) <= 1


def test_created_by_and_capture_policy_id_are_passed_through(tmp_path: Path) -> None:
    repository = _repository("snapshot", "trace")
    client = TestClient(_build_app(repository))
    event = _capture_one_snapshot_event(client, repository, path="/items")

    trace_repository, span_repository, brick_repository = _dbs(tmp_path)
    _ingest_events([event], trace_repository, span_repository)

    bricks = generate_bricks(
        trace_repository,
        span_repository,
        brick_repository,
        channel="snapshot",
        created_by="ci-job",
        capture_policy_id="custom-policy",
    )

    assert bricks[0].created_by == "ci-job"
    assert bricks[0].capture_policy_id == "custom-policy"


def test_custom_channel_is_respected(tmp_path: Path) -> None:
    repository = _repository("custom")
    app = Starlette(
        routes=[Route("/items", _get_items)],
        middleware=[Middleware(SnapshotMiddleware, repository=repository, channel="custom")],
    )
    client = TestClient(app)
    client.get("/items")
    event = repository.get_any(["custom"], timeout=1.0)
    assert event is not None

    trace_repository, span_repository, brick_repository = _dbs(tmp_path)
    _ingest_events([event], trace_repository, span_repository)

    # Default channel ("snapshot") sees nothing on the "custom" channel.
    assert generate_bricks(trace_repository, span_repository, brick_repository) == []

    bricks = generate_bricks(trace_repository, span_repository, brick_repository, channel="custom")
    assert len(bricks) == 1


def test_request_headers_and_query_params_carried_through(tmp_path: Path) -> None:
    from nanobar_api.capture.policy import CapturePolicy

    repository = _repository("snapshot")
    policy = CapturePolicy(header_allowlist=("content-type",), query_param_allowlist=("q",))
    app = Starlette(
        routes=[Route("/items", _get_items)],
        middleware=[Middleware(SnapshotMiddleware, repository=repository, policy=policy, channel="snapshot")],
    )
    client = TestClient(app)
    event = _capture_one_snapshot_event(
        client, repository, path="/items", params={"q": "widgets"}, headers={"content-type": "application/json"}
    )

    trace_repository, span_repository, brick_repository = _dbs(tmp_path)
    _ingest_events([event], trace_repository, span_repository)

    bricks = generate_bricks(trace_repository, span_repository, brick_repository, channel="snapshot")

    brick = bricks[0]
    assert brick.request["headers"].get("content-type") == "application/json"
    assert brick.request["query_params"] == {"q": "widgets"}


def _event_with_status_code(status_code: int | None, event_id: str = "evt-1") -> Event:
    return Event(
        event_id=event_id,
        channel="snapshot",
        recorded_at_ns=1,
        monotonic_ns=1,
        payload={
            "request": {"method": "GET", "path": "/x", "headers": {}, "query_params": {}, "body_b64": ""},
            "response": {"status_code": status_code, "body_b64": ""},
            "content_hash": f"hash-{event_id}",
            "error": False,
        },
    )


@pytest.mark.parametrize(
    ("status_code", "expected_scenario_type"),
    [
        (200, "success"),
        (201, "success"),
        (299, "success"),
        (400, "invalid_input"),
        (401, "unauthorized"),
        (403, "forbidden"),
        (404, "not_found"),
        (409, "conflict"),
        (422, "validation_error"),
        (500, "server_error"),
        (503, "server_error"),
        (418, None),  # unrecognized status code -- classify as None, not a guess
        (None, None),  # missing status code entirely
    ],
)
def test_regression_scenario_type_classified_from_status_code(
    tmp_path: Path, status_code: int | None, expected_scenario_type: str | None
) -> None:
    trace_repository, span_repository, brick_repository = _dbs(tmp_path)
    _ingest_events([_event_with_status_code(status_code)], trace_repository, span_repository)

    bricks = generate_bricks(trace_repository, span_repository, brick_repository, channel="snapshot")

    assert bricks[0].regression_scenario_type == expected_scenario_type


def test_body_b64_round_trips_through_base64_correctly(tmp_path: Path) -> None:
    # Directly exercises the base64-decode path with a hand-built event, independent of
    # the real middleware, to pin down exact decode semantics.
    body = json.dumps({"x": 1}).encode()
    event = Event(
        event_id="evt-1",
        channel="snapshot",
        recorded_at_ns=1,
        monotonic_ns=1,
        payload={
            "request": {
                "method": "POST",
                "path": "/x",
                "headers": {},
                "query_params": {},
                "body_b64": base64.b64encode(body).decode("ascii"),
            },
            "response": {
                "status_code": 201,
                "body_b64": base64.b64encode(b'{"ok": true}').decode("ascii"),
            },
            "content_hash": "deadbeef",
            "error": False,
        },
    )

    trace_repository, span_repository, brick_repository = _dbs(tmp_path)
    _ingest_events([event], trace_repository, span_repository)

    bricks = generate_bricks(trace_repository, span_repository, brick_repository, channel="snapshot")

    brick = bricks[0]
    assert brick.request["payload"] == {"x": 1}
    assert brick.response["payload"] == {"ok": True}
    assert brick.response["status_code"] == 201
    assert brick.content_hash == "sha256:deadbeef"


def test_capture_layer_produced_event_uses_request_response_as_is(tmp_path: Path) -> None:
    repository = _repository()
    capture_layer(
        repository,
        "validator",
        {"method": "POST", "path_params": {}, "query_params": {}, "body": {"name": "Ada"}},
        {"name": "Ada"},
        nanobar_type="validator-request-response",
    )
    event = repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None

    trace_repository, span_repository, brick_repository = _dbs(tmp_path)
    _ingest_events([event], trace_repository, span_repository)

    bricks = generate_bricks(trace_repository, span_repository, brick_repository, channel="snapshot")

    assert len(bricks) == 1
    brick = bricks[0]
    assert brick.request == {"method": "POST", "path_params": {}, "query_params": {}, "body": {"name": "Ada"}}
    assert brick.response == {"name": "Ada"}
    assert brick.source["nanobar_type"] == "validator-request-response"


def test_capture_layer_produced_event_classifies_success() -> None:
    assert _classify_capture_layer_scenario({"error": False}, {"name": "Ada"}) == "success"


def test_capture_layer_produced_event_classifies_validation_error() -> None:
    assert _classify_capture_layer_scenario({"error": False}, {"errors": ["name: required"]}) == "invalid_input"


def test_capture_layer_produced_event_classifies_server_error() -> None:
    assert _classify_capture_layer_scenario({"error": True}, {}) == "server_error"


def test_capture_layer_produced_event_regression_scenario_type_set_on_brick(tmp_path: Path) -> None:
    repository = _repository()
    capture_layer(
        repository,
        "validator",
        {"body": {}},
        {"errors": ["name: required field missing"]},
        nanobar_type="validator-request-response",
    )
    event = repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None

    trace_repository, span_repository, brick_repository = _dbs(tmp_path)
    _ingest_events([event], trace_repository, span_repository)

    bricks = generate_bricks(trace_repository, span_repository, brick_repository, channel="snapshot")

    assert bricks[0].regression_scenario_type == "invalid_input"


def test_snapshot_middleware_event_source_has_no_nanobar_type_key(tmp_path: Path) -> None:
    repository = _repository("snapshot", "trace")
    client = TestClient(_build_app(repository))
    event = _capture_one_snapshot_event(client, repository, path="/items")

    trace_repository, span_repository, brick_repository = _dbs(tmp_path)
    _ingest_events([event], trace_repository, span_repository)

    bricks = generate_bricks(trace_repository, span_repository, brick_repository, channel="snapshot")

    assert "nanobar_type" not in bricks[0].source


def test_classify_db_scenario_type_no_error_is_success() -> None:
    from nanobar_api.bricks.generate import _classify_db_scenario_type

    assert _classify_db_scenario_type(None) == "success"


def test_classify_db_scenario_type_integrity_error_is_conflict() -> None:
    from nanobar_api.bricks.generate import _classify_db_scenario_type

    assert _classify_db_scenario_type("IntegrityError") == "conflict"


def test_classify_db_scenario_type_other_error_is_server_error() -> None:
    from nanobar_api.bricks.generate import _classify_db_scenario_type

    assert _classify_db_scenario_type("OperationalError") == "server_error"


def test_orm_produced_event_uses_db_scenario_classifier(tmp_path: Path) -> None:
    repository = _repository()
    capture_layer(
        repository,
        "orm",
        {"statement": "INSERT INTO t VALUES (1)", "executemany": False},
        {"error_type": "IntegrityError", "error_message": "UNIQUE constraint failed"},
        nanobar_type="orm-request-response",
        error=True,
        route_key="POST /orders",
    )
    event = repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None

    trace_repository, span_repository, brick_repository = _dbs(tmp_path)
    _ingest_events([event], trace_repository, span_repository)

    bricks = generate_bricks(trace_repository, span_repository, brick_repository, channel="snapshot")

    assert bricks[0].regression_scenario_type == "conflict"
    assert bricks[0].source["route_key"] == "POST /orders"
