from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from nanobar_api.telemetry.persistence import build_session_factory
from nanobar_api.telemetry.span_repository import SpanRepository
from nanobar_api.telemetry.telemetry_controller import TelemetryController
from nanobar_api.telemetry.telemetry_service import IngestSpanRequest


def _telemetry_session(tmp_path: Path) -> Session:
    return build_session_factory(str(tmp_path / "telemetry.db"))()


def test_ingest_span_returns_a_success_envelope(tmp_path: Path) -> None:
    session = _telemetry_session(tmp_path)
    controller = TelemetryController(session)

    envelope = controller.ingest_span(
        IngestSpanRequest(
            event_id="evt-1",
            trace_id="trace-1",
            span_id="span-1",
            channel="trace",
            recorded_at_ns=1,
            monotonic_ns=1,
            payload={"name": "x"},
            entry_point="GET /x",
        )
    )

    assert envelope["status"] == "success"
    assert envelope["result"]["data"] == {"trace_id": "trace-1", "event_id": "evt-1", "trace_created": True}
    assert SpanRepository(session).get("evt-1") is not None


def test_ingest_span_is_not_an_http_response(tmp_path: Path) -> None:
    """Decision 3: the controller returns a plain `Envelope` dict, never a `Starlette`
    `Response`/status code -- callable identically from a worker with no HTTP context at all."""
    session = _telemetry_session(tmp_path)
    controller = TelemetryController(session)

    envelope = controller.ingest_span(
        IngestSpanRequest(
            event_id="evt-1",
            trace_id="trace-1",
            span_id="span-1",
            channel="trace",
            recorded_at_ns=1,
            monotonic_ns=1,
            payload={},
            entry_point="GET /x",
        )
    )

    assert isinstance(envelope, dict)
    assert not hasattr(envelope, "status_code")
