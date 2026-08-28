from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from starlette.requests import Request
from starlette.responses import JSONResponse

from nanobar_api.capture.layer_capture import capture_layer, to_payload_dict
from nanobar_api.envelope import error
from nanobar_api.framework.nanobar_api_controller import NanobarAPIController, NanobarAPIError
from nanobar_api.telemetry import NanobarProps
from nanobar_api.validation import ValidationError

logger = logging.getLogger(__name__)


async def _request_payload_snapshot(request: Request) -> dict[str, Any]:
    """Best-effort request snapshot for `capture_layer()`, and the fix for a real contradiction
    in the source spec: `validate()` is declared *sync* (`def validate(self, request) -> Any`),
    but the spec's own example implementation calls `parse(SomeDataclass, await request.json())`
    — `await` inside a sync method is not legal Python. Resolved here, not in `validate()`:
    the JSON body is read once, asynchronously, before `validate()` runs, and stashed on
    `request.state.json_body` (Starlette's own per-request mutable namespace) so a sync
    `validate()` can read it — `request.state.json_body` — without awaiting anything itself.
    `None` when the body isn't valid JSON (or is empty), matching `request.json()`'s own
    behavior of raising in that case rather than a magic empty-dict default.
    """
    payload: dict[str, Any] = {
        "method": request.method,
        "path": request.url.path,
        "path_params": dict(request.path_params),
        "query_params": dict(request.query_params),
    }
    try:
        body = await request.json()
    except Exception:
        body = None
    else:
        payload["body"] = body
    request.state.json_body = body
    return payload


class NanobarAPIValidatorGate(ABC):
    """Request-validation layer between routing and the controller — the first of the three
    layers a nanobar route call passes through (`nanobar_routes` -> `nanobar_validator_gate` ->
    `nanobar_controller`), producing the `validator-request-response` brick.
    """

    controller_cls: ClassVar[type[NanobarAPIController]]

    @abstractmethod
    def validate(self, request: Request) -> Any:
        """Return a parsed/validated object, or raise nanobar_api.validation.ValidationError.

        Most implementations call `nanobar_api.validation.parse(SomeDataclass,
        request.state.json_body)` — the JSON body is already read and stashed there by the time
        `validate()` runs (`request.state.json_body` is `None` if the body wasn't valid JSON),
        since this method is sync and can't `await request.json()` itself. Nothing forces that
        specific mechanism — a validator that needs path/query params instead reads
        `request.path_params`/`.query_params` directly, same as any handler.
        """

    async def __call__(self, request: Request, request_type: str) -> Any:
        """Concrete, non-overridable dataflow:
        1. call `self.validate(request)` inside `telemetry.span(...)`.
        2. capture a `validator-request-response` brick via `capture_layer()` either way.
        3. on `ValidationError`: short-circuit with a 400 error envelope — the controller is
           never reached. `error=False` on the captured brick, matching `SnapshotMiddleware`'s
           own convention that `error` means an unhandled exception, not any non-2xx outcome —
           a `ValidationError` is handled, not a system fault (`_classify_scenario_type`'s own
           4xx/5xx distinction draws the same line).
        4. on success: construct `self.controller_cls(request, request_type)` and call
           `controller.handle(validated)`. A `NanobarAPIError` it raises (or lets propagate from
           a service/repository underneath) becomes a `nanobar_api.envelope.error()` response
           carrying that exception's own `status_code`; any *other* exception becomes the same
           shape at 500 (logged server-side first, so an unexpected bug is never silently
           swallowed, just never left to reach the router layer as a raw exception either). This
           is the whole point of the split described in `NanobarAPIController.handle()`'s own
           docstring: that method still lets a controller-level failure propagate uncaught out of
           itself, on purpose -- this is where it's actually turned into a real `Response`, so
           every route this framework's pipeline serves always gets one back, never an exception.
        """
        telemetry = request.app.state.telemetry
        request_payload = await _request_payload_snapshot(request)

        with telemetry.span(f"validator.{request_type}", nanobar=NanobarProps(type="validator-request-response")):
            try:
                validated = self.validate(request)
            except ValidationError as exc:
                capture_layer(
                    telemetry.repository,
                    "validator",
                    request_payload,
                    {"errors": exc.errors},
                    nanobar_type="validator-request-response",
                    route_key=request_type,
                )
                return JSONResponse(error("; ".join(exc.errors)), status_code=400)

            capture_layer(
                telemetry.repository,
                "validator",
                request_payload,
                to_payload_dict(validated),
                nanobar_type="validator-request-response",
                route_key=request_type,
            )

        controller = self.controller_cls(request, request_type)
        try:
            return await controller.handle(validated)
        except NanobarAPIError as exc:
            return JSONResponse(error(str(exc)), status_code=exc.status_code)
        except Exception as exc:
            logger.exception("unhandled controller-level exception for %s", request_type)
            return JSONResponse(error(str(exc)), status_code=500)
