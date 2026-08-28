from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from starlette.applications import Starlette

from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.nanobar.controller import NanobarController, NanobarNotFoundError
from nanobar_api.nanobar.model import Nanobar
from nanobar_api.nanobar.repository import NanobarRepository
from nanobar_api.nanobar.service import UpdateNanobarRequest
from nanobar_api.persistence import build_session_factory
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
    app.state.taxonomy = {}
    app.state.nanobar_type_system_db_path = str(tmp_path / "nanobar_type_system.db")
    return _FakeRequest(_FakeApp(app.state))


def _broken_request() -> _FakeRequest:
    """No `bricks_session_factory` on `app.state` -- `load_required_services()` raises
    `AttributeError`, exercising `load_fallback_services()`'s no-op."""
    app = Starlette(routes=[])
    app.state.telemetry = NanobarTelemetry(_repository(), channel="trace")
    return _FakeRequest(_FakeApp(app.state))


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


def _seed_nanobar(tmp_path: Path) -> str:
    session = build_session_factory(str(tmp_path / "bricks.db"), repository=_repository())()
    nanobar = NanobarRepository(session).create(_make_nanobar())
    session.close()
    return nanobar.nanobar_id


def test_update_nanobar_controller_success(tmp_path: Path) -> None:
    nanobar_id = _seed_nanobar(tmp_path)
    controller = NanobarController(_working_request(tmp_path), "test")  # type: ignore[arg-type]

    result = asyncio.run(
        controller.handle(
            UpdateNanobarRequest(
                nanobar_id=nanobar_id,
                label="new label",
                scenario_description=None,
                component_source_description=None,
                domain=None,
                app_box=None,
                criticality=None,
            )
        )
    )

    assert result["label"] == "new label"


def test_update_nanobar_controller_not_found_raises(tmp_path: Path) -> None:
    controller = NanobarController(_working_request(tmp_path), "test")  # type: ignore[arg-type]

    with pytest.raises(NanobarNotFoundError):
        asyncio.run(
            controller.handle(
                UpdateNanobarRequest(
                    nanobar_id="does-not-exist",
                    label=None,
                    scenario_description=None,
                    component_source_description=None,
                    domain=None,
                    app_box=None,
                    criticality=None,
                )
            )
        )


def test_update_nanobar_controller_falls_back_when_wiring_is_broken() -> None:
    controller = NanobarController(_broken_request(), "test")  # type: ignore[arg-type]

    assert controller.services == {}
