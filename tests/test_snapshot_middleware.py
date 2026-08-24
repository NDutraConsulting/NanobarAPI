import base64
import hashlib
from collections.abc import Awaitable, Callable

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from nanobar_api.capture.policy import CapturePolicy
from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.middleware.snapshot import SnapshotMiddleware


async def _echo(request: Request) -> JSONResponse:
    body = await request.body()
    return JSONResponse({"received": len(body)})


async def _boom(request: Request) -> JSONResponse:
    raise RuntimeError("boom")


def _repository(*channels: str) -> EventQueueRepository:
    return EventQueueRepository([ChannelConfig(name=c) for c in channels or ("snapshot",)])


def _build_app(
    repository: EventQueueRepository, policy: CapturePolicy | None = None, channel: str = "snapshot"
) -> Starlette:
    return Starlette(
        routes=[
            Route("/echo", _echo, methods=["GET", "POST"]),
            Route("/boom", _boom),
        ],
        middleware=[Middleware(SnapshotMiddleware, repository=repository, policy=policy, channel=channel)],
    )


def _base_scope(**overrides: object) -> Scope:
    scope: dict[str, object] = {
        "type": "http",
        "method": "GET",
        "path": "/x",
        "raw_path": b"/x",
        "query_string": b"",
        "headers": [],
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 123),
        "http_version": "1.1",
        "app": Starlette(),
    }
    scope.update(overrides)
    return scope


async def _receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


async def _drive(
    app: Callable[[Scope, Receive, Send], Awaitable[None]], scope: Scope, receive: Receive = _receive
) -> list[Message]:
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return sent


def test_get_with_no_body_captures_request_and_response() -> None:
    repository = _repository()
    client = TestClient(_build_app(repository))

    response = client.get("/echo")

    assert response.status_code == 200
    event = repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None
    assert event.channel == "snapshot"
    assert event.payload["error"] is False
    assert event.payload["request"]["method"] == "GET"
    assert event.payload["request"]["path"] == "/echo"
    assert event.payload["request"]["body_total_bytes"] == 0
    assert event.payload["request"]["body_truncated"] is False
    assert event.payload["response"]["status_code"] == 200
    assert event.payload["response"]["body_truncated"] is False
    assert "content_hash" in event.payload
    # Exactly one event — no duplicates.
    assert repository.get_any(["snapshot"], timeout=0.1) is None


def test_post_with_body_under_cap_captures_full_body_untruncated() -> None:
    repository = _repository()
    client = TestClient(_build_app(repository))
    body = b'{"hello": "world"}'

    response = client.post("/echo", content=body)

    assert response.status_code == 200
    event = repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None
    request_payload = event.payload["request"]
    assert request_payload["body_total_bytes"] == len(body)
    assert request_payload["body_truncated"] is False
    assert base64.b64decode(request_payload["body_b64"]) == body
    assert request_payload["body_sha256"] == hashlib.sha256(body).hexdigest()


def test_body_larger_than_cap_is_truncated_but_hash_reflects_full_body() -> None:
    repository = _repository()
    policy = CapturePolicy(body_cap_bytes=16)
    client = TestClient(_build_app(repository, policy=policy))
    body = b"x" * 100  # well over the 16-byte cap

    response = client.post("/echo", content=body)

    assert response.status_code == 200
    event = repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None
    request_payload = event.payload["request"]
    assert request_payload["body_total_bytes"] == 100
    assert request_payload["body_truncated"] is True
    captured = base64.b64decode(request_payload["body_b64"])
    assert len(captured) == 16
    assert captured == body[:16]
    # The hash must reflect the FULL, untruncated body, independently verified here.
    assert request_payload["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert request_payload["body_sha256"] != hashlib.sha256(captured).hexdigest()


def test_headers_and_query_params_outside_allowlist_are_excluded() -> None:
    repository = _repository()
    policy = CapturePolicy(header_allowlist=("content-type",), query_param_allowlist=("keep",))
    client = TestClient(_build_app(repository, policy=policy))

    response = client.get(
        "/echo",
        params={"keep": "yes", "drop": "no"},
        headers={"authorization": "Bearer secret", "content-type": "text/plain"},
    )

    assert response.status_code == 200
    event = repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None
    request_payload = event.payload["request"]
    assert request_payload["query_params"] == {"keep": "yes"}
    assert "drop" not in request_payload["query_params"]
    assert request_payload["headers"].get("content-type") == "text/plain"
    assert "authorization" not in request_payload["headers"]


@pytest.mark.anyio
async def test_endpoint_exception_still_emits_event_with_error_true_and_reraises() -> None:
    repository = _repository()

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        raise RuntimeError("boom")

    mw = SnapshotMiddleware(app, repository, channel="snapshot")

    with pytest.raises(RuntimeError, match="boom"):
        await _drive(mw, _base_scope())

    event = repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None
    assert event.payload["error"] is True
    # No send() call ever happened, so there is no response to report.
    assert event.payload["response"]["status_code"] is None
    assert event.payload["response"]["body_total_bytes"] == 0


@pytest.mark.anyio
async def test_reentrancy_guard_prevents_double_capture() -> None:
    repository = _repository()

    async def inner_app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    inner_mw = SnapshotMiddleware(inner_app, repository, channel="snapshot")
    outer_mw = SnapshotMiddleware(inner_mw, repository, channel="snapshot")

    sent = await _drive(outer_mw, _base_scope())

    assert sent[0]["status"] == 200
    assert repository.get_any(["snapshot"], timeout=1.0) is not None
    # Only the outer instance actually captured — the inner saw the scope already marked.
    assert repository.get_any(["snapshot"], timeout=0.1) is None


@pytest.mark.anyio
async def test_multi_chunk_request_body_stops_buffering_once_cap_reached() -> None:
    repository = _repository()
    policy = CapturePolicy(body_cap_bytes=5)
    chunks = iter(
        [
            {"type": "http.request", "body": b"12345", "more_body": True},
            {"type": "http.request", "body": b"67890", "more_body": False},
        ]
    )

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await receive()
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    mw = SnapshotMiddleware(app, repository, policy=policy, channel="snapshot")

    async def receive() -> Message:
        return next(chunks)

    await _drive(mw, _base_scope(), receive=receive)

    event = repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None
    request_payload = event.payload["request"]
    # Both chunks are hashed (full body), but only the first 5 bytes are buffered.
    assert request_payload["body_total_bytes"] == 10
    assert request_payload["body_truncated"] is True
    assert base64.b64decode(request_payload["body_b64"]) == b"12345"
    assert request_payload["body_sha256"] == hashlib.sha256(b"1234567890").hexdigest()


@pytest.mark.anyio
async def test_non_request_receive_message_passes_through_unchanged() -> None:
    repository = _repository()

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        message = await receive()
        assert message == {"type": "http.disconnect"}
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    mw = SnapshotMiddleware(app, repository, channel="snapshot")

    async def receive() -> Message:
        return {"type": "http.disconnect"}

    await _drive(mw, _base_scope(), receive=receive)

    event = repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None
    assert event.payload["request"]["body_total_bytes"] == 0


@pytest.mark.anyio
async def test_non_start_non_body_send_message_passes_through_unchanged() -> None:
    repository = _repository()

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.pathsend", "path": "/tmp/x"})
        await send({"type": "http.response.body", "body": b""})

    mw = SnapshotMiddleware(app, repository, channel="snapshot")

    sent = await _drive(mw, _base_scope())

    assert sent[1] == {"type": "http.response.pathsend", "path": "/tmp/x"}
    event = repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None


@pytest.mark.anyio
async def test_non_http_scope_bypasses_capture_entirely() -> None:
    repository = _repository()
    calls: list[Scope] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        calls.append(scope)

    mw = SnapshotMiddleware(app, repository, channel="snapshot")
    scope = _base_scope(type="lifespan")

    async def send(message: Message) -> None:
        pass

    await mw(scope, _receive, send)

    assert calls == [scope]
    assert repository.get_any(["snapshot"], timeout=0.1) is None


def test_default_channel_is_snapshot() -> None:
    repository = _repository()
    app = Starlette(
        routes=[Route("/echo", _echo)],
        middleware=[Middleware(SnapshotMiddleware, repository=repository)],
    )
    client = TestClient(app)

    client.get("/echo")

    assert repository.get_any(["snapshot"], timeout=1.0) is not None


def test_custom_channel_is_respected() -> None:
    repository = _repository("custom")
    client = TestClient(_build_app(repository, channel="custom"))

    client.get("/echo")

    assert repository.get_any(["custom"], timeout=1.0) is not None


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
