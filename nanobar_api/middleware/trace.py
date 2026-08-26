"""Server-span tracing middleware that emits finished spans onto the eventbus.

Ported from focusari_asgi's `middleware/opentelemetry.py` (method, route resolution,
and status capture in the send-wrapper are the same logic, correct as-is). Adapted for
NanobarAPI in two ways:

* focusari_asgi's forked routing exposes the matched `Route`/`Mount` object via
  `scope["route"]`; real (upstream) Starlette does not set that key at all, so route
  resolution here replays the router's own matching algorithm against `scope["app"].router`
  to find the same leaf route (see `_resolve_route_path`).
* Instead of relying on a synchronous OTel exporter, the finished span's data is
  serialized into an `Event` and handed to an `EventQueueRepository` in a `finally`
  block *after* the span's context manager has exited — nothing in the request path
  calls a real exporter.
"""

from __future__ import annotations

import contextvars
import os
import time
import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from opentelemetry import propagate, trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import SpanKind, Status, StatusCode
from starlette.routing import BaseRoute, Match, Mount

from nanobar_api.eventbus.events import Event
from nanobar_api.eventbus.queue_repository import EventQueueRepository

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: contextvars for the active request's trace/span id, populated from the real OTel
#: span at span start and reset at span end, so logs and downstream spans emitted
#: anywhere during the request can correlate without threading the span through.
current_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_trace_id", default=None)
current_span_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_span_id", default=None)

#: The active route key ("METHOD /path"), set by `NanobarController.handle()` at controller
#: entry and reset at exit — `nanobar_api.orm.NanobarORMWrapper`'s SQLAlchemy event listeners
#: read this to stamp a `route_key` on `orm-request-response` captures, the same correlation key
#: `nanobar_api.bricks.binding` already uses for validator/controller layers. Named
#: `current_controller_name` in the source spec; `route_key` matches the value actually threaded
#: through this codebase's capture call sites instead of introducing a second, parallel concept.
current_route_key: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_route_key", default=None)

_SCOPE_KEY = "nanobar.trace"

#: Environment variable that opts into local trace capture — see `configure_tracing`.
TRACING_ENABLED_ENV_VAR = "NANOBAR_TRACING_ENABLED"
_TRUTHY = {"1", "true", "yes", "on"}


def configure_tracing(enabled: bool | None = None) -> bool:
    """Configures a local, non-exporting OTel `TracerProvider` so `EventBusTraceMiddleware`
    can capture real spans into this project's own eventbus, without requiring any external
    OTel SDK setup or backend — capturing spans locally and exporting them to a real
    external system are different concerns, and only the second one actually needs an
    external service. A bare `TracerProvider()` with no span processors attached generates
    real, correctly-random trace/span ids and supports real W3C context propagation; it just
    never sends anything anywhere, which is exactly what "capture into our own SQLite
    eventbus" needs and nothing more.

    Tracing is opt-in, not automatic, specifically so instrumentation is never silently
    active. `enabled` defaults to the `NANOBAR_TRACING_ENABLED` environment variable
    (`"1"`/`"true"`/`"yes"`/`"on"`, case-insensitive; anything else, including unset, is
    treated as disabled) when not given explicitly.

    Idempotent and safe to call more than once (e.g. once per `EventBusTraceMiddleware`
    instance constructed): does nothing if a real provider is already active, whether
    configured by an earlier call to this function or by the app itself for genuine external
    OTLP export — existing configuration is always respected, never overridden.

    Returns whether a real (non-NoOp/Proxy) tracer provider is active after this call.
    """
    if enabled is None:
        enabled = os.environ.get(TRACING_ENABLED_ENV_VAR, "").strip().lower() in _TRUTHY

    current = trace.get_tracer_provider()
    if not isinstance(current, trace.NoOpTracerProvider | trace.ProxyTracerProvider):
        return True  # a real provider — ours from an earlier call, or the app's own — is active

    if not enabled:
        return False

    trace.set_tracer_provider(TracerProvider())
    return True


class EventBusTraceMiddleware:
    """Create an OpenTelemetry server span per HTTP request and publish it to the eventbus.

    Uses real `Tracer`/`Span`/`propagate` objects so W3C trace context (traceparent,
    tracestate, baggage) is standards-correct, but never exports synchronously in the
    request path: at span end, the relevant fields are serialized into an `Event` and
    handed to `repository.put(channel, event)` for an out-of-band consumer to forward
    (e.g. to a real OTLP exporter) later.

    Calls `configure_tracing()` on construction, so local capture into the eventbus works
    immediately when `NANOBAR_TRACING_ENABLED` is set — no external OTel SDK setup or
    backend required for that. With the env var unset (the default) and no real provider
    configured for some other reason (e.g. the app's own external OTLP export setup), this
    middleware is a correct no-op: it still checks `scope["type"]` and passes non-HTTP
    scopes through, but emits nothing.
    """

    def __init__(self, app: ASGIApp, repository: EventQueueRepository, channel: str = "trace") -> None:
        self.app = app
        self.repository = repository
        self.channel = channel
        configure_tracing()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get(_SCOPE_KEY):
            await self.app(scope, receive, send)
            return

        scope[_SCOPE_KEY] = True
        try:
            await self._trace(scope, receive, send)
        finally:
            del scope[_SCOPE_KEY]

    async def _trace(self, scope: Scope, receive: Receive, send: Send) -> None:
        tracer_provider = trace.get_tracer_provider()
        if isinstance(tracer_provider, trace.NoOpTracerProvider | trace.ProxyTracerProvider):
            # No real tracer configured: nothing to correlate, so don't manufacture spans/events.
            await self.app(scope, receive, send)
            return

        original_method = scope.get("method", "")
        method = original_method.upper()

        headers: dict[str, list[str]] = {}
        for name, value in scope.get("headers", []):
            headers.setdefault(name.decode("latin-1").lower(), []).append(value.decode("latin-1"))

        attributes: dict[str, str | int] = {
            "http.request.method": method,
            "url.path": scope.get("path", ""),
            "url.scheme": scope.get("scheme", "http"),
        }
        if original_method != method:
            attributes["http.request.method_original"] = original_method
        query_string = scope.get("query_string", b"").decode("latin-1")
        if query_string:
            attributes["url.query"] = query_string
        server = scope.get("server")
        if server is not None:
            attributes["server.address"] = server[0]
            if server[1] is not None:
                attributes["server.port"] = server[1]
        if scope.get("http_version"):
            attributes["network.protocol.version"] = scope["http_version"]
        client = scope.get("client")
        if client is not None:
            attributes["client.address"] = client[0]
        if headers.get("user-agent"):
            attributes["user_agent.original"] = headers["user-agent"][0]

        # Captured before `self.app(...)` runs: real Starlette mutates scope["root_path"]
        # in place as it descends through Mounts while dispatching, so by the time route
        # resolution replays the match in the `finally` block below, the scope's root_path
        # would otherwise already reflect the leaf route rather than the request's original
        # entry point.
        original_root_path = scope.get("root_path", "")

        async def send_with_telemetry(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_code = message["status"]
                attributes["http.response.status_code"] = status_code
                span.set_attribute("http.response.status_code", status_code)
                if status_code >= 500:
                    attributes["error.type"] = str(status_code)
                    span.set_attribute("error.type", str(status_code))
                    span.set_status(Status(StatusCode.ERROR))
            await send(message)

        error_occurred = False
        route_path: str | None = None
        span_name = method
        trace_id_hex = ""
        span_id_hex = ""
        trace_token: contextvars.Token[str | None] | None = None
        span_token: contextvars.Token[str | None] | None = None

        try:
            with tracer_provider.get_tracer("nanobar_api").start_as_current_span(
                method,
                context=propagate.extract(headers),
                kind=SpanKind.SERVER,
                attributes=attributes,
            ) as span:
                span_context = span.get_span_context()
                trace_id_hex = format(span_context.trace_id, "032x")
                span_id_hex = format(span_context.span_id, "016x")
                trace_token = current_trace_id.set(trace_id_hex)
                span_token = current_span_id.set(span_id_hex)

                try:
                    await self.app(scope, receive, send_with_telemetry)
                except Exception as exc:
                    error_occurred = True
                    attributes["error.type"] = type(exc).__qualname__
                    span.set_attribute("error.type", type(exc).__qualname__)
                    raise
                finally:
                    route_path = _resolve_route_path(scope, original_root_path)
                    if route_path is not None:
                        span_name = f"{method} {route_path}"
                        span.update_name(span_name)
                        span.set_attribute("http.route", route_path)
                        attributes["http.route"] = route_path
        finally:
            # trace_token/span_token are only ever None here if `start_as_current_span`
            # itself failed to enter (no span, nothing to correlate) — not reachable with
            # a real tracer in practice, but guarded for type-safety.
            if trace_token is not None:  # pragma: no branch
                current_trace_id.reset(trace_token)
            if span_token is not None:  # pragma: no branch
                current_span_id.reset(span_token)
            self._emit(span_name, method, route_path, attributes, error_occurred, trace_id_hex, span_id_hex)

    def _emit(
        self,
        span_name: str,
        method: str,
        route_path: str | None,
        attributes: dict[str, str | int],
        error_occurred: bool,
        trace_id_hex: str,
        span_id_hex: str,
    ) -> None:
        payload: dict[str, Any] = {
            "name": span_name,
            "http.request.method": method,
            "http.route": route_path,
            "status_code": attributes.get("http.response.status_code"),
            "attributes": dict(attributes),
            "error": error_occurred,
        }
        event = Event(
            event_id=str(uuid.uuid4()),
            channel=self.channel,
            recorded_at_ns=time.time_ns(),
            monotonic_ns=time.monotonic_ns(),
            payload=payload,
            trace_id=trace_id_hex or None,
            span_id=span_id_hex or None,
        )
        self.repository.put(self.channel, event)


def _resolve_route_path(scope: Scope, original_root_path: str) -> str | None:
    """Replay Starlette's own route matching to find the leaf route's path template.

    Real Starlette (unlike focusari_asgi's fork) never stores the matched route object
    on the scope, so this walks `scope["app"].router.routes` with each route's own
    (non-mutating) `.matches()` the same way `Router.app()` does, to recover the same
    leaf route without depending on execution-order side effects. `original_root_path`
    restores the scope's root_path to what it was before real dispatch mutated it in
    place while descending through any Mounts.
    """
    app = scope.get("app")
    router = getattr(app, "router", None)
    routes = getattr(router, "routes", None)
    if not routes:
        return None
    match_scope = {**scope, "root_path": original_root_path}
    return _match_routes(routes, match_scope)


def _match_routes(routes: Sequence[BaseRoute], scope: Scope) -> str | None:
    for route in routes:
        match, child_scope = route.matches(scope)
        if match is Match.NONE:
            continue

        nested_scope = {**scope, **child_scope}
        if isinstance(route, Mount):
            nested = _match_routes(route.routes, nested_scope)
            if nested is not None:
                return nested
            # Mount wraps an opaque ASGI app (not a Router we can descend into further);
            # its own root_path is the best available route identifier.
            root_path = nested_scope.get("root_path")
            return root_path if root_path else "/"

        path_format = getattr(route, "path_format", None)
        if isinstance(path_format, str):
            prefix = nested_scope.get("root_path") or ""
            return f"{prefix.rstrip('/')}{path_format}"
        return None  # pragma: no cover - defensive: only exotic BaseRoute subclasses (e.g. Host) lack path_format
    return None
