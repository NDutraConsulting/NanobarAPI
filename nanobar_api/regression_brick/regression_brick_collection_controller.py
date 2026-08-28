"""Controllers for `RegressionBrick`'s collection routes -- see
`regression_brick_collection_service.py`'s module docstring for the collection-vs-analysis split.
Each builds its service fresh, from ambient app state, inside `load_required_services()`, matching
`app/controllers/blog_controller.py`'s shape exactly. `load_fallback_services()` is a no-op in all
four: a missing `services["..."]` key here means an app-wiring bug, not a soft runtime condition
worth degrading gracefully for.
"""

from __future__ import annotations

from typing import Any

from nanobar_api.framework.nanobar_api_controller import NanobarAPIController, NanobarAPIError
from nanobar_api.regression_brick.regression_brick_collection_service import (
    AddBrickTagService,
    RemoveBrickTagService,
    SetBrickScenarioService,
    SetReviewStatusService,
)
from nanobar_api.regression_brick.repository import RegressionBrickRepository


class RegressionBrickNotFoundError(NanobarAPIError):
    """Raised by a collection controller's `build_response()` when its service returns
    `status != "success"` -- every collection service's one real failure case is "no such brick."
    Caught directly by `NanobarAPIValidatorGate.__call__` and turned into a real 404 `Response`
    (see `NanobarAPIError`'s own docstring) -- no per-app `exception_handlers` registration
    needed for this anymore, same pattern as blog's `NotFoundError`.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=404)


class SetReviewStatusController(NanobarAPIController):
    def load_required_services(self) -> None:
        session = self.request.app.state.bricks_session_factory()
        self.services["session"] = session
        self.services["service"] = SetReviewStatusService(
            self.request.app.state.telemetry, RegressionBrickRepository(session)
        )

    def load_fallback_services(self) -> None:
        pass

    def run_etl_workflow(self, validated: Any) -> Any:
        result = self.services["service"](validated, route_key=self.request_type)
        self.services["session"].close()
        return result

    def build_response(self, result: Any) -> dict[str, Any]:
        if result.status != "success":
            raise RegressionBrickNotFoundError(result.result.msg_summary)
        return result.result.data  # type: ignore[no-any-return]


class SetBrickScenarioController(NanobarAPIController):
    def load_required_services(self) -> None:
        session = self.request.app.state.bricks_session_factory()
        self.services["session"] = session
        self.services["service"] = SetBrickScenarioService(
            self.request.app.state.telemetry, RegressionBrickRepository(session)
        )

    def load_fallback_services(self) -> None:
        pass

    def run_etl_workflow(self, validated: Any) -> Any:
        result = self.services["service"](validated, route_key=self.request_type)
        self.services["session"].close()
        return result

    def build_response(self, result: Any) -> dict[str, Any]:
        if result.status != "success":
            raise RegressionBrickNotFoundError(result.result.msg_summary)
        return result.result.data  # type: ignore[no-any-return]


class AddBrickTagController(NanobarAPIController):
    def load_required_services(self) -> None:
        session = self.request.app.state.bricks_session_factory()
        self.services["session"] = session
        self.services["service"] = AddBrickTagService(
            self.request.app.state.telemetry, RegressionBrickRepository(session)
        )

    def load_fallback_services(self) -> None:
        pass

    def run_etl_workflow(self, validated: Any) -> Any:
        result = self.services["service"](validated, route_key=self.request_type)
        self.services["session"].close()
        return result

    def build_response(self, result: Any) -> list[str]:
        if result.status != "success":
            raise RegressionBrickNotFoundError(result.result.msg_summary)
        return result.result.data  # type: ignore[no-any-return]


class RemoveBrickTagController(NanobarAPIController):
    def load_required_services(self) -> None:
        session = self.request.app.state.bricks_session_factory()
        self.services["session"] = session
        self.services["service"] = RemoveBrickTagService(
            self.request.app.state.telemetry, RegressionBrickRepository(session)
        )

    def load_fallback_services(self) -> None:
        pass

    def run_etl_workflow(self, validated: Any) -> Any:
        result = self.services["service"](validated, route_key=self.request_type)
        self.services["session"].close()
        return result

    def build_response(self, result: Any) -> list[str]:
        if result.status != "success":
            raise RegressionBrickNotFoundError(result.result.msg_summary)
        return result.result.data  # type: ignore[no-any-return]
