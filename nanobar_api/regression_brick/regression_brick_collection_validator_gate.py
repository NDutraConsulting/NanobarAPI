"""Validator gates for `RegressionBrick`'s collection routes -- one per `NanobarRouteRule`,
following `tests/test_validator_gate.py`'s `GreetGate` worked example exactly (`controller_cls` +
`validate()` calling `nanobar_api.validation.parse` against `request.state.json_body`), same shape
as `app/validators/blog_validator_gateway.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

from starlette.requests import Request

from nanobar_api.framework.nanobar_api_validator_gate import NanobarAPIValidatorGate
from nanobar_api.regression_brick.model import REVIEW_STATUSES
from nanobar_api.regression_brick.regression_brick_collection_controller import (
    AddBrickTagController,
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
from nanobar_api.validation import ValidationError, parse

#: This dashboard has no per-admin identity yet -- a single shared admin login, see
#: nanobar_api.admin_auth -- so this is the same placeholder the old plain-function route
#: handlers already stamped on every collection write.
_UPDATED_BY = "dashboard"


@dataclass
class _StatusBody:
    status: str


class SetReviewStatusGate(NanobarAPIValidatorGate):
    controller_cls = SetReviewStatusController

    def validate(self, request: Request) -> SetReviewStatusRequest:
        body = request.state.json_body
        if body is None:
            raise ValidationError(["request body must be valid JSON"])
        if not isinstance(body, dict):
            raise ValidationError(["request body must include a 'status' string field"])
        parsed = parse(_StatusBody, body)
        if parsed.status not in REVIEW_STATUSES:
            raise ValidationError([f"invalid review status {parsed.status!r}, must be one of {REVIEW_STATUSES}"])
        return SetReviewStatusRequest(
            regression_brick_id=request.path_params["brick_id"], status=parsed.status, updated_by=_UPDATED_BY
        )


@dataclass
class _ScenarioBody:
    regression_scenario_label: str | None = None
    description: str | None = None


class SetBrickScenarioGate(NanobarAPIValidatorGate):
    controller_cls = SetBrickScenarioController

    def validate(self, request: Request) -> SetBrickScenarioRequest:
        body = request.state.json_body
        if body is None:
            raise ValidationError(["request body must be valid JSON"])
        if not isinstance(body, dict):
            raise ValidationError(["request body must be a JSON object"])
        parsed = parse(_ScenarioBody, body)
        return SetBrickScenarioRequest(
            regression_brick_id=request.path_params["brick_id"],
            regression_scenario_label=parsed.regression_scenario_label,
            description=parsed.description,
            updated_by=_UPDATED_BY,
        )


@dataclass
class _TagBody:
    tag: str


class AddBrickTagGate(NanobarAPIValidatorGate):
    controller_cls = AddBrickTagController

    def validate(self, request: Request) -> AddBrickTagRequest:
        body = request.state.json_body
        if body is None:
            raise ValidationError(["request body must be valid JSON"])
        if not isinstance(body, dict):
            raise ValidationError(["request body must include a non-empty 'tag' string field"])
        parsed = parse(_TagBody, body)
        if not parsed.tag:
            raise ValidationError(["request body must include a non-empty 'tag' string field"])
        return AddBrickTagRequest(regression_brick_id=request.path_params["brick_id"], tag=parsed.tag)


class RemoveBrickTagGate(NanobarAPIValidatorGate):
    controller_cls = RemoveBrickTagController

    def validate(self, request: Request) -> RemoveBrickTagRequest:
        # Path params only -- DELETE carries no body, same as blog's own
        # MarkNotificationReadGate reading `request.path_params` directly.
        return RemoveBrickTagRequest(
            regression_brick_id=request.path_params["brick_id"], tag=request.path_params["tag"]
        )
