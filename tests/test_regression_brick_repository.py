from __future__ import annotations

from pathlib import Path

import pytest

from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.nanobar.model import Nanobar, NanobarBrickBinding
from nanobar_api.nanobar.repository import NanobarRepository
from nanobar_api.persistence import build_session_factory
from nanobar_api.regression_brick.model import RegressionBrick
from nanobar_api.regression_brick.repository import RegressionBrickRepository


def _repository() -> EventQueueRepository:
    return EventQueueRepository([ChannelConfig(name="trace"), ChannelConfig(name="snapshot")])


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


def test_create_and_get_round_trip(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = RegressionBrickRepository(session)
        brick = repo.create(_make_brick())

        assert repo.get(brick.regression_brick_id) is brick


def test_get_returns_none_for_unknown_id(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = RegressionBrickRepository(session)
        assert repo.get("does-not-exist") is None


def test_get_uses_the_cache_on_second_call(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = RegressionBrickRepository(session)
        brick = repo.create(_make_brick())

        repo.get(brick.regression_brick_id)
        assert repo.get_cached(brick.regression_brick_id) is brick
        assert repo.get(brick.regression_brick_id) is brick


def test_get_by_content_hash(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = RegressionBrickRepository(session)
        brick = repo.create(_make_brick(content_hash="sha256:abc"))

        assert repo.get_by_content_hash("sha256:abc") is brick
        assert repo.get_by_content_hash("does-not-exist") is None


def test_get_many_returns_matching_bricks_ignoring_unknown_ids(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = RegressionBrickRepository(session)
        first = repo.create(_make_brick(content_hash="sha256:one"))
        second = repo.create(_make_brick(content_hash="sha256:two"))

        found = repo.get_many([first.regression_brick_id, second.regression_brick_id, "does-not-exist"])

        assert {b.regression_brick_id for b in found} == {first.regression_brick_id, second.regression_brick_id}


def test_get_many_with_no_ids_does_not_query(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = RegressionBrickRepository(session)

        assert repo.get_many([]) == []


def test_set_and_get_review_status_defaults_to_new(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = RegressionBrickRepository(session)
        brick = repo.create(_make_brick())

        assert repo.get_review_status(brick.regression_brick_id).status == "new"

        repo.set_review_status(brick.regression_brick_id, "reviewed", "admin")
        assert repo.get_review_status(brick.regression_brick_id).status == "reviewed"

        # Updating again exercises the update branch, not just insert.
        repo.set_review_status(brick.regression_brick_id, "flagged", "admin2")
        status = repo.get_review_status(brick.regression_brick_id)
        assert status.status == "flagged"
        assert status.updated_by == "admin2"


def test_set_review_status_rejects_an_undeclared_status(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = RegressionBrickRepository(session)
        brick = repo.create(_make_brick())

        with pytest.raises(ValueError, match="invalid review status"):
            repo.set_review_status(brick.regression_brick_id, "not-a-real-status", "admin")


def test_list_by_review_status_filters_and_defaults_unset_to_new(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = RegressionBrickRepository(session)
        new_brick = repo.create(_make_brick(content_hash="h-new"))
        reviewed_brick = repo.create(_make_brick(content_hash="h-reviewed"))
        repo.set_review_status(reviewed_brick.regression_brick_id, "reviewed", "admin")

        assert [b.regression_brick_id for b in repo.list_by_review_status("new")] == [new_brick.regression_brick_id]
        assert [b.regression_brick_id for b in repo.list_by_review_status("reviewed")] == [
            reviewed_brick.regression_brick_id
        ]
        assert len(repo.list_by_review_status()) == 2


def test_list_by_review_status_rejects_an_undeclared_status(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = RegressionBrickRepository(session)
        with pytest.raises(ValueError, match="invalid review status"):
            repo.list_by_review_status("not-a-real-status")


def test_nanobars_for_and_bricks_for_are_symmetric(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        rb_repo = RegressionBrickRepository(session)
        nb_repo = NanobarRepository(session)
        brick = rb_repo.create(_make_brick())
        nanobar = nb_repo.create(_make_nanobar())
        nb_repo.bind_brick(
            NanobarBrickBinding(
                nanobar_id=nanobar.nanobar_id,
                regression_brick_id=brick.regression_brick_id,
                match_method="exact",
                matcher_version="v1",
                matched_by="test",
            )
        )

        assert [n.nanobar_id for n in rb_repo.nanobars_for(brick.regression_brick_id)] == [nanobar.nanobar_id]


def test_set_and_get_scenario_defaults_to_none(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = RegressionBrickRepository(session)
        brick = repo.create(_make_brick())

        default = repo.get_scenario(brick.regression_brick_id)
        assert default.regression_scenario_label is None

        repo.set_scenario(brick.regression_brick_id, regression_scenario_label="timeout", updated_by="admin")
        assert repo.get_scenario(brick.regression_brick_id).regression_scenario_label == "timeout"

        # Updating again exercises the update branch, not just insert.
        repo.set_scenario(brick.regression_brick_id, description="third-party timeout", updated_by="admin2")
        scenario = repo.get_scenario(brick.regression_brick_id)
        assert scenario.regression_scenario_label is None
        assert scenario.description == "third-party timeout"


def test_add_tag_is_idempotent_and_remove_tag(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = RegressionBrickRepository(session)
        brick = repo.create(_make_brick())

        repo.add_tag(brick.regression_brick_id, "flaky")
        repo.add_tag(brick.regression_brick_id, "flaky")
        assert repo.tags_for(brick.regression_brick_id) == ["flaky"]

        repo.remove_tag(brick.regression_brick_id, "flaky")
        assert repo.tags_for(brick.regression_brick_id) == []


def test_list_by_tag(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = RegressionBrickRepository(session)
        tagged = repo.create(_make_brick(content_hash="h-tagged"))
        untagged = repo.create(_make_brick(content_hash="h-untagged"))
        repo.add_tag(tagged.regression_brick_id, "flaky")

        assert [b.regression_brick_id for b in repo.list_by_tag("flaky")] == [tagged.regression_brick_id]
        assert untagged.regression_brick_id not in [b.regression_brick_id for b in repo.list_by_tag("flaky")]


def test_get_state_is_none_until_soft_deleted(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = RegressionBrickRepository(session)
        brick = repo.create(_make_brick())

        assert repo.get_state(brick.regression_brick_id) is None

        repo.soft_delete(brick.regression_brick_id, deleted_by="admin", reason="dup")
        state = repo.get_state(brick.regression_brick_id)
        assert state is not None
        assert state.deleted_at is not None
        assert state.deleted_by == "admin"
        assert state.deletion_reason == "dup"


def test_soft_delete_twice_updates_the_existing_row(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = RegressionBrickRepository(session)
        brick = repo.create(_make_brick())

        repo.soft_delete(brick.regression_brick_id, deleted_by="admin", reason="dup")
        repo.soft_delete(brick.regression_brick_id, deleted_by="admin2", reason="reclassified")

        state = repo.get_state(brick.regression_brick_id)
        assert state is not None
        assert state.deleted_by == "admin2"
        assert state.deletion_reason == "reclassified"


def test_restore_clears_deletion_fields(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = RegressionBrickRepository(session)
        brick = repo.create(_make_brick())
        repo.soft_delete(brick.regression_brick_id, deleted_by="admin", reason="dup")

        restored = repo.restore(brick.regression_brick_id, updated_by="admin2")
        assert restored is not None
        assert restored.deleted_at is None
        assert restored.deleted_by is None
        assert restored.deletion_reason is None


def test_restore_returns_none_when_no_state_row_exists(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = RegressionBrickRepository(session)
        brick = repo.create(_make_brick())

        assert repo.restore(brick.regression_brick_id, updated_by="admin") is None


def test_set_state_data_creates_and_then_updates_the_row(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = RegressionBrickRepository(session)
        brick = repo.create(_make_brick())

        repo.set_state_data(brick.regression_brick_id, updated_by="admin", data={"included_in_test_flow": True})
        state = repo.get_state(brick.regression_brick_id)
        assert state is not None
        assert state.data == {"included_in_test_flow": True}

        repo.set_state_data(brick.regression_brick_id, updated_by="admin2", data={"last_replay_status": "passed"})
        state = repo.get_state(brick.regression_brick_id)
        assert state is not None
        assert state.data == {"last_replay_status": "passed"}
        assert state.updated_by == "admin2"


def test_add_log_and_list_logs_ordered_by_insertion(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())
    with Session() as session:
        repo = RegressionBrickRepository(session)
        brick = repo.create(_make_brick())

        repo.add_log(brick.regression_brick_id, {"event": "replayed"})
        repo.add_log(brick.regression_brick_id, {"event": "flagged"})

        entries = repo.list_logs(brick.regression_brick_id)
        assert [e.data for e in entries] == [{"event": "replayed"}, {"event": "flagged"}]
