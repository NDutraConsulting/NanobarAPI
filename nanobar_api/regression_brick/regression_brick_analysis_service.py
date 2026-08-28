"""`ReplayBrickService` -- the "verify nothing broke" half of the `RegressionBrick` domain, per
the user's own collection/analysis split ("during a dev cycle you would run
`regression_brick_analysis_api.py` or `regression_brick_analysis_cli.py`"). Wraps
`verdict.py`'s `evaluate_verdict()` plus its own HTTP dispatch (folded in from the now-deleted
`nanobar_api/bricks/replay.py`), the last checkpointed item from
`.focusari/regression-brick-refactor-plan-with-tasks.md` Phase 4 -- rewritten per
`.focusari/2026-08-27-regression-brick-clarification.md`/
`.focusari/2026-08-27.1800.build_and_update_plan_with_tasks.md` Phase 4: dispatch collapses to
this file + `verdict.py`, over a real `httpx2.Client` this service takes as a dependency rather
than building itself -- production now passes an in-process `starlette.testclient.TestClient`
bound to the live app itself, with isolation from live data coming from a per-request
`nanobar-mode: shadow` header (`nanobar_api.shadow`) rather than a separately-run deployment (an
earlier iteration of this design, briefly; see `app/admin/nanobar/api.py`'s `replay_brick_action`
for the current shape and why a bare in-process `TestClient` -- unlike the very first version of
this mechanism -- doesn't hit the "ASGI lifespan never entered" problem here: it's the *same*,
already-running app's lifespan, not a fresh one this call would need to enter itself).

**Self-contained bricks, no runtime telemetry query.** `brick.entry_point`/`brick.nanobar_type`
are read straight off the row (Phase 1/2 of the plan doc) -- no `span_id -> Span -> Trace`
lookup happens here or anywhere at replay time. A brick predating those columns (no Phase 7
backfill yet) falls back to the same `route_key`/`method`+`path` derivation `replay.py` used to
do at replay time, via `_legacy_entry_point()` below -- only exercised for old data.

**HTTP-shaped `nanobar_type`s dispatch via `httpx2` against `client`.** `api-response` and
everything nested under it (`validator`/`controller`/`service`/`orm-request-response`,
`*-to-db`) replay as a real HTTP request.

**`event-to-subscriber` dispatches via the two well-known routes `replay_routes.py` provides
(Phase 5)** -- POST `REPLAY_TRIGGER_EVENT_PATH` (dispatches the brick's own `request` payload
onto the domain channel `entry_point` names, synchronously, via
`NanobarEventBus.dispatch_now()`, so the resulting span lands under this one HTTP request's own
trace -- see that function's own docstring for why), then poll `REPLAY_SPANS_PATH` for the
`event-to-subscriber` span it just produced. This is the concrete meaning of "we can see the
generated spans in the shadow DB" (`.focusari/2026-08-27-regression-brick-clarification.md` Part
3) -- no direct telemetry-db dependency needed here, just two more `httpx2` calls against `client`.

**`worker`/`worker-*` still has no dispatch** -- state-machine seeding (D2) is out of scope for
this pass, deliberately, not guessed at (confirmed with the user). `handle()` returns a clear
`status="error"` result naming the gap rather than guessing at a request to send.

**`client: httpx2.Client` is a constructor dependency, `regression_brick_id: str` is the
per-call request field** -- same reasoning `TraceScannerService`/the original `app: ASGIApp`
version of this service already established for `events_conn`/`app`:
`NanobarAPIService.__call__` runs the request through `capture_layer()`'s `to_payload_dict()`
(`dataclasses.asdict()`), which can't handle a raw client object. An `httpx2.Client` (not just a
base URL string) is accepted directly, not built internally from a URL, so a caller controls its
own transport -- production passes an in-process `starlette.testclient.TestClient(app)` bound to
the live app itself (see `app/main.py`'s `build_app()`), tests build one the same way, pointed at
a differently-configured `build_app()` instance where useful. Building the client is deliberately
left to the caller, matching this codebase's "framework code never imports from `app/`" boundary
-- this file only ever sees the client it's handed, never how it was constructed or what makes it
isolated from live data (that's `nanobar_api.shadow`/`app/db/blog_session.py`'s concern, not
this one's).

**`_verdict_inputs()` (capture_layer()-sourced-brick response-shape adaptation) still lives
here**, unchanged in spirit from the original version of this file -- it's a pure `(brick,
replayed_response) -> (adapted_brick, adapted_replayed_response)` transform needing the
*replayed* response as input, so it can't run before the replay itself. Its branch condition is
now `brick.nanobar_type is not None` (the new first-class column) instead of
`brick.source.get("route_key") is not None` -- same meaning (capture_layer()-sourced bricks
always stamp `nanobar_type`; `SnapshotMiddleware`-sourced ones never do), just reading the
promoted column instead of digging into `source_json`. Response-shape normalization at
brick-creation time (so this adaptation could shrink or disappear) is still an open item --
Phase 2's own notes, not resolved here.

**The verdict model** (Phase 6, D4 resolved) -- `evaluate_verdict()` now matches the
clarification doc's Part 1 directly: one diff pass over `{"status_code", "payload"}`, no
separately-gated layers. See `nanobar_api/bricks/verdict.py`'s own module docstring.
"""

from __future__ import annotations

import dataclasses
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx2

from nanobar_api.bricks.verdict import DEFAULT_VOLATILE_FIELDS, evaluate_verdict
from nanobar_api.framework.nanobar_api_service import NanobarAPIService, ServiceResult, ServiceResultBody
from nanobar_api.regression_brick.model import RegressionBrick
from nanobar_api.regression_brick.replay_routes import REPLAY_SPANS_PATH, REPLAY_TRIGGER_EVENT_PATH
from nanobar_api.regression_brick.repository import RegressionBrickRepository
from nanobar_api.telemetry import NanobarTelemetry

#: `evaluate_verdict()`'s status layer needs *some* real HTTP status code to compare against a
#: `capture_layer()`-sourced brick's own status-code-less `response` -- derived from the coarser
#: `_classify_capture_layer_scenario()` vocabulary (`bricks/generate.py`) that produced
#: `regression_scenario_type` in the first place, not re-guessed here. Falls back to 200 (the
#: common case) for a brick with no/unknown `regression_scenario_type`.
_CAPTURE_LAYER_SCENARIO_STATUS_CODES = {"success": 200, "invalid_input": 400, "server_error": 500}

#: `nanobar_type` values (and prefixes) with no HTTP entry point at all -- see module docstring.
#: `None` (a `SnapshotMiddleware`-sourced brick) and every other known value (`api-response` and
#: everything nested under it) are HTTP-shaped.
_NON_HTTP_NANOBAR_TYPE_PREFIXES = ("worker", "event-to-subscriber")

#: `worker`/`worker-*` still has no dispatch mechanism at all (D2 out of scope, see module
#: docstring) -- `event-to-subscriber` is handled separately in `handle()`, not lumped in here.
_UNDISPATCHABLE_NANOBAR_TYPE_PREFIXES = ("worker",)

#: Bounded polling for the span a triggered `event-to-subscriber` dispatch just produced --
#: `dispatch_now()` runs synchronously, but `TelemetryDrainWorker` still has to drain the
#: resulting `capture_layer()` event off the queue before it's visible via `REPLAY_SPANS_PATH`.
_REPLAY_SPAN_POLL_ATTEMPTS = 30
_REPLAY_SPAN_POLL_INTERVAL_S = 0.1


def _is_http_shaped(nanobar_type: str | None) -> bool:
    if nanobar_type is None:
        return True
    return not nanobar_type.startswith(_NON_HTTP_NANOBAR_TYPE_PREFIXES)


def _is_dispatchable(nanobar_type: str | None) -> bool:
    if nanobar_type is None:
        return True
    return not nanobar_type.startswith(_UNDISPATCHABLE_NANOBAR_TYPE_PREFIXES)


#: Real HTTP methods -- used only to sanity-check a stored `entry_point` before trusting it (see
#: `_resolve_entry_point()`). Not an exhaustive protocol validator, just enough to distinguish a
#: genuine `f"{method} {path}"` entry_point from `default_entry_point_resolver()`'s own
#: non-"api"-kind fallback shape (`f"{kind}:{name}"`, e.g. `"validator:POST /items"`).
_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


def _legacy_entry_point(brick: RegressionBrick) -> str:
    """Fallback entry_point derivation -- the same one `nanobar_api/bricks/replay.py` used to do
    at replay time, before Phase 1 made `entry_point` a stored column. Two live reasons this
    still gets exercised, not just old data: (1) a brick with no stored `entry_point` at all
    (predates Phase 1's column, or Phase 7's backfill hasn't reached it, or genuinely has no
    `span_id`), and (2) `_resolve_entry_point()` below rejecting a stored `entry_point` that
    doesn't actually look like one.
    """
    route_key = brick.source.get("route_key")
    if route_key is not None:
        return str(route_key)
    return f"{brick.request['method']} {brick.request['path']}"


def _resolve_entry_point(brick: RegressionBrick) -> str:
    """**A real, discovered bug this guards against, not hypothetical defensiveness**: a
    `Trace` row is created once, from whichever span for its `trace_id` the drain worker happens
    to ingest *first* (`Trace`'s own model docstring) -- and a request's nested
    `capture_layer()` spans (validator/controller/service/orm, on the `"snapshot"` channel) are
    emitted and queued *before* the enclosing `EventBusTraceMiddleware` HTTP span finishes (on
    the `"trace"` channel), since the outer span can't close until the handler it wraps returns.
    Confirmed live: for a real `POST /admin/app/api/posts` request, `Trace.entry_point` ended up
    `"validator:POST /admin/app/api/posts"` -- `default_entry_point_resolver()`'s own
    non-`"api"`-kind fallback shape (`f"{kind}:{name}"`), permanently stuck on the trace because
    nothing upgrades it once a real `kind="api"` event arrives. This isn't a rare race; given the
    architecture, a nested span reliably finishes (and queues) before its parent.

    The real fix belongs in `telemetry_drain_worker.py` (upgrade `Trace.entry_point` in place
    when a later `kind="api"` event arrives for an existing trace_id) -- flagged in
    `.focusari/2026-08-27.1800.build_and_update_plan_with_tasks.md`, not fixed here; that file's
    "resolve once, not twice" open item already anticipated a related but narrower version of
    this problem. This function only guards `ReplayBrickService` against acting on a
    known-malformed value: if `brick.entry_point` doesn't start with a real HTTP method, it falls
    back to `_legacy_entry_point()` instead of firing a request with `method="validator:POST"`.
    """
    entry_point = brick.entry_point
    if entry_point is not None:
        method, _, _ = entry_point.partition(" ")
        if method.upper() in _HTTP_METHODS:
            return entry_point
    return _legacy_entry_point(brick)


_PATH_PARAM_NAME_REGEX = re.compile(r"\{([^}]+)\}")


def _substitute_path_params(path: str, path_param_source: Any) -> str:
    """Replaces every `{param}` placeholder in `path` with the same-named value out of
    `path_param_source`, when it's a dict and has one.

    **A real, confirmed bug this fixes, not hypothetical defensiveness**: `brick.entry_point`
    (what `_resolve_entry_point()` returns `path` from) is always Starlette's own route
    *template* -- literally `{"code.function.name": ..., "http.route": "/admin/app/api/posts/
    {post_id}", ...}`, never a resolved path with a real id substituted in (see
    `EventBusTraceMiddleware._resolve_route_path()`/`Trace.entry_point`'s own docstring for why:
    it's populated from the route's own `path_format`, by design, so traces group by route
    regardless of which resource id was hit). Reproduced live: replaying a real
    `controller-request-response` brick for `POST /admin/app/api/posts/{post_id}` sent that
    literal string, curly braces and all, as the URL -- Starlette's router then parsed the
    literal text `{post_id}` as the `post_id` path param's *value*, producing a 404 ("post
    '{post_id}' not found") for a brick whose original capture was a real 200.

    The real value is recoverable without guessing: this project's own validator convention
    (`UpdatePostGate`/`NanobarGate`/`MarkNotificationReadGate` --
    `app/validators/blog_validator_gateway.py`/`nanobar_api/nanobar/validator_gate.py`) always
    embeds a path param into the validated request dataclass under the exact same field name
    (`UpdatePostRequest(post_id=request.path_params["post_id"], ...)`), so `to_payload_dict
    (validated)` -- a controller/service-layer brick's own `.request` -- already carries it. A
    placeholder with no matching key is left unsubstituted -- there's nothing honest to guess for
    a `SnapshotMiddleware`-sourced brick, which has no equivalent source at all (flagged, not
    solved -- see `_build_replay_request()`'s own call site).
    """
    if not isinstance(path_param_source, dict):
        return path

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        return str(path_param_source[name]) if name in path_param_source else match.group(0)

    return _PATH_PARAM_NAME_REGEX.sub(_replace, path)


def _build_replay_request(
    brick: RegressionBrick, extra_headers: dict[str, str] | None
) -> tuple[str, str, Any, dict[str, str]]:
    """Returns `(method, path, body_payload, headers)`. Three brick shapes exist, keyed off
    `nanobar_type` (see module docstring): a `SnapshotMiddleware`-sourced brick's `request` is
    the raw HTTP shape (`{"method", "path", "headers", "payload", ...}`) -- `payload` is the
    body. A `controller-request-response`/`service-request-response` brick's `request` *is* the
    validated request dataclass's own fields directly (e.g. `{"title": ..., "body": ...}`) --
    the whole dict is the body.

    **`validator-request-response` is a real third shape, not a variant of the second one** --
    confirmed live, not a hypothetical: `NanobarAPIValidatorGate.__call__` captures its own
    brick's `request` as `_request_payload_snapshot()`'s *pre-validation* snapshot
    (`nanobar_api/framework/nanobar_api_validator_gate.py`) -- `{"method", "path", "path_params",
    "query_params", "body"}` -- where the actually-submitted JSON lives nested under `"body"`,
    not at the top level. Treating this brick like the second shape (resending the whole wrapper
    as the JSON body) sends something the target dataclass never declared at its top level --
    reproduced live as `"title: required field missing; body: expected str, got dict"` against a
    real blog post brick: no top-level `title` (the wrapper never had one), and a top-level
    `body` key present but holding the wrapper's own nested dict, colliding with the target
    dataclass's own, unrelated `body: str` field of the same name.

    `path` is also run through `_substitute_path_params()` here -- see that function's own
    docstring for the separate, real bug this fixes (an unresolved `{param}` template sent as the
    literal URL). `path_param_source` differs per shape: a validator-layer brick's wrapper
    already carries a dedicated `"path_params"` field (the authoritative source, captured
    straight off `request.path_params`); a controller/service-layer brick has no separate field
    for it, but this codebase's own validator convention means `body_payload` itself usually
    already contains it under the matching name. A `SnapshotMiddleware`-sourced brick
    (`nanobar_type is None`) has neither -- `_substitute_path_params(path, None)` is a no-op for
    it, same as before this existed (flagged, not solved: this codebase's own docstrings already
    establish `SnapshotMiddleware`-sourced replay as the less-supported path overall).
    """
    entry_point = _resolve_entry_point(brick)
    method, _, path = entry_point.partition(" ")
    body_payload: Any
    path_param_source: Any
    if brick.nanobar_type is None:
        body_payload = brick.request.get("payload")
        path_param_source = None
    elif brick.nanobar_type == "validator-request-response":
        body_payload = brick.request.get("body")
        path_param_source = brick.request.get("path_params")
    else:
        body_payload = brick.request
        path_param_source = body_payload
    path = _substitute_path_params(path, path_param_source)
    # Headers are resent as captured -- safe because this project's default `CapturePolicy`
    # already excludes authorization/cookie/set-cookie headers from ever being captured.
    # `.get("headers", {})` is `{}` for a capture_layer()-sourced brick, which never had any.
    headers = {**brick.request.get("headers", {}), **(extra_headers or {})}
    return method, path, body_payload, headers


def _event_channel(brick: RegressionBrick) -> str | None:
    """`event-to-subscriber`'s `entry_point` convention is `f"event-{channel}"` -- returns the
    bare channel, or `None` if `entry_point` isn't stored/shaped that way (predates Phase 1, or
    Phase 7's backfill hasn't reached it -- there's no reliable fallback source for a bare
    domain-channel name on an old brick, unlike the HTTP surfaces' `route_key`/`request` fields:
    `NanobarEventBus._dispatch()`'s own `_safe_capture()` call never records which channel
    produced the capture -- a real, separate gap, not fixed here).
    """
    entry_point = brick.entry_point
    if entry_point is not None and entry_point.startswith("event-"):
        return entry_point.removeprefix("event-")
    return None


def _dispatch_event_to_subscriber(
    client: httpx2.Client, brick: RegressionBrick, extra_headers: dict[str, str] | None
) -> dict[str, Any]:
    """Trigger + read-back, via the two well-known routes `replay_routes.py` provides. Always
    returns a `{"status_code": None, "payload": ...}` shape (there's no real HTTP status code for
    a domain event) -- `_verdict_inputs()` fills in a real comparison status_code on both sides,
    so this one is never actually compared. A dispatch/poll failure produces a `payload` shaped
    to fail the diff honestly (a clear marker key), never a silent pass.
    """
    channel = _event_channel(brick)
    if channel is None:
        return {
            "status_code": None,
            "payload": {"__replay_error__": f"no event channel on brick {brick.regression_brick_id!r}"},
        }

    trigger_response = client.post(
        REPLAY_TRIGGER_EVENT_PATH, json={"channel": channel, "payload": brick.request}, headers=extra_headers or {}
    )
    if trigger_response.status_code >= 400:
        return {
            "status_code": None,
            "payload": {"__replay_error__": f"trigger-event failed: HTTP {trigger_response.status_code}"},
        }
    trace_id = trigger_response.json()["trace_id"]

    for _ in range(_REPLAY_SPAN_POLL_ATTEMPTS):
        spans_response = client.get(REPLAY_SPANS_PATH.format(trace_id=trace_id), params={"channel": "snapshot"})
        if spans_response.status_code < 400:
            for span in spans_response.json():
                if span.get("payload", {}).get("nanobar_type") == "event-to-subscriber":
                    return {"status_code": None, "payload": span["payload"].get("response")}
        time.sleep(_REPLAY_SPAN_POLL_INTERVAL_S)

    return {
        "status_code": None,
        "payload": {"__replay_error__": f"no event-to-subscriber span observed for trace {trace_id!r} in time"},
    }


def _verdict_inputs(
    brick: RegressionBrick, replayed_response: dict[str, Any]
) -> tuple[RegressionBrick, dict[str, Any]]:
    """`evaluate_verdict()` expects both a brick's own `response` and the freshly
    `replayed_response` in the same `{"status_code", "payload"}` HTTP shape. A
    `capture_layer()`-sourced brick's `response` never had that shape at all -- it's the raw
    captured value (a controller's return value, or -- for `event-to-subscriber` -- a
    subscriber's return value), since `capture_layer()` never observed an HTTP status code in
    the first place. Comparing it against `replayed_response` unadapted would compare
    `status_code=None` against a real `status_code=200` (or, for event-to-subscriber, `None`
    against `None` -- harmlessly equal, but still worth routing through the same adaptation for
    one consistent code path) on *every* capture_layer()-sourced brick, failing the status
    comparison unconditionally regardless of whether the replay actually matched.

    Adapts both sides onto the same footing for a capture_layer()-sourced brick (`nanobar_type
    is not None`): the brick's own response wrapped as `{"status_code": <derived from
    regression_scenario_type>, "payload": response}`, same derived status_code stamped onto both
    sides. **HTTP-shaped surfaces only** additionally unwrap the replayed envelope's inner
    `result.data` (a real `httpx2` round trip always produces one) -- `event-to-subscriber`'s
    `replayed_response["payload"]` is already the raw captured value
    (`_dispatch_event_to_subscriber()`'s own read-back), no envelope to unwrap. A
    `SnapshotMiddleware`-sourced brick (`nanobar_type is None`) is already HTTP-shaped -- passed
    through unchanged.
    """
    if brick.nanobar_type is None:
        return brick, replayed_response

    expected_status_code = _CAPTURE_LAYER_SCENARIO_STATUS_CODES.get(brick.regression_scenario_type or "", 200)
    # `RegressionBrick` is a mutable SQLAlchemy ORM row, not a frozen dataclass -- no
    # `dataclasses.replace()` equivalent. This copy is never added to a session/persisted; it
    # exists only so `evaluate_verdict()` (which reads `.response` alone) sees the adapted shape.
    comparison_brick = RegressionBrick(
        regression_brick_id=brick.regression_brick_id,
        schema_version=brick.schema_version,
        brick_version=brick.brick_version,
        forked_from_regression_brick_id=brick.forked_from_regression_brick_id,
        source=brick.source,
        request=brick.request,
        response={"status_code": expected_status_code, "payload": brick.response},
        trace_refs=brick.trace_refs,
        capture_policy_id=brick.capture_policy_id,
        content_hash=brick.content_hash,
        regression_scenario_type=brick.regression_scenario_type,
        created_by=brick.created_by,
    )

    if _is_http_shaped(brick.nanobar_type):
        replayed_payload = replayed_response.get("payload")
        unwrapped_data = replayed_payload.get("result", {}).get("data") if isinstance(replayed_payload, dict) else None
        comparison_replayed_response = {"status_code": replayed_response.get("status_code"), "payload": unwrapped_data}
    else:
        comparison_replayed_response = {
            "status_code": expected_status_code,
            "payload": replayed_response.get("payload"),
        }

    return comparison_brick, comparison_replayed_response


#: `evaluate_verdict()`'s own `volatile_fields` param is typed `frozenset[str]` -- not JSON-safe
#: (`capture_layer()`'s `json.dumps()` raises on every call otherwise, since
#: `NanobarAPIService.__call__` runs every request through it unconditionally). `sorted()` for a
#: deterministic default tuple, not `tuple(DEFAULT_VOLATILE_FIELDS)`'s arbitrary (hash-order)
#: iteration.
_DEFAULT_VOLATILE_FIELDS_TUPLE: tuple[str, ...] = tuple(sorted(DEFAULT_VOLATILE_FIELDS))


@dataclass(frozen=True)
class ReplayBrickRequest:
    regression_brick_id: str
    extra_headers: dict[str, str] | None = None
    response_schema: dict[str, Any] | None = None
    volatile_fields: tuple[str, ...] = _DEFAULT_VOLATILE_FIELDS_TUPLE


class ReplayBrickService(NanobarAPIService):
    def __init__(
        self, telemetry: NanobarTelemetry, repository: RegressionBrickRepository, client: httpx2.Client
    ) -> None:
        super().__init__(telemetry)
        self.repository = repository
        self.client = client

    def handle(self, request: ReplayBrickRequest) -> ServiceResult:
        """ "Brick not found" and "no dispatch mechanism for this nanobar_type yet" are both
        service-layer business-outcome checks (not exceptions) -- same reasoning
        `regression_brick_collection_service.py`'s own services already established.
        """
        brick = self.repository.get(request.regression_brick_id)
        if brick is None:
            return ServiceResult(
                status="error",
                result=ServiceResultBody(
                    type="object", data=None, msg_summary=f"brick {request.regression_brick_id!r} not found"
                ),
            )

        if not _is_dispatchable(brick.nanobar_type):
            return ServiceResult(
                status="error",
                result=ServiceResultBody(
                    type="object",
                    data=None,
                    msg_summary=(
                        f"nanobar_type {brick.nanobar_type!r} has no replay dispatch yet "
                        "(worker, D2/state-machine-seeding out of scope this pass)"
                    ),
                ),
            )

        if _is_http_shaped(brick.nanobar_type):
            method, path, body_payload, headers = _build_replay_request(brick, request.extra_headers)
            kwargs: dict[str, Any] = {"headers": headers}
            if body_payload:
                kwargs["json"] = body_payload
            response = self.client.request(method, path, **kwargs)

            try:
                response_payload = response.json()
            except ValueError:
                response_payload = {}
            if not isinstance(response_payload, dict):
                response_payload = {}
            replayed_response = {"status_code": response.status_code, "payload": response_payload}
        else:
            replayed_response = _dispatch_event_to_subscriber(self.client, brick, request.extra_headers)

        verdict_brick, verdict_replayed_response = _verdict_inputs(brick, replayed_response)
        verdict = evaluate_verdict(
            verdict_brick, verdict_replayed_response, request.response_schema, frozenset(request.volatile_fields)
        )

        return ServiceResult(
            status="success",
            result=ServiceResultBody(
                type="object",
                data={"replayed_response": replayed_response, "verdict": dataclasses.asdict(verdict)},
                msg_summary=f"replay {'passed' if verdict.overall_passed else 'failed'}",
            ),
        )
