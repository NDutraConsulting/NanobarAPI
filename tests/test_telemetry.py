from __future__ import annotations

import re

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider

from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.middleware.trace import current_span_id, current_trace_id
from nanobar_api.telemetry import NanobarProps, NanobarTelemetry

_HEX32 = re.compile(r"^[0-9a-f]{32}$")

# A real SDK TracerProvider so spans carry real, non-NoOp trace/span ids. The OTel API only
# allows the global provider to be set once per process; setting it here at import time is
# the one place that happens for this whole test module (matches test_middleware_trace.py).
if isinstance(otel_trace.get_tracer_provider(), otel_trace.NoOpTracerProvider | otel_trace.ProxyTracerProvider):
    otel_trace.set_tracer_provider(TracerProvider())


def _repository() -> EventQueueRepository:
    return EventQueueRepository([ChannelConfig(name="trace")])


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ------------------------------------------------------------------------- @span basics ---


def test_span_decorator_wraps_sync_function_and_stays_sync_callable() -> None:
    telemetry = NanobarTelemetry(_repository())

    @telemetry.span("db.orders.select")
    def get_order(order_id: str) -> str:
        return f"order-{order_id}"

    result = get_order("42")  # no event loop, no await -- must still work

    assert result == "order-42"


@pytest.mark.anyio
async def test_span_decorator_wraps_async_function() -> None:
    telemetry = NanobarTelemetry(_repository())

    @telemetry.span("agent.tool.call")
    async def call_tool(payload: str) -> str:
        return f"result-{payload}"

    result = await call_tool("x")

    assert result == "result-x"


def test_span_emits_event_with_expected_payload() -> None:
    repository = _repository()
    telemetry = NanobarTelemetry(repository)

    @telemetry.span("db.orders.select")
    def get_order() -> None:
        pass

    get_order()

    event = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    assert event.channel == "trace"
    assert event.payload["name"] == "db.orders.select"
    assert event.payload["status"] == "closed"
    assert event.trace_id is not None
    assert event.span_id is not None
    assert _HEX32.match(event.trace_id)


def test_code_location_attributes_reflect_the_decorated_function() -> None:
    repository = _repository()
    telemetry = NanobarTelemetry(repository)

    @telemetry.span("db.orders.select")
    def get_order() -> None:
        pass

    get_order()

    event = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    # __qualname__-based, so it correctly includes the enclosing test function for a nested
    # def like this one -- not just the bare function name.
    assert event.payload["code.function.name"].endswith(".get_order")
    assert __name__ in event.payload["code.function.name"]
    assert event.payload["code.file.path"] == __file__
    assert isinstance(event.payload["code.line.number"], int)


def test_code_function_name_override_skips_auto_derivation() -> None:
    repository = _repository()
    telemetry = NanobarTelemetry(repository)

    @telemetry.span("db.orders.select", code_function_name="custom.override")
    def get_order() -> None:
        pass

    get_order()

    event = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    assert event.payload["code.function.name"] == "custom.override"
    assert "code.file.path" not in event.payload


def test_code_location_gracefully_degrades_for_uninspectable_functions() -> None:
    """A builtin (no Python source to introspect) must not raise -- code.file.path/
    code.line.number are simply omitted, code.function.name still works fine."""
    repository = _repository()
    telemetry = NanobarTelemetry(repository)

    wrapped = telemetry.span("builtin.call")(len)

    result = wrapped([1, 2, 3])

    assert result == 3
    event = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    assert event.payload["code.function.name"] == "builtins.len"
    assert "code.file.path" not in event.payload
    assert "code.line.number" not in event.payload


def test_custom_channel_is_respected() -> None:
    repository = EventQueueRepository([ChannelConfig(name="custom"), ChannelConfig(name="trace")])
    telemetry = NanobarTelemetry(repository, channel="custom")

    @telemetry.span("db.orders.select")
    def get_order() -> None:
        pass

    get_order()

    assert repository.get_any(["custom"], timeout=1.0) is not None
    assert repository.get_any(["trace"], timeout=0.1) is None


# ---------------------------------------------------------------- nesting / trace vs span ---


def test_nested_span_inherits_ambient_trace_id() -> None:
    repository = _repository()
    telemetry = NanobarTelemetry(repository)

    @telemetry.span("db.orders.select")
    def inner() -> None:
        pass

    @telemetry.span("api.orders.create")
    def outer() -> None:
        inner()

    outer()

    events = {}
    while (event := repository.get_any(["trace"], timeout=0.2)) is not None:
        events[event.payload["name"]] = event

    assert set(events) == {"api.orders.create", "db.orders.select"}
    assert events["api.orders.create"].trace_id == events["db.orders.select"].trace_id
    assert events["api.orders.create"].span_id != events["db.orders.select"].span_id


def test_span_with_no_ambient_trace_becomes_its_own_root() -> None:
    repository = _repository()
    telemetry = NanobarTelemetry(repository)

    @telemetry.span("db.orders.select")
    def get_order() -> None:
        pass

    get_order()

    event = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    assert event.trace_id is not None


def test_trace_starts_new_root_even_when_nested_inside_an_active_span() -> None:
    repository = _repository()
    telemetry = NanobarTelemetry(repository)

    @telemetry.trace("background_job")
    def background_job() -> None:
        pass

    @telemetry.span("api.orders.create")
    def outer() -> None:
        background_job()

    outer()

    events = {}
    while (event := repository.get_any(["trace"], timeout=0.2)) is not None:
        events[event.payload["name"]] = event

    assert set(events) == {"api.orders.create", "background_job"}
    assert events["api.orders.create"].trace_id != events["background_job"].trace_id


# -------------------------------------------------------------------------- nanobar= tag ---


def test_nanobar_props_omitted_means_no_nanobar_fields_in_payload() -> None:
    repository = _repository()
    telemetry = NanobarTelemetry(repository)

    @telemetry.span("db.orders.select")
    def get_order() -> None:
        pass

    get_order()

    event = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    assert "nanobar_type" not in event.payload


def test_nanobar_props_included_carries_all_fields_through() -> None:
    repository = _repository()
    telemetry = NanobarTelemetry(repository)
    props = NanobarProps(
        type="api-to-db",
        label="Order lookup",
        scenario_description="Fetch a single order by id.",
        component_source_description="checkout.repository",
        domain="checkout",
    )

    @telemetry.span("db.orders.select", nanobar=props)
    def get_order() -> None:
        pass

    get_order()

    event = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    assert event.payload["nanobar_type"] == "api-to-db"
    assert event.payload["nanobar_label"] == "Order lookup"
    assert event.payload["nanobar_scenario_description"] == "Fetch a single order by id."
    assert event.payload["nanobar_domain"] == "checkout"
    assert event.payload["nanobar_component_source_description"] == "checkout.repository"


def test_nanobar_props_with_only_type_omits_none_fields() -> None:
    repository = _repository()
    telemetry = NanobarTelemetry(repository)

    @telemetry.span("db.orders.select", nanobar=NanobarProps(type="api-to-db"))
    def get_order() -> None:
        pass

    get_order()

    event = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    assert event.payload["nanobar_type"] == "api-to-db"
    assert "nanobar_label" not in event.payload
    assert "nanobar_scenario_description" not in event.payload
    assert "nanobar_component_source_description" not in event.payload


# ------------------------------------------------------------------------- failure status ---


def test_sync_exception_marks_status_error_and_still_propagates() -> None:
    repository = _repository()
    telemetry = NanobarTelemetry(repository)

    @telemetry.span("db.orders.select")
    def get_order() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        get_order()

    event = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    assert event.payload["status"] == "error"
    assert event.payload["error.type"] == "ValueError"


@pytest.mark.anyio
async def test_async_exception_marks_status_error_and_still_propagates() -> None:
    repository = _repository()
    telemetry = NanobarTelemetry(repository)

    @telemetry.span("agent.tool.call")
    async def call_tool() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await call_tool()

    event = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    assert event.payload["status"] == "error"
    assert event.payload["error.type"] == "RuntimeError"


def test_contextvars_reset_after_exception() -> None:
    repository = _repository()
    telemetry = NanobarTelemetry(repository)

    @telemetry.span("db.orders.select")
    def get_order() -> None:
        raise ValueError("boom")

    assert current_trace_id.get() is None
    with pytest.raises(ValueError):
        get_order()
    assert current_trace_id.get() is None
    assert current_span_id.get() is None


# -------------------------------------------------------------------- context manager use ---


def test_span_usable_directly_as_a_context_manager() -> None:
    repository = _repository()
    telemetry = NanobarTelemetry(repository)

    with telemetry.span("api.orders.create", nanobar=NanobarProps(type="api-to-db")):
        pass

    event = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    assert event.payload["name"] == "api.orders.create"
    assert event.payload["nanobar_type"] == "api-to-db"


def test_context_manager_code_location_reflects_the_calling_function() -> None:
    repository = _repository()
    telemetry = NanobarTelemetry(repository)

    def create_order() -> None:
        with telemetry.span("api.orders.create"):
            pass

    create_order()

    event = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    assert event.payload["code.function.name"].endswith(".create_order")
    assert __name__ in event.payload["code.function.name"]


def test_context_manager_nests_under_decorator_span() -> None:
    repository = _repository()
    telemetry = NanobarTelemetry(repository)

    @telemetry.span("api.orders.create")
    def outer() -> None:
        with telemetry.span("api.orders.create.audit"):
            pass

    outer()

    events = {}
    while (event := repository.get_any(["trace"], timeout=0.2)) is not None:
        events[event.payload["name"]] = event

    assert set(events) == {"api.orders.create", "api.orders.create.audit"}
    assert events["api.orders.create"].trace_id == events["api.orders.create.audit"].trace_id


def test_context_manager_exception_still_emits_and_propagates() -> None:
    repository = _repository()
    telemetry = NanobarTelemetry(repository)

    with pytest.raises(ValueError, match="boom"):
        with telemetry.span("api.orders.create"):
            raise ValueError("boom")

    event = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    assert event.payload["status"] == "error"


# ------------------------------------------------------------------------ use_opentelemetry ---


def test_simple_mode_still_emits_a_correlatable_span() -> None:
    repository = _repository()
    telemetry = NanobarTelemetry(repository, use_opentelemetry=False)

    @telemetry.span("db.orders.select")
    def get_order() -> None:
        pass

    get_order()

    event = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    assert event.trace_id is not None
    assert event.span_id is not None
    assert event.payload["status"] == "closed"


def test_simple_mode_nested_span_inherits_ambient_trace_id() -> None:
    repository = _repository()
    telemetry = NanobarTelemetry(repository, use_opentelemetry=False)

    @telemetry.span("db.orders.select")
    def inner() -> None:
        pass

    @telemetry.span("api.orders.create")
    def outer() -> None:
        inner()

    outer()

    events = {}
    while (event := repository.get_any(["trace"], timeout=0.2)) is not None:
        events[event.payload["name"]] = event

    assert events["api.orders.create"].trace_id == events["db.orders.select"].trace_id


def test_simple_mode_trace_starts_new_root() -> None:
    repository = _repository()
    telemetry = NanobarTelemetry(repository, use_opentelemetry=False)

    @telemetry.trace("background_job")
    def background_job() -> None:
        pass

    @telemetry.span("api.orders.create")
    def outer() -> None:
        background_job()

    outer()

    events = {}
    while (event := repository.get_any(["trace"], timeout=0.2)) is not None:
        events[event.payload["name"]] = event

    assert events["api.orders.create"].trace_id != events["background_job"].trace_id


def test_simple_mode_child_nests_under_real_otel_mode_parent() -> None:
    """Confirms the ADR's claim: the flag changes how an id is minted, never the
    correlation mechanism -- a simple-mode span nests correctly under a real-OTel-mode
    parent since both populate the same current_trace_id/current_span_id contextvars."""
    repository = _repository()
    otel_telemetry = NanobarTelemetry(repository, use_opentelemetry=True)
    simple_telemetry = NanobarTelemetry(repository, use_opentelemetry=False)

    @simple_telemetry.span("db.orders.select")
    def inner() -> None:
        pass

    @otel_telemetry.span("api.orders.create")
    def outer() -> None:
        inner()

    outer()

    events = {}
    while (event := repository.get_any(["trace"], timeout=0.2)) is not None:
        events[event.payload["name"]] = event

    assert events["api.orders.create"].trace_id == events["db.orders.select"].trace_id


def test_simple_mode_exception_marks_status_error() -> None:
    repository = _repository()
    telemetry = NanobarTelemetry(repository, use_opentelemetry=False)

    @telemetry.span("db.orders.select")
    def get_order() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        get_order()

    event = repository.get_any(["trace"], timeout=1.0)
    assert event is not None
    assert event.payload["status"] == "error"
    assert event.payload["error.type"] == "ValueError"


# ------------------------------------------------------------------------------- opt-out ---


def test_no_tracer_configured_is_a_zero_overhead_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _repository()
    monkeypatch.setattr(otel_trace, "get_tracer_provider", lambda: otel_trace.NoOpTracerProvider())
    telemetry = NanobarTelemetry(repository)

    @telemetry.span("db.orders.select")
    def get_order(order_id: str) -> str:
        return f"order-{order_id}"

    result = get_order("42")

    assert result == "order-42"
    assert repository.get_any(["trace"], timeout=0.2) is None


def test_no_tracer_configured_simple_mode_is_also_a_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _repository()
    monkeypatch.setattr(otel_trace, "get_tracer_provider", lambda: otel_trace.NoOpTracerProvider())
    telemetry = NanobarTelemetry(repository, use_opentelemetry=False)

    @telemetry.span("db.orders.select")
    def get_order() -> None:
        pass

    get_order()

    assert repository.get_any(["trace"], timeout=0.2) is None


# --------------------------------------------------------------------------- functools.wraps ---


def test_decorator_preserves_function_metadata() -> None:
    telemetry = NanobarTelemetry(_repository())

    @telemetry.span("db.orders.select")
    def get_order(order_id: str) -> str:
        """Fetch an order."""
        return order_id

    assert get_order.__name__ == "get_order"
    assert get_order.__doc__ == "Fetch an order."
