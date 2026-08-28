"""`NanobarAPIService` — replaces the empty `class Service: pass` stub in place, no back-compat
shim (nothing in the codebase constructed the old stub — confirmed via grep, same as
`NanobarAPIController`'s own rename).

Per `.focusari/nanobar_ServiceDomain_abstract_class_buildplan-with-tasks.md` §1: the
controller-to-service call boundary, producing the `service-request-response` brick.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from nanobar_api.capture.layer_capture import capture_layer, to_payload_dict
from nanobar_api.telemetry import NanobarProps, NanobarTelemetry

ServiceStatus = Literal["success", "error", "timeout"]
ServiceResultType = Literal["object", "array", "binary", "socket"]


@dataclass(frozen=True)
class SourceInfoEntry:
    source_type: str
    source_file_url: str
    source_status_code: int  # 100-9999 per the source spec's own range -- see Open Decision 2


@dataclass(frozen=True)
class ServiceResultBody:
    type: ServiceResultType
    data: Any
    msg_summary: str


@dataclass(frozen=True)
class ServiceResult:
    """Deliberately distinct from `nanobar_api.envelope.Envelope` — an internal controller<->
    service call contract, not the public HTTP wire contract `adapt_handler`'s `success()`
    wraps route-handler return values in. Conflating them risks leaking `source_info` (which may
    carry file paths) onto a public HTTP response by accident."""

    status: ServiceStatus
    result: ServiceResultBody
    source_info: list[SourceInfoEntry] = field(default_factory=list)


class NanobarAPIService(ABC):
    """`__init__` takes only `telemetry`, not a separate `repository` too — a deliberate
    deviation from the source spec's `__init__(self, telemetry, repository)`: `NanobarTelemetry`
    already carries its own `.repository`, and `NanobarAPIValidatorGate`/`NanobarAPIController`
    (this codebase's other two capture-producing base classes) already establish the "derive the
    repository from telemetry" convention. Taking a second, independent `repository` param would
    invite the two to silently disagree.
    """

    def __init__(self, telemetry: NanobarTelemetry) -> None:
        self.telemetry = telemetry

    @abstractmethod
    def handle(self, request: Any) -> ServiceResult: ...

    def __call__(self, request: Any, *, route_key: str | None = None) -> ServiceResult:
        """Concrete, non-overridable: wraps `handle(request)` in a child `telemetry.span(...)`
        and captures a `service-request-response` brick via `capture_layer()`. `route_key` is
        optional (not every service call happens behind an HTTP route — a worker or event
        subscriber calling a service has no route key at all) — passed through to
        `capture_layer()` for `nanobar_api.bricks.binding` to use when it is available, same
        convention `NanobarAPIValidatorGate`/`NanobarAPIController` already established.
        """
        with self.telemetry.span("service", nanobar=NanobarProps(type="service-request-response")):
            result = self.handle(request)
            capture_layer(
                self.telemetry.repository,
                "service",
                to_payload_dict(request),
                to_payload_dict(result),
                nanobar_type="service-request-response",
                route_key=route_key,
            )
        return result
