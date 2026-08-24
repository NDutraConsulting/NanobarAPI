from __future__ import annotations

from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from nanobar_api.bricks.generate import generate_bricks
from nanobar_api.bricks.replay import replay_brick
from nanobar_api.bricks.schema import RegressionBrick
from nanobar_api.bricks.store import connect as connect_bricks
from nanobar_api.capture.policy import CapturePolicy
from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.eventbus.store import connect as connect_events, insert_events
from nanobar_api.middleware.snapshot import SnapshotMiddleware


async def _get_items(request: Request) -> JSONResponse:
    return JSONResponse(
        {"status": "success", "msg": "", "result": {"type": "array", "data": [{"id": 1, "name": "widget"}]}}
    )


async def _create_item(request: Request) -> JSONResponse:
    body = await request.json()
    return JSONResponse(
        {"status": "success", "msg": "", "result": {"type": "object", "data": {"echo": body}}},
        status_code=201,
    )


async def _not_found(request: Request) -> JSONResponse:
    return JSONResponse({"status": "error", "msg": "nope", "result": {"type": "object", "data": None}}, status_code=404)


def _repository() -> EventQueueRepository:
    return EventQueueRepository([ChannelConfig(name="snapshot")])


def _build_app(repository: EventQueueRepository) -> Starlette:
    return Starlette(
        routes=[
            Route("/items", _get_items),
            Route("/items", _create_item, methods=["POST"]),
            Route("/missing", _not_found),
        ],
        middleware=[Middleware(SnapshotMiddleware, repository=repository, channel="snapshot")],
    )


def _brick_for(
    app: Starlette, repository: EventQueueRepository, db_dir: Path, **request_kwargs: object
) -> RegressionBrick:
    client = TestClient(app)
    method = str(request_kwargs.pop("method", "GET"))
    path = str(request_kwargs.pop("path"))
    client.request(method, path, **request_kwargs)  # type: ignore[arg-type]
    event = repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None

    events_conn = connect_events(str(db_dir / "events.db"))
    bricks_conn = connect_bricks(str(db_dir / "regression_bricks.db"))
    try:
        insert_events(events_conn, [event])
        bricks = generate_bricks(events_conn, bricks_conn, channel="snapshot")
        assert len(bricks) == 1
        return bricks[0]
    finally:
        events_conn.close()
        bricks_conn.close()


def test_replay_get_against_unmodified_app_matches_original_response(tmp_path: Path) -> None:
    repository = _repository()
    app = _build_app(repository)
    brick = _brick_for(app, repository, tmp_path, path="/items")

    replayed = replay_brick(app, brick)

    assert replayed["status_code"] == brick.response["status_code"] == 200
    assert replayed["payload"] == brick.response["payload"]
    assert replayed["payload"] == {
        "status": "success",
        "msg": "",
        "result": {"type": "array", "data": [{"id": 1, "name": "widget"}]},
    }


def test_replay_post_with_json_body_against_unmodified_app_matches(tmp_path: Path) -> None:
    repository = _repository()
    app = _build_app(repository)
    brick = _brick_for(app, repository, tmp_path, method="POST", path="/items", json={"name": "gizmo"})

    replayed = replay_brick(app, brick)

    assert replayed["status_code"] == 201
    assert replayed["payload"] == brick.response["payload"]
    assert replayed["payload"]["result"]["data"]["echo"] == {"name": "gizmo"}


def test_replay_error_status_against_unmodified_app_matches(tmp_path: Path) -> None:
    repository = _repository()
    app = _build_app(repository)
    brick = _brick_for(app, repository, tmp_path, path="/missing")

    replayed = replay_brick(app, brick)

    assert replayed["status_code"] == 404 == brick.response["status_code"]
    assert replayed["payload"] == brick.response["payload"]


def test_replay_result_shape_matches_brick_response_shape(tmp_path: Path) -> None:
    repository = _repository()
    app = _build_app(repository)
    brick = _brick_for(app, repository, tmp_path, path="/items")

    replayed = replay_brick(app, brick)

    assert set(replayed.keys()) == {"status_code", "payload"}


def test_replay_non_json_body_response_falls_back_to_empty_dict(tmp_path: Path) -> None:
    async def _plain_text(request: Request) -> PlainTextResponse:
        return PlainTextResponse("not json")

    repository = EventQueueRepository([ChannelConfig(name="snapshot")])
    app = Starlette(
        routes=[Route("/text", _plain_text)],
        middleware=[Middleware(SnapshotMiddleware, repository=repository, channel="snapshot")],
    )
    brick = _brick_for(app, repository, tmp_path, path="/text")

    replayed = replay_brick(app, brick)

    assert replayed["status_code"] == 200
    assert replayed["payload"] == {}


def test_replay_headers_from_brick_are_resent(tmp_path: Path) -> None:
    async def _echo_content_type(request: Request) -> JSONResponse:
        return JSONResponse({"status": "success", "msg": "", "result": {"type": "object", "data": {}}})

    repository = EventQueueRepository([ChannelConfig(name="snapshot")])
    policy = CapturePolicy(header_allowlist=("content-type",))
    app = Starlette(
        routes=[Route("/x", _echo_content_type)],
        middleware=[Middleware(SnapshotMiddleware, repository=repository, policy=policy, channel="snapshot")],
    )
    client = TestClient(app)
    client.get("/x", headers={"content-type": "application/json"})
    event = repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None

    events_conn = connect_events(str(tmp_path / "events.db"))
    bricks_conn = connect_bricks(str(tmp_path / "regression_bricks.db"))
    try:
        insert_events(events_conn, [event])
        brick = generate_bricks(events_conn, bricks_conn, channel="snapshot")[0]
    finally:
        events_conn.close()
        bricks_conn.close()

    assert brick.request["headers"].get("content-type") == "application/json"
    assert "authorization" not in brick.request["headers"]

    replayed = replay_brick(app, brick)
    assert replayed["status_code"] == 200


def test_replay_non_dict_json_response_falls_back_to_empty_dict(tmp_path: Path) -> None:
    # Valid JSON, but not an object (a bare list) -- must still fall back to {}, not the
    # ValueError path (which only covers "not valid JSON at all").
    async def _list_response(request: Request) -> JSONResponse:
        return JSONResponse([1, 2, 3])

    repository = EventQueueRepository([ChannelConfig(name="snapshot")])
    app = Starlette(
        routes=[Route("/list", _list_response)],
        middleware=[Middleware(SnapshotMiddleware, repository=repository, channel="snapshot")],
    )
    brick = _brick_for(app, repository, tmp_path, path="/list")

    replayed = replay_brick(app, brick)

    assert replayed["status_code"] == 200
    assert replayed["payload"] == {}


def test_replay_get_with_no_body_sends_no_json(tmp_path: Path) -> None:
    # Regression guard: brick.request["payload"] is {} for a bodyless GET, and replay_brick
    # must not send an empty-but-present JSON body in that case (falsy payload => no `json=`
    # kwarg at all), so it doesn't accidentally turn a GET into one carrying a body.
    repository = _repository()
    app = _build_app(repository)
    brick = _brick_for(app, repository, tmp_path, path="/items")

    assert brick.request["payload"] == {}

    replayed = replay_brick(app, brick)

    assert replayed["status_code"] == 200
