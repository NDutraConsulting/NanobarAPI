from __future__ import annotations

from pathlib import Path

from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.nanobar.model import MonitorTargetRef, Nanobar, NanobarBrickBinding
from nanobar_api.persistence import build_session_factory
from nanobar_api.regression_brick.model import RegressionBrick


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


def test_monitor_target_refs_round_trips_through_the_json_column(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())

    with Session() as session:
        nb = _make_nanobar()
        nb.monitor_target_refs = [MonitorTargetRef(target_type="route", stable_name="GET /x")]
        session.add(nb)
        session.commit()
        nanobar_id = nb.nanobar_id

    with Session() as session:
        reloaded = session.get(Nanobar, nanobar_id)
        assert reloaded is not None
        assert reloaded.monitor_target_refs == [MonitorTargetRef(target_type="route", stable_name="GET /x")]


def test_monitor_target_refs_defaults_to_empty_list(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())

    with Session() as session:
        nb = _make_nanobar()
        session.add(nb)
        session.commit()

        assert nb.monitor_target_refs == []


def test_endpoint_scenario_frequency_property_round_trips(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())

    with Session() as session:
        nb = _make_nanobar()
        session.add(nb)
        session.commit()

        assert nb.endpoint_scenario_frequency == {"state": "unmeasured"}
        nb.endpoint_scenario_frequency = {"state": "measured", "value": 0.1}
        session.commit()
        assert nb.endpoint_scenario_frequency_json == {"state": "measured", "value": 0.1}


def test_source_info_property_round_trips_including_none(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())

    with Session() as session:
        nb = _make_nanobar()
        session.add(nb)
        session.commit()

        assert nb.source_info is None
        nb.source_info = {"code.function.name": "x"}
        session.commit()
        assert nb.source_info_json == {"code.function.name": "x"}


def test_nanobar_soft_delete_sets_deleted_at_without_removing_the_row(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())

    with Session() as session:
        nb = _make_nanobar()
        session.add(nb)
        session.commit()
        assert nb.deleted_at is None

        nb.deleted_at = "2026-08-27T00:00:00+00:00"
        session.commit()

    with Session() as session:
        assert session.query(Nanobar).count() == 1
        reloaded = session.query(Nanobar).one()
        assert reloaded.deleted_at == "2026-08-27T00:00:00+00:00"


def test_nanobar_brick_binding_survives_a_soft_deleted_nanobar(tmp_path: Path) -> None:
    """No FK cascade fires on a soft delete (a plain column UPDATE) -- the binding stays exactly
    as it was, unlike the old `ON DELETE CASCADE` behavior a hard delete would have triggered."""
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())

    with Session() as session:
        nb = _make_nanobar()
        brick = RegressionBrick(
            schema_version="1.0", brick_version=1, source={}, request={}, response={}, content_hash="h", created_by="t"
        )
        session.add_all([nb, brick])
        session.commit()
        session.add(
            NanobarBrickBinding(
                nanobar_id=nb.nanobar_id,
                regression_brick_id=brick.regression_brick_id,
                match_method="exact",
                matcher_version="v1",
                matched_by="test",
            )
        )
        nb.deleted_at = "2026-08-27T00:00:00+00:00"
        session.commit()

        assert session.query(NanobarBrickBinding).count() == 1
