"""`TelemetryController` -- plain, transport-agnostic controller (Decision 3): no
`NanobarAPIController` subclassing, no `Request` dependency (that base class is Starlette-
`Request`-bound; see `.focusari/telemetry-domain-refactor-plan-with-tasks.md`'s own research
findings for why that doesn't fit a caller with no `Request`, e.g. the draining worker).
Constructed directly with its own dependency (a SQLAlchemy `Session`) so the identical class,
unmodified, is callable from an HTTP route (via a thin api_router translating this class's plain
`nanobar_api.envelope.Envelope` return value into a real status code) and from the new draining
worker (Phase 5, reading the envelope directly and branching on `.status`) -- "Returning an HTTP
response code for all callers should not make any difference," per the user's own confirmed
reasoning for Decision 3.

No `telemetry` dependency either, unlike the old HTTP-bound `NanobarAPIController` -- `IngestSpanService`
doesn't self-instrument (see `telemetry_service.py`'s own docstring: "it should not capture
itself"), so there's nothing here that would need one.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from nanobar_api.envelope import Envelope, success
from nanobar_api.telemetry.telemetry_service import IngestSpanRequest, IngestSpanService


class TelemetryController:
    def __init__(self, session: Session) -> None:
        self.session = session

    def ingest_span(self, request: IngestSpanRequest) -> Envelope:
        """`IngestSpanService` has no defined business-failure outcome today (a pure
        get-or-create-then-insert, always `status="success"`) -- no `error(...)` branch here to
        match, same "controller-level failures propagate uncaught, not invented" precedent
        `NanobarAPIController.handle()`'s own docstring already establishes. Add one if/when a
        real failure mode (e.g. a malformed `payload`) needs to surface as `Envelope` `"error"`
        rather than an exception.
        """
        service = IngestSpanService(self.session)
        result = service(request)
        return success(result.result.data)
