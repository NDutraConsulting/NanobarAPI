from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from nanobar_api.telemetry.model import SourceActivityInfo
from nanobar_api.telemetry.persistence import build_session_factory
from nanobar_api.telemetry.span_repository import SpanRepository
from nanobar_api.telemetry.telemetry_service import IngestSpanRequest, IngestSpanService
from nanobar_api.telemetry.trace_repository import TraceRepository


def _telemetry_session(tmp_path: Path) -> Session:
    return build_session_factory(str(tmp_path / "telemetry.db"))()


def _make_request(**overrides: object) -> IngestSpanRequest:
    defaults: dict[str, object] = {
        "event_id": "evt-1",
        "trace_id": "trace-1",
        "span_id": "span-1",
        "channel": "trace",
        "recorded_at_ns": 1,
        "monotonic_ns": 1,
        "payload": {"name": "x"},
        "entry_point": "GET /x",
    }
    defaults.update(overrides)
    return IngestSpanRequest(**defaults)  # type: ignore[arg-type]


def test_ingest_span_creates_trace_and_span(tmp_path: Path) -> None:
    session = _telemetry_session(tmp_path)
    service = IngestSpanService(session)

    result = service(_make_request())

    assert result.status == "success"
    assert result.result.data == {"trace_id": "trace-1", "event_id": "evt-1", "trace_created": True}
    assert TraceRepository(session).get("trace-1") is not None
    assert SpanRepository(session).get("evt-1") is not None


def test_ingest_span_second_span_on_same_trace_does_not_recreate_trace(tmp_path: Path) -> None:
    session = _telemetry_session(tmp_path)
    service = IngestSpanService(session)

    service(_make_request())
    result = service(_make_request(event_id="evt-2", span_id="span-2"))

    assert result.result.data["trace_created"] is False


def test_ingest_span_is_idempotent_for_a_repeated_event_id(tmp_path: Path) -> None:
    """`EventBusTraceMiddleware`/`SnapshotMiddleware` can both capture the same span under the
    same `span_id` with different payloads (see `model.py`'s `Span` docstring) -- idempotency is
    keyed on `event_id`, the real primary key, not `span_id`."""
    session = _telemetry_session(tmp_path)
    service = IngestSpanService(session)

    first = service(_make_request())
    second = service(_make_request(payload={"different": "payload"}))

    assert first.result.data["trace_id"] == second.result.data["trace_id"]
    assert first.result.data["event_id"] == second.result.data["event_id"]
    assert second.result.data["trace_created"] is False
    assert second.result.msg_summary == "span already ingested (idempotent no-op)"
    assert SpanRepository(session).get("evt-1").payload_json == {"name": "x"}  # type: ignore[union-attr]


def test_ingest_span_stamps_app_box_and_source_activity_info_on_new_trace(tmp_path: Path) -> None:
    session = _telemetry_session(tmp_path)
    service = IngestSpanService(session)
    info = SourceActivityInfo(
        source_name="cron", source_url="internal://cron", created_at="2026-08-27T00:00:00+00:00", created_by="sched"
    )

    service(_make_request(entry_point="worker-domain.appointments", app_box="workers", source_activity_info=info))

    trace = TraceRepository(session).get("trace-1")
    assert trace is not None
    assert trace.app_box == "workers"
    assert trace.source_activity_info == info
