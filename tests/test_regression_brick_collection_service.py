from __future__ import annotations

from pathlib import Path

from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.persistence import build_session_factory
from nanobar_api.regression_brick.model import RegressionBrick
from nanobar_api.regression_brick.regression_brick_collection_service import (
    AddBrickTagRequest,
    AddBrickTagService,
    RemoveBrickTagRequest,
    RemoveBrickTagService,
    SetBrickScenarioRequest,
    SetBrickScenarioService,
    SetReviewStatusRequest,
    SetReviewStatusService,
)
from nanobar_api.regression_brick.repository import RegressionBrickRepository
from nanobar_api.telemetry import NanobarTelemetry


def _repository() -> EventQueueRepository:
    return EventQueueRepository([ChannelConfig(name="trace"), ChannelConfig(name="snapshot")])


def _brick_repository(tmp_path: Path) -> RegressionBrickRepository:
    session = build_session_factory(str(tmp_path / "bricks.db"), repository=_repository())()
    return RegressionBrickRepository(session)


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


def test_set_review_status_updates_and_returns_it(tmp_path: Path) -> None:
    brick_repository = _brick_repository(tmp_path)
    brick = brick_repository.create(_make_brick())
    service = SetReviewStatusService(NanobarTelemetry(_repository(), channel="trace"), brick_repository)

    result = service(
        SetReviewStatusRequest(regression_brick_id=brick.regression_brick_id, status="flagged", updated_by="dashboard")
    )

    assert result.status == "success"
    assert result.result.data["status"] == "flagged"
    assert result.result.data["updated_by"] == "dashboard"


def test_set_review_status_brick_not_found(tmp_path: Path) -> None:
    brick_repository = _brick_repository(tmp_path)
    service = SetReviewStatusService(NanobarTelemetry(_repository(), channel="trace"), brick_repository)

    result = service(
        SetReviewStatusRequest(regression_brick_id="does-not-exist", status="flagged", updated_by="dashboard")
    )

    assert result.status == "error"
    assert "does-not-exist" in result.result.msg_summary


def test_set_brick_scenario_sets_both_fields(tmp_path: Path) -> None:
    brick_repository = _brick_repository(tmp_path)
    brick = brick_repository.create(_make_brick())
    service = SetBrickScenarioService(NanobarTelemetry(_repository(), channel="trace"), brick_repository)

    result = service(
        SetBrickScenarioRequest(
            regression_brick_id=brick.regression_brick_id,
            regression_scenario_label="timeout",
            description="third-party timeout",
            updated_by="dashboard",
        )
    )

    assert result.status == "success"
    assert result.result.data["regression_scenario_label"] == "timeout"
    assert result.result.data["description"] == "third-party timeout"


def test_set_brick_scenario_omitted_fields_keep_current_value(tmp_path: Path) -> None:
    brick_repository = _brick_repository(tmp_path)
    brick = brick_repository.create(_make_brick())
    service = SetBrickScenarioService(NanobarTelemetry(_repository(), channel="trace"), brick_repository)
    service(
        SetBrickScenarioRequest(
            regression_brick_id=brick.regression_brick_id,
            regression_scenario_label="timeout",
            description=None,
            updated_by="dashboard",
        )
    )

    result = service(
        SetBrickScenarioRequest(
            regression_brick_id=brick.regression_brick_id,
            regression_scenario_label=None,
            description="third-party timeout",
            updated_by="dashboard",
        )
    )

    assert result.result.data["regression_scenario_label"] == "timeout"
    assert result.result.data["description"] == "third-party timeout"


def test_set_brick_scenario_brick_not_found(tmp_path: Path) -> None:
    brick_repository = _brick_repository(tmp_path)
    service = SetBrickScenarioService(NanobarTelemetry(_repository(), channel="trace"), brick_repository)

    result = service(
        SetBrickScenarioRequest(
            regression_brick_id="does-not-exist",
            regression_scenario_label=None,
            description=None,
            updated_by="dashboard",
        )
    )

    assert result.status == "error"


def test_add_brick_tag_adds_and_returns_full_tag_list(tmp_path: Path) -> None:
    brick_repository = _brick_repository(tmp_path)
    brick = brick_repository.create(_make_brick())
    service = AddBrickTagService(NanobarTelemetry(_repository(), channel="trace"), brick_repository)

    result = service(AddBrickTagRequest(regression_brick_id=brick.regression_brick_id, tag="flaky"))

    assert result.status == "success"
    assert result.result.data == ["flaky"]


def test_add_brick_tag_brick_not_found(tmp_path: Path) -> None:
    brick_repository = _brick_repository(tmp_path)
    service = AddBrickTagService(NanobarTelemetry(_repository(), channel="trace"), brick_repository)

    result = service(AddBrickTagRequest(regression_brick_id="does-not-exist", tag="flaky"))

    assert result.status == "error"


def test_remove_brick_tag_removes_and_returns_remaining_tags(tmp_path: Path) -> None:
    brick_repository = _brick_repository(tmp_path)
    brick = brick_repository.create(_make_brick())
    brick_repository.add_tag(brick.regression_brick_id, "flaky")
    brick_repository.add_tag(brick.regression_brick_id, "checkout")
    service = RemoveBrickTagService(NanobarTelemetry(_repository(), channel="trace"), brick_repository)

    result = service(RemoveBrickTagRequest(regression_brick_id=brick.regression_brick_id, tag="flaky"))

    assert result.status == "success"
    assert result.result.data == ["checkout"]


def test_remove_brick_tag_not_present_is_a_no_op(tmp_path: Path) -> None:
    brick_repository = _brick_repository(tmp_path)
    brick = brick_repository.create(_make_brick())
    service = RemoveBrickTagService(NanobarTelemetry(_repository(), channel="trace"), brick_repository)

    result = service(RemoveBrickTagRequest(regression_brick_id=brick.regression_brick_id, tag="never-added"))

    assert result.status == "success"
    assert result.result.data == []


def test_remove_brick_tag_brick_not_found(tmp_path: Path) -> None:
    brick_repository = _brick_repository(tmp_path)
    service = RemoveBrickTagService(NanobarTelemetry(_repository(), channel="trace"), brick_repository)

    result = service(RemoveBrickTagRequest(regression_brick_id="does-not-exist", tag="flaky"))

    assert result.status == "error"
