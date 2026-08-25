from __future__ import annotations

import base64
import json
import sqlite3
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

from nanobar_api.bricks.generate import generate_bricks
from nanobar_api.bricks.store import connect as connect_bricks
from nanobar_api.eventbus.events import Event
from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.eventbus.store import connect as connect_events, get_unprocessed, insert_events
from nanobar_api.middleware.snapshot import SnapshotMiddleware
from nanobar_api.middleware.trace import EventBusTraceMiddleware

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


def _dbs(tmp_path: Path) -> tuple[sqlite3.Connection, sqlite3.Connection]:
    events_conn = connect_events(str(tmp_path / "events.db"))
    bricks_conn = connect_bricks(str(tmp_path / "regression_bricks.db"))
    return events_conn, bricks_conn


def test_generate_bricks_builds_brick_from_real_snapshot_event(tmp_path: Path) -> None:
    repository = _repository("snapshot", "trace")
    client = TestClient(_build_app(repository))
    event = _capture_one_snapshot_event(client, repository, path="/items")

    events_conn, bricks_conn = _dbs(tmp_path)
    try:
        insert_events(events_conn, [event])

        bricks = generate_bricks(events_conn, bricks_conn, channel="snapshot")

        assert len(bricks) == 1
        brick = bricks[0]
        assert brick.regression_brick_id.startswith("rbrick-")
        assert brick.schema_version == "1.0"
        assert brick.brick_version == 1
        assert brick.created_by == "nanobarapi"
        assert brick.capture_policy_id == "default-v1"
        assert brick.content_hash == f"sha256:{event.payload['content_hash']}"

        assert brick.request["method"] == "GET"
        assert brick.request["path"] == "/items"
        assert brick.request["payload"] == {}

        assert brick.response["status_code"] == 200
        assert brick.response["payload"] == {
            "status": "success",
            "msg": "",
            "result": {"type": "array", "data": [{"id": 1, "name": "widget"}]},
        }
    finally:
        events_conn.close()
        bricks_conn.close()


def test_source_is_honest_and_minimal_not_fabricated(tmp_path: Path) -> None:
    repository = _repository("snapshot", "trace")
    client = TestClient(_build_app(repository))
    event = _capture_one_snapshot_event(client, repository, path="/items")

    events_conn, bricks_conn = _dbs(tmp_path)
    try:
        insert_events(events_conn, [event])

        bricks = generate_bricks(events_conn, bricks_conn, channel="snapshot")

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
    finally:
        events_conn.close()
        bricks_conn.close()


def test_trace_refs_populated_when_trace_id_present(tmp_path: Path) -> None:
    repository = _repository("snapshot", "trace")
    client = TestClient(_build_app(repository))
    event = _capture_one_snapshot_event(client, repository, path="/items")
    assert event.trace_id is not None  # sanity: the real tracer produced one

    events_conn, bricks_conn = _dbs(tmp_path)
    try:
        insert_events(events_conn, [event])

        bricks = generate_bricks(events_conn, bricks_conn, channel="snapshot")

        brick = bricks[0]
        assert brick.trace_refs == [{"trace_id": event.trace_id, "span_ids": [event.span_id]}]
    finally:
        events_conn.close()
        bricks_conn.close()


def test_trace_refs_empty_when_trace_id_absent(tmp_path: Path) -> None:
    # No trace middleware in this app, so current_trace_id is never set.
    repository = _repository("snapshot")
    app = Starlette(
        routes=[Route("/items", _get_items)],
        middleware=[Middleware(SnapshotMiddleware, repository=repository, channel="snapshot")],
    )
    client = TestClient(app)
    event = _capture_one_snapshot_event(client, repository, path="/items")
    assert event.trace_id is None

    events_conn, bricks_conn = _dbs(tmp_path)
    try:
        insert_events(events_conn, [event])

        bricks = generate_bricks(events_conn, bricks_conn, channel="snapshot")

        assert bricks[0].trace_refs == []
    finally:
        events_conn.close()
        bricks_conn.close()


def test_request_body_decoded_as_json(tmp_path: Path) -> None:
    repository = _repository("snapshot", "trace")
    client = TestClient(_build_app(repository))
    event = _capture_one_snapshot_event(
        client, repository, method="POST", path="/echo", content=json.dumps({"a": 1, "b": [1, 2]})
    )

    events_conn, bricks_conn = _dbs(tmp_path)
    try:
        insert_events(events_conn, [event])

        bricks = generate_bricks(events_conn, bricks_conn, channel="snapshot")

        assert bricks[0].request["payload"] == {"a": 1, "b": [1, 2]}
    finally:
        events_conn.close()
        bricks_conn.close()


def test_non_json_request_body_falls_back_to_empty_dict(tmp_path: Path) -> None:
    repository = _repository("snapshot", "trace")
    client = TestClient(_build_app(repository))
    event = _capture_one_snapshot_event(
        client, repository, method="POST", path="/echo", content=b"\xff\xfe not json at all"
    )

    events_conn, bricks_conn = _dbs(tmp_path)
    try:
        insert_events(events_conn, [event])

        bricks = generate_bricks(events_conn, bricks_conn, channel="snapshot")

        assert bricks[0].request["payload"] == {}
    finally:
        events_conn.close()
        bricks_conn.close()


def test_dedup_repeated_content_hash_does_not_insert_second_brick_row(tmp_path: Path) -> None:
    repository = _repository("snapshot", "trace")
    client = TestClient(_build_app(repository))

    event_1 = _capture_one_snapshot_event(client, repository, path="/items")
    event_2 = _capture_one_snapshot_event(client, repository, path="/items")
    # Same endpoint, same (deterministic) request/response bytes -> identical content_hash,
    # despite being two distinct underlying events (different event_id/trace_id/span_id).
    assert event_1.payload["content_hash"] == event_2.payload["content_hash"]
    assert event_1.event_id != event_2.event_id

    events_conn, bricks_conn = _dbs(tmp_path)
    try:
        insert_events(events_conn, [event_1])
        first_batch = generate_bricks(events_conn, bricks_conn, channel="snapshot")
        assert len(first_batch) == 1

        insert_events(events_conn, [event_2])
        second_batch = generate_bricks(events_conn, bricks_conn, channel="snapshot")

        # No newly-inserted brick for the duplicate content_hash.
        assert second_batch == []
    finally:
        events_conn.close()
        bricks_conn.close()


def test_dedup_still_marks_the_duplicate_event_processed(tmp_path: Path) -> None:
    repository = _repository("snapshot", "trace")
    client = TestClient(_build_app(repository))

    event_1 = _capture_one_snapshot_event(client, repository, path="/items")
    event_2 = _capture_one_snapshot_event(client, repository, path="/items")

    events_conn, bricks_conn = _dbs(tmp_path)
    try:
        insert_events(events_conn, [event_1, event_2])

        generate_bricks(events_conn, bricks_conn, channel="snapshot")

        # Both events consumed in one call; nothing left unprocessed for a second call.
        assert get_unprocessed(events_conn, "snapshot") == []
    finally:
        events_conn.close()
        bricks_conn.close()


def test_second_call_does_not_reprocess_already_marked_events(tmp_path: Path) -> None:
    repository = _repository("snapshot", "trace")
    client = TestClient(_build_app(repository))
    event = _capture_one_snapshot_event(client, repository, path="/items")

    events_conn, bricks_conn = _dbs(tmp_path)
    try:
        insert_events(events_conn, [event])

        first_call = generate_bricks(events_conn, bricks_conn, channel="snapshot")
        second_call = generate_bricks(events_conn, bricks_conn, channel="snapshot")

        assert len(first_call) == 1
        assert second_call == []
    finally:
        events_conn.close()
        bricks_conn.close()


def test_malformed_payload_is_marked_processed_and_skipped_not_crashed(tmp_path: Path) -> None:
    malformed_event = Event(
        event_id="evt-malformed",
        channel="snapshot",
        recorded_at_ns=1,
        monotonic_ns=1,
        payload={"unexpected": "shape"},  # missing request/response/content_hash entirely
    )

    events_conn, bricks_conn = _dbs(tmp_path)
    try:
        insert_events(events_conn, [malformed_event])

        bricks = generate_bricks(events_conn, bricks_conn, channel="snapshot")

        assert bricks == []
        # Marked processed despite being unusable -- no retry loop at this stage.
        assert get_unprocessed(events_conn, "snapshot") == []
    finally:
        events_conn.close()
        bricks_conn.close()


def test_limit_is_respected(tmp_path: Path) -> None:
    repository = _repository("snapshot", "trace")
    client = TestClient(_build_app(repository))
    events = [_capture_one_snapshot_event(client, repository, path="/items") for _ in range(3)]

    events_conn, bricks_conn = _dbs(tmp_path)
    try:
        insert_events(events_conn, events)

        bricks = generate_bricks(events_conn, bricks_conn, channel="snapshot", limit=1)

        # Only one event claimed this call; the identical-content duplicates would dedup
        # anyway, so assert on the unprocessed count directly instead.
        assert len(get_unprocessed(events_conn, "snapshot")) == 2
        assert len(bricks) <= 1
    finally:
        events_conn.close()
        bricks_conn.close()


def test_created_by_and_capture_policy_id_are_passed_through(tmp_path: Path) -> None:
    repository = _repository("snapshot", "trace")
    client = TestClient(_build_app(repository))
    event = _capture_one_snapshot_event(client, repository, path="/items")

    events_conn, bricks_conn = _dbs(tmp_path)
    try:
        insert_events(events_conn, [event])

        bricks = generate_bricks(
            events_conn,
            bricks_conn,
            channel="snapshot",
            created_by="ci-job",
            capture_policy_id="custom-policy",
        )

        assert bricks[0].created_by == "ci-job"
        assert bricks[0].capture_policy_id == "custom-policy"
    finally:
        events_conn.close()
        bricks_conn.close()


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

    events_conn, bricks_conn = _dbs(tmp_path)
    try:
        insert_events(events_conn, [event])

        # Default channel ("snapshot") sees nothing on the "custom" channel.
        assert generate_bricks(events_conn, bricks_conn) == []

        bricks = generate_bricks(events_conn, bricks_conn, channel="custom")
        assert len(bricks) == 1
    finally:
        events_conn.close()
        bricks_conn.close()


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

    events_conn, bricks_conn = _dbs(tmp_path)
    try:
        insert_events(events_conn, [event])

        bricks = generate_bricks(events_conn, bricks_conn, channel="snapshot")

        brick = bricks[0]
        assert brick.request["headers"].get("content-type") == "application/json"
        assert brick.request["query_params"] == {"q": "widgets"}
    finally:
        events_conn.close()
        bricks_conn.close()


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
    events_conn, bricks_conn = _dbs(tmp_path)
    try:
        insert_events(events_conn, [_event_with_status_code(status_code)])

        bricks = generate_bricks(events_conn, bricks_conn, channel="snapshot")

        assert bricks[0].regression_scenario_type == expected_scenario_type
    finally:
        events_conn.close()
        bricks_conn.close()


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

    events_conn, bricks_conn = _dbs(tmp_path)
    try:
        insert_events(events_conn, [event])

        bricks = generate_bricks(events_conn, bricks_conn, channel="snapshot")

        brick = bricks[0]
        assert brick.request["payload"] == {"x": 1}
        assert brick.response["payload"] == {"ok": True}
        assert brick.response["status_code"] == 201
        assert brick.content_hash == "sha256:deadbeef"
    finally:
        events_conn.close()
        bricks_conn.close()
