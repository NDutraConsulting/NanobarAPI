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


def _classify_scenario_type(status_code: int | None) -> str | None:
    """Classifies a captured response's status code into a coarse `regression_scenario_type` —
    which member of its nanobar's class this brick represents (see
    `.focusari/2026-08-24-refactor-nanobar-track-types-with-tasks.md` §1). Best-effort: an
    unrecognized or missing status code classifies as `None` rather than guessing.

    401 and 403 are kept distinct (`"unauthorized"`/`"forbidden"`) rather than merged into one
    value — they're semantically different failures (failed authentication vs. failed
    authorization) and a human reviewing bricks later may care about the distinction.
    """
    if status_code is None:
        return None
    if 200 <= status_code < 300:
        return "success"
    if status_code == 400:
        return "invalid_input"
    if status_code == 401:
        return "unauthorized"
    if status_code == 403:
        return "forbidden"
    if status_code == 404:
        return "not_found"
    if status_code == 409:
        return "conflict"
    if status_code == 422:
        return "validation_error"
    if 500 <= status_code < 600:
        return "server_error"
    return None


def _classify_capture_layer_scenario(payload: dict[str, Any], response_payload: Any) -> str | None:
    """`_classify_scenario_type`'s counterpart for `capture_layer()`-produced events (validator/
    controller layers) — there's no HTTP status code to classify, so this reads the signals
    those layers actually produce instead: `payload["error"]` (an unhandled exception,
    `NanobarController.handle`'s uncaught-failure case) and a validator-layer `ValidationError`'s
    `{"errors": [...]}` response shape (per the API-Domain plan's Design Decision that a
    `ValidationError` still classifies as `"invalid_input"`, symmetric with the 400 case above).
    Deliberately narrower than `_classify_scenario_type`'s full vocabulary — these are the only
    outcomes generically detectable without layer-specific knowledge; anything else is `None`
    rather than guessed.
    """
    if payload.get("error") is True:
        return "server_error"
    if isinstance(response_payload, dict) and "errors" in response_payload:
        return "invalid_input"
    return "success"


def _classify_db_scenario_type(error_type: str | None) -> str | None:
    """`_classify_scenario_type`'s DB-boundary counterpart — there's no HTTP status code at the
    ORM layer either, but unlike the generic `_classify_capture_layer_scenario` above, the exact
    SQLAlchemy exception class name (`nanobar_api.orm.NanobarORMWrapper`'s `handle_error`
    listener stamps it as `response["error_type"]`) is a real, specific signal: an
    `IntegrityError` is a `"conflict"` (a unique/foreign-key violation, not an infrastructure
    fault), not a generic `"server_error"`. Per the Service-Domain plan's own settled Design
    Decision.
    """
    if error_type is None:
        return "success"
    if error_type == "IntegrityError":
        return "conflict"
    return "server_error"


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
            if not isinstance(request_payload, dict) or not isinstance(response_payload, dict):
                raise TypeError("request/response payload must each be an object")
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

        nanobar_type = event.payload.get("nanobar_type")
        if nanobar_type is not None:
            # capture_layer()-produced (validator/controller/orm layers): request_payload/
            # response_payload are already structured Python data, not SnapshotMiddleware's
            # base64-encoded-body/status_code shape — used as-is, no decoding needed.
            brick_request: dict[str, Any] = request_payload
            brick_response: dict[str, Any] = response_payload
            if nanobar_type == "orm-request-response":
                regression_scenario_type = _classify_db_scenario_type(response_payload.get("error_type"))
            else:
                regression_scenario_type = _classify_capture_layer_scenario(event.payload, response_payload)
        else:
            brick_request = {
                "method": request_payload.get("method"),
                "path": request_payload.get("path"),
                "headers": request_payload.get("headers", {}),
                # query_params is carried through too, even though it's not strictly asked
                # for: it's part of "what request happened" and is already policy-filtered
                # by SnapshotMiddleware, so keeping it is free and useful for a human
                # inspecting a brick later.
                "query_params": request_payload.get("query_params", {}),
                "payload": _decode_body_json(request_payload.get("body_b64", "")),
            }
            brick_response = {
                "status_code": response_payload.get("status_code"),
                "payload": _decode_body_json(response_payload.get("body_b64", "")),
            }
            regression_scenario_type = _classify_scenario_type(response_payload.get("status_code"))

        brick = RegressionBrick(
            regression_brick_id=f"rbrick-{uuid.uuid4().hex[:12]}",
            schema_version=schema_version,
            brick_version=1,
            # Kept honest and minimal for this thin slice: only fields we actually know at
            # this stage. The full RegressionBrick JSON example in
            # nanobarapi-architecture-rules.md includes host/project/file/class/function
            # fields, but those require code-location instrumentation that doesn't exist yet
            # — inventing values for them would be worse than omitting them.
            source={
                "trace_id": event.trace_id,
                "span_id": event.span_id,
                "channel": event.channel,
                **({"nanobar_type": nanobar_type} if nanobar_type is not None else {}),
                **({"route_key": route_key} if (route_key := event.payload.get("route_key")) is not None else {}),
            },
            request=brick_request,
            response=brick_response,
            content_hash=content_hash,
            created_by=created_by,
            trace_refs=trace_refs,
            capture_policy_id=capture_policy_id,
            regression_scenario_type=regression_scenario_type,
        )

        if get_brick_by_content_hash(bricks_conn, content_hash) is None:
            insert_brick(bricks_conn, brick)
            new_bricks.append(brick)
        # else: dedup skip — an identical (request, response) pair was already captured as a
        # brick. Not an error: the underlying event is still fully "processed" by this call.

        processed_event_ids.append(event.event_id)

    mark_processed(events_conn, processed_event_ids)

    return new_bricks
