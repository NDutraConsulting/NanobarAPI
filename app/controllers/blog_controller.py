"""Controllers for the blog domain's validated/mutating routes -- each builds its service fresh,
from ambient app state, inside `load_required_services()`, matching
`tests/test_validator_gate.py`'s `GreetController` worked example (`self.services["greeter"] =
lambda name: ...`) rather than taking dependencies through `__init__` (which
`NanobarAPIValidatorGate.__call__` doesn't support -- it always constructs
`self.controller_cls(request, request_type)` with no extra arguments).

`load_fallback_services()` is a no-op in all three: unlike `GreetController`'s "hello, stranger"
degraded-but-real response, there's no meaningful degraded mode for "the blog database is
unreachable" -- `load_required_services()` failing here means an app-wiring bug, not a soft
runtime condition, so a missing `services["..."]` key surfaces as a real 500 from
`run_etl_workflow()` rather than being silently papered over.
"""

from __future__ import annotations

from typing import Any

from app.crud.blog_crud import AppointmentRepository, NotificationRepository, PostRepository
from app.db.blog_session import resolve_session_factory as resolve_blog_session_factory
from app.services.blog_service import (
    BookAppointmentService,
    CreatePostService,
    MarkNotificationReadService,
    UpdatePostService,
)
from nanobar_api.framework.nanobar_api_controller import NanobarAPIController, NanobarAPIError


class NotFoundError(NanobarAPIError):
    """Raised by `MarkNotificationReadController.build_response()`/`UpdatePostController.
    build_response()` for their one real business-outcome failure case. **Never returned as a raw
    `Response`** -- found via live verification: `NanobarAPIController.handle()` runs whatever
    `build_response()` returns through `capture_layer()` *before* `adapt_handler`'s "a real
    `Response` passes through unchanged" escape hatch ever gets checked, and `capture_layer()`'s
    `to_payload_dict()` has no special case for a `Response` object -- it silently wraps it as
    `{"value": <Response object>}`, which then fails `json.dumps()` inside `capture_layer()`
    itself. Raising a `NanobarAPIError` subclass instead is the correct escape hatch:
    `NanobarAPIValidatorGate.__call__` catches it and turns it into a real 404 `Response` itself
    (see `NanobarAPIError`'s own docstring) -- no per-app `exception_handlers` registration
    needed for this anymore.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=404)


class CreatePostController(NanobarAPIController):
    """`load_fallback_services()` is a no-op in all three controllers below: unlike
    `GreetController`'s "hello, stranger" degraded-but-real response, there's no meaningful
    degraded mode for "the blog database is unreachable" -- `load_required_services()` failing
    here means an app-wiring bug, not a soft runtime condition, so a missing `services["..."]`
    key surfaces as a real 500 from `run_etl_workflow()` rather than being silently papered over.
    """

    def load_required_services(self) -> None:
        session = resolve_blog_session_factory(self.request)()
        self.services["session"] = session
        self.services["post_service"] = CreatePostService(self.request.app.state.telemetry, PostRepository(session))

    def load_fallback_services(self) -> None:
        pass

    def run_etl_workflow(self, validated: Any) -> Any:
        result = self.services["post_service"](validated, route_key=self.request_type)
        self.services["session"].close()
        return result

    def build_response(self, result: Any) -> dict[str, Any]:
        # CreatePostService.handle() always returns status="success" in this design -- no error
        # branch to handle here (see NotFoundError's docstring for why one wouldn't return a
        # Response even if there were).
        return result.result.data  # type: ignore[no-any-return]


class UpdatePostController(NanobarAPIController):
    def load_required_services(self) -> None:
        session = resolve_blog_session_factory(self.request)()
        self.services["session"] = session
        self.services["post_service"] = UpdatePostService(self.request.app.state.telemetry, PostRepository(session))

    def load_fallback_services(self) -> None:
        pass

    def run_etl_workflow(self, validated: Any) -> Any:
        result = self.services["post_service"](validated, route_key=self.request_type)
        self.services["session"].close()
        return result

    def build_response(self, result: Any) -> dict[str, Any]:
        if result.status != "success":
            raise NotFoundError(result.result.msg_summary)
        return result.result.data  # type: ignore[no-any-return]


class BookAppointmentController(NanobarAPIController):
    def load_required_services(self) -> None:
        session = resolve_blog_session_factory(self.request)()
        self.services["session"] = session
        self.services["appointment_service"] = BookAppointmentService(
            self.request.app.state.telemetry, AppointmentRepository(session), self.request.app.state.event_bus
        )

    def load_fallback_services(self) -> None:
        pass

    def run_etl_workflow(self, validated: Any) -> Any:
        result = self.services["appointment_service"](validated, route_key=self.request_type)
        self.services["session"].close()
        return result

    def build_response(self, result: Any) -> dict[str, Any]:
        return result.result.data  # type: ignore[no-any-return]


class MarkNotificationReadController(NanobarAPIController):
    def load_required_services(self) -> None:
        session = resolve_blog_session_factory(self.request)()
        self.services["session"] = session
        self.services["notification_service"] = MarkNotificationReadService(
            self.request.app.state.telemetry, NotificationRepository(session)
        )

    def load_fallback_services(self) -> None:
        pass

    def run_etl_workflow(self, validated: Any) -> Any:
        result = self.services["notification_service"](validated, route_key=self.request_type)
        self.services["session"].close()
        return result

    def build_response(self, result: Any) -> dict[str, Any]:
        if result.status != "success":
            raise NotFoundError(result.result.msg_summary)
        return result.result.data  # type: ignore[no-any-return]
