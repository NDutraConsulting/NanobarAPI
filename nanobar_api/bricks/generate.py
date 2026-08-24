"""Brick generation — turns unprocessed `"snapshot"` channel events into `RegressionBrick`s.

Per the regression-brick system plan (`.focusari/regression-brick-system-plan.md` §6,
"Contract Source & Brick Generation"), this is an **explicit batch/CI step**, not a
continuous production worker — a plain synchronous function meant to be invoked once (e.g.
by a CI job, or a test), not something that polls or threads on its own. Continuous
re-inference would just bless whatever the app currently does, bugs included (the "oracle
problem" flagged in the design doc); an explicit, human-reviewable step is what keeps this
a regression check rather than drift detection.

Consumes exactly the payload shape `nanobar_api.middleware.snapshot.SnapshotMiddleware`
emits onto its channel (see that module) — request/response sub-dicts with
method/path/query_params/headers/body_b64/body_sha256/body_total_bytes/body_truncated for
the request, status_code instead of method/path/query_params for the response, plus a
top-level `content_hash` string and an `error` bool.
"""

from __future__ import annotations

import base64
import json
import logging
import sqlite3
import uuid
from typing import Any

from nanobar_api.bricks.schema import RegressionBrick
from nanobar_api.bricks.store import get_brick_by_content_hash, insert_brick
from nanobar_api.eventbus.store import get_unprocessed, mark_processed

logger = logging.getLogger(__name__)


def _decode_body_json(body_b64: str) -> dict[str, Any]:
    """Decode a base64 request/response body and try to parse it as JSON.

    Falls back to `{}` whenever the bytes are empty or not valid JSON (e.g. binary
    content, or a truncated body whose tail was cut mid-token) — a brick's request/response
    "payload" is a best-effort structured view of the body, not a guarantee the original
    body was JSON at all, so silently degrading to `{}` here is preferable to raising and
    losing the whole event.
    """
    raw = base64.b64decode(body_b64) if body_b64 else b""
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def generate_bricks(
    events_conn: sqlite3.Connection,
    bricks_conn: sqlite3.Connection,
    channel: str = "snapshot",
    schema_version: str = "1.0",
    created_by: str = "nanobarapi",
    capture_policy_id: str | None = "default-v1",
    limit: int = 100,
) -> list[RegressionBrick]:
    """Turn up to `limit` unprocessed events on `channel` into `RegressionBrick`s.

    Returns only the newly-inserted bricks (not ones skipped as duplicates of an
    already-stored `content_hash` — the caller wants to know what's new). Every event this
    call attempts, whether it produces a new brick, is deduped, or fails to parse, is marked
    processed in `events_conn` at the end, so a second call never reprocesses it.
    """
    events = get_unprocessed(events_conn, channel, limit)

    new_bricks: list[RegressionBrick] = []
    processed_event_ids: list[str] = []

    for event in events:
        try:
            request_payload = event.payload["request"]
            response_payload = event.payload["response"]
            raw_content_hash = event.payload["content_hash"]
        except (KeyError, TypeError):
            # Payload doesn't match the expected snapshot shape at all — nothing usable to
            # build a brick from. Still mark processed (no retry loop at this stage, that's
            # future work), but log it so the skip is at least observable rather than silent.
            logger.warning("skipping event %s: payload does not match snapshot shape", event.event_id)
            processed_event_ids.append(event.event_id)
            continue

        content_hash = f"sha256:{raw_content_hash}"

        trace_refs: list[dict[str, Any]] = (
            [{"trace_id": event.trace_id, "span_ids": [event.span_id] if event.span_id else []}]
            if event.trace_id
            else []
        )

        brick = RegressionBrick(
            regression_brick_id=f"rbrick-{uuid.uuid4().hex[:12]}",
            schema_version=schema_version,
            brick_version=1,
            # Kept honest and minimal for this thin slice: only fields we actually know at
            # this stage. The full RegressionBrick JSON example in
            # nanobarapi-architecture-rules.md includes host/project/file/class/function
            # fields, but those require code-location instrumentation that doesn't exist yet
            # — inventing values for them would be worse than omitting them.
            source={"trace_id": event.trace_id, "span_id": event.span_id, "channel": event.channel},
            request={
                "method": request_payload.get("method"),
                "path": request_payload.get("path"),
                "headers": request_payload.get("headers", {}),
                # query_params is carried through too, even though it's not strictly asked
                # for: it's part of "what request happened" and is already policy-filtered
                # by SnapshotMiddleware, so keeping it is free and useful for a human
                # inspecting a brick later.
                "query_params": request_payload.get("query_params", {}),
                "payload": _decode_body_json(request_payload.get("body_b64", "")),
            },
            response={
                "status_code": response_payload.get("status_code"),
                "payload": _decode_body_json(response_payload.get("body_b64", "")),
            },
            content_hash=content_hash,
            created_by=created_by,
            trace_refs=trace_refs,
            capture_policy_id=capture_policy_id,
        )

        if get_brick_by_content_hash(bricks_conn, content_hash) is None:
            insert_brick(bricks_conn, brick)
            new_bricks.append(brick)
        # else: dedup skip — an identical (request, response) pair was already captured as a
        # brick. Not an error: the underlying event is still fully "processed" by this call.

        processed_event_ids.append(event.event_id)

    mark_processed(events_conn, processed_event_ids)

    return new_bricks
