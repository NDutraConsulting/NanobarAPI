"""Validator gate for `Nanobar`'s `update_nanobar` route -- matches
`app/validators/blog_validator_gateway.py`'s shape.
"""

from __future__ import annotations

from dataclasses import dataclass

from starlette.requests import Request

from nanobar_api.framework.nanobar_api_validator_gate import NanobarAPIValidatorGate
from nanobar_api.nanobar.controller import NanobarController
from nanobar_api.nanobar.service import UpdateNanobarRequest
from nanobar_api.validation import ValidationError, parse


@dataclass
class _UpdateNanobarBody:
    label: str | None = None
    scenario_description: str | None = None
    component_source_description: str | None = None
    domain: str | None = None
    app_box: str | None = None
    criticality: float | None = None


class NanobarGate(NanobarAPIValidatorGate):
    controller_cls = NanobarController

    def validate(self, request: Request) -> UpdateNanobarRequest:
        body = request.state.json_body
        if body is None:
            raise ValidationError(["request body must be valid JSON"])
        if not isinstance(body, dict):
            raise ValidationError(["request body must be a JSON object"])
        parsed = parse(_UpdateNanobarBody, body)
        if parsed.criticality is not None and not (0.0 <= parsed.criticality <= 1.0):
            raise ValidationError(["'criticality' must be a number between 0.0 and 1.0"])
        return UpdateNanobarRequest(
            nanobar_id=request.path_params["nanobar_id"],
            label=parsed.label,
            scenario_description=parsed.scenario_description,
            component_source_description=parsed.component_source_description,
            domain=parsed.domain,
            app_box=parsed.app_box,
            criticality=parsed.criticality,
        )
