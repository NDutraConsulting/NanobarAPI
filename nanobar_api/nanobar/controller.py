"""Controller for `Nanobar`'s `update_nanobar` route -- matches
`app/controllers/blog_controller.py`'s shape.
"""

from __future__ import annotations

from typing import Any

from nanobar_api.framework.nanobar_api_controller import NanobarAPIController, NanobarAPIError
from nanobar_api.nanobar.repository import NanobarRepository
from nanobar_api.nanobar.service import NanobarService


class NanobarNotFoundError(NanobarAPIError):
    """Raised by `build_response()` when the service returns `status != "success"` -- its one
    real failure case is "no such nanobar." Caught directly by `NanobarAPIValidatorGate.__call__`
    and turned into a real 404 `Response` (see `NanobarAPIError`'s own docstring) -- no per-app
    `exception_handlers` registration needed for this anymore, same pattern as blog's
    `NotFoundError` and `regression_brick_collection_controller.py`'s `RegressionBrickNotFoundError`.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=404)


class NanobarController(NanobarAPIController):
    def load_required_services(self) -> None:
        session = self.request.app.state.bricks_session_factory()
        self.services["session"] = session
        self.services["service"] = NanobarService(
            self.request.app.state.telemetry,
            NanobarRepository(session),
            static_taxonomy=self.request.app.state.taxonomy,
            dynamic_taxonomy_db_path=self.request.app.state.nanobar_type_system_db_path,
        )

    def load_fallback_services(self) -> None:
        pass

    def run_etl_workflow(self, validated: Any) -> Any:
        result = self.services["service"](validated, route_key=self.request_type)
        self.services["session"].close()
        return result

    def build_response(self, result: Any) -> dict[str, Any]:
        if result.status != "success":
            raise NanobarNotFoundError(result.result.msg_summary)
        return result.result.data  # type: ignore[no-any-return]
