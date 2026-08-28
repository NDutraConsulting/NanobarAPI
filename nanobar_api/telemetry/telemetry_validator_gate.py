"""`TelemetryValidatorGate` -- plain, transport-agnostic validator gate (Decision 3): validates
JSON *shape* only ("in the same way pydantic validates a json shape," the user's own words) via
`nanobar_api.validation.parse`, and decides success/error, returning a plain
`nanobar_api.envelope.Envelope` -- never an HTTP response or status code directly. No
`NanobarAPIValidatorGate` subclassing, same reasoning as `TelemetryController`'s own docstring --
and, like that class, no `telemetry` dependency either ("it should not capture itself").

`parse(IngestSpanRequest, raw)` already validates `source_activity_info` too, recursively -- it's
typed `SourceActivityInfo | None` on `IngestSpanRequest`, and `nanobar_api.validation.parse`
already handles a nested dataclass field, so no separate shape check is needed for it here.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from nanobar_api.envelope import Envelope, error
from nanobar_api.telemetry.telemetry_controller import TelemetryController
from nanobar_api.telemetry.telemetry_service import IngestSpanRequest
from nanobar_api.validation import ValidationError, parse


class TelemetryValidatorGate:
    def __init__(self, session: Session) -> None:
        self.session = session

    def validate(self, raw: dict[str, Any]) -> IngestSpanRequest:
        return parse(IngestSpanRequest, raw)

    def __call__(self, raw: dict[str, Any]) -> Envelope:
        try:
            validated = self.validate(raw)
        except ValidationError as exc:
            return error("; ".join(exc.errors))
        controller = TelemetryController(self.session)
        return controller.ingest_span(validated)
