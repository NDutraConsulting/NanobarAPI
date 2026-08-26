"""`IntegrationTestWorker` — Worker-Domain plan Phase D. Implements the source spec's `init()`
self-test lifecycle:

```
init(){
    nanobarType="api-request-response";
    registerRegressionBrickCallbacks();
    fanoutRegressionBrickRequests(getNanobars());
}
```

`mode="cron"`: this worker's lifecycle belongs to an external scheduler, which calls
`run_once()` directly (per `NanobarWorker.run_forever()`'s own documented `mode="cron"`
contract) — building a real cron daemon is explicitly out of scope here, same as every other
`mode="cron"` worker in this codebase. `run_once()` claims whatever trigger events are queued
on its configured channel (typically inserted by whatever *does* own the schedule — a human via
the dashboard, or a real external cron job calling `nanobar_api.eventbus.store.insert_events`),
then fans out a full nanobar sweep per claimed trigger.

Per-nanobar fan-out (not per-brick): matches the source spec's own
`fanoutRegressionBrickRequests(getNanobars())` structure — one task per `Nanobar`, replaying
that nanobar's own bound bricks sequentially within the task. Uses
`nanobar_api.concurrency.run_until_satisfied` with `is_satisfied` always `False` — a different
stopping rule than that primitive's own "race to first" motivating example (a real Design
Decision, not incidental reuse; see `concurrency.py`'s own docstring and the Worker-Domain plan's
§1 cross-reference), since every nanobar's replay should finish, not just the first.

`replay_brick()` is deliberately synchronous (its own docstring: `TestClient` itself blocks, so
there is no real `await` anywhere in its body) — offloaded to a thread via `anyio.to_thread.run_sync`
so concurrent nanobars' replays genuinely overlap rather than serializing on the event loop.

Each brick's replay verdict — via `evaluate_verdict()` (the same layered status/schema/
pinned-field comparison `replay.py`'s own thin-slice checkpoint proved correct), not a naive
equality check — is **published**, not awaited inline ("so the fanouts do not need an await"),
onto `NanobarEventBus`; `registerRegressionBrickCallbacks()` is whatever `NanobarCallback` an app
subscribes to that channel, not built here (no default subscriber is prescribed by the source
spec). Every published event is tagged `synthetic: true`, mirroring the existing
`"nanobar.replay": True` scope key `regression-brick-system-plan.md` §2 already specifies for
the same reason.

**Taxonomy Phase D — gap-filling, not just replay.** When `taxonomy` is given, `process()` also
fires a synthesized request (`nanobar_api.synthesis`) for each of a nanobar's `synthesizable:
true` coverage gaps, after the replay fan-out above. `None` (the default) skips this entirely,
matching every other optional-taxonomy-param already established in this codebase
(`bricks/binding.py`'s bind functions) — a worker with no taxonomy configured just replays, same
as before this phase existed.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

import anyio
import anyio.to_thread
from starlette.testclient import TestClient

from nanobar_api.bricks.replay import replay_brick
from nanobar_api.bricks.schema import Nanobar
from nanobar_api.bricks.store import get_bricks_for_nanobar, list_nanobars
from nanobar_api.bricks.verdict import evaluate_verdict
from nanobar_api.capture.contract import build_contracts_for_routes
from nanobar_api.concurrency import run_until_satisfied
from nanobar_api.eventbus.dispatch import NanobarEventBus
from nanobar_api.eventbus.events import Event
from nanobar_api.synthesis import SYNTHESIS_STRATEGIES, is_expected_outcome
from nanobar_api.taxonomy import NanobarTypeTaxonomy, detect_coverage_gaps
from nanobar_api.telemetry import NanobarTelemetry
from nanobar_api.workers import NanobarWorker, WorkerConfig

if TYPE_CHECKING:
    from starlette.applications import Starlette

#: The domain channel each replay verdict/synthesis outcome is published onto -- what
#: `registerRegressionBrickCallbacks()` in the source spec's `init()` pseudocode names the
#: destination of.
INTEGRATION_TEST_RESULTS_CHANNEL = "domain.integration-test-results"


def _stable_route_key(nanobar: Nanobar) -> tuple[str, str] | None:
    """`(method, path)` from the first `target_type="route"` monitor target ref -- the same
    `"METHOD /path"` `stable_name` shape `bricks/binding.py`'s auto-registration already stamps.
    `None` when the nanobar has no route ref at all (nothing to fire a synthetic request against).
    """
    for ref in nanobar.monitor_target_refs:
        if ref.target_type != "route":
            continue
        method, _, path = ref.stable_name.partition(" ")
        if method and path:
            return method.upper(), path
    return None


class IntegrationTestWorker(NanobarWorker):
    config = WorkerConfig(channels=("integration-tests",), mode="cron")

    def __init__(
        self,
        worker_id: str,
        conn: sqlite3.Connection,
        telemetry: NanobarTelemetry,
        *,
        bricks_conn: sqlite3.Connection,
        app: Starlette,
        event_bus: NanobarEventBus,
        taxonomy: NanobarTypeTaxonomy | None = None,
        claim_limit: int = 10,
        lease_seconds: float = 30.0,
        log_dir: str = "logs",
    ) -> None:
        super().__init__(
            worker_id, conn, telemetry, claim_limit=claim_limit, lease_seconds=lease_seconds, log_dir=log_dir
        )
        self.bricks_conn = bricks_conn
        self.app = app
        self.event_bus = event_bus
        self.taxonomy = taxonomy

    def process(self, event: Event) -> None:
        """Ignores `event.payload` -- the trigger's own content doesn't scope which nanobars
        run (a future `target_type` filter is a reasonable extension, not built here since
        nothing in the source spec asks for one); every claimed trigger event fans out a full
        sweep over `list_nanobars()`.
        """
        nanobars = list_nanobars(self.bricks_conn)

        async def fetch(nanobar: Nanobar) -> list[dict[str, Any]]:
            bricks = get_bricks_for_nanobar(self.bricks_conn, nanobar.nanobar_id)
            outcomes: list[dict[str, Any]] = []
            for brick in bricks:
                replayed_response = await anyio.to_thread.run_sync(replay_brick, self.app, brick)
                verdict = evaluate_verdict(brick, replayed_response)
                outcomes.append(
                    {
                        "nanobar_id": nanobar.nanobar_id,
                        "regression_brick_id": brick.regression_brick_id,
                        "passed": verdict.overall_passed,
                    }
                )
            return outcomes

        def on_event(outcomes: list[dict[str, Any]]) -> None:
            for outcome in outcomes:
                self.event_bus.publish(INTEGRATION_TEST_RESULTS_CHANNEL, {**outcome, "synthetic": True})

        anyio.run(run_until_satisfied, nanobars, fetch, on_event, lambda: False)

        if self.taxonomy is not None:
            self._fill_synthesizable_gaps(nanobars, self.taxonomy)

    def _fill_synthesizable_gaps(self, nanobars: list[Nanobar], taxonomy: NanobarTypeTaxonomy) -> None:
        contracts_by_route_key = {
            (contract.method.upper(), contract.path): contract
            for contract in build_contracts_for_routes(list(self.app.routes))
        }
        client = TestClient(self.app)

        for nanobar in nanobars:
            entry = taxonomy.get(nanobar.nanobar_type)
            if entry is None:
                continue
            route_key = _stable_route_key(nanobar)
            if route_key is None:
                continue
            contract = contracts_by_route_key.get(route_key)
            if contract is None:
                continue

            bound_bricks = get_bricks_for_nanobar(self.bricks_conn, nanobar.nanobar_id)
            gaps = detect_coverage_gaps(nanobar, bound_bricks, taxonomy)

            for scenario_type in gaps:
                scenario = entry.expected_scenarios.get(scenario_type)
                if scenario is None or not scenario.synthesizable:
                    continue
                strategy = SYNTHESIS_STRATEGIES.get(scenario_type)
                if strategy is None:
                    continue
                synthesized = strategy(contract)
                if synthesized is None:
                    continue

                response = client.request(synthesized["method"], synthesized["path"], json=synthesized.get("json"))
                self.event_bus.publish(
                    INTEGRATION_TEST_RESULTS_CHANNEL,
                    {
                        "nanobar_id": nanobar.nanobar_id,
                        "kind": "synthesis",
                        "scenario_type": scenario_type,
                        "status_code": response.status_code,
                        "matched_expected_scenario": is_expected_outcome(scenario_type, response.status_code),
                        "synthetic": True,
                    },
                )
