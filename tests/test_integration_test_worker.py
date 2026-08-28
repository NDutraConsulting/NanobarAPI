from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from sqlalchemy.orm import Session, sessionmaker
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

from nanobar_api.bricks.generate import generate_bricks
from nanobar_api.eventbus.dispatch import NanobarCallback, NanobarEventBus
from nanobar_api.eventbus.events import Event
from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.eventbus.store import connect as connect_events, get_unprocessed, insert_events
from nanobar_api.integration_test_worker import INTEGRATION_TEST_RESULTS_CHANNEL, IntegrationTestWorker
from nanobar_api.middleware.snapshot import SnapshotMiddleware
from nanobar_api.nanobar.model import MonitorTargetRef, Nanobar, NanobarBrickBinding
from nanobar_api.nanobar.repository import NanobarRepository
from nanobar_api.openapi import endpoint_schema
from nanobar_api.persistence import build_session_factory
from nanobar_api.regression_brick.model import RegressionBrick
from nanobar_api.regression_brick.repository import RegressionBrickRepository
from nanobar_api.taxonomy import ExpectedScenario, NanobarTypeEntry
from nanobar_api.telemetry import NanobarTelemetry
from nanobar_api.telemetry.model import Span
from nanobar_api.telemetry.persistence import build_session_factory as build_telemetry_session_factory
from nanobar_api.telemetry.span_repository import SpanRepository
from nanobar_api.telemetry.trace_repository import TraceRepository
from nanobar_api.workers import WorkerConfig

if isinstance(otel_trace.get_tracer_provider(), otel_trace.NoOpTracerProvider | otel_trace.ProxyTracerProvider):
    otel_trace.set_tracer_provider(TracerProvider())


class _CollectingCallback(NanobarCallback):
    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []

    def handle(self, event: Event) -> Any:
        self.received.append(event.payload)


def _snapshot_app(*, items: list[dict[str, Any]], snapshot_repository: EventQueueRepository) -> Starlette:
    async def get_items(request: Request) -> JSONResponse:
        return JSONResponse({"status": "success", "msg": "", "result": {"type": "array", "data": items}})

    return Starlette(
        routes=[Route("/items", get_items)],
        middleware=[Middleware(SnapshotMiddleware, repository=snapshot_repository, channel="snapshot")],
    )


def _bricks_session_factory(tmp_path: Path) -> sessionmaker[Session]:
    return build_session_factory(
        str(tmp_path / "regression_bricks.db"), repository=EventQueueRepository([ChannelConfig(name="snapshot")])
    )


def _insert_nanobar(bricks_session_factory: sessionmaker[Session], nanobar: Nanobar) -> None:
    session = bricks_session_factory()
    try:
        NanobarRepository(session).create(nanobar)
    finally:
        session.close()


def _capture_brick(app: Starlette, snapshot_repository: EventQueueRepository, tmp_path: Path) -> RegressionBrick:
    TestClient(app).get("/items")
    event = snapshot_repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None

    # No EventBusTraceMiddleware wired in `_snapshot_app`, so `event.trace_id`/`.span_id` are
    # `None` -- synthesize them the way `TelemetryDrainWorker` would drop (not ingest) such an
    # event in production; this test is about brick/binding behavior, not trace-context realism.
    trace_id = event.trace_id or f"synthetic-trace-{event.event_id}"
    span_id = event.span_id or f"synthetic-span-{event.event_id}"

    telemetry_session = build_telemetry_session_factory(str(tmp_path / "capture-telemetry.db"))()
    bricks_session = _bricks_session_factory(tmp_path)()
    try:
        trace_repository = TraceRepository(telemetry_session)
        span_repository = SpanRepository(telemetry_session)
        # A real "GET /items" -- not `event.payload.get("name") or event.channel`'s fallback
        # (a bogus non-HTTP string like "snapshot") -- now load-bearing: `generate_bricks()`
        # stamps this straight onto `brick.entry_point`, which `ReplayBrickService` (via
        # `IntegrationTestWorker.process()`) uses directly to build the replay request.
        trace_repository.get_or_create(trace_id, entry_point="GET /items")
        span_repository.create(
            Span(
                event_id=event.event_id,
                span_id=span_id,
                trace_id=trace_id,
                channel=event.channel,
                recorded_at_ns=event.recorded_at_ns,
                monotonic_ns=event.monotonic_ns,
                payload_json=event.payload,
            )
        )
        bricks = generate_bricks(
            trace_repository, span_repository, RegressionBrickRepository(bricks_session), channel="snapshot"
        )
        assert len(bricks) == 1
        return bricks[0]
    finally:
        telemetry_session.close()
        bricks_session.close()


def _bind_brick_to_new_nanobar(
    bricks_session_factory: sessionmaker[Session], brick: RegressionBrick, nanobar_id: str = "nb-1"
) -> None:
    session = bricks_session_factory()
    try:
        nanobar_repository = NanobarRepository(session)
        nanobar_repository.create(
            Nanobar(
                nanobar_id=nanobar_id,
                schema_version="1.0",
                system_name="demo",
                system_version="1.0.0",
                nanobar_type="api-response",
                request_object_id="req-1",
                response_object_id="res-1",
                regression_weight=0.5,
                endpoint_scenario_frequency={"state": "unmeasured"},
                created_by="test",
                monitor_target_refs=[MonitorTargetRef("openapi_operation", "items")],
            )
        )
        nanobar_repository.bind_brick(
            NanobarBrickBinding(
                nanobar_id=nanobar_id,
                regression_brick_id=brick.regression_brick_id,
                match_method="exact",
                matcher_version="v1",
                matched_by="test",
            )
        )
    finally:
        session.close()


def _worker(
    tmp_path: Path,
    *,
    app: Starlette,
    bricks_session_factory: sessionmaker[Session],
    event_bus: NanobarEventBus,
    taxonomy: Any = None,
) -> IntegrationTestWorker:
    worker_events_conn = connect_events(str(tmp_path / "worker-events.db"))
    # "snapshot" is required too, not just "trace" -- process() now constructs a
    # ReplayBrickService (a real NanobarAPIService), whose inherited self-capture emits onto
    # the "snapshot" channel by default (capture_layer()'s own default channel).
    telemetry = NanobarTelemetry(EventQueueRepository([ChannelConfig(name="trace"), ChannelConfig(name="snapshot")]))
    return IntegrationTestWorker(
        "itw-1",
        worker_events_conn,
        telemetry,
        bricks_session_factory=bricks_session_factory,
        app=app,
        event_bus=event_bus,
        taxonomy=taxonomy,
    )


def _trigger(worker: IntegrationTestWorker) -> None:
    insert_events(
        worker.conn,
        [Event(event_id="trigger-1", channel="integration-tests", recorded_at_ns=1, monotonic_ns=1, payload={})],
    )


def test_config_is_cron_mode_on_the_integration_tests_channel() -> None:
    assert IntegrationTestWorker.config == WorkerConfig(channels=("integration-tests",), mode="cron")


def test_process_replays_every_bound_brick_and_publishes_passing_verdicts(tmp_path: Path) -> None:
    snapshot_repository = EventQueueRepository([ChannelConfig(name="snapshot")])
    app = _snapshot_app(items=[{"id": 1, "name": "widget"}], snapshot_repository=snapshot_repository)
    brick = _capture_brick(app, snapshot_repository, tmp_path)

    bricks_session_factory = _bricks_session_factory(tmp_path)
    _bind_brick_to_new_nanobar(bricks_session_factory, brick)

    domain_repository = EventQueueRepository([ChannelConfig(name="domain.integration-test-results")])
    event_bus = NanobarEventBus(
        domain_repository, NanobarTelemetry(EventQueueRepository([ChannelConfig(name="trace")]))
    )
    callback = _CollectingCallback()
    event_bus.subscribe(INTEGRATION_TEST_RESULTS_CHANNEL, callback)

    worker = _worker(tmp_path, app=app, bricks_session_factory=bricks_session_factory, event_bus=event_bus)
    _trigger(worker)

    worker.run_once()

    assert get_unprocessed(worker.conn, "integration-tests") == []  # trigger event acked

    event = domain_repository.get_any([INTEGRATION_TEST_RESULTS_CHANNEL], timeout=1.0)
    assert event is not None
    event_bus._dispatch(event)  # drive the subscriber directly -- no background thread in this test

    assert len(callback.received) == 1
    result = callback.received[0]
    assert result["nanobar_id"] == "nb-1"
    assert result["regression_brick_id"] == brick.regression_brick_id
    assert result["passed"] is True
    assert result["synthetic"] is True


def test_process_publishes_failing_verdict_when_the_app_has_regressed(tmp_path: Path) -> None:
    snapshot_repository = EventQueueRepository([ChannelConfig(name="snapshot")])
    original_app = _snapshot_app(items=[{"id": 1, "name": "widget"}], snapshot_repository=snapshot_repository)
    brick = _capture_brick(original_app, snapshot_repository, tmp_path)

    bricks_session_factory = _bricks_session_factory(tmp_path)
    _bind_brick_to_new_nanobar(bricks_session_factory, brick)

    # A different app, standing in for "the app changed since this brick was captured" -- the
    # regressed shape a real replay is meant to catch.
    regressed_repository = EventQueueRepository([ChannelConfig(name="snapshot")])
    regressed_app = _snapshot_app(items=[], snapshot_repository=regressed_repository)

    domain_repository = EventQueueRepository([ChannelConfig(name="domain.integration-test-results")])
    event_bus = NanobarEventBus(
        domain_repository, NanobarTelemetry(EventQueueRepository([ChannelConfig(name="trace")]))
    )
    callback = _CollectingCallback()
    event_bus.subscribe(INTEGRATION_TEST_RESULTS_CHANNEL, callback)

    worker = _worker(tmp_path, app=regressed_app, bricks_session_factory=bricks_session_factory, event_bus=event_bus)
    _trigger(worker)

    worker.run_once()

    event = domain_repository.get_any([INTEGRATION_TEST_RESULTS_CHANNEL], timeout=1.0)
    assert event is not None
    event_bus._dispatch(event)

    assert callback.received[0]["passed"] is False


def test_process_ignores_nanobars_with_no_bound_bricks(tmp_path: Path) -> None:
    snapshot_repository = EventQueueRepository([ChannelConfig(name="snapshot")])
    app = _snapshot_app(items=[], snapshot_repository=snapshot_repository)

    bricks_session_factory = _bricks_session_factory(tmp_path)
    _insert_nanobar(
        bricks_session_factory,
        Nanobar(
            nanobar_id="nb-empty",
            schema_version="1.0",
            system_name="demo",
            system_version="1.0.0",
            nanobar_type="api-response",
            request_object_id="req-1",
            response_object_id="res-1",
            regression_weight=0.5,
            endpoint_scenario_frequency={"state": "unmeasured"},
            created_by="test",
        ),
    )

    domain_repository = EventQueueRepository([ChannelConfig(name="domain.integration-test-results")])
    event_bus = NanobarEventBus(
        domain_repository, NanobarTelemetry(EventQueueRepository([ChannelConfig(name="trace")]))
    )

    worker = _worker(tmp_path, app=app, bricks_session_factory=bricks_session_factory, event_bus=event_bus)
    _trigger(worker)

    worker.run_once()  # must not raise despite the nanobar having zero bricks to replay

    assert domain_repository.get_any([INTEGRATION_TEST_RESULTS_CHANNEL], timeout=0.2) is None


def test_process_with_no_nanobars_at_all_is_a_noop(tmp_path: Path) -> None:
    snapshot_repository = EventQueueRepository([ChannelConfig(name="snapshot")])
    app = _snapshot_app(items=[], snapshot_repository=snapshot_repository)
    bricks_session_factory = _bricks_session_factory(tmp_path)

    domain_repository = EventQueueRepository([ChannelConfig(name="domain.integration-test-results")])
    event_bus = NanobarEventBus(
        domain_repository, NanobarTelemetry(EventQueueRepository([ChannelConfig(name="trace")]))
    )

    worker = _worker(tmp_path, app=app, bricks_session_factory=bricks_session_factory, event_bus=event_bus)
    _trigger(worker)

    worker.run_once()

    assert get_unprocessed(worker.conn, "integration-tests") == []


# --------------------------------------------------------------- Phase D: synthesizable gaps ---


@dataclass
class _CreateItemRequest:
    title: str


@endpoint_schema(request=_CreateItemRequest)
async def _create_item(request: Request) -> Response:
    body = await request.json()
    if not body:
        return JSONResponse(
            {"status": "error", "msg": "title required", "result": {"type": "object", "data": None}}, status_code=400
        )
    return JSONResponse({"status": "success", "msg": "", "result": {"type": "object", "data": body}}, status_code=201)


async def _get_item(request: Request) -> Response:
    if request.path_params["item_id"] != "real-item":
        return JSONResponse(
            {"status": "error", "msg": "not found", "result": {"type": "object", "data": None}}, status_code=404
        )
    return JSONResponse({"status": "success", "msg": "", "result": {"type": "object", "data": {"id": "real-item"}}})


async def _protected_thing(request: Request) -> Response:
    if request.headers.get("x-fake-auth") != "yes":
        return JSONResponse(
            {"status": "error", "msg": "unauthorized", "result": {"type": "object", "data": None}}, status_code=401
        )
    return JSONResponse({"status": "success", "msg": "", "result": {"type": "object", "data": {}}})


def _gap_filling_app() -> Starlette:
    return Starlette(
        routes=[
            Route("/items", _create_item, methods=["POST"]),
            Route("/items/{item_id}", _get_item, methods=["GET"]),
            Route("/admin/thing", _protected_thing, methods=["GET"]),
        ]
    )


def _gap_taxonomy() -> Any:
    from nanobar_api.taxonomy import NanobarTypeTaxonomy

    scenarios = {
        "validation_error": ExpectedScenario(weight=0.6, required=True, synthesizable=True),
        "not_found": ExpectedScenario(weight=0.5, required=True, synthesizable=True),
        "unauthorized": ExpectedScenario(weight=0.7, required=True, synthesizable=True),
    }
    taxonomy: NanobarTypeTaxonomy = {
        "create-item": NanobarTypeEntry(expected_scenarios={"validation_error": scenarios["validation_error"]}),
        "get-item": NanobarTypeEntry(expected_scenarios={"not_found": scenarios["not_found"]}),
        "protected-thing": NanobarTypeEntry(expected_scenarios={"unauthorized": scenarios["unauthorized"]}),
    }
    return taxonomy


def _make_route_nanobar(nanobar_id: str, nanobar_type: str, route_key: str) -> Nanobar:
    return Nanobar(
        nanobar_id=nanobar_id,
        schema_version="1.0",
        system_name="demo",
        system_version="1.0.0",
        nanobar_type=nanobar_type,
        request_object_id="req-1",
        response_object_id="res-1",
        regression_weight=0.5,
        endpoint_scenario_frequency={"state": "unmeasured"},
        created_by="test",
        monitor_target_refs=[MonitorTargetRef("route", route_key)],
    )


def test_fills_a_validation_error_gap_and_publishes_a_matching_synthesis_outcome(tmp_path: Path) -> None:
    app = _gap_filling_app()
    bricks_session_factory = _bricks_session_factory(tmp_path)
    _insert_nanobar(bricks_session_factory, _make_route_nanobar("nb-create", "create-item", "POST /items"))

    domain_repository = EventQueueRepository([ChannelConfig(name="domain.integration-test-results")])
    event_bus = NanobarEventBus(
        domain_repository, NanobarTelemetry(EventQueueRepository([ChannelConfig(name="trace")]))
    )
    callback = _CollectingCallback()
    event_bus.subscribe(INTEGRATION_TEST_RESULTS_CHANNEL, callback)

    worker = _worker(
        tmp_path, app=app, bricks_session_factory=bricks_session_factory, event_bus=event_bus, taxonomy=_gap_taxonomy()
    )
    _trigger(worker)

    worker.run_once()

    event = domain_repository.get_any([INTEGRATION_TEST_RESULTS_CHANNEL], timeout=1.0)
    assert event is not None
    event_bus._dispatch(event)

    synthesis_events = [r for r in callback.received if r.get("kind") == "synthesis"]
    assert len(synthesis_events) == 1
    result = synthesis_events[0]
    assert result["nanobar_id"] == "nb-create"
    assert result["scenario_type"] == "validation_error"
    assert result["status_code"] == 400
    assert result["matched_expected_scenario"] is True
    assert result["synthetic"] is True


def test_fills_a_not_found_gap(tmp_path: Path) -> None:
    app = _gap_filling_app()
    bricks_session_factory = _bricks_session_factory(tmp_path)
    _insert_nanobar(bricks_session_factory, _make_route_nanobar("nb-get", "get-item", "GET /items/{item_id}"))

    domain_repository = EventQueueRepository([ChannelConfig(name="domain.integration-test-results")])
    event_bus = NanobarEventBus(
        domain_repository, NanobarTelemetry(EventQueueRepository([ChannelConfig(name="trace")]))
    )
    callback = _CollectingCallback()
    event_bus.subscribe(INTEGRATION_TEST_RESULTS_CHANNEL, callback)

    worker = _worker(
        tmp_path, app=app, bricks_session_factory=bricks_session_factory, event_bus=event_bus, taxonomy=_gap_taxonomy()
    )
    _trigger(worker)

    worker.run_once()

    event = domain_repository.get_any([INTEGRATION_TEST_RESULTS_CHANNEL], timeout=1.0)
    assert event is not None
    event_bus._dispatch(event)

    result = callback.received[0]
    assert result["scenario_type"] == "not_found"
    assert result["status_code"] == 404
    assert result["matched_expected_scenario"] is True


def test_fills_an_unauthorized_gap(tmp_path: Path) -> None:
    app = _gap_filling_app()
    bricks_session_factory = _bricks_session_factory(tmp_path)
    _insert_nanobar(bricks_session_factory, _make_route_nanobar("nb-protected", "protected-thing", "GET /admin/thing"))

    domain_repository = EventQueueRepository([ChannelConfig(name="domain.integration-test-results")])
    event_bus = NanobarEventBus(
        domain_repository, NanobarTelemetry(EventQueueRepository([ChannelConfig(name="trace")]))
    )
    callback = _CollectingCallback()
    event_bus.subscribe(INTEGRATION_TEST_RESULTS_CHANNEL, callback)

    worker = _worker(
        tmp_path, app=app, bricks_session_factory=bricks_session_factory, event_bus=event_bus, taxonomy=_gap_taxonomy()
    )
    _trigger(worker)

    worker.run_once()

    event = domain_repository.get_any([INTEGRATION_TEST_RESULTS_CHANNEL], timeout=1.0)
    assert event is not None
    event_bus._dispatch(event)

    result = callback.received[0]
    assert result["scenario_type"] == "unauthorized"
    assert result["status_code"] == 401
    assert result["matched_expected_scenario"] is True


def test_gap_filling_is_skipped_entirely_without_a_taxonomy(tmp_path: Path) -> None:
    app = _gap_filling_app()
    bricks_session_factory = _bricks_session_factory(tmp_path)
    _insert_nanobar(bricks_session_factory, _make_route_nanobar("nb-create", "create-item", "POST /items"))

    domain_repository = EventQueueRepository([ChannelConfig(name="domain.integration-test-results")])
    event_bus = NanobarEventBus(
        domain_repository, NanobarTelemetry(EventQueueRepository([ChannelConfig(name="trace")]))
    )

    worker = _worker(
        tmp_path, app=app, bricks_session_factory=bricks_session_factory, event_bus=event_bus
    )  # no taxonomy
    _trigger(worker)

    worker.run_once()

    assert domain_repository.get_any([INTEGRATION_TEST_RESULTS_CHANNEL], timeout=0.2) is None


def test_gap_filling_skips_a_nanobar_with_no_route_target_ref(tmp_path: Path) -> None:
    app = _gap_filling_app()
    bricks_session_factory = _bricks_session_factory(tmp_path)
    _insert_nanobar(
        bricks_session_factory,
        Nanobar(
            nanobar_id="nb-no-route",
            schema_version="1.0",
            system_name="demo",
            system_version="1.0.0",
            nanobar_type="create-item",
            request_object_id="req-1",
            response_object_id="res-1",
            regression_weight=0.5,
            endpoint_scenario_frequency={"state": "unmeasured"},
            created_by="test",
        ),
    )

    domain_repository = EventQueueRepository([ChannelConfig(name="domain.integration-test-results")])
    event_bus = NanobarEventBus(
        domain_repository, NanobarTelemetry(EventQueueRepository([ChannelConfig(name="trace")]))
    )

    worker = _worker(
        tmp_path, app=app, bricks_session_factory=bricks_session_factory, event_bus=event_bus, taxonomy=_gap_taxonomy()
    )
    _trigger(worker)

    worker.run_once()  # must not raise despite the nanobar having no route ref to synthesize against

    assert domain_repository.get_any([INTEGRATION_TEST_RESULTS_CHANNEL], timeout=0.2) is None


def test_gap_filling_skips_a_nanobar_type_unknown_to_the_taxonomy(tmp_path: Path) -> None:
    app = _gap_filling_app()
    bricks_session_factory = _bricks_session_factory(tmp_path)
    _insert_nanobar(bricks_session_factory, _make_route_nanobar("nb-unknown", "totally-unknown-type", "POST /items"))

    domain_repository = EventQueueRepository([ChannelConfig(name="domain.integration-test-results")])
    event_bus = NanobarEventBus(
        domain_repository, NanobarTelemetry(EventQueueRepository([ChannelConfig(name="trace")]))
    )

    worker = _worker(
        tmp_path, app=app, bricks_session_factory=bricks_session_factory, event_bus=event_bus, taxonomy=_gap_taxonomy()
    )
    _trigger(worker)

    worker.run_once()

    assert domain_repository.get_any([INTEGRATION_TEST_RESULTS_CHANNEL], timeout=0.2) is None


def test_gap_filling_skips_a_route_key_with_no_matching_contract(tmp_path: Path) -> None:
    app = _gap_filling_app()
    bricks_session_factory = _bricks_session_factory(tmp_path)
    _insert_nanobar(
        bricks_session_factory, _make_route_nanobar("nb-ghost", "create-item", "POST /does-not-exist-as-a-route")
    )

    domain_repository = EventQueueRepository([ChannelConfig(name="domain.integration-test-results")])
    event_bus = NanobarEventBus(
        domain_repository, NanobarTelemetry(EventQueueRepository([ChannelConfig(name="trace")]))
    )

    worker = _worker(
        tmp_path, app=app, bricks_session_factory=bricks_session_factory, event_bus=event_bus, taxonomy=_gap_taxonomy()
    )
    _trigger(worker)

    worker.run_once()

    assert domain_repository.get_any([INTEGRATION_TEST_RESULTS_CHANNEL], timeout=0.2) is None


def test_gap_filling_skips_a_required_gap_that_is_not_synthesizable(tmp_path: Path) -> None:
    from nanobar_api.taxonomy import NanobarTypeTaxonomy

    app = _gap_filling_app()
    bricks_session_factory = _bricks_session_factory(tmp_path)
    _insert_nanobar(bricks_session_factory, _make_route_nanobar("nb-create", "create-item", "POST /items"))

    taxonomy: NanobarTypeTaxonomy = {
        "create-item": NanobarTypeEntry(
            expected_scenarios={"validation_error": ExpectedScenario(weight=0.6, required=True, synthesizable=False)}
        )
    }

    domain_repository = EventQueueRepository([ChannelConfig(name="domain.integration-test-results")])
    event_bus = NanobarEventBus(
        domain_repository, NanobarTelemetry(EventQueueRepository([ChannelConfig(name="trace")]))
    )

    worker = _worker(
        tmp_path, app=app, bricks_session_factory=bricks_session_factory, event_bus=event_bus, taxonomy=taxonomy
    )
    _trigger(worker)

    worker.run_once()

    assert domain_repository.get_any([INTEGRATION_TEST_RESULTS_CHANNEL], timeout=0.2) is None


def test_stable_route_key_skips_non_route_refs_and_returns_none_if_none_found() -> None:
    from nanobar_api.integration_test_worker import _stable_route_key

    nanobar = Nanobar(
        nanobar_id="nb-x",
        schema_version="1.0",
        system_name="demo",
        system_version="1.0.0",
        nanobar_type="create-item",
        request_object_id="req-1",
        response_object_id="res-1",
        regression_weight=0.5,
        endpoint_scenario_frequency={"state": "unmeasured"},
        created_by="test",
        monitor_target_refs=[MonitorTargetRef("openapi_operation", "items")],
    )

    assert _stable_route_key(nanobar) is None


def test_stable_route_key_skips_a_route_ref_with_no_space_in_stable_name() -> None:
    from nanobar_api.integration_test_worker import _stable_route_key

    nanobar = _make_route_nanobar("nb-x", "create-item", "not-a-method-and-path")

    assert _stable_route_key(nanobar) is None


def test_gap_filling_skips_a_synthesizable_scenario_with_no_registered_strategy(tmp_path: Path) -> None:
    from nanobar_api.taxonomy import NanobarTypeTaxonomy

    app = _gap_filling_app()
    bricks_session_factory = _bricks_session_factory(tmp_path)
    _insert_nanobar(bricks_session_factory, _make_route_nanobar("nb-create", "create-item", "POST /items"))

    # "forbidden" is a real, declared-synthesizable scenario type with no built strategy
    # (synthesis.py's own documented gap) -- must be skipped, not crash.
    taxonomy: NanobarTypeTaxonomy = {
        "create-item": NanobarTypeEntry(
            expected_scenarios={"forbidden": ExpectedScenario(weight=0.65, required=True, synthesizable=True)}
        )
    }

    domain_repository = EventQueueRepository([ChannelConfig(name="domain.integration-test-results")])
    event_bus = NanobarEventBus(
        domain_repository, NanobarTelemetry(EventQueueRepository([ChannelConfig(name="trace")]))
    )

    worker = _worker(
        tmp_path, app=app, bricks_session_factory=bricks_session_factory, event_bus=event_bus, taxonomy=taxonomy
    )
    _trigger(worker)

    worker.run_once()

    assert domain_repository.get_any([INTEGRATION_TEST_RESULTS_CHANNEL], timeout=0.2) is None


def test_gap_filling_skips_a_scenario_the_strategy_declines_to_synthesize(tmp_path: Path) -> None:
    # not_found against a route with no {param} segment -- synthesize_not_found_request()
    # itself returns None, nothing honest to fire.
    app = _gap_filling_app()
    bricks_session_factory = _bricks_session_factory(tmp_path)
    _insert_nanobar(bricks_session_factory, _make_route_nanobar("nb-create", "get-item", "POST /items"))

    domain_repository = EventQueueRepository([ChannelConfig(name="domain.integration-test-results")])
    event_bus = NanobarEventBus(
        domain_repository, NanobarTelemetry(EventQueueRepository([ChannelConfig(name="trace")]))
    )

    worker = _worker(
        tmp_path, app=app, bricks_session_factory=bricks_session_factory, event_bus=event_bus, taxonomy=_gap_taxonomy()
    )
    _trigger(worker)

    worker.run_once()

    assert domain_repository.get_any([INTEGRATION_TEST_RESULTS_CHANNEL], timeout=0.2) is None
