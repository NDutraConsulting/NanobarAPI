"""Hermetic replay of a single `RegressionBrick` against a fresh `TestClient`.

Per the regression-brick system plan (`.focusari/regression-brick-system-plan.md` §8, step
4 — the "thin-slice proof"), for this slice "hermetic" mainly means "replay in-process
against a fresh `TestClient` of the given app, not against any real external/production
system." No shadow-DB hydration yet (this slice is scoped to one read-only GET endpoint,
which needs no mutating-state replay).

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


def replay_brick(app: ASGIApp, brick: RegressionBrick) -> dict[str, Any]:
    """Replay `brick.request` against a fresh `TestClient` of `app`.

    Headers are resent as captured (`brick.request.get("headers", {})`) — safe to resend
    because this project's default `CapturePolicy` already excludes
    authorization/cookie/set-cookie headers from ever being captured in the first place, so
    there is nothing sensitive in a brick's stored headers to withhold at replay time.

    Returns a dict shaped exactly like a brick's own `response`: `{"status_code": ...,
    "payload": ...}`, where `payload` is the parsed JSON body of the fresh response, or
    `{}` if that body isn't valid JSON (or is empty) — the same graceful-fallback approach
    `generate.py` uses when building a brick's request/response payload in the first place,
    so a brick's original response and a replayed response are always directly comparable.
    """
    client = TestClient(app)

    method = brick.request["method"]
    path = brick.request["path"]
    headers = brick.request.get("headers", {})
    body_payload = brick.request.get("payload")
    # Note: brick.request["query_params"] (if captured) is not replayed here — the thin
    # slice this replays against is a single GET endpoint with no query params, and
    # re-serializing them back onto `path` is future work once that's actually exercised.

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
