"""Service layer for `RegressionBrick`'s **collection** concern -- curating the existing set of
bricks (review status, scenario labeling, tags), as distinct from the **analysis** concern
(replaying a brick against the live app to verify nothing broke -- a separate,
`regression_brick_analysis_*` stack, not built here). A `RegressionBrick` row itself is never
mutated (it's forked, per the immutability trigger) -- every operation here writes to one of its
mutable side-tables (`BrickReviewStatus`/`BrickScenario`/`BrickTag`), never the brick itself.

One `NanobarAPIService` subclass per operation, mirroring `app/services/blog_service.py`'s shape
exactly. "Brick not found" is a real business-outcome check requiring a repository lookup, not a
validator-gate concern (same reasoning `app/services/blog_service.py`'s
`MarkNotificationReadService` already documents) -- every service below checks it first and
returns `status="error"` rather than raising, letting the controller decide the HTTP status.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from nanobar_api.framework.nanobar_api_service import NanobarAPIService, ServiceResult, ServiceResultBody
from nanobar_api.regression_brick.repository import RegressionBrickRepository
from nanobar_api.telemetry import NanobarTelemetry


def _not_found(regression_brick_id: str) -> ServiceResult:
    return ServiceResult(
        status="error",
        result=ServiceResultBody(type="object", data=None, msg_summary=f"brick {regression_brick_id!r} not found"),
    )


@dataclass
class SetReviewStatusRequest:
    regression_brick_id: str
    status: str
    updated_by: str


class SetReviewStatusService(NanobarAPIService):
    def __init__(self, telemetry: NanobarTelemetry, repository: RegressionBrickRepository) -> None:
        super().__init__(telemetry)
        self.repository = repository

    def handle(self, request: SetReviewStatusRequest) -> ServiceResult:
        if self.repository.get(request.regression_brick_id) is None:
            return _not_found(request.regression_brick_id)
        self.repository.set_review_status(request.regression_brick_id, request.status, request.updated_by)
        updated = self.repository.get_review_status(request.regression_brick_id)
        return ServiceResult(
            status="success",
            result=ServiceResultBody(
                type="object", data=dataclasses.asdict(updated), msg_summary="review status updated"
            ),
        )


@dataclass
class SetBrickScenarioRequest:
    regression_brick_id: str
    regression_scenario_label: str | None
    description: str | None
    updated_by: str


class SetBrickScenarioService(NanobarAPIService):
    """`regression_scenario_label`/`description` are partial-update fields: `None` (whether the
    caller omitted the key or sent it explicitly null -- the validator gate can't tell the two
    apart from JSON shape alone, and no caller has ever needed to) keeps the currently-stored
    value rather than clearing it."""

    def __init__(self, telemetry: NanobarTelemetry, repository: RegressionBrickRepository) -> None:
        super().__init__(telemetry)
        self.repository = repository

    def handle(self, request: SetBrickScenarioRequest) -> ServiceResult:
        if self.repository.get(request.regression_brick_id) is None:
            return _not_found(request.regression_brick_id)
        current = self.repository.get_scenario(request.regression_brick_id)
        label = (
            request.regression_scenario_label
            if request.regression_scenario_label is not None
            else current.regression_scenario_label
        )
        description = request.description if request.description is not None else current.description
        self.repository.set_scenario(
            request.regression_brick_id,
            regression_scenario_label=label,
            description=description,
            updated_by=request.updated_by,
        )
        updated = self.repository.get_scenario(request.regression_brick_id)
        return ServiceResult(
            status="success",
            result=ServiceResultBody(type="object", data=dataclasses.asdict(updated), msg_summary="scenario updated"),
        )


@dataclass
class AddBrickTagRequest:
    regression_brick_id: str
    tag: str


class AddBrickTagService(NanobarAPIService):
    def __init__(self, telemetry: NanobarTelemetry, repository: RegressionBrickRepository) -> None:
        super().__init__(telemetry)
        self.repository = repository

    def handle(self, request: AddBrickTagRequest) -> ServiceResult:
        if self.repository.get(request.regression_brick_id) is None:
            return _not_found(request.regression_brick_id)
        self.repository.add_tag(request.regression_brick_id, request.tag)
        tags = self.repository.tags_for(request.regression_brick_id)
        return ServiceResult(
            status="success", result=ServiceResultBody(type="array", data=tags, msg_summary="tag added")
        )


@dataclass
class RemoveBrickTagRequest:
    regression_brick_id: str
    tag: str


class RemoveBrickTagService(NanobarAPIService):
    def __init__(self, telemetry: NanobarTelemetry, repository: RegressionBrickRepository) -> None:
        super().__init__(telemetry)
        self.repository = repository

    def handle(self, request: RemoveBrickTagRequest) -> ServiceResult:
        if self.repository.get(request.regression_brick_id) is None:
            return _not_found(request.regression_brick_id)
        self.repository.remove_tag(request.regression_brick_id, request.tag)
        tags = self.repository.tags_for(request.regression_brick_id)
        return ServiceResult(
            status="success", result=ServiceResultBody(type="array", data=tags, msg_summary="tag removed")
        )
