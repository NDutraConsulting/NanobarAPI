import re
from collections.abc import Awaitable, Callable

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from nanobar_api.eventbus.events import Event
from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.middleware.trace import (
    TRACING_ENABLED_ENV_VAR,
    EventBusTraceMiddleware,
    configure_tracing,
    current_span_id,
    current_trace_id,
)

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_HEX16 = re.compile(r"^[0-9a-f]{16}$")

# A real SDK TracerProvider so spans carry real, non-NoOp trace/span ids. The OTel API only
# allows the global provider to be set once per process; setting it here at import time is
# the one place that happens for this whole test module.
otel_trace.set_tracer_provider(TracerProvider())


async def _ping(request: Request) -> JSONResponse:
    return JSONResponse(
        {"trace_id": current_trace_id.get(), "span_id": current_span_id.get()},
        headers={"trace-id": current_trace_id.get() or "", "span-id": current_span_id.get() or ""},
    )


async def _boom(request: Request) -> JSONResponse:
    raise RuntimeError("boom")


async def _opaque_app(scope: Scope, receive: Receive, send: Send) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b""})


def _repository(*channels: str) -> EventQueueRepository:
    return EventQueueRepository([ChannelConfig(name=c) for c in channels or ("trace",)])


def _build_app(repository: EventQueueRepository, channel: str = "trace") -> Starlette:
    return Starlette(
        routes=[
            Route("/items/{item_id}", _ping),
            Route("/boom", _boom),
        ],
        middleware=[Middleware(EventBusTraceMiddleware, repository=repository, channel=channel)],
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


async def _drive(app: Callable[[Scope, Receive, Send], Awaitable[None]], scope: Scope) -> list[Message]:
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    await app(scope, _receive, send)
    return sent


def test_request_emits_one_event_with_expected_payload() -> None:
    repository = _repository()
    client = TestClient(_build_app(repository))

    response = client.get("/items/42", headers={"user-agent": "pytest-agent"})

    assert response.status_code == 200
    event = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    assert event.channel == "trace"
    assert event.payload["name"] == "GET /items/{item_id}"
    assert event.payload["http.request.method"] == "GET"
    assert event.payload["http.route"] == "/items/{item_id}"
    assert event.payload["status_code"] == 200
    assert event.payload["error"] is False
    assert event.payload["attributes"]["user_agent.original"] == "pytest-agent"
    assert event.payload["attributes"]["client.address"] == "testclient"
    assert event.payload["attributes"]["server.address"] == "testserver"
    # Exactly one event reaches the channel — no duplicates.
    assert repository.get_any(["trace"], timeout=0.1) is None


def test_trace_and_span_ids_are_nonempty_hex() -> None:
    repository = _repository()
    client = TestClient(_build_app(repository))

    response = client.get("/items/1")

    event = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    assert event.trace_id is not None
    assert event.span_id is not None
    assert _HEX32.match(event.trace_id)
    assert _HEX16.match(event.span_id)
    # The same ids were visible to the handler via the response headers, confirming they
    # match what the contextvars exposed during the request.
    assert response.headers["trace-id"] == event.trace_id
    assert response.headers["span-id"] == event.span_id


def test_default_channel_is_trace() -> None:
    repository = _repository()
    app = Starlette(
        routes=[Route("/items/{item_id}", _ping)],
        middleware=[Middleware(EventBusTraceMiddleware, repository=repository)],
    )
    client = TestClient(app)

    client.get("/items/1")

    assert repository.get_any(["trace"], timeout=1.0) is not None


def test_custom_channel_is_respected() -> None:
    repository = _repository("custom")
    client = TestClient(_build_app(repository, channel="custom"))

    client.get("/items/1")

    assert repository.get_any(["custom"], timeout=1.0) is not None


def test_nested_router_mount_resolves_combined_route_path() -> None:
    repository = _repository()
    app = Starlette(
        routes=[Mount("/sub", routes=[Route("/inner", _ping)])],
        middleware=[Middleware(EventBusTraceMiddleware, repository=repository)],
    )
    client = TestClient(app)

    response = client.get("/sub/inner")

    assert response.status_code == 200
    event = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    assert event.payload["http.route"] == "/sub/inner"


def test_opaque_mount_uses_its_root_path() -> None:
    repository = _repository()
    app = Starlette(
        routes=[Mount("/files", app=_opaque_app)],
        middleware=[Middleware(EventBusTraceMiddleware, repository=repository)],
    )
    client = TestClient(app)

    client.get("/files/anything")

    event = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    assert event.payload["http.route"] == "/files"


def test_opaque_root_mount_defaults_to_slash() -> None:
    repository = _repository()
    app = Starlette(
        routes=[Mount("", app=_opaque_app)],
        middleware=[Middleware(EventBusTraceMiddleware, repository=repository)],
    )
    client = TestClient(app)

    client.get("/anything")

    event = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    assert event.payload["http.route"] == "/"


def test_no_route_match_leaves_span_named_after_method() -> None:
    repository = _repository()
    client = TestClient(_build_app(repository))

    response = client.get("/does-not-exist")

    assert response.status_code == 404
    event = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    assert event.payload["http.route"] is None
    assert event.payload["name"] == "GET"


def test_no_tracer_configured_skips_instrumentation(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _repository()
    monkeypatch.setattr(otel_trace, "get_tracer_provider", lambda: otel_trace.NoOpTracerProvider())
    client = TestClient(_build_app(repository))

    response = client.get("/items/1")

    assert response.status_code == 200
    assert repository.get_any(["trace"], timeout=0.2) is None


# ------------------------------------------------------------------- configure_tracing ---


def _patch_no_real_provider(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Monkeypatches the OTel global provider accessors to look like nothing real is
    configured yet, and returns a list that captured `set_tracer_provider` calls append to
    — this avoids touching real global OTel state, which (per this module's own top-level
    comment) can only genuinely be set once per process.
    """
    set_calls: list[object] = []
    monkeypatch.setattr(otel_trace, "get_tracer_provider", lambda: otel_trace.NoOpTracerProvider())
    monkeypatch.setattr(otel_trace, "set_tracer_provider", set_calls.append)
    return set_calls


def test_configure_tracing_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    set_calls = _patch_no_real_provider(monkeypatch)
    monkeypatch.delenv(TRACING_ENABLED_ENV_VAR, raising=False)

    result = configure_tracing()

    assert result is False
    assert set_calls == []


def test_configure_tracing_enabled_via_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    set_calls = _patch_no_real_provider(monkeypatch)
    monkeypatch.setenv(TRACING_ENABLED_ENV_VAR, "1")

    result = configure_tracing()

    assert result is True
    assert len(set_calls) == 1
    assert isinstance(set_calls[0], TracerProvider)


@pytest.mark.parametrize("value", ["true", "YES", "On", "1"])
def test_configure_tracing_env_var_truthy_variants(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    set_calls = _patch_no_real_provider(monkeypatch)
    monkeypatch.setenv(TRACING_ENABLED_ENV_VAR, value)

    assert configure_tracing() is True
    assert len(set_calls) == 1


@pytest.mark.parametrize("value", ["0", "false", "no", "", "garbage"])
def test_configure_tracing_env_var_falsy_variants(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    set_calls = _patch_no_real_provider(monkeypatch)
    monkeypatch.setenv(TRACING_ENABLED_ENV_VAR, value)

    assert configure_tracing() is False
    assert set_calls == []


def test_configure_tracing_explicit_true_overrides_missing_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    set_calls = _patch_no_real_provider(monkeypatch)
    monkeypatch.delenv(TRACING_ENABLED_ENV_VAR, raising=False)

    assert configure_tracing(enabled=True) is True
    assert len(set_calls) == 1


def test_configure_tracing_explicit_false_overrides_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    set_calls = _patch_no_real_provider(monkeypatch)
    monkeypatch.setenv(TRACING_ENABLED_ENV_VAR, "1")

    assert configure_tracing(enabled=False) is False
    assert set_calls == []


def test_configure_tracing_does_not_override_an_already_real_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    set_calls: list[object] = []
    monkeypatch.setattr(otel_trace, "get_tracer_provider", lambda: TracerProvider())
    monkeypatch.setattr(otel_trace, "set_tracer_provider", set_calls.append)
    monkeypatch.setenv(TRACING_ENABLED_ENV_VAR, "1")

    result = configure_tracing()

    assert result is True
    assert set_calls == []  # already real — must not call set_tracer_provider again


@pytest.mark.anyio
async def test_reentrancy_guard_prevents_double_instrumentation() -> None:
    repository = _repository()
    inner_mw = EventBusTraceMiddleware(_opaque_app, repository, channel="trace")
    outer_mw = EventBusTraceMiddleware(inner_mw, repository, channel="trace")

    sent = await _drive(outer_mw, _base_scope())

    assert sent[0]["status"] == 200
    assert repository.get_any(["trace"], timeout=1.0) is not None
    # Only the outer instance actually traced — the inner saw the scope already marked.
    assert repository.get_any(["trace"], timeout=0.1) is None


@pytest.mark.anyio
async def test_non_http_scope_bypasses_tracing_entirely() -> None:
    repository = _repository()
    calls: list[Scope] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        calls.append(scope)

    mw = EventBusTraceMiddleware(app, repository, channel="trace")
    scope = _base_scope(type="lifespan")

    async def send(message: Message) -> None:
        pass

    await mw(scope, _receive, send)

    assert calls == [scope]
    assert repository.get_any(["trace"], timeout=0.1) is None


@pytest.mark.anyio
async def test_contextvars_set_during_request_and_reset_after() -> None:
    repository = _repository()
    observed: dict[str, str | None] = {}

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        observed["trace_id"] = current_trace_id.get()
        observed["span_id"] = current_span_id.get()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    mw = EventBusTraceMiddleware(app, repository, channel="trace")

    assert current_trace_id.get() is None
    assert current_span_id.get() is None

    await _drive(mw, _base_scope())

    assert observed["trace_id"] is not None
    assert observed["span_id"] is not None
    assert current_trace_id.get() is None
    assert current_span_id.get() is None

    event = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    assert event.trace_id == observed["trace_id"]
    assert event.span_id == observed["span_id"]


@pytest.mark.anyio
async def test_endpoint_exception_still_emits_event_and_resets_contextvars() -> None:
    repository = _repository()

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        raise RuntimeError("boom")

    mw = EventBusTraceMiddleware(app, repository, channel="trace")

    with pytest.raises(RuntimeError, match="boom"):
        await _drive(mw, _base_scope())

    assert current_trace_id.get() is None
    assert current_span_id.get() is None

    event = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    assert event.payload["error"] is True
    assert event.payload["attributes"]["error.type"] == "RuntimeError"
    assert event.payload["status_code"] is None


@pytest.mark.anyio
async def test_error_status_marks_event_as_error() -> None:
    repository = _repository()

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 503, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    mw = EventBusTraceMiddleware(app, repository, channel="trace")

    await _drive(mw, _base_scope())

    event = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    assert event.payload["status_code"] == 503
    assert event.payload["attributes"]["error.type"] == "503"


@pytest.mark.anyio
async def test_original_method_case_is_preserved_when_normalized() -> None:
    repository = _repository()
    mw = EventBusTraceMiddleware(_opaque_app, repository, channel="trace")

    await _drive(mw, _base_scope(method="get", query_string=b"a=1"))

    event: Event | None = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    assert event.payload["attributes"]["http.request.method_original"] == "get"
    assert event.payload["attributes"]["http.request.method"] == "GET"
    assert event.payload["attributes"]["url.query"] == "a=1"


@pytest.mark.anyio
async def test_missing_optional_scope_fields_are_tolerated() -> None:
    repository = _repository()
    mw = EventBusTraceMiddleware(_opaque_app, repository, channel="trace")
    scope = _base_scope()
    del scope["server"]
    del scope["client"]
    del scope["http_version"]

    await _drive(mw, scope)

    event = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    assert "server.address" not in event.payload["attributes"]
    assert "client.address" not in event.payload["attributes"]
    assert "network.protocol.version" not in event.payload["attributes"]


@pytest.mark.anyio
async def test_server_with_no_port_omits_server_port() -> None:
    repository = _repository()
    mw = EventBusTraceMiddleware(_opaque_app, repository, channel="trace")

    await _drive(mw, _base_scope(server=("testserver", None)))

    event = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    assert event.payload["attributes"]["server.address"] == "testserver"
    assert "server.port" not in event.payload["attributes"]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
