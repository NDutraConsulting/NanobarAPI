"""`NanobarAPIStateMachine` -- **stub only, not wired up anywhere yet.** Scaffolding for D2
(`.focusari/2026-08-27.1800.build_and_update_plan_with_tasks.md` Phase 5's confirmed-deferred
item): before a `worker`/`event-to-subscriber` brick can be replayed meaningfully, the shadow db
often needs to already look like it did at the moment the original trigger fired (e.g. a blog
post needs to look "due for publishing" before `PostPublisherThread`'s sweep will do anything
interesting with it; an appointment needs to exist and be unconfirmed before
`AppointmentNotificationCallback` produces a real notification). Today, replay for these two
surfaces (`nanobar_api/regression_brick/regression_brick_analysis_service.py`) either has no
dispatch at all (`worker`) or re-publishes the brick's own captured payload with no db state
prepared first (`event-to-subscriber`) -- fine for a stateless subscriber, silently wrong for one
whose behavior depends on rows that don't exist in the shadow db.

Not built now, deliberately -- confirmed with the user this is app-specific enough (what state,
for which surface, seeded how) that it shouldn't be guessed at. This file exists so the shape of
*how* it would plug in is written down, not lost, for whenever it's picked back up.

**Why a base class in `nanobar_api/framework/`, not something ad hoc in `app/`**: every other
domain boundary in this codebase (`NanobarAPIService`, `NanobarAPIController`,
`NanobarAPIValidatorGate`, `NanobarAPIRepository`) is a framework-provided abstract base an app
subclasses -- state-machine seeding for replay is the same shape of problem (an app declares
*what* needs to happen; the framework decides *when* it gets called), so it gets the same
treatment rather than becoming a one-off convention.

## How this would plug in (design sketch, not built)

1. **Registration.** An app builds one `NanobarAPIStateMachine` subclass per `worker`/
   `event-to-subscriber` surface that needs seeding (e.g. `AppointmentSeeder` for
   `domain.appointments`), and registers it somewhere `replay_routes.py`'s handlers can find it
   by channel -- most likely `app.state.replay_state_seeders: dict[str, NanobarAPIStateMachine]`,
   the same "app opts in via `app.state`" convention `replay_routes.py`'s existing two handlers
   already use for `event_bus`/`telemetry_session_factory`. A channel with no registered seeder
   just means "nothing to seed," not an error -- most subscribers are stateless.

2. **A third framework route**, alongside `REPLAY_TRIGGER_EVENT_PATH`/`REPLAY_SPANS_PATH`
   (`nanobar_api/regression_brick/replay_routes.py`): something like
   `POST /__nanobar_replay__/seed-state`, body `{"channel": str, "brick_request": dict}` --
   looks up `request.app.state.replay_state_seeders.get(channel)`, no-ops if there isn't one,
   else calls `seeder.seed(brick_request)`.

3. **`regression_brick_analysis_service.py`'s dispatch functions** (`_dispatch_event_to_subscriber()`
   today, a future `_dispatch_worker()`) would POST to the new seed-state route *before*
   triggering the event/worker -- same two-call shape `_dispatch_event_to_subscriber()` already
   has for trigger-then-poll, just one more call in front.

4. **Idempotency matters more here than it does for `dispatch_now()`.** A replay can be re-run
   (the dashboard's "Run" button, or an `IntegrationTestWorker` sweep) against a shadow db that
   already has a previous run's seeded state sitting in it. `seed()` must tolerate being called
   against state it, or a prior identical call, already produced -- `teardown()` exists so a
   caller *can* clean up between runs, but nothing here assumes it always will.

5. **`worker` replay dispatch itself is a separate, still-unbuilt mechanism** -- invoking a
   worker's `run_once()` isn't just "seed state then make an HTTP/event call" the way
   `event-to-subscriber` is; workers claim triggers via `nanobar_api.eventbus.store`'s
   lease/claim semantics (`NanobarWorker`), not a simple direct invocation. This class only
   solves the seeding half; the dispatch half needs its own design pass.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from nanobar_api.telemetry import NanobarTelemetry


class NanobarAPIStateMachine(ABC):
    """One subclass per `worker`/`event-to-subscriber` channel needing shadow-db state prepared
    before a regression-brick replay can meaningfully exercise it. `channel` is the bare domain
    channel name (e.g. `"domain.appointments"`, matching `brick.entry_point`'s
    `f"worker-{channel}"`/`f"event-{channel}"` convention with the prefix stripped) -- not yet
    read by anything; see module docstring step 1 for the registration shape this is meant for.

    `__init__` takes only `telemetry`, matching every other framework base class's constructor
    convention -- a concrete subclass adds whatever *additional* app-specific dependency it
    actually needs to seed state (e.g. a `blog_session_factory`), the same way concrete
    `NanobarAPIService` subclasses already do (see `ReplayBrickService.__init__`).
    """

    channel: str

    def __init__(self, telemetry: NanobarTelemetry) -> None:
        self.telemetry = telemetry

    #: Write whatever pre-existing state this brick's replay depends on into the shadow db,
    #: before the worker/event trigger fires. `brick_request` is the brick's own captured
    #: `request` payload (the same dict `_dispatch_event_to_subscriber()` already re-publishes
    #: as-is today) -- a seeder reads whatever fields it needs out of it (e.g. an appointment
    #: id) to know what to prepare. Must tolerate being called against state a previous
    #: identical call already produced -- see module docstring step 4.
    @abstractmethod
    def seed(self, brick_request: dict[str, Any]) -> None: ...

    #: Undo whatever `seed()` wrote, for a caller that wants a clean shadow db between replay
    #: runs. Not assumed to be called automatically by anything yet -- a manual escape hatch for
    #: now, not a guaranteed lifecycle hook.
    @abstractmethod
    def teardown(self, brick_request: dict[str, Any]) -> None: ...
