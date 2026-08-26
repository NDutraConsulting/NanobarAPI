"""Non-ASGI counterpart to `SnapshotMiddleware`'s capture logic.

Per `.focusari/nanobar_APIDomain_abstract_class_buildplan-with-tasks.md` §3: the validator and
controller layers have no raw ASGI byte stream to tee the way `SnapshotMiddleware`
(`nanobar_api/middleware/snapshot.py`) does — they work with already-parsed Python objects — so
this takes structured request/response payloads directly and produces the same
request/response/content_hash/error event shape, tagged with `nanobar_type` so
`generate_bricks()` can later tell which layer a given brick came from.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import time
import uuid
from typing import Any

from nanobar_api.eventbus.events import Event
from nanobar_api.eventbus.queue_repository import EventQueueRepository
from nanobar_api.middleware.trace import current_span_id, current_trace_id


def to_payload_dict(value: Any) -> dict[str, Any]:
    """Best-effort `dict[str, Any]` shaping for `capture_layer()`'s request/response payloads —
    the validator/controller layers pass through arbitrary Python objects (a dataclass from
    `nanobar_api.validation.parse()`, a plain value, already a dict), not raw ASGI bytes."""
    if isinstance(value, dict):
        return value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    return {"value": value}


def capture_layer(
    repository: EventQueueRepository,
    layer: str,
    request_payload: dict[str, Any],
    response_payload: dict[str, Any],
    *,
    nanobar_type: str | None = None,
    route_key: str | None = None,
    error: bool = False,
    channel: str = "snapshot",
) -> None:
    """Emit one `Event` capturing a single layer's request/response pair.

    `nanobar_type` defaults to `f"{layer}-request-response"` when not given — the convention
    every domain plan reusing this function follows unless a call site needs a more specific
    boundary name (e.g. an event-to-subscriber boundary, which isn't a request/response shape).
    Put on `channel` (default `"snapshot"`) so it drains through the same proven batch/drain/
    dedup path `SnapshotMiddleware` already uses, rather than a new channel.

    `route_key` is the caller's own stable identity for what produced this capture (e.g. a REST
    rule's `"METHOD /path"` key) — optional (not every capture site has one, e.g. an
    event-to-subscriber boundary), but when given, `nanobar_api.bricks.binding` uses it as a
    direct, unambiguous way to auto-register and bind the resulting brick to a `Nanobar` row,
    without needing to correlate multiple same-trace bricks against each other.
    """
    content_hash = hashlib.sha256(
        json.dumps({"request": request_payload, "response": response_payload}, sort_keys=True).encode("utf-8")
    ).hexdigest()
    payload: dict[str, Any] = {
        "request": request_payload,
        "response": response_payload,
        "content_hash": content_hash,
        "error": error,
        "nanobar_type": nanobar_type if nanobar_type is not None else f"{layer}-request-response",
    }
    if route_key is not None:
        payload["route_key"] = route_key
    event = Event(
        event_id=str(uuid.uuid4()),
        channel=channel,
        recorded_at_ns=time.time_ns(),
        monotonic_ns=time.monotonic_ns(),
        payload=payload,
        trace_id=current_trace_id.get(),
        span_id=current_span_id.get(),
    )
    repository.put(channel, event)
