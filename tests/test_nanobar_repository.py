from __future__ import annotations

from pathlib import Path

from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.nanobar.model import MonitorTargetRef, Nanobar, NanobarBrickBinding
from nanobar_api.nanobar.repository import UNMAPPED_DOMAIN, NanobarRepository
from nanobar_api.persistence import build_session_factory
from nanobar_api.regression_brick.model import RegressionBrick
from nanobar_api.regression_brick.repository import RegressionBrickRepository


def _repository() -> EventQueueRepository:
    return EventQueueRepository([ChannelConfig(name="trace"), ChannelConfig(name="snapshot")])


def _make_nanobar(**overrides: object) -> Nanobar:
    defaults: dict[str, object] = {
        "schema_version": "1.0",
        "system_name": "test",
        "system_version": "0.0.0",
        "nanobar_type": "api-response",
        "request_object_id": "req-1",
        "response_object_id": "res-1",
        "regression_weight": 0.5,
        "endpoint_scenario_frequency": {"state": "unmeasured"},
        "created_by": "test",
    }
    defaults.update(overrides)
    return Nanobar(**defaults)


def _make_brick(**overrides: object) -> RegressionBrick:
    defaults: dict[str, object] = {
        "schema_version": "1.0",
        "brick_version": 1,
        "source": {},
        "request": {},
        "response": {},
        "content_hash": "h",
        "created_by": "test",
    }
    defaults.update(overrides)
    return RegressionBrick(**defaults)


def test_create_and_get_round_trip(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = NanobarRepository(session)
        nanobar = repo.create(_make_nanobar())

        assert repo.get(nanobar.nanobar_id) is nanobar


def test_get_returns_none_for_unknown_id(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = NanobarRepository(session)
        assert repo.get("does-not-exist") is None


def test_get_uses_the_cache_on_second_call(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = NanobarRepository(session)
        nanobar = repo.create(_make_nanobar())

        repo.get(nanobar.nanobar_id)
        assert repo.get_cached(nanobar.nanobar_id) is nanobar
        assert repo.get(nanobar.nanobar_id) is nanobar


def test_update_fields_overwrites_human_navigation_fields(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = NanobarRepository(session)
        nanobar = repo.create(_make_nanobar())

        updated = repo.update_fields(
            nanobar.nanobar_id,
            label="Checkout",
            scenario_description="desc",
            component_source_description="src",
            domain="checkout",
            criticality=0.9,
        )

        assert updated is not None
        assert updated.label == "Checkout"
        assert updated.domain == "checkout"
        assert updated.criticality == 0.9
        assert repo.get_cached(nanobar.nanobar_id) is None


def test_update_fields_returns_none_for_unknown_id(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = NanobarRepository(session)
        assert repo.update_fields("does-not-exist", criticality=0.5) is None


def test_set_regression_weight(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = NanobarRepository(session)
        nanobar = repo.create(_make_nanobar())

        updated = repo.set_regression_weight(nanobar.nanobar_id, 0.75)
        assert updated is not None
        assert updated.regression_weight == 0.75


def test_set_regression_weight_returns_none_for_unknown_id(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = NanobarRepository(session)
        assert repo.set_regression_weight("does-not-exist", 0.5) is None


def test_set_domain(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = NanobarRepository(session)
        nanobar = repo.create(_make_nanobar())

        updated = repo.set_domain(nanobar.nanobar_id, "checkout")
        assert updated is not None
        assert updated.domain == "checkout"


def test_set_domain_returns_none_for_unknown_id(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = NanobarRepository(session)
        assert repo.set_domain("does-not-exist", "checkout") is None


def test_soft_delete_and_restore(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = NanobarRepository(session)
        nanobar = repo.create(_make_nanobar())

        deleted = repo.soft_delete(nanobar.nanobar_id, deleted_at="2026-08-27T00:00:00+00:00")
        assert deleted is not None
        assert deleted.deleted_at == "2026-08-27T00:00:00+00:00"

        restored = repo.restore(nanobar.nanobar_id)
        assert restored is not None
        assert restored.deleted_at is None


def test_soft_delete_returns_none_for_unknown_id(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = NanobarRepository(session)
        assert repo.soft_delete("does-not-exist", deleted_at="now") is None


def test_restore_returns_none_for_unknown_id(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = NanobarRepository(session)
        assert repo.restore("does-not-exist") is None


def test_list_known_route_keys(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = NanobarRepository(session)
        nanobar = _make_nanobar()
        nanobar.monitor_target_refs = [MonitorTargetRef(target_type="route", stable_name="GET /x")]
        repo.create(nanobar)

        assert repo.list_known_route_keys() == {"GET /x"}


def test_list_and_count_nanobars_with_filters(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = NanobarRepository(session)
        matching = _make_nanobar(label="Checkout Flow", domain="checkout")
        matching.monitor_target_refs = [MonitorTargetRef(target_type="route", stable_name="GET /checkout")]
        repo.create(matching)
        other = _make_nanobar(nanobar_type="worker-response", domain=None)
        repo.create(other)

        assert repo.count_nanobars() == 2
        assert repo.count_nanobars(target_type="route") == 1
        assert repo.count_nanobars(nanobar_type="worker-response") == 1
        assert repo.count_nanobars(domain="checkout") == 1
        assert repo.count_nanobars(domain=UNMAPPED_DOMAIN) == 1
        assert repo.count_nanobars(q="checkout") == 1

        assert [n.nanobar_id for n in repo.list_nanobars(domain="checkout")] == [matching.nanobar_id]
        assert repo.list_nanobars(page=3, page_size=1) == []


def test_get_or_create_by_route_key_creates_once_and_reuses(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = NanobarRepository(session)

        nanobar, created = repo.get_or_create_by_route_key(
            nanobar_type="api-response", route_key="GET /x", domain="checkout"
        )
        assert created is True
        assert nanobar.domain == "checkout"
        assert nanobar.request_object_id == "req-GET /x"

        again, created_again = repo.get_or_create_by_route_key(nanobar_type="api-response", route_key="GET /x")
        assert created_again is False
        assert again.nanobar_id == nanobar.nanobar_id


def test_get_or_create_by_route_key_skips_non_matching_candidates(tmp_path: Path) -> None:
    """Two existing nanobars share `nanobar_type` but only the second matches `route_key` --
    exercises `_find_by_route_key`'s loop actually continuing past a non-matching candidate,
    not just the zero- or one-candidate cases."""
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = NanobarRepository(session)
        repo.get_or_create_by_route_key(nanobar_type="api-response", route_key="GET /a")
        second, created = repo.get_or_create_by_route_key(nanobar_type="api-response", route_key="GET /b")
        assert created is True

        found, created_again = repo.get_or_create_by_route_key(nanobar_type="api-response", route_key="GET /b")
        assert created_again is False
        assert found.nanobar_id == second.nanobar_id


def test_bind_brick_and_bricks_for(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        nb_repo = NanobarRepository(session)
        rb_repo = RegressionBrickRepository(session)
        nanobar = nb_repo.create(_make_nanobar())
        brick = rb_repo.create(_make_brick())

        nb_repo.bind_brick(
            NanobarBrickBinding(
                nanobar_id=nanobar.nanobar_id,
                regression_brick_id=brick.regression_brick_id,
                match_method="exact",
                matcher_version="v1",
                matched_by="test",
            )
        )

        assert [b.regression_brick_id for b in nb_repo.bricks_for(nanobar.nanobar_id)] == [brick.regression_brick_id]
