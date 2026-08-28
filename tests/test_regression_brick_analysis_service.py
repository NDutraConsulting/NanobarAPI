from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx2
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

from nanobar_api.eventbus.dispatch import NanobarCallback, NanobarEventBus
from nanobar_api.eventbus.events import Event
from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.persistence import build_session_factory
from nanobar_api.regression_brick.model import RegressionBrick
from nanobar_api.regression_brick.regression_brick_analysis_service import ReplayBrickRequest, ReplayBrickService
from nanobar_api.regression_brick.replay_routes import build_replay_routes
from nanobar_api.regression_brick.repository import RegressionBrickRepository
from nanobar_api.telemetry import NanobarTelemetry
from nanobar_api.telemetry.persistence import build_session_factory as build_telemetry_session_factory
from nanobar_api.telemetry.telemetry_drain_worker import telemetry_drain_worker_lifespan


def _repository() -> EventQueueRepository:
    return EventQueueRepository([ChannelConfig(name="trace"), ChannelConfig(name="snapshot")])


def _telemetry() -> NanobarTelemetry:
    return NanobarTelemetry(_repository(), channel="trace")


def _brick_repository(tmp_path: Path) -> RegressionBrickRepository:
    session = build_session_factory(str(tmp_path / "bricks.db"), repository=_repository())()
    return RegressionBrickRepository(session)


async def _echo_items(request: Request) -> JSONResponse:
    return JSONResponse(
        {"status": "success", "msg": "", "result": {"type": "array", "data": [{"id": 1, "name": "widget"}]}}
    )


async def _create_item(request: Request) -> JSONResponse:
    body = await request.json()
    return JSONResponse({"status": "success", "msg": "", "result": {"type": "object", "data": {"echo": body}}})


def _app() -> Starlette:
    return Starlette(
        routes=[
            Route("/items", _echo_items),
            Route("/items", _create_item, methods=["POST"]),
        ]
    )


class _EchoSubscriber(NanobarCallback):
    def handle(self, event: Event) -> Any:
        return {"received": event.payload}


def _event_app(tmp_path: Path) -> Starlette:
    """An app mounting `build_replay_routes()` alongside a real `NanobarEventBus` with one
    subscriber -- what `event-to-subscriber` dispatch (`_dispatch_event_to_subscriber()`) talks
    to, standing in for a real shadow deployment the same way `_app()` does for the HTTP path.

    A real lifespan (a genuine `TelemetryDrainWorker`, same as `app/main.py`'s) is wired in --
    `capture_layer()`'s "snapshot"-channel events must actually drain into `Span`/`Trace` rows
    for `REPLAY_SPANS_PATH` to ever see them. Callers must enter it (`with TestClient(app):`),
    matching every other real shadow-deployment stand-in in this codebase.
    """
    repository = EventQueueRepository(
        [ChannelConfig(name="domain.orders"), ChannelConfig(name="snapshot"), ChannelConfig(name="trace")]
    )
    event_bus = NanobarEventBus(repository, NanobarTelemetry(repository, channel="trace"))
    event_bus.subscribe("domain.orders", _EchoSubscriber())
    telemetry_session_factory = build_telemetry_session_factory(str(tmp_path / "replay-telemetry.db"))

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with telemetry_drain_worker_lifespan(["snapshot"], repository, telemetry_session_factory):
            yield

    app = Starlette(routes=build_replay_routes(), lifespan=lifespan)
    app.state.event_bus = event_bus
    app.state.telemetry_session_factory = telemetry_session_factory
    return app


def _make_event_brick(**overrides: object) -> RegressionBrick:
    defaults: dict[str, object] = {
        "regression_brick_id": "rbrick-event",
        "schema_version": "1.0",
        "brick_version": 1,
        "source": {"trace_id": "t-1", "span_id": "s-1", "channel": "snapshot"},
        "entry_point": "event-domain.orders",
        "nanobar_type": "event-to-subscriber",
        "request": {"order_id": "o1"},
        "response": {"received": {"order_id": "o1"}},
        "content_hash": "sha256:event-1",
        "created_by": "test",
    }
    defaults.update(overrides)
    return RegressionBrick(**defaults)


def _client(app: Starlette) -> httpx2.Client:
    """`TestClient` is itself a real `httpx2.Client` subclass (`starlette.testclient.
    TestClient(httpx.Client)`) with its own sync-compatible ASGI bridge -- `httpx2.ASGITransport`
    is async-only and can't back a sync `httpx2.Client` directly, so this is the correct way to
    exercise `ReplayBrickService`'s real `.request(method, path, ...)` call path against an
    in-process app with no real socket. Production instead builds a plain
    `httpx2.Client(base_url=...)` pointed at a real, persistently-running shadow deployment
    (`app/admin/nanobar/shadow_deployment.py`)."""
    return TestClient(app)


def _make_snapshot_brick(**overrides: object) -> RegressionBrick:
    defaults: dict[str, object] = {
        "regression_brick_id": "rbrick-1",
        "schema_version": "1.0",
        "brick_version": 1,
        "source": {"trace_id": "t-1", "span_id": "s-1", "channel": "snapshot"},
        "entry_point": "GET /items",
        "request": {"method": "GET", "path": "/items", "headers": {}, "payload": {}},
        "response": {
            "status_code": 200,
            "payload": {
                "status": "success",
                "msg": "",
                "result": {"type": "array", "data": [{"id": 1, "name": "widget"}]},
            },
        },
        "content_hash": "sha256:abc",
        "created_by": "test",
    }
    defaults.update(overrides)
    return RegressionBrick(**defaults)


def _make_capture_layer_brick(**overrides: object) -> RegressionBrick:
    defaults: dict[str, object] = {
        "regression_brick_id": "rbrick-2",
        "schema_version": "1.0",
        "brick_version": 1,
        "source": {"route_key": "POST /items", "nanobar_type": "controller-request-response"},
        "entry_point": "POST /items",
        "nanobar_type": "controller-request-response",
        "request": {"name": "gizmo"},
        # A capture_layer()-sourced brick's `.response` is the controller's own raw return
        # value (`to_payload_dict(response)` inside `NanobarAPIController.handle()`'s own
        # `capture_layer()` call) -- not a full HTTP envelope like `{"result": {"data": ...}}`.
        "response": {"echo": {"name": "gizmo"}},
        "regression_scenario_type": "success",
        "content_hash": "sha256:def",
        "created_by": "test",
    }
    defaults.update(overrides)
    return RegressionBrick(**defaults)


def test_replay_against_unchanged_app_passes(tmp_path: Path) -> None:
    brick_repository = _brick_repository(tmp_path)
    brick = brick_repository.create(_make_snapshot_brick())
    service = ReplayBrickService(_telemetry(), brick_repository, _client(_app()))

    result = service(ReplayBrickRequest(regression_brick_id=brick.regression_brick_id))

    assert result.status == "success"
    assert result.result.data["verdict"]["overall_passed"] is True
    assert result.result.msg_summary == "replay passed"


def test_replay_against_regressed_response_fails(tmp_path: Path) -> None:
    brick_repository = _brick_repository(tmp_path)
    brick = brick_repository.create(
        _make_snapshot_brick(
            response={
                "status_code": 200,
                "payload": {
                    "status": "success",
                    "msg": "",
                    "result": {"type": "array", "data": [{"id": 1, "name": "DIFFERENT"}]},
                },
            }
        )
    )
    service = ReplayBrickService(_telemetry(), brick_repository, _client(_app()))

    result = service(ReplayBrickRequest(regression_brick_id=brick.regression_brick_id))

    assert result.status == "success"  # the service call itself succeeded
    assert result.result.data["verdict"]["overall_passed"] is False
    assert result.result.msg_summary == "replay failed"


def test_replay_unknown_brick_returns_error_status(tmp_path: Path) -> None:
    brick_repository = _brick_repository(tmp_path)
    service = ReplayBrickService(_telemetry(), brick_repository, _client(_app()))

    result = service(ReplayBrickRequest(regression_brick_id="does-not-exist"))

    assert result.status == "error"
    assert result.result.data is None
    assert "does-not-exist" in result.result.msg_summary


def test_replay_capture_layer_sourced_brick_uses_verdict_adaptation(tmp_path: Path) -> None:
    """The entry_point-derived method/path and the verdict-input adaptation (`_verdict_inputs()`)
    both fire for a capture_layer()-sourced brick -- proves the whole pipeline still works end
    to end after the switch from `route_key`-detection to `nanobar_type`-detection."""
    brick_repository = _brick_repository(tmp_path)
    brick = brick_repository.create(_make_capture_layer_brick())
    service = ReplayBrickService(_telemetry(), brick_repository, _client(_app()))

    result = service(ReplayBrickRequest(regression_brick_id=brick.regression_brick_id))

    assert result.status == "success"
    assert result.result.data["replayed_response"]["status_code"] == 200
    assert result.result.data["verdict"]["overall_passed"] is True


def test_replay_falls_back_to_legacy_entry_point_derivation_when_column_is_unset(tmp_path: Path) -> None:
    """A brick predating Phase 1's `entry_point` column (or Phase 7's backfill hasn't reached
    it) still replays correctly, via `brick.source["route_key"]`/`brick.request`."""
    brick_repository = _brick_repository(tmp_path)
    brick = brick_repository.create(_make_snapshot_brick(entry_point=None))
    service = ReplayBrickService(_telemetry(), brick_repository, _client(_app()))

    result = service(ReplayBrickRequest(regression_brick_id=brick.regression_brick_id))

    assert result.status == "success"
    assert result.result.data["verdict"]["overall_passed"] is True


def test_replay_returns_error_for_a_nanobar_type_with_no_dispatch_yet(tmp_path: Path) -> None:
    """`worker`/`event-to-subscriber` surfaces have no HTTP entry point -- Phase 5, not built
    yet. `handle()` must report that clearly rather than attempting (and mis-firing) an HTTP
    request against a non-HTTP entry point."""
    brick_repository = _brick_repository(tmp_path)
    brick = brick_repository.create(
        _make_capture_layer_brick(
            regression_brick_id="rbrick-worker",
            entry_point="worker-domain.appointments",
            nanobar_type="worker",
            content_hash="sha256:worker",
        )
    )
    service = ReplayBrickService(_telemetry(), brick_repository, _client(_app()))

    result = service(ReplayBrickRequest(regression_brick_id=brick.regression_brick_id))

    assert result.status == "error"
    assert result.result.data is None
    assert "worker" in result.result.msg_summary


def test_replay_extra_headers_are_forwarded(tmp_path: Path) -> None:
    async def _echo_header(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "success",
                "msg": "",
                "result": {"type": "object", "data": {"x-extra": request.headers.get("x-extra")}},
            }
        )

    app = Starlette(routes=[Route("/echo", _echo_header)])
    brick_repository = _brick_repository(tmp_path)
    brick = brick_repository.create(
        _make_snapshot_brick(
            regression_brick_id="rbrick-3",
            entry_point="GET /echo",
            request={"method": "GET", "path": "/echo", "headers": {}, "payload": {}},
            response={"status_code": 200, "payload": {}},
            content_hash="sha256:ghi",
        )
    )
    service = ReplayBrickService(_telemetry(), brick_repository, _client(app))

    result = service(
        ReplayBrickRequest(regression_brick_id=brick.regression_brick_id, extra_headers={"x-extra": "hello"})
    )

    assert result.result.data["replayed_response"]["payload"]["result"]["data"]["x-extra"] == "hello"


def test_replay_respects_custom_volatile_fields(tmp_path: Path) -> None:
    """A field not in the default volatile set must fail the pinned-field layer when it
    differs; declaring it volatile via the request must mask it instead."""
    brick_repository = _brick_repository(tmp_path)
    brick = brick_repository.create(
        _make_snapshot_brick(
            response={
                "status_code": 200,
                "payload": {
                    "status": "success",
                    "msg": "",
                    "result": {"type": "array", "data": [{"id": 1, "name": "widget", "nonce": "brick-nonce"}]},
                },
            }
        )
    )

    async def _get_items_with_nonce(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "success",
                "msg": "",
                "result": {"type": "array", "data": [{"id": 1, "name": "widget", "nonce": "replay-nonce"}]},
            }
        )

    app = Starlette(routes=[Route("/items", _get_items_with_nonce)])
    service = ReplayBrickService(_telemetry(), brick_repository, _client(app))

    default_result = service(ReplayBrickRequest(regression_brick_id=brick.regression_brick_id))
    assert default_result.result.data["verdict"]["overall_passed"] is False

    masked_result = service(
        ReplayBrickRequest(
            regression_brick_id=brick.regression_brick_id,
            volatile_fields=("nonce",),
        )
    )
    assert masked_result.result.data["verdict"]["overall_passed"] is True


def test_replay_respects_response_schema(tmp_path: Path) -> None:
    brick_repository = _brick_repository(tmp_path)
    brick = brick_repository.create(_make_snapshot_brick())
    service = ReplayBrickService(_telemetry(), brick_repository, _client(_app()))

    schema: dict[str, Any] = {"type": "object", "required": ["nonexistent_required_field"]}
    result = service(ReplayBrickRequest(regression_brick_id=brick.regression_brick_id, response_schema=schema))

    assert result.result.data["verdict"]["overall_passed"] is False
    assert any("schema" in d for d in result.result.data["verdict"]["diffs"])


def test_replay_handles_a_non_json_response_body(tmp_path: Path) -> None:
    """`replayed_response["payload"]` gracefully degrades to `{}` for a body that isn't valid
    JSON at all -- same fallback `generate.py`/the old `replay_brick()` already used."""

    async def _plain_text(request: Request) -> Response:
        return PlainTextResponse("not json")

    app = Starlette(routes=[Route("/items", _plain_text)])
    brick_repository = _brick_repository(tmp_path)
    brick = brick_repository.create(_make_snapshot_brick())
    service = ReplayBrickService(_telemetry(), brick_repository, _client(app))

    result = service(ReplayBrickRequest(regression_brick_id=brick.regression_brick_id))

    assert result.result.data["replayed_response"]["payload"] == {}


def test_replay_handles_a_non_dict_json_response_body(tmp_path: Path) -> None:
    """A syntactically valid JSON body that isn't an object (e.g. a bare list) also degrades to
    `{}`, not a raw list -- `evaluate_verdict()`/downstream code assume a dict payload."""

    async def _json_list(request: Request) -> JSONResponse:
        return JSONResponse([1, 2, 3])

    app = Starlette(routes=[Route("/items", _json_list)])
    brick_repository = _brick_repository(tmp_path)
    brick = brick_repository.create(_make_snapshot_brick())
    service = ReplayBrickService(_telemetry(), brick_repository, _client(app))

    result = service(ReplayBrickRequest(regression_brick_id=brick.regression_brick_id))

    assert result.result.data["replayed_response"]["payload"] == {}


# ---------------------------------------------------------------------------
# event-to-subscriber dispatch (Phase 5)
# ---------------------------------------------------------------------------


def test_replay_event_to_subscriber_brick_against_unchanged_subscriber_passes(tmp_path: Path) -> None:
    brick_repository = _brick_repository(tmp_path)
    brick = brick_repository.create(_make_event_brick())
    with TestClient(_event_app(tmp_path)) as client:
        service = ReplayBrickService(_telemetry(), brick_repository, client)

        result = service(ReplayBrickRequest(regression_brick_id=brick.regression_brick_id))

    assert result.status == "success"
    assert result.result.data["verdict"]["overall_passed"] is True


def test_replay_event_to_subscriber_brick_against_regressed_subscriber_fails(tmp_path: Path) -> None:
    brick_repository = _brick_repository(tmp_path)
    brick = brick_repository.create(_make_event_brick(response={"received": {"order_id": "DIFFERENT"}}))
    with TestClient(_event_app(tmp_path)) as client:
        service = ReplayBrickService(_telemetry(), brick_repository, client)

        result = service(ReplayBrickRequest(regression_brick_id=brick.regression_brick_id))

    assert result.status == "success"
    assert result.result.data["verdict"]["overall_passed"] is False
    assert any("order_id" in d for d in result.result.data["verdict"]["diffs"])


def test_replay_event_to_subscriber_brick_with_no_stored_entry_point_fails_the_verdict_honestly(
    tmp_path: Path,
) -> None:
    """No `entry_point` (predates Phase 1/7) means the channel can't be recovered -- reported as
    a diff (the verdict fails, honestly), not a crash or a silent pass."""
    brick_repository = _brick_repository(tmp_path)
    brick = brick_repository.create(_make_event_brick(entry_point=None))
    with TestClient(_event_app(tmp_path)) as client:
        service = ReplayBrickService(_telemetry(), brick_repository, client)

        result = service(ReplayBrickRequest(regression_brick_id=brick.regression_brick_id))

    assert result.status == "success"
    assert result.result.data["verdict"]["overall_passed"] is False
    assert any("__replay_error__" in d for d in result.result.data["verdict"]["diffs"])


def test_replay_event_to_subscriber_brick_gives_up_when_no_span_appears_in_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A channel with no subscriber never produces an `event-to-subscriber` span at all --
    proves the poll loop actually gives up (bounded), rather than hanging or crashing."""
    import nanobar_api.regression_brick.regression_brick_analysis_service as service_module

    monkeypatch.setattr(service_module, "_REPLAY_SPAN_POLL_ATTEMPTS", 2)
    monkeypatch.setattr(service_module, "_REPLAY_SPAN_POLL_INTERVAL_S", 0.01)

    app = _event_app(tmp_path)
    app.state.event_bus._subscribers.clear()  # no subscriber -> no capture_layer() span ever

    brick_repository = _brick_repository(tmp_path)
    brick = brick_repository.create(_make_event_brick())
    with TestClient(app) as client:
        service = ReplayBrickService(_telemetry(), brick_repository, client)

        result = service(ReplayBrickRequest(regression_brick_id=brick.regression_brick_id))

    assert result.status == "success"
    assert result.result.data["verdict"]["overall_passed"] is False
    # `_diff_paths` reports mismatched *keys*, not values, when one side has a key the other
    # doesn't -- `__replay_error__` (present only on the replayed side) is exactly that signal.
    assert any("__replay_error__" in d for d in result.result.data["verdict"]["diffs"])


def test_replay_event_to_subscriber_brick_with_non_domain_channel_fails_the_verdict_honestly(
    tmp_path: Path,
) -> None:
    """`entry_point` whose channel isn't `"domain."`-prefixed makes the trigger endpoint itself
    reject it (400, `NanobarEventBus.dispatch_now()`'s own channel-namespace check) -- reported
    as a diff, not an unhandled crash through the service."""
    brick_repository = _brick_repository(tmp_path)
    brick = brick_repository.create(_make_event_brick(entry_point="event-not-a-domain-channel"))
    with TestClient(_event_app(tmp_path)) as client:
        service = ReplayBrickService(_telemetry(), brick_repository, client)

        result = service(ReplayBrickRequest(regression_brick_id=brick.regression_brick_id))

    assert result.status == "success"
    assert result.result.data["verdict"]["overall_passed"] is False


class _FakeSpansResponse:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        return self._body


class _FakeReplayClient:
    """A minimal stand-in exercising `_dispatch_event_to_subscriber()`'s polling branches
    directly -- a transient non-200 poll response, and a span that exists but doesn't match
    `nanobar_type`, are both awkward to provoke through a real app/route (they're not error
    conditions `replay_routes.py`'s own handlers ever actually produce)."""

    def __init__(self, poll_responses: list[_FakeSpansResponse]) -> None:
        self._poll_responses = iter(poll_responses)

    def post(self, path: str, json: dict[str, Any], headers: dict[str, str]) -> _FakeSpansResponse:
        return _FakeSpansResponse(200, {"event_id": "ev-1", "trace_id": "trace-x"})

    def get(self, path: str, params: dict[str, Any]) -> _FakeSpansResponse:
        return next(self._poll_responses)


def test_dispatch_event_to_subscriber_tolerates_a_transient_non_200_poll_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import nanobar_api.regression_brick.regression_brick_analysis_service as service_module

    monkeypatch.setattr(service_module, "_REPLAY_SPAN_POLL_INTERVAL_S", 0.0)
    client = _FakeReplayClient(
        [
            _FakeSpansResponse(503, None),  # transient failure -- keep polling, don't give up
            _FakeSpansResponse(200, [{"payload": {"nanobar_type": "event-to-subscriber", "response": {"ok": True}}}]),
        ]
    )
    brick = _make_event_brick()

    result = service_module._dispatch_event_to_subscriber(client, brick, None)  # type: ignore[arg-type]

    assert result == {"status_code": None, "payload": {"ok": True}}


def test_dispatch_event_to_subscriber_skips_spans_with_a_different_nanobar_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import nanobar_api.regression_brick.regression_brick_analysis_service as service_module

    monkeypatch.setattr(service_module, "_REPLAY_SPAN_POLL_INTERVAL_S", 0.0)
    client = _FakeReplayClient(
        [
            _FakeSpansResponse(
                200,
                [
                    {"payload": {"nanobar_type": "controller-request-response", "response": {"irrelevant": True}}},
                    {"payload": {"nanobar_type": "event-to-subscriber", "response": {"ok": True}}},
                ],
            )
        ]
    )
    brick = _make_event_brick()

    result = service_module._dispatch_event_to_subscriber(client, brick, None)  # type: ignore[arg-type]

    assert result == {"status_code": None, "payload": {"ok": True}}


def test_replay_worker_nanobar_type_still_has_no_dispatch(tmp_path: Path) -> None:
    brick_repository = _brick_repository(tmp_path)
    brick = brick_repository.create(
        _make_event_brick(
            regression_brick_id="rbrick-worker",
            entry_point="worker-domain.appointments",
            nanobar_type="worker",
            content_hash="sha256:worker-2",
        )
    )
    service = ReplayBrickService(_telemetry(), brick_repository, _client(_event_app(tmp_path)))

    result = service(ReplayBrickRequest(regression_brick_id=brick.regression_brick_id))

    assert result.status == "error"
    assert result.result.data is None
    assert "worker" in result.result.msg_summary
