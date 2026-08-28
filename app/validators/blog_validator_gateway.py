"""Validator gates for the blog domain's mutating routes -- one per `NanobarRouteRule`, following
`tests/test_validator_gate.py`'s `GreetGate` worked example exactly (`controller_cls` +
`validate()` calling `nanobar_api.validation.parse` against `request.state.json_body`).
"""

from __future__ import annotations

from dataclasses import dataclass

from starlette.requests import Request

from app.controllers.blog_controller import (
    BookAppointmentController,
    CreatePostController,
    MarkNotificationReadController,
    UpdatePostController,
)
from app.services.blog_service import (
    BookAppointmentRequest,
    CreatePostRequest,
    MarkNotificationReadRequest,
    UpdatePostRequest,
)
from nanobar_api.framework.nanobar_api_validator_gate import NanobarAPIValidatorGate
from nanobar_api.validation import parse


class CreatePostGate(NanobarAPIValidatorGate):
    controller_cls = CreatePostController

    def validate(self, request: Request) -> CreatePostRequest:
        return parse(CreatePostRequest, request.state.json_body or {})


@dataclass
class _UpdatePostBody:
    title: str
    body: str


class UpdatePostGate(NanobarAPIValidatorGate):
    controller_cls = UpdatePostController

    def validate(self, request: Request) -> UpdatePostRequest:
        # post_id is a path param, not body -- same split as MarkNotificationReadGate below:
        # _UpdatePostBody only exists to get parse()'s shape validation on title/body.
        fields = parse(_UpdatePostBody, request.state.json_body or {})
        return UpdatePostRequest(post_id=request.path_params["post_id"], title=fields.title, body=fields.body)


class BookAppointmentGate(NanobarAPIValidatorGate):
    controller_cls = BookAppointmentController

    def validate(self, request: Request) -> BookAppointmentRequest:
        return parse(BookAppointmentRequest, request.state.json_body or {})


class MarkNotificationReadGate(NanobarAPIValidatorGate):
    controller_cls = MarkNotificationReadController

    def validate(self, request: Request) -> MarkNotificationReadRequest:
        # Path param, not body -- ValidationError isn't the right tool here (the route wouldn't
        # even match without a notification_id segment); a missing/unknown id is the service
        # layer's "not found" business-outcome case (MarkNotificationReadService), not a
        # validation-layer concern.
        return MarkNotificationReadRequest(notification_id=request.path_params["notification_id"])
