from __future__ import annotations

import threading
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from nanobar_api.bricks.binding import bind_composite_nanobars, bind_new_bricks_to_nanobars
from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.nanobar.model import NanobarBrickBinding
from nanobar_api.nanobar.repository import NanobarRepository
from nanobar_api.persistence import build_session_factory
from nanobar_api.regression_brick.model import RegressionBrick
from nanobar_api.regression_brick.repository import RegressionBrickRepository
from nanobar_api.taxonomy import ExpectedScenario, NanobarTypeEntry, NanobarTypeTaxonomy


def _repository() -> EventQueueRepository:
    return EventQueueRepository([ChannelConfig(name="snapshot")])


def _repos(tmp_path: Path) -> tuple[Session, NanobarRepository, RegressionBrickRepository]:
    session_factory = build_session_factory(str(tmp_path / "regression_bricks.db"), repository=_repository())
    session = session_factory()
    return session, NanobarRepository(session), RegressionBrickRepository(session)


def _make_brick(
    brick_id: str,
    content_hash: str,
    *,
    nanobar_type: str | None = "validator-request-response",
    route_key: str | None = "GET /health",
    request: dict[str, object] | None = None,
    regression_scenario_type: str | None = None,
) -> RegressionBrick:
    source: dict[str, object] = {"trace_id": "trace-1", "span_id": "span-1", "channel": "snapshot"}
    if nanobar_type is not None:
        source["nanobar_type"] = nanobar_type
    if route_key is not None:
        source["route_key"] = route_key
    return RegressionBrick(
        regression_brick_id=brick_id,
        schema_version="1.0",
        brick_version=1,
        source=source,
        request=request if request is not None else {"body": {}},
        response={"ok": True},
        content_hash=content_hash,
        regression_scenario_type=regression_scenario_type,
        created_by="test",
    )


def test_get_or_create_creates_then_reuses(tmp_path: Path) -> None:
    session, nanobar_repository, _brick_repository = _repos(tmp_path)
    try:
        first, created_first = nanobar_repository.get_or_create_by_route_key(
            nanobar_type="validator-request-response", route_key="GET /health"
        )
        second, created_second = nanobar_repository.get_or_create_by_route_key(
            nanobar_type="validator-request-response", route_key="GET /health"
        )

        assert created_first is True
        assert created_second is False
        assert first.nanobar_id == second.nanobar_id
        assert first.nanobar_type == "validator-request-response"
        assert first.monitor_target_refs[0].stable_name == "GET /health"
        assert first.monitor_target_refs[0].target_type == "route"
    finally:
        session.close()


def test_get_or_create_stamps_domain_on_a_new_row(tmp_path: Path) -> None:
    session, nanobar_repository, _brick_repository = _repos(tmp_path)
    try:
        nanobar, created = nanobar_repository.get_or_create_by_route_key(
            nanobar_type="validator-request-response", route_key="GET /health", domain="admin/nanobar"
        )

        assert created is True
        assert nanobar.domain == "admin/nanobar"
    finally:
        session.close()


def test_get_or_create_domain_defaults_to_none(tmp_path: Path) -> None:
    session, nanobar_repository, _brick_repository = _repos(tmp_path)
    try:
        nanobar, _ = nanobar_repository.get_or_create_by_route_key(
            nanobar_type="validator-request-response", route_key="GET /health"
        )

        assert nanobar.domain is None
    finally:
        session.close()


def test_get_or_create_distinguishes_by_nanobar_type(tmp_path: Path) -> None:
    session, nanobar_repository, _brick_repository = _repos(tmp_path)
    try:
        validator_nanobar, _ = nanobar_repository.get_or_create_by_route_key(
            nanobar_type="validator-request-response", route_key="GET /health"
        )
        controller_nanobar, _ = nanobar_repository.get_or_create_by_route_key(
            nanobar_type="controller-request-response", route_key="GET /health"
        )

        assert validator_nanobar.nanobar_id != controller_nanobar.nanobar_id
    finally:
        session.close()


def test_get_or_create_placeholder_metadata_matches_seed_script_convention(tmp_path: Path) -> None:
    session, nanobar_repository, _brick_repository = _repos(tmp_path)
    try:
        nanobar, _ = nanobar_repository.get_or_create_by_route_key(
            nanobar_type="validator-request-response", route_key="GET /health"
        )

        assert nanobar.regression_weight == 0.5
        assert nanobar.endpoint_scenario_frequency == {"state": "unmeasured"}
        assert nanobar.request_object_id == "req-GET /health"
        assert nanobar.response_object_id == "res-GET /health"
    finally:
        session.close()


def test_get_or_create_concurrent_first_sight_creates_exactly_one_row(tmp_path: Path) -> None:
    """Atomicity now comes from the engine-level `BEGIN IMMEDIATE` listener installed by
    `build_session_factory` (`nanobar_api/persistence.py`), not a per-call manual transaction --
    all threads share one `session_factory`/engine, each opening its own `Session`, matching how
    a real multi-request server shares one engine across concurrent request-scoped sessions."""
    session_factory = build_session_factory(str(tmp_path / "regression_bricks.db"), repository=_repository())

    created_flags: list[bool] = []
    lock = threading.Lock()
    errors: list[BaseException] = []

    def _worker() -> None:
        session = session_factory()
        try:
            _, was_created = NanobarRepository(session).get_or_create_by_route_key(
                nanobar_type="validator-request-response", route_key="GET /health"
            )
            with lock:
                created_flags.append(was_created)
        except BaseException as exc:  # pragma: no cover - surfaced via `errors` assertion below
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=_worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert created_flags.count(True) == 1
    assert created_flags.count(False) == 4

    session = session_factory()
    try:
        nanobars = NanobarRepository(session).list_nanobars()
        assert len(nanobars) == 1
    finally:
        session.close()


def test_bind_new_bricks_creates_nanobars_and_bindings(tmp_path: Path) -> None:
    session, nanobar_repository, brick_repository = _repos(tmp_path)
    try:
        bricks = [
            _make_brick("rbrick-1", "sha256:one", nanobar_type="validator-request-response"),
            _make_brick("rbrick-2", "sha256:two", nanobar_type="controller-request-response"),
        ]
        for brick in bricks:
            brick_repository.create(brick)

        result = bind_new_bricks_to_nanobars(nanobar_repository, bricks)

        assert result.nanobars_created == 2
        assert result.bindings_created == 2
        assert result.skipped == 0

        nanobars = nanobar_repository.list_nanobars()
        assert len(nanobars) == 2
        for brick in bricks:
            bound_nanobars = brick_repository.nanobars_for(brick.regression_brick_id)
            assert len(bound_nanobars) == 1
            assert bound_nanobars[0].nanobar_type == brick.source["nanobar_type"]
    finally:
        session.close()


def test_bind_new_bricks_stamps_domain_from_route_key_domains(tmp_path: Path) -> None:
    session, nanobar_repository, brick_repository = _repos(tmp_path)
    try:
        brick = _make_brick("rbrick-1", "sha256:one", route_key="GET /admin/nanobar/dashboard")
        brick_repository.create(brick)

        bind_new_bricks_to_nanobars(
            nanobar_repository, [brick], route_key_domains={"GET /admin/nanobar/dashboard": "admin/nanobar"}
        )

        nanobars = nanobar_repository.list_nanobars()
        assert nanobars[0].domain == "admin/nanobar"
    finally:
        session.close()


def test_bind_new_bricks_domain_is_none_for_a_route_key_missing_from_the_mapping(tmp_path: Path) -> None:
    session, nanobar_repository, brick_repository = _repos(tmp_path)
    try:
        brick = _make_brick("rbrick-1", "sha256:one", route_key="GET /unmapped")
        brick_repository.create(brick)

        bind_new_bricks_to_nanobars(nanobar_repository, [brick], route_key_domains={"GET /other": "admin/app"})

        nanobars = nanobar_repository.list_nanobars()
        assert nanobars[0].domain is None
    finally:
        session.close()


def test_bind_new_bricks_reuses_nanobar_for_bricks_sharing_route_key_and_type(tmp_path: Path) -> None:
    session, nanobar_repository, brick_repository = _repos(tmp_path)
    try:
        bricks = [
            _make_brick("rbrick-1", "sha256:one"),
            _make_brick("rbrick-2", "sha256:two"),
        ]
        for brick in bricks:
            brick_repository.create(brick)

        result = bind_new_bricks_to_nanobars(nanobar_repository, bricks)

        assert result.nanobars_created == 1
        assert result.bindings_created == 2
        nanobars = nanobar_repository.list_nanobars()
        assert len(nanobars) == 1
        bricks_bound = nanobar_repository.bricks_for(nanobars[0].nanobar_id)
        assert {b.regression_brick_id for b in bricks_bound} == {"rbrick-1", "rbrick-2"}
    finally:
        session.close()


def test_bind_new_bricks_skips_bricks_without_nanobar_type_or_route_key(tmp_path: Path) -> None:
    session, nanobar_repository, _brick_repository = _repos(tmp_path)
    try:
        bricks = [
            _make_brick("rbrick-1", "sha256:one", nanobar_type=None),
            _make_brick("rbrick-2", "sha256:two", route_key=None),
        ]

        result = bind_new_bricks_to_nanobars(nanobar_repository, bricks)

        assert result.nanobars_created == 0
        assert result.bindings_created == 0
        assert result.skipped == 2
        assert nanobar_repository.list_nanobars() == []
    finally:
        session.close()


def test_bind_new_bricks_uses_match_method_exact(tmp_path: Path) -> None:
    session, nanobar_repository, brick_repository = _repos(tmp_path)
    try:
        brick = _make_brick("rbrick-1", "sha256:one")
        brick_repository.create(brick)

        bind_new_bricks_to_nanobars(nanobar_repository, [brick])

        nanobar = nanobar_repository.list_nanobars()[0]
        binding = session.query(NanobarBrickBinding).one()
        assert binding.nanobar_id == nanobar.nanobar_id
        assert binding.match_method == "exact"
        assert binding.matcher_version == "v1"
        assert binding.matched_by == "auto-registration"
        assert binding.confidence == 1.0
    finally:
        session.close()


def test_get_or_create_finds_match_among_multiple_nanobars_of_same_type(tmp_path: Path) -> None:
    session, nanobar_repository, _brick_repository = _repos(tmp_path)
    try:
        nanobar_repository.get_or_create_by_route_key(
            nanobar_type="validator-request-response", route_key="GET /health"
        )
        nanobar_repository.get_or_create_by_route_key(
            nanobar_type="validator-request-response", route_key="POST /orders"
        )

        found, was_created = nanobar_repository.get_or_create_by_route_key(
            nanobar_type="validator-request-response", route_key="POST /orders"
        )

        assert was_created is False
        assert found.monitor_target_refs[0].stable_name == "POST /orders"
    finally:
        session.close()


def test_bind_composite_creates_composite_nanobar_when_all_member_types_present(tmp_path: Path) -> None:
    session, nanobar_repository, brick_repository = _repos(tmp_path)
    try:
        controller_brick = _make_brick("rbrick-1", "sha256:one", nanobar_type="controller-request-response")
        orm_brick = _make_brick("rbrick-2", "sha256:two", nanobar_type="orm-request-response")
        for brick in (controller_brick, orm_brick):
            brick_repository.create(brick)

        result = bind_composite_nanobars(
            nanobar_repository,
            [controller_brick, orm_brick],
            composite_nanobar_type="controller-to-db",
            member_nanobar_types=("controller-request-response", "orm-request-response"),
        )

        assert result.nanobars_created == 1
        assert result.bindings_created == 2
        assert result.skipped == 0

        nanobars = nanobar_repository.list_nanobars()
        composite = next(n for n in nanobars if n.nanobar_type == "controller-to-db")
        bound = nanobar_repository.bricks_for(composite.nanobar_id)
        assert {b.regression_brick_id for b in bound} == {"rbrick-1", "rbrick-2"}
    finally:
        session.close()


def test_bind_composite_skips_route_key_missing_a_member_type(tmp_path: Path) -> None:
    session, nanobar_repository, brick_repository = _repos(tmp_path)
    try:
        controller_brick = _make_brick(
            "rbrick-1", "sha256:one", nanobar_type="controller-request-response", route_key="GET /health"
        )
        # orm brick for a DIFFERENT route -- no composite should form for either route.
        orm_brick = _make_brick("rbrick-2", "sha256:two", nanobar_type="orm-request-response", route_key="POST /other")
        for brick in (controller_brick, orm_brick):
            brick_repository.create(brick)

        result = bind_composite_nanobars(
            nanobar_repository,
            [controller_brick, orm_brick],
            composite_nanobar_type="controller-to-db",
            member_nanobar_types=("controller-request-response", "orm-request-response"),
        )

        assert result.nanobars_created == 0
        assert result.bindings_created == 0
        assert result.skipped == 2
        assert nanobar_repository.list_nanobars() == []
    finally:
        session.close()


def test_bind_composite_derives_route_key_for_untagged_api_brick(tmp_path: Path) -> None:
    session, nanobar_repository, brick_repository = _repos(tmp_path)
    try:
        api_brick = _make_brick(
            "rbrick-1",
            "sha256:one",
            nanobar_type=None,
            route_key=None,
            request={"method": "POST", "path": "/orders"},
        )
        orm_brick = _make_brick("rbrick-2", "sha256:two", nanobar_type="orm-request-response", route_key="POST /orders")
        for brick in (api_brick, orm_brick):
            brick_repository.create(brick)

        result = bind_composite_nanobars(
            nanobar_repository,
            [api_brick, orm_brick],
            composite_nanobar_type="api-to-db",
            member_nanobar_types=("api-request-response", "orm-request-response"),
        )

        assert result.nanobars_created == 1
        assert result.bindings_created == 2
        nanobars = nanobar_repository.list_nanobars()
        composite = next(n for n in nanobars if n.nanobar_type == "api-to-db")
        assert composite.monitor_target_refs[0].stable_name == "POST /orders"
    finally:
        session.close()


def test_bind_composite_reuses_existing_composite_nanobar_across_calls(tmp_path: Path) -> None:
    session, nanobar_repository, brick_repository = _repos(tmp_path)
    try:
        first_pair = [
            _make_brick("rbrick-1", "sha256:one", nanobar_type="controller-request-response"),
            _make_brick("rbrick-2", "sha256:two", nanobar_type="orm-request-response"),
        ]
        second_pair = [
            _make_brick("rbrick-3", "sha256:three", nanobar_type="controller-request-response"),
            _make_brick("rbrick-4", "sha256:four", nanobar_type="orm-request-response"),
        ]
        for brick in [*first_pair, *second_pair]:
            brick_repository.create(brick)

        bind_composite_nanobars(
            nanobar_repository,
            first_pair,
            composite_nanobar_type="controller-to-db",
            member_nanobar_types=("controller-request-response", "orm-request-response"),
        )
        result = bind_composite_nanobars(
            nanobar_repository,
            second_pair,
            composite_nanobar_type="controller-to-db",
            member_nanobar_types=("controller-request-response", "orm-request-response"),
        )

        assert result.nanobars_created == 0  # reused the nanobar created for the first pair
        nanobars = [n for n in nanobar_repository.list_nanobars() if n.nanobar_type == "controller-to-db"]
        assert len(nanobars) == 1
    finally:
        session.close()


def test_bind_composite_ignores_brick_with_no_derivable_route_key(tmp_path: Path) -> None:
    session, nanobar_repository, brick_repository = _repos(tmp_path)
    try:
        no_route_key_brick = _make_brick(
            "rbrick-1", "sha256:one", nanobar_type=None, route_key=None, request={"body": {}}
        )
        orm_brick = _make_brick("rbrick-2", "sha256:two", nanobar_type="orm-request-response")
        for brick in (no_route_key_brick, orm_brick):
            brick_repository.create(brick)

        result = bind_composite_nanobars(
            nanobar_repository,
            [no_route_key_brick, orm_brick],
            composite_nanobar_type="api-to-db",
            member_nanobar_types=("api-request-response", "orm-request-response"),
        )

        assert result.bindings_created == 0
        assert nanobar_repository.list_nanobars() == []
    finally:
        session.close()


def test_bind_composite_treats_empty_string_route_key_as_absent(tmp_path: Path) -> None:
    # `_effective_route_key()` must use truthiness, not `is not None` -- an empty-string
    # `route_key` (or empty method/path) is not a real route key. Before that fix, an empty
    # string passed `is not None` and was used as-is, so any two otherwise-unrelated
    # malformed/untagged bricks of different member types would collide under one degenerate
    # "" route key and get spuriously bound into the same composite nanobar. Both bricks here
    # carry empty route info specifically so they *would* collide under the old logic --
    # distinct-but-both-missing route keys (as in the "no derivable route key" test above)
    # wouldn't actually exercise the bug, since they'd never land in the same bucket anyway.
    session, nanobar_repository, brick_repository = _repos(tmp_path)
    try:
        empty_route_key_api_brick = _make_brick(
            "rbrick-1", "sha256:one", nanobar_type=None, route_key="", request={"method": "", "path": ""}
        )
        empty_route_key_orm_brick = _make_brick(
            "rbrick-2",
            "sha256:two",
            nanobar_type="orm-request-response",
            route_key="",
            request={"method": "", "path": ""},
        )
        for brick in (empty_route_key_api_brick, empty_route_key_orm_brick):
            brick_repository.create(brick)

        result = bind_composite_nanobars(
            nanobar_repository,
            [empty_route_key_api_brick, empty_route_key_orm_brick],
            composite_nanobar_type="api-to-db",
            member_nanobar_types=("api-request-response", "orm-request-response"),
        )

        assert result.bindings_created == 0
        assert nanobar_repository.list_nanobars() == []
    finally:
        session.close()


def test_bind_composite_ignores_bricks_of_uninvolved_nanobar_types(tmp_path: Path) -> None:
    session, nanobar_repository, brick_repository = _repos(tmp_path)
    try:
        validator_brick = _make_brick("rbrick-1", "sha256:one", nanobar_type="validator-request-response")
        controller_brick = _make_brick("rbrick-2", "sha256:two", nanobar_type="controller-request-response")
        orm_brick = _make_brick("rbrick-3", "sha256:three", nanobar_type="orm-request-response")
        for brick in (validator_brick, controller_brick, orm_brick):
            brick_repository.create(brick)

        result = bind_composite_nanobars(
            nanobar_repository,
            [validator_brick, controller_brick, orm_brick],
            composite_nanobar_type="controller-to-db",
            member_nanobar_types=("controller-request-response", "orm-request-response"),
        )

        assert result.bindings_created == 2
        composite = next(n for n in nanobar_repository.list_nanobars() if n.nanobar_type == "controller-to-db")
        bound = nanobar_repository.bricks_for(composite.nanobar_id)
        assert {b.regression_brick_id for b in bound} == {"rbrick-2", "rbrick-3"}
    finally:
        session.close()


# ------------------------------------------------- taxonomy-driven regression_weight recompute

_WEIGHT_TEST_TAXONOMY: NanobarTypeTaxonomy = {
    "validator-request-response": NanobarTypeEntry(
        expected_scenarios={
            "success": ExpectedScenario(weight=1.0, required=True, synthesizable=False),
            "invalid_input": ExpectedScenario(weight=0.6, required=True, synthesizable=True),
        }
    ),
    "controller-request-response": NanobarTypeEntry(
        expected_scenarios={"success": ExpectedScenario(weight=1.0, required=True, synthesizable=False)}
    ),
    "orm-request-response": NanobarTypeEntry(
        expected_scenarios={"success": ExpectedScenario(weight=1.0, required=True, synthesizable=False)}
    ),
}


def test_bind_new_bricks_with_taxonomy_recomputes_weight(tmp_path: Path) -> None:
    session, nanobar_repository, brick_repository = _repos(tmp_path)
    try:
        brick = _make_brick(
            "rbrick-1", "sha256:one", nanobar_type="validator-request-response", regression_scenario_type="success"
        )
        brick_repository.create(brick)

        bind_new_bricks_to_nanobars(nanobar_repository, [brick], taxonomy=_WEIGHT_TEST_TAXONOMY)

        nanobar = nanobar_repository.list_nanobars()[0]
        # 1 of 2 required scenarios covered (success, weight 1.0, out of total 1.6) * default criticality 0.5
        assert nanobar.regression_weight == pytest.approx((1.0 / 1.6) * 0.5)
    finally:
        session.close()


def test_bind_new_bricks_without_taxonomy_leaves_weight_as_placeholder(tmp_path: Path) -> None:
    session, nanobar_repository, brick_repository = _repos(tmp_path)
    try:
        brick = _make_brick(
            "rbrick-1", "sha256:one", nanobar_type="validator-request-response", regression_scenario_type="success"
        )
        brick_repository.create(brick)

        bind_new_bricks_to_nanobars(nanobar_repository, [brick])  # no taxonomy -- unchanged behavior

        nanobar = nanobar_repository.list_nanobars()[0]
        assert nanobar.regression_weight == 0.5  # the placeholder default, never recomputed
    finally:
        session.close()


def test_bind_new_bricks_recomputes_weight_once_per_nanobar_using_all_its_bricks(tmp_path: Path) -> None:
    session, nanobar_repository, brick_repository = _repos(tmp_path)
    try:
        first = _make_brick(
            "rbrick-1", "sha256:one", nanobar_type="validator-request-response", regression_scenario_type="success"
        )
        second = _make_brick(
            "rbrick-2",
            "sha256:two",
            nanobar_type="validator-request-response",
            regression_scenario_type="invalid_input",
        )
        brick_repository.create(first)
        brick_repository.create(second)

        bind_new_bricks_to_nanobars(nanobar_repository, [first, second], taxonomy=_WEIGHT_TEST_TAXONOMY)

        nanobar = nanobar_repository.list_nanobars()[0]
        # Both required scenarios now covered across both bricks bound to the same nanobar.
        assert nanobar.regression_weight == pytest.approx(0.5)
    finally:
        session.close()


def test_bind_composite_with_taxonomy_recomputes_composite_weight(tmp_path: Path) -> None:
    session, nanobar_repository, brick_repository = _repos(tmp_path)
    try:
        controller_brick = _make_brick(
            "rbrick-1",
            "sha256:one",
            nanobar_type="controller-request-response",
            regression_scenario_type="success",
        )
        orm_brick = _make_brick(
            "rbrick-2", "sha256:two", nanobar_type="orm-request-response", regression_scenario_type="success"
        )
        for brick in (controller_brick, orm_brick):
            brick_repository.create(brick)

        # "controller-to-db" isn't itself a taxonomy entry -- unknown-type fallback applies
        # (returns the placeholder unchanged), proving the wiring runs without crashing even
        # when the composite type has no taxonomy entry of its own.
        bind_composite_nanobars(
            nanobar_repository,
            [controller_brick, orm_brick],
            composite_nanobar_type="controller-to-db",
            member_nanobar_types=("controller-request-response", "orm-request-response"),
            taxonomy=_WEIGHT_TEST_TAXONOMY,
        )

        composite = next(n for n in nanobar_repository.list_nanobars() if n.nanobar_type == "controller-to-db")
        assert composite.regression_weight == 0.5  # unknown type to the taxonomy -- unchanged
    finally:
        session.close()
