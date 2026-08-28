from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from nanobar_api.telemetry.persistence import build_session_factory
from nanobar_api.telemetry.span_repository import SpanRepository
from nanobar_api.telemetry.telemetry_validator_gate import TelemetryValidatorGate


def _telemetry_session(tmp_path: Path) -> Session:
    return build_session_factory(str(tmp_path / "telemetry.db"))()


def _valid_raw(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "event_id": "evt-1",
        "trace_id": "trace-1",
        "span_id": "span-1",
        "channel": "trace",
        "recorded_at_ns": 1,
        "monotonic_ns": 1,
        "payload": {"name": "x"},
        "entry_point": "GET /x",
    }
    raw.update(overrides)
    return raw


def test_call_with_valid_shape_ingests_and_returns_success(tmp_path: Path) -> None:
    session = _telemetry_session(tmp_path)
    gate = TelemetryValidatorGate(session)

    envelope = gate(_valid_raw())

    assert envelope["status"] == "success"
    assert SpanRepository(session).get("evt-1") is not None


def test_call_with_missing_required_field_returns_error_envelope_without_writing(tmp_path: Path) -> None:
    session = _telemetry_session(tmp_path)
    gate = TelemetryValidatorGate(session)
    raw = _valid_raw()
    del raw["entry_point"]

    envelope = gate(raw)

    assert envelope["status"] == "error"
    assert "entry_point" in envelope["msg"]
    assert SpanRepository(session).get("evt-1") is None


def test_call_with_wrong_type_returns_error_envelope(tmp_path: Path) -> None:
    session = _telemetry_session(tmp_path)
    gate = TelemetryValidatorGate(session)

    envelope = gate(_valid_raw(recorded_at_ns="not-a-number"))

    assert envelope["status"] == "error"


def test_call_validates_nested_source_activity_info_shape(tmp_path: Path) -> None:
    session = _telemetry_session(tmp_path)
    gate = TelemetryValidatorGate(session)

    envelope = gate(_valid_raw(source_activity_info={"source_name": "cron"}))  # missing required nested fields

    assert envelope["status"] == "error"


def test_validate_returns_parsed_request_directly(tmp_path: Path) -> None:
    session = _telemetry_session(tmp_path)
    gate = TelemetryValidatorGate(session)

    validated = gate.validate(_valid_raw())

    assert validated.trace_id == "trace-1"
    assert validated.entry_point == "GET /x"
