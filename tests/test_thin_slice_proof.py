from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import anyio
import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

from nanobar_api import NanobarAPI, endpoint_schema, success
from nanobar_api.bricks import RegressionBrick, evaluate_verdict, generate_bricks, replay_brick
from nanobar_api.bricks.store import connect as bricks_connect, get_brick
from nanobar_api.eventbus import ChannelConfig, EventQueueRepository, eventbus_lifespan
from nanobar_api.eventbus.store import connect as events_connect
from nanobar_api.middleware.snapshot import SnapshotMiddleware


@dataclass
class Pong:
    message: str


def _inner_app(message: str) -> NanobarAPI:
    @endpoint_schema(response=Pong, summary="Ping")
    async def ping(request: Request) -> Response:
        return JSONResponse(success({"message": message}))

    return NanobarAPI(routes=[Route("/ping", ping, methods=["GET"])], openapi_url=None, docs_url=None)


async def _capture_one_brick(tmp_path: Path) -> tuple[sqlite3.Connection, str]:
    """Records one real request end-to-end (SnapshotMiddleware -> eventbus -> events.db),
    generates a brick from it, and returns (bricks_conn, regression_brick_id).
    """
    events_db_path = str(tmp_path / "events.db")
    bricks_db_path = str(tmp_path / "regression_bricks.db")

    repository = EventQueueRepository([ChannelConfig(name="snapshot")])
    inner = _inner_app("pong")
    recorder = SnapshotMiddleware(inner, repository)

    bricks_conn = bricks_connect(bricks_db_path)
    async with eventbus_lifespan(repository, events_db_path, channels=["snapshot"]):
        client = TestClient(recorder)
        response = client.get("/ping")
        assert response.status_code == 200

        events_conn = events_connect(events_db_path)
        deadline = time.monotonic() + 5.0
        bricks: list[RegressionBrick] = []
        while time.monotonic() < deadline and not bricks:
            bricks = generate_bricks(events_conn, bricks_conn)
            if not bricks:
                await anyio.sleep(0.05)

    assert len(bricks) == 1, "expected exactly one brick generated from the one captured request"
    return bricks_conn, bricks[0].regression_brick_id


@pytest.mark.anyio
async def test_thin_slice_replay_against_unchanged_app_passes(tmp_path: Path) -> None:
    """The checkpoint's first half: no false positive on an unchanged endpoint."""
    bricks_conn, brick_id = await _capture_one_brick(tmp_path)
    try:
        brick = get_brick(bricks_conn, brick_id)
        assert brick is not None

        unchanged_app = _inner_app("pong")
        replayed = replay_brick(unchanged_app, brick)

        verdict = evaluate_verdict(brick, replayed)

        assert verdict.overall_passed is True
        assert verdict.status_layer.passed is True
        assert verdict.pinned_field_layer.passed is True
    finally:
        bricks_conn.close()


@pytest.mark.anyio
async def test_thin_slice_replay_against_regressed_app_fails(tmp_path: Path) -> None:
    """The checkpoint's second half: a real regression is actually caught."""
    bricks_conn, brick_id = await _capture_one_brick(tmp_path)
    try:
        brick = get_brick(bricks_conn, brick_id)
        assert brick is not None

        regressed_app = _inner_app("BROKEN")  # the endpoint now returns something different
        replayed = replay_brick(regressed_app, brick)

        verdict = evaluate_verdict(brick, replayed)

        assert verdict.overall_passed is False
        assert verdict.pinned_field_layer.passed is False
        assert "message" in verdict.pinned_field_layer.detail
    finally:
        bricks_conn.close()


@pytest.mark.anyio
async def test_thin_slice_replay_against_status_regression_fails_fast(tmp_path: Path) -> None:
    """A regression that changes the status code is caught by the cheapest layer first."""
    bricks_conn, brick_id = await _capture_one_brick(tmp_path)
    try:
        brick = get_brick(bricks_conn, brick_id)
        assert brick is not None

        async def broken_ping(request: Request) -> Response:
            return JSONResponse({"error": "boom"}, status_code=500)

        broken_app = NanobarAPI(routes=[Route("/ping", broken_ping, methods=["GET"])], openapi_url=None, docs_url=None)
        replayed = replay_brick(broken_app, brick)

        verdict = evaluate_verdict(brick, replayed)

        assert verdict.overall_passed is False
        assert verdict.status_layer.passed is False
        assert "skipped" in verdict.pinned_field_layer.detail.lower()
    finally:
        bricks_conn.close()
