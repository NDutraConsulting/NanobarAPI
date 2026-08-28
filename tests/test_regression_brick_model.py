from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.persistence import build_session_factory
from nanobar_api.regression_brick.model import (
    REVIEW_STATUSES,
    BrickLog,
    BrickReviewStatus,
    BrickScenario,
    BrickState,
    BrickTag,
    RegressionBrick,
    RegressionBrickStateFields,
)
from nanobar_api.state_machine import InvalidTransition


def _repository() -> EventQueueRepository:
    return EventQueueRepository([ChannelConfig(name="trace"), ChannelConfig(name="snapshot")])


def _make_brick(**overrides: object) -> RegressionBrick:
    defaults: dict[str, object] = {
        "schema_version": "1.0",
        "brick_version": 1,
        "source": {"a": 1},
        "request": {"b": 2},
        "response": {"c": 3},
        "content_hash": "sha256:abc",
        "created_by": "test",
    }
    defaults.update(overrides)
    return RegressionBrick(**defaults)


def test_json_columns_round_trip_through_the_dict_properties(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())

    with Session() as session:
        brick = _make_brick()
        session.add(brick)
        session.commit()
        brick_id = brick.regression_brick_id

    with Session() as session:
        reloaded = session.get(RegressionBrick, brick_id)
        assert reloaded is not None
        assert reloaded.source == {"a": 1}
        assert reloaded.request == {"b": 2}
        assert reloaded.response == {"c": 3}
        assert reloaded.trace_refs == []


def test_trace_refs_property_round_trips(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())

    with Session() as session:
        brick = _make_brick()
        brick.trace_refs = [{"trace_id": "tr-1", "span_ids": ["sp-1"]}]
        session.add(brick)
        session.commit()
        assert brick.trace_refs_json == [{"trace_id": "tr-1", "span_ids": ["sp-1"]}]


def test_self_contained_replay_fields_default_to_none(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())

    with Session() as session:
        brick = _make_brick()
        session.add(brick)
        session.commit()
        brick_id = brick.regression_brick_id

    with Session() as session:
        reloaded = session.get(RegressionBrick, brick_id)
        assert reloaded is not None
        assert reloaded.entry_point is None
        assert reloaded.app_box is None
        assert reloaded.nanobar_type is None
        assert reloaded.source_info is None
        assert reloaded.regression_scenario_description is None


def test_source_info_property_round_trips(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())

    with Session() as session:
        brick = _make_brick(
            entry_point="GET /items",
            app_box="api",
            nanobar_type="controller-request-response",
            regression_scenario_description="lists all items",
        )
        brick.source_info = {"trace_id": "tr-1", "span_id": "sp-1", "channel": "snapshot"}
        session.add(brick)
        session.commit()
        brick_id = brick.regression_brick_id

    with Session() as session:
        reloaded = session.get(RegressionBrick, brick_id)
        assert reloaded is not None
        assert reloaded.entry_point == "GET /items"
        assert reloaded.app_box == "api"
        assert reloaded.nanobar_type == "controller-request-response"
        assert reloaded.regression_scenario_description == "lists all items"
        assert reloaded.source_info == {"trace_id": "tr-1", "span_id": "sp-1", "channel": "snapshot"}
        assert reloaded.source_info_json == {"trace_id": "tr-1", "span_id": "sp-1", "channel": "snapshot"}


def test_content_hash_must_be_unique(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())

    with Session() as session:
        session.add(_make_brick(content_hash="dup"))
        session.commit()
        session.add(_make_brick(content_hash="dup"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_review_status_check_constraint_rejects_an_undeclared_status(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())

    with Session() as session:
        brick = _make_brick()
        session.add(brick)
        session.commit()
        session.add(
            BrickReviewStatus(regression_brick_id=brick.regression_brick_id, status="not-a-real-status", updated_by="t")
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_review_status_accepts_every_declared_status(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())

    with Session() as session:
        for status in REVIEW_STATUSES:
            brick = _make_brick(content_hash=f"h-{status}")
            session.add(brick)
            session.commit()
            session.add(BrickReviewStatus(regression_brick_id=brick.regression_brick_id, status=status, updated_by="t"))
            session.commit()


def test_scenario_and_tag_side_tables_round_trip(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())

    with Session() as session:
        brick = _make_brick()
        session.add(brick)
        session.commit()

        session.add(
            BrickScenario(
                regression_brick_id=brick.regression_brick_id,
                regression_scenario_label="timeout",
                description="third-party timeout",
                updated_by="t",
            )
        )
        session.add(BrickTag(regression_brick_id=brick.regression_brick_id, tag="flaky"))
        session.commit()

        assert session.query(BrickScenario).one().regression_scenario_label == "timeout"
        assert session.query(BrickTag).one().tag == "flaky"


def test_regression_brick_state_fields_declares_any_to_any_transitions() -> None:
    machine = RegressionBrickStateFields.state_machine_for("status", "new")

    assert machine.can_transition_to("reviewed") is True
    machine.transition_to("flagged")
    assert machine.state == "flagged"
    assert machine.can_transition_to("promoted") is True


def test_regression_brick_state_fields_disallows_undeclared_status() -> None:
    machine = RegressionBrickStateFields.state_machine_for("status", "new")

    with pytest.raises(InvalidTransition):
        machine.transition_to("not-a-real-status")


def test_regression_brick_state_fields_is_idempotent_on_regression_brick_id() -> None:
    previous = {"regression_brick_id": "rbrick-1", "status": "new"}
    candidate = {"regression_brick_id": "rbrick-1", "status": "reviewed"}

    assert RegressionBrickStateFields.is_idempotent_retry(previous, candidate) is True


def test_brick_state_side_table_records_soft_delete_without_touching_the_immutable_row(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())

    with Session() as session:
        brick = _make_brick()
        session.add(brick)
        session.commit()

        session.add(
            BrickState(
                regression_brick_id=brick.regression_brick_id,
                deleted_at="2026-08-27T00:00:00+00:00",
                deleted_by="admin",
                deletion_reason="test",
                updated_by="admin",
            )
        )
        session.commit()

        # The brick row itself is untouched -- still exists, still immutable.
        assert session.query(RegressionBrick).count() == 1
        brick.brick_version = 2
        with pytest.raises(IntegrityError):
            session.commit()


def test_brick_state_defaults_deletion_fields_to_none(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())

    with Session() as session:
        brick = _make_brick()
        session.add(brick)
        session.commit()

        state = BrickState(regression_brick_id=brick.regression_brick_id, updated_by="admin")
        session.add(state)
        session.commit()

        assert state.deleted_at is None
        assert state.deleted_by is None
        assert state.deletion_reason is None
        assert state.data == {}


def test_brick_state_data_json_round_trips_testing_flow_details(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())

    with Session() as session:
        brick = _make_brick()
        session.add(brick)
        session.commit()

        session.add(
            BrickState(
                regression_brick_id=brick.regression_brick_id,
                updated_by="admin",
                data={"included_in_test_flow": True, "last_replay_status": "passed"},
            )
        )
        session.commit()

        assert session.query(BrickState).one().data_json == {
            "included_in_test_flow": True,
            "last_replay_status": "passed",
        }


def test_brick_log_records_many_entries_per_brick(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())

    with Session() as session:
        brick = _make_brick()
        session.add(brick)
        session.commit()

        session.add(BrickLog(regression_brick_id=brick.regression_brick_id, data={"event": "replayed"}))
        session.add(BrickLog(regression_brick_id=brick.regression_brick_id, data={"event": "flagged"}))
        session.commit()

        entries = session.query(BrickLog).order_by(BrickLog.id).all()
        assert [e.data for e in entries] == [{"event": "replayed"}, {"event": "flagged"}]
        assert all(e.created_at is not None for e in entries)


def test_brick_log_is_deleted_when_its_brick_is_hard_deleted(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())

    with Session() as session:
        brick = _make_brick()
        session.add(brick)
        session.commit()
        session.add(BrickLog(regression_brick_id=brick.regression_brick_id, data={"event": "created"}))
        session.commit()

        session.delete(brick)
        session.commit()

        assert session.query(BrickLog).count() == 0
