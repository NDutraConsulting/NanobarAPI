"""NanobarTelemetry — `@span`/`@trace` decorators for instrumenting arbitrary code (not just
the ASGI request path) with correlated spans. See `.focusari/nanobar-telemetry-adr.md` for the
full design; this docstring only summarizes.

Every span carries standard OTel semantic-convention code-location attributes
(`code.function.name`/`code.file.path`/`code.line.number`) unconditionally, and is optionally
tagged to a nanobar via `NanobarProps`. Tagging only attaches `nanobar_type`/`label`/...
metadata to the emitted event — it does **not** synchronously write a `nanobars` row to
`regression_bricks.db`. That mirrors how `generate_bricks()` already works: capture goes to the
eventbus (`events.db`) fast and in-process, and deriving durable rows in `regression_bricks.db`
from captured events is a separate, later batch step — not decided or built here, same as the
ADR's own §5 leaves "does `@span` populate a brick's `trace_refs`" to `generate_bricks()`, not
this module.

Both `@span(name)` and `@trace(name)` respect `configure_tracing()`'s opt-in exactly like
`EventBusTraceMiddleware` does: with no real `TracerProvider` configured
(`NANOBAR_TRACING_ENABLED` unset), every call is a zero-overhead passthrough — the wrapped
function still runs, nothing else happens.
"""

from __future__ import annotations

import functools
import inspect
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import TracerProvider

from nanobar_api.eventbus.events import Event
from nanobar_api.eventbus.queue_repository import EventQueueRepository
from nanobar_api.middleware.trace import configure_tracing, current_span_id, current_trace_id


@dataclass(frozen=True)
class NanobarProps:
    """Optionally passed to `.span(...)`/`.trace(...)` to tag a span as evidence for a nanobar.

    A nanobar's identity is `(type, code.function.name)` — derived from the decorated function
    itself, not a separately-typed key (see the ADR §2.3 for why a `nanobar_track_key` was
    considered and rejected). `label`/`scenario_description`/`component_source_description`/
    `domain` are carried through to whatever later step turns this into an actual `nanobars`
    row. `source_info` isn't a field here — it's populated from the same code-location data
    every span already carries (`code.function.name`/`code.file.path`/`code.line.number`, see
    `_code_location`), not something a caller needs to supply separately.
    """

    type: str
    label: str | None = None
    scenario_description: str | None = None
    component_source_description: str | None = None
    domain: str | None = None


def _code_function_name(func: Callable[..., Any]) -> str:
    """`module.Class.method` — matches the documented format of OTel's own
    `code.function.name` semantic-convention attribute (confirmed against the real installed
    `opentelemetry-semconv` package; their own example is `com.example.MyHttpService.serveRequest`).
    `func.__module__` is used for the module portion specifically for portability — it doesn't
    break across machines/checkouts or a different working directory the way a literal
    filesystem path would.
    """
    return f"{func.__module__}.{func.__qualname__}"


def _code_location(func: Callable[..., Any]) -> dict[str, Any]:
    attrs: dict[str, Any] = {"code.function.name": _code_function_name(func)}
    try:
        attrs["code.file.path"] = inspect.getsourcefile(func)
    except TypeError:
        pass
    try:
        _, line_number = inspect.getsourcelines(func)
        attrs["code.line.number"] = line_number
    except (TypeError, OSError):
        pass
    return attrs


def _caller_code_location() -> dict[str, Any]:
    """Best-effort code-location attributes for `with telemetry.span(...):` used directly
    (not as a decorator) — there's no decorated function to introspect in that usage, so this
    reads the call site's own frame instead. Must be called from exactly one frame inside
    `_SpanContext.__enter__` for the frame-depth math below to land on the right frame.
    """
    frame = inspect.currentframe()
    try:
        caller = frame.f_back.f_back if frame is not None and frame.f_back is not None else None
        if caller is None:  # pragma: no branch - not reachable with a real call stack in practice
            return {}  # pragma: no cover
        code = caller.f_code
        module_name = caller.f_globals.get("__name__", "?")
        qualname = getattr(code, "co_qualname", code.co_name)
        return {
            "code.function.name": f"{module_name}.{qualname}",
            "code.file.path": code.co_filename,
            "code.line.number": caller.f_lineno,
        }
    finally:
        del frame


class NanobarTelemetry:
    """See `.focusari/nanobar-telemetry-adr.md` for the full design.

    An instance, not free functions, because emitting a span requires a live
    `EventQueueRepository` to hand the finished span to — pass the *same* instance the app's
    `EventBusTraceMiddleware` is constructed with, so a trace started by the middleware and
    continued by a `@span`-decorated function land in the same queryable trace.
    """

    def __init__(
        self, repository: EventQueueRepository, *, channel: str = "trace", use_opentelemetry: bool = True
    ) -> None:
        self.repository = repository
        self.channel = channel
        self.use_opentelemetry = use_opentelemetry
        configure_tracing()

    def span(
        self, name: str, *, nanobar: NanobarProps | None = None, code_function_name: str | None = None
    ) -> _SpanContext:
        """Creates a **child** span under whatever OTel span is currently active — or the root
        of its own trace if none is active (graceful degradation, not a failure)."""
        return _SpanContext(self, name, new_root=False, nanobar=nanobar, code_function_name=code_function_name)

    def trace(
        self, name: str, *, nanobar: NanobarProps | None = None, code_function_name: str | None = None
    ) -> _SpanContext:
        """Always starts a **new root trace**, deliberately detaching from any ambient context —
        for entry points not behind `EventBusTraceMiddleware` (a background worker, a scheduled
        job) where attributing the work to whatever request happened to be active would be
        wrong."""
        return _SpanContext(self, name, new_root=True, nanobar=nanobar, code_function_name=code_function_name)

    def _start(
        self, name: str, *, new_root: bool, nanobar: NanobarProps | None, code_location: dict[str, Any]
    ) -> AbstractContextManager[None]:
        tracer_provider = trace.get_tracer_provider()
        if isinstance(tracer_provider, trace.NoOpTracerProvider | trace.ProxyTracerProvider):
            return _noop()
        if self.use_opentelemetry:
            return self._start_otel(
                name, new_root=new_root, nanobar=nanobar, code_location=code_location, tracer_provider=tracer_provider
            )
        return self._start_simple(name, new_root=new_root, nanobar=nanobar, code_location=code_location)

    @contextmanager
    def _start_otel(
        self,
        name: str,
        *,
        new_root: bool,
        nanobar: NanobarProps | None,
        code_location: dict[str, Any],
        tracer_provider: TracerProvider,
    ) -> Iterator[None]:
        tracer = tracer_provider.get_tracer("nanobar_api")
        # An empty Context() has no span in it, so the SDK generates a new trace_id with no
        # parent — the simplest correct way to force detachment, consistent with how
        # middleware/trace.py already passes `context=` explicitly for the same reason.
        # `None` (the default) instead lets the SDK use whatever OTel context is ambient.
        detached_context = Context() if new_root else None
        error_type: str | None = None
        with tracer.start_as_current_span(name, context=detached_context) as span:
            span_context = span.get_span_context()
            trace_id = format(span_context.trace_id, "032x")
            span_id = format(span_context.span_id, "016x")
            trace_token = current_trace_id.set(trace_id)
            span_token = current_span_id.set(span_id)
            try:
                yield
            except Exception as exc:
                error_type = type(exc).__qualname__
                span.set_attribute("error.type", error_type)
                raise
            finally:
                self._emit(name, trace_id, span_id, nanobar=nanobar, code_location=code_location, error_type=error_type)
                current_trace_id.reset(trace_token)
                current_span_id.reset(span_token)

    @contextmanager
    def _start_simple(
        self, name: str, *, new_root: bool, nanobar: NanobarProps | None, code_location: dict[str, Any]
    ) -> Iterator[None]:
        parent_trace_id = None if new_root else current_trace_id.get()
        trace_id = parent_trace_id or uuid.uuid4().hex
        span_id = uuid.uuid4().hex[:16]
        trace_token = current_trace_id.set(trace_id)
        span_token = current_span_id.set(span_id)
        error_type: str | None = None
        try:
            yield
        except Exception as exc:
            error_type = type(exc).__qualname__
            raise
        finally:
            self._emit(name, trace_id, span_id, nanobar=nanobar, code_location=code_location, error_type=error_type)
            current_trace_id.reset(trace_token)
            current_span_id.reset(span_token)

    def _emit(
        self,
        name: str,
        trace_id: str,
        span_id: str,
        *,
        nanobar: NanobarProps | None,
        code_location: dict[str, Any],
        error_type: str | None,
    ) -> None:
        payload: dict[str, Any] = {"name": name, "status": "error" if error_type else "closed", **code_location}
        if error_type is not None:
            payload["error.type"] = error_type
        if nanobar is not None:
            payload["nanobar_type"] = nanobar.type
            if nanobar.label is not None:
                payload["nanobar_label"] = nanobar.label
            if nanobar.scenario_description is not None:
                payload["nanobar_scenario_description"] = nanobar.scenario_description
            if nanobar.component_source_description is not None:
                payload["nanobar_component_source_description"] = nanobar.component_source_description
            if nanobar.domain is not None:
                payload["nanobar_domain"] = nanobar.domain
        event = Event(
            event_id=str(uuid.uuid4()),
            channel=self.channel,
            recorded_at_ns=time.time_ns(),
            monotonic_ns=time.monotonic_ns(),
            payload=payload,
            trace_id=trace_id,
            span_id=span_id,
        )
        self.repository.put(self.channel, event)


@contextmanager
def _noop() -> Iterator[None]:
    yield


class _SpanContext:
    """Returned by `NanobarTelemetry.span()`/`.trace()` — usable directly as
    `with telemetry.span(...):`, or as `@telemetry.span(...)` to decorate a function (sync or
    async; both stay their original calling convention, never forced through a threadpool the
    way `nanobar_api.routing.adapt_handler` does for ASGI endpoints — a plain sync function
    decorated here must still be callable synchronously with no event loop required, since it
    may be called from fully sync code, e.g. `nanobar_api.bricks.store`'s functions).
    """

    def __init__(
        self,
        telemetry: NanobarTelemetry,
        name: str,
        *,
        new_root: bool,
        nanobar: NanobarProps | None,
        code_function_name: str | None,
    ) -> None:
        self._telemetry = telemetry
        self._name = name
        self._new_root = new_root
        self._nanobar = nanobar
        self._code_function_name_override = code_function_name
        self._active_cm: AbstractContextManager[None] | None = None

    def __enter__(self) -> _SpanContext:
        code_location = (
            {"code.function.name": self._code_function_name_override}
            if self._code_function_name_override
            else _caller_code_location()
        )
        self._active_cm = self._telemetry._start(
            self._name, new_root=self._new_root, nanobar=self._nanobar, code_location=code_location
        )
        self._active_cm.__enter__()
        return self

    def __exit__(self, *exc_info: object) -> bool | None:
        assert self._active_cm is not None
        return self._active_cm.__exit__(*exc_info)  # type: ignore[arg-type]

    def __call__(self, func: Callable[..., Any]) -> Callable[..., Any]:
        code_location = (
            {"code.function.name": self._code_function_name_override}
            if self._code_function_name_override
            else _code_location(func)
        )
        telemetry, name, new_root, nanobar = self._telemetry, self._name, self._new_root, self._nanobar

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with telemetry._start(name, new_root=new_root, nanobar=nanobar, code_location=code_location):
                    return await func(*args, **kwargs)

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with telemetry._start(name, new_root=new_root, nanobar=nanobar, code_location=code_location):
                return func(*args, **kwargs)

        return sync_wrapper
