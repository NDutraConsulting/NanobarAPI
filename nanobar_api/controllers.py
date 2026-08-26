"""`NanobarController` — replaces the empty `class Controller: pass` stub in place, no
back-compat shim (nothing in the codebase constructed the old stub — confirmed via grep).

Per `.focusari/nanobar_APIDomain_abstract_class_buildplan-with-tasks.md` §2.3: the third of the
three layers a nanobar route call passes through (`nanobar_routes` -> `nanobar_validator_gate` ->
`nanobar_controller`), producing the `controller-request-response` brick.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from nanobar_api.capture.layer_capture import capture_layer, to_payload_dict
from nanobar_api.middleware.trace import current_route_key
from nanobar_api.telemetry import NanobarProps

if TYPE_CHECKING:
    from starlette.requests import Request


class NanobarController(ABC):
    def __init__(self, request: Request, request_type: str) -> None:
        self.request = request
        self.request_type = request_type
        self.services: dict[str, Any] = {}
        try:
            self.load_required_services()
        except Exception:
            self.load_fallback_services()

    @abstractmethod
    def load_required_services(self) -> None: ...

    @abstractmethod
    def load_fallback_services(self) -> None: ...

    @abstractmethod
    def run_etl_workflow(self, validated: Any) -> Any: ...

    @abstractmethod
    def build_response(self, result: Any) -> Any: ...

    def get_response(self) -> Any:
        return self.build_response(self._etl_result)

    async def handle(self, validated: Any) -> Any:
        """Concrete, non-overridable lifecycle: runs `run_etl_workflow()`/`get_response()`
        inside a `telemetry.span(...)` (a child span — nested under the validator's, not a new
        root, since this always runs behind `EventBusTraceMiddleware`) and captures a
        `controller-request-response` brick via `capture_layer()`.

        Controller-level failures (an exception from `run_etl_workflow()`/`build_response()`)
        are deliberately not caught here — the source spec doesn't design a controller-layer
        failure-capture path the way `NanobarValidatorGate` explicitly does for
        `ValidationError`, so this doesn't invent one; the exception propagates uncaught,
        producing a real 500 with no controller-layer brick for that call. Flagged, not solved.

        Also sets `current_route_key` (`nanobar_api.middleware.trace`) for the duration of the
        call, reset in a `finally` — `nanobar_api.orm.NanobarORMWrapper`'s SQLAlchemy listeners
        read it so a DB query issued from inside `run_etl_workflow()` can stamp the same
        `route_key` its `orm-request-response` capture needs for composite-nanobar binding.
        """
        telemetry = self.request.app.state.telemetry
        route_key_token = current_route_key.set(self.request_type)
        try:
            with telemetry.span(
                f"controller.{self.request_type}", nanobar=NanobarProps(type="controller-request-response")
            ):
                self._etl_result = self.run_etl_workflow(validated)
                response = self.get_response()
                capture_layer(
                    telemetry.repository,
                    "controller",
                    to_payload_dict(validated),
                    to_payload_dict(response),
                    nanobar_type="controller-request-response",
                    route_key=self.request_type,
                )
        finally:
            current_route_key.reset(route_key_token)
        return response
