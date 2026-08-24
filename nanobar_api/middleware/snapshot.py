"""ASGI middleware that captures request/response snapshots onto the eventbus.

Per the regression-brick system plan (`.focusari/regression-brick-system-plan.md` §3,
"Snapshot Capture Protocol"), this observes request and response traffic passing through
the ASGI pipeline without altering it: `receive` and `send` are teed (the same pattern
`EventBusTraceMiddleware` in `nanobar_api.middleware.trace` uses for its `send_with_telemetry`
wrapper), and the observed data is serialized into an `Event` and handed to an
`EventQueueRepository` after the wrapped app completes — never a cache-and-replay of the
body, and never a blocker in the request path.

Unlike focusari_asgi's historical `_CachedRequest` (which supports both `.body()` and
`.stream()` call styles for downstream consumers), this middleware only needs to *observe*
bytes passing through unchanged, so the receive-side tee is a simple hash+buffer, not a
replayable cache.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from typing import TYPE_CHECKING, Any

from nanobar_api.capture.policy import (
    CapturePolicy,
    apply_header_allowlist,
    apply_query_param_allowlist,
    default_capture_policy,
)
from nanobar_api.eventbus.events import Event
from nanobar_api.eventbus.queue_repository import EventQueueRepository
from nanobar_api.middleware.trace import current_span_id, current_trace_id

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: Distinct from `nanobar_api.middleware.trace`'s "nanobar.trace" scope key — this
#: middleware and the trace middleware are independent and must not interfere with each
#: other's reentrancy tracking when both are stacked on the same request.
_SCOPE_KEY = "nanobar.snapshot"


class _CaptureState:
    """Accumulates a running sha256 over all observed bytes plus a size-capped buffer.

    Mutated in place (via `.observe()`) rather than reassigned, so it can be closed over
    by an inner `send`/`receive` wrapper without `nonlocal`.
    """

    __slots__ = ("buffer", "hash", "total_bytes")

    def __init__(self) -> None:
        self.hash = hashlib.sha256()
        self.buffer = bytearray()
        self.total_bytes = 0

    def observe(self, data: bytes, cap_bytes: int) -> None:
        if not data:
            return
        # The full body is always hashed, regardless of the cap, so body_sha256 reflects
        # the complete payload even when body_b64 below is truncated.
        self.hash.update(data)
        self.total_bytes += len(data)
        remaining = cap_bytes - len(self.buffer)
        if remaining > 0:
            self.buffer.extend(data[:remaining])

    def as_dict(self, cap_bytes: int) -> dict[str, Any]:
        return {
            "body_b64": base64.b64encode(bytes(self.buffer)).decode("ascii"),
            "body_sha256": self.hash.hexdigest(),
            "body_total_bytes": self.total_bytes,
            "body_truncated": self.total_bytes > cap_bytes,
        }


class SnapshotMiddleware:
    """Capture one `Event` per HTTP request with allow-listed request/response snapshots.

    Header and query-param capture is governed by `policy` (a `CapturePolicy`); anything
    not explicitly allow-listed is never observed at all (see `nanobar_api.capture.policy`
    and `.focusari/data-privacy-adr.md` §3). Body bytes are always fully hashed (sha256)
    regardless of `policy.body_cap_bytes`, but only buffered/captured up to that cap.
    """

    def __init__(
        self,
        app: ASGIApp,
        repository: EventQueueRepository,
        policy: CapturePolicy | None = None,
        channel: str = "snapshot",
    ) -> None:
        self.app = app
        self.repository = repository
        self.policy = policy if policy is not None else default_capture_policy()
        self.channel = channel

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get(_SCOPE_KEY):
            await self.app(scope, receive, send)
            return

        scope[_SCOPE_KEY] = True
        try:
            await self._snapshot(scope, receive, send)
        finally:
            del scope[_SCOPE_KEY]

    async def _snapshot(self, scope: Scope, receive: Receive, send: Send) -> None:
        cap_bytes = self.policy.body_cap_bytes
        request_state = _CaptureState()
        response_state = _CaptureState()
        response_meta: dict[str, Any] = {"status_code": None, "headers": {}}

        async def wrapped_receive() -> Message:
            message = await receive()
            if message["type"] == "http.request":
                request_state.observe(message.get("body", b""), cap_bytes)
            # Every other message type (e.g. http.disconnect) passes through untouched —
            # this is pure observation, never a cache-and-replay of what the app receives.
            return message

        async def wrapped_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_meta["status_code"] = message["status"]
                response_meta["headers"] = apply_header_allowlist(self.policy, message.get("headers", []))
            elif message["type"] == "http.response.body":
                response_state.observe(message.get("body", b""), cap_bytes)
            await send(message)

        error_occurred = False
        try:
            await self.app(scope, wrapped_receive, wrapped_send)
        except Exception:
            error_occurred = True
            raise
        finally:
            # Fires even when the app raised before any send() call — response_meta then
            # simply keeps its None status_code / empty headers defaults, no crash.
            self._emit(scope, request_state, response_meta, response_state, error_occurred)

    def _emit(
        self,
        scope: Scope,
        request_state: _CaptureState,
        response_meta: dict[str, Any],
        response_state: _CaptureState,
        error_occurred: bool,
    ) -> None:
        cap_bytes = self.policy.body_cap_bytes
        request_dict: dict[str, Any] = {
            "method": scope.get("method", ""),
            "path": scope.get("path", ""),
            "query_params": apply_query_param_allowlist(self.policy, scope.get("query_string", b"")),
            "headers": apply_header_allowlist(self.policy, scope.get("headers", [])),
            **request_state.as_dict(cap_bytes),
        }
        response_dict: dict[str, Any] = {
            "status_code": response_meta["status_code"],
            "headers": response_meta["headers"],
            **response_state.as_dict(cap_bytes),
        }
        # Whole-snapshot dedup hash, distinct from the per-body sha256 fields above: a
        # canonical (sort_keys) JSON serialization of request+response, hashed as a unit.
        content_hash = hashlib.sha256(
            json.dumps({"request": request_dict, "response": response_dict}, sort_keys=True).encode("utf-8")
        ).hexdigest()
        payload: dict[str, Any] = {
            "request": request_dict,
            "response": response_dict,
            "content_hash": content_hash,
            "error": error_occurred,
        }
        event = Event(
            event_id=str(uuid.uuid4()),
            channel=self.channel,
            recorded_at_ns=time.time_ns(),
            monotonic_ns=time.monotonic_ns(),
            payload=payload,
            trace_id=current_trace_id.get(),
            span_id=current_span_id.get(),
        )
        self.repository.put(self.channel, event)
