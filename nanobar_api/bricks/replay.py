"""Hermetic replay of a single `RegressionBrick` against a fresh `TestClient`.

Per the regression-brick system plan (`.focusari/regression-brick-system-plan.md` §8, step
4 — the "thin-slice proof"), for this slice "hermetic" mainly means "replay in-process
against a fresh `TestClient` of the given app, not against any real external/production
system." No shadow-DB hydration yet (this slice is scoped to one read-only GET endpoint,
which needs no mutating-state replay) -- but see
`admin/nanobar/replay_app.py`/`nanobar-dashboard-search-and-replay-upgrade-plan.md` for a
scoped-down local approximation of that, built for the dashboard's own "Run" button.

`starlette.testclient.TestClient` is itself synchronous — it drives its own event loop
internally rather than exposing an awaitable API (confirmed against this repo's own
`tests/test_smoke.py`, which calls `client.get(...)` with no `await`). Replaying a brick
therefore needs no `await` anywhere in its body, so this is a plain `def`, not `async def`
— matching the actual (synchronous) shape of the underlying operation rather than adding
async ceremony a real `await` never needs.
"""

from __future__ import annotations

from typing import Any

from starlette.testclient import TestClient
from starlette.types import ASGIApp

from nanobar_api.bricks.schema import RegressionBrick


def replay_brick(
    app: ASGIApp, brick: RegressionBrick, *, extra_headers: dict[str, str] | None = None
) -> dict[str, Any]:
    """Replay `brick.request` against a fresh `TestClient` of `app`.

    Two brick shapes exist, and this handles both: a `SnapshotMiddleware`-sourced brick's
    `request` is the raw HTTP shape (`{"method", "path", "headers", "payload", ...}`) —
    replayed as `method`/`path` directly, same as always. A `capture_layer()`-sourced brick
    (stamped `source["route_key"]`, e.g. everything this project's own dashboard binds — see
    `app/controllers/blog_controller.py`) has no such shape at all: its `request` *is* the
    validated request dataclass's own fields (e.g. `{"title": ..., "body": ...}` for a
    `CreatePostRequest`) — exactly the JSON body a real client would POST, just not tagged
    with a method/path anywhere on it. `route_key` (`"METHOD /path"`, the same identity
    `NanobarRouteRule.key`/`request_type` already use everywhere else in this codebase)
    supplies those instead.

    Headers are resent as captured (`brick.request.get("headers", {})`, empty for a
    `capture_layer()`-sourced brick, which never had any) — safe to resend because this
    project's default `CapturePolicy` already excludes authorization/cookie/set-cookie
    headers from ever being captured in the first place, so there is nothing sensitive in a
    brick's stored headers to withhold at replay time. `extra_headers`, when given, are
    merged in on top (e.g. a session cookie + CSRF header + `traceparent` a caller wants the
    replayed request to carry — see `admin/nanobar/api.py`'s `replay_brick_action`).

    Returns a dict shaped exactly like a brick's own `response`: `{"status_code": ...,
    "payload": ...}`, where `payload` is the parsed JSON body of the fresh response, or
    `{}` if that body isn't valid JSON (or is empty) — the same graceful-fallback approach
    `generate.py` uses when building a brick's request/response payload in the first place,
    so a brick's original response and a replayed response are always directly comparable.
    """
    client = TestClient(app)

    route_key = brick.source.get("route_key")
    if route_key is not None:
        method, _, path = route_key.partition(" ")
        body_payload: Any = brick.request
        # Note: query_params aren't part of this shape at all -- capture_layer()'s
        # request_payload is the validated dataclass, which never carried them.
    else:
        method = brick.request["method"]
        path = brick.request["path"]
        body_payload = brick.request.get("payload")
        # Note: brick.request["query_params"] (if captured) is not replayed here — the thin
        # slice this replays against is a single GET endpoint with no query params, and
        # re-serializing them back onto `path` is future work once that's actually exercised.

    headers = {**brick.request.get("headers", {}), **(extra_headers or {})}

    kwargs: dict[str, Any] = {"headers": headers}
    if body_payload:
        kwargs["json"] = body_payload

    response = client.request(method, path, **kwargs)

    try:
        response_payload = response.json()
    except ValueError:
        response_payload = {}
    if not isinstance(response_payload, dict):
        response_payload = {}

    return {"status_code": response.status_code, "payload": response_payload}
