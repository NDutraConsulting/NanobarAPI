"""Framework-provided routes any app can mount to support regression-brick replay for
`worker`/`event-to-subscriber` `nanobar_type`s (Phase 5,
`.focusari/2026-08-27.1800.build_and_update_plan_with_tasks.md`) -- fixed, convention-based
paths under `/__nanobar_replay__/`. `regression_brick_analysis_service.py` builds a request
against these paths directly, by convention, the same way it builds one from any other brick's
`entry_point` -- this is what lets it stay framework code that never imports from `app/`: an app
opts into replay support for these surfaces simply by mounting `build_replay_routes()`'s routes,
nothing else to wire.

**Only `event-to-subscriber` is implemented** (D2/D3 of the plan doc: state-machine seeding for
`worker` replay is out of scope for this pass, deliberately, not guessed at -- `worker` replay
dispatch still returns a clear "not built yet" error from `regression_brick_analysis_service.py`,
unaffected by these two routes existing).

Both handlers read from `request.app.state` (`event_bus`, `telemetry_session_factory`) -- no
app-specific import needed here, matching every other handler in this codebase's own convention
of reading `request.app.state` at request time rather than closing over it at route-definition
time (routes are built before `app.state` is populated).
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from nanobar_api.telemetry.span_repository import SpanRepository

#: `regression_brick_analysis_service.py` POSTs `{"channel": str, "payload": dict}` here for any
#: `event-to-subscriber`-typed brick -- dispatches synchronously
#: (`NanobarEventBus.dispatch_now()`) so the resulting span lands under this request's own trace.
REPLAY_TRIGGER_EVENT_PATH = "/__nanobar_replay__/trigger-event"

#: `regression_brick_analysis_service.py` polls this (by the `trace_id` the trigger response
#: returned) to read back the span the triggered dispatch just produced -- the concrete mechanism
#: behind "we can see the generated spans in the shadow DB"
#: (`.focusari/2026-08-27-regression-brick-clarification.md` Part 3).
REPLAY_SPANS_PATH = "/__nanobar_replay__/spans/{trace_id}"


async def _trigger_event(request: Request) -> JSONResponse:
    body: dict[str, Any] = await request.json()
    channel = body["channel"]
    payload = body.get("payload") or {}
    try:
        event = request.app.state.event_bus.dispatch_now(channel, payload)
    except ValueError as exc:
        # `NanobarEventBus.dispatch_now()` rejects a non-`"domain."`-prefixed channel -- a bad
        # `channel` value (e.g. a brick whose `entry_point` was never really an event-to-
        # subscriber one) is caller error, reported as 400, not a crash.
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"event_id": event.event_id, "trace_id": event.trace_id})


async def _spans_for_trace(request: Request) -> JSONResponse:
    trace_id = request.path_params["trace_id"]
    channel = request.query_params.get("channel")
    session = request.app.state.telemetry_session_factory()
    try:
        spans = SpanRepository(session).list_by_trace_id(trace_id, channel=channel)
    finally:
        session.close()
    return JSONResponse([{"span_id": s.span_id, "channel": s.channel, "payload": s.payload_json} for s in spans])


def build_replay_routes() -> list[Route]:
    return [
        Route(REPLAY_TRIGGER_EVENT_PATH, _trigger_event, methods=["POST"]),
        Route(REPLAY_SPANS_PATH, _spans_for_trace, methods=["GET"]),
    ]
