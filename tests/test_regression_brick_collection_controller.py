from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from starlette.applications import Starlette

from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.persistence import build_session_factory
from nanobar_api.regression_brick.model import RegressionBrick
from nanobar_api.regression_brick.regression_brick_collection_controller import (
    AddBrickTagController,
    RegressionBrickNotFoundError,
    RemoveBrickTagController,
    SetBrickScenarioController,
    SetReviewStatusController,
)
from nanobar_api.regression_brick.regression_brick_collection_service import (
    AddBrickTagRequest,
    RemoveBrickTagRequest,
    SetBrickScenarioRequest,
    SetReviewStatusRequest,
)
from nanobar_api.regression_brick.repository import RegressionBrickRepository
from nanobar_api.telemetry import NanobarTelemetry


def _repository() -> EventQueueRepository:
    return EventQueueRepository([ChannelConfig(name="trace"), ChannelConfig(name="snapshot")])


class _FakeApp:
    def __init__(self, state: object) -> None:
        self.state = state


class _FakeRequest:
    def __init__(self, app: object) -> None:
        self.app = app


def _working_request(tmp_path: Path) -> _FakeRequest:
    app = Starlette(routes=[])
    app.state.telemetry = NanobarTelemetry(_repository(), channel="trace")
    app.state.bricks_session_factory = build_session_factory(str(tmp_path / "bricks.db"), repository=_repository())
    return _FakeRequest(_FakeApp(app.state))


def _broken_request() -> _FakeRequest:
    """No `bricks_session_factory` on `app.state` -- `load_required_services()` raises
    `AttributeError`, exercising each controller's `load_fallback_services()` no-op."""
    app = Starlette(routes=[])
    app.state.telemetry = NanobarTelemetry(_repository(), channel="trace")
    return _FakeRequest(_FakeApp(app.state))


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


def _seed_brick(tmp_path: Path) -> str:
    session = build_session_factory(str(tmp_path / "bricks.db"), repository=_repository())()
    brick = RegressionBrickRepository(session).create(_make_brick())
    session.close()
    return brick.regression_brick_id


def test_set_review_status_controller_success(tmp_path: Path) -> None:
    brick_id = _seed_brick(tmp_path)
    controller = SetReviewStatusController(_working_request(tmp_path), "test")  # type: ignore[arg-type]

    result = asyncio.run(
        controller.handle(
            SetReviewStatusRequest(regression_brick_id=brick_id, status="flagged", updated_by="dashboard")
        )
    )

    assert result["status"] == "flagged"


def test_set_review_status_controller_not_found_raises(tmp_path: Path) -> None:
    controller = SetReviewStatusController(_working_request(tmp_path), "test")  # type: ignore[arg-type]

    with pytest.raises(RegressionBrickNotFoundError):
        asyncio.run(
            controller.handle(
                SetReviewStatusRequest(regression_brick_id="does-not-exist", status="flagged", updated_by="dashboard")
            )
        )


def test_set_review_status_controller_falls_back_when_wiring_is_broken() -> None:
    controller = SetReviewStatusController(_broken_request(), "test")  # type: ignore[arg-type]

    assert controller.services == {}


def test_set_brick_scenario_controller_success(tmp_path: Path) -> None:
    brick_id = _seed_brick(tmp_path)
    controller = SetBrickScenarioController(_working_request(tmp_path), "test")  # type: ignore[arg-type]

    result = asyncio.run(
        controller.handle(
            SetBrickScenarioRequest(
                regression_brick_id=brick_id,
                regression_scenario_label="timeout",
                description=None,
                updated_by="dashboard",
            )
        )
    )

    assert result["regression_scenario_label"] == "timeout"


def test_set_brick_scenario_controller_not_found_raises(tmp_path: Path) -> None:
    controller = SetBrickScenarioController(_working_request(tmp_path), "test")  # type: ignore[arg-type]

    with pytest.raises(RegressionBrickNotFoundError):
        asyncio.run(
            controller.handle(
                SetBrickScenarioRequest(
                    regression_brick_id="does-not-exist",
                    regression_scenario_label=None,
                    description=None,
                    updated_by="dashboard",
                )
            )
        )


def test_set_brick_scenario_controller_falls_back_when_wiring_is_broken() -> None:
    controller = SetBrickScenarioController(_broken_request(), "test")  # type: ignore[arg-type]

    assert controller.services == {}


def test_add_brick_tag_controller_success(tmp_path: Path) -> None:
    brick_id = _seed_brick(tmp_path)
    controller = AddBrickTagController(_working_request(tmp_path), "test")  # type: ignore[arg-type]

    result = asyncio.run(controller.handle(AddBrickTagRequest(regression_brick_id=brick_id, tag="flaky")))

    assert result == ["flaky"]


def test_add_brick_tag_controller_not_found_raises(tmp_path: Path) -> None:
    controller = AddBrickTagController(_working_request(tmp_path), "test")  # type: ignore[arg-type]

    with pytest.raises(RegressionBrickNotFoundError):
        asyncio.run(controller.handle(AddBrickTagRequest(regression_brick_id="does-not-exist", tag="flaky")))


def test_add_brick_tag_controller_falls_back_when_wiring_is_broken() -> None:
    controller = AddBrickTagController(_broken_request(), "test")  # type: ignore[arg-type]

    assert controller.services == {}


def test_remove_brick_tag_controller_success(tmp_path: Path) -> None:
    brick_id = _seed_brick(tmp_path)
    controller = RemoveBrickTagController(_working_request(tmp_path), "test")  # type: ignore[arg-type]

    result = asyncio.run(controller.handle(RemoveBrickTagRequest(regression_brick_id=brick_id, tag="never-added")))

    assert result == []


def test_remove_brick_tag_controller_not_found_raises(tmp_path: Path) -> None:
    controller = RemoveBrickTagController(_working_request(tmp_path), "test")  # type: ignore[arg-type]

    with pytest.raises(RegressionBrickNotFoundError):
        asyncio.run(controller.handle(RemoveBrickTagRequest(regression_brick_id="does-not-exist", tag="flaky")))


def test_remove_brick_tag_controller_falls_back_when_wiring_is_broken() -> None:
    controller = RemoveBrickTagController(_broken_request(), "test")  # type: ignore[arg-type]

    assert controller.services == {}
