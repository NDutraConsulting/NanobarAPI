from __future__ import annotations

from pathlib import Path

from nanobar_api.telemetry.model import SourceActivityInfo, Span, Trace
from nanobar_api.telemetry.persistence import build_session_factory


def _make_trace(trace_id: str = "trace-1", **overrides: object) -> Trace:
    defaults: dict[str, object] = {"trace_id": trace_id, "entry_point": "GET /x"}
    defaults.update(overrides)
    return Trace(**defaults)


def _make_span(span_id: str = "span-1", trace_id: str = "trace-1", **overrides: object) -> Span:
    defaults: dict[str, object] = {
        "event_id": span_id,
        "span_id": span_id,
        "trace_id": trace_id,
        "channel": "trace",
        "recorded_at_ns": 1,
        "monotonic_ns": 1,
        "payload_json": {"name": "x"},
    }
    defaults.update(overrides)
    return Span(**defaults)


def test_app_box_defaults_to_none(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))

    with Session() as session:
        trace = _make_trace()
        session.add(trace)
        session.commit()
        assert trace.app_box is None


def test_source_activity_info_round_trips_including_none(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))

    with Session() as session:
        trace = _make_trace()
        session.add(trace)
        session.commit()
        assert trace.source_activity_info is None

        trace.source_activity_info = SourceActivityInfo(
            source_name="cron-nightly-sweep",
            source_url="internal://cron/nightly-sweep",
            created_at="2026-08-27T00:00:00+00:00",
            created_by="scheduler",
            security_info=["jwt"],
            security_checked=True,
        )
        session.commit()

    with Session() as session:
        reloaded = session.get(Trace, "trace-1")
        assert reloaded is not None
        assert reloaded.source_activity_info == SourceActivityInfo(
            source_name="cron-nightly-sweep",
            source_url="internal://cron/nightly-sweep",
            created_at="2026-08-27T00:00:00+00:00",
            created_by="scheduler",
            security_info=["jwt"],
            security_checked=True,
        )


def test_source_activity_info_security_info_defaults_to_empty_list() -> None:
    info = SourceActivityInfo(
        source_name="ui-button",
        source_url="/admin/nanobar/dashboard",
        created_at="2026-08-27T00:00:00+00:00",
        created_by="human",
    )
    assert info.security_info == []
    assert info.security_checked is False


def test_span_processed_at_defaults_to_none(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))

    with Session() as session:
        session.add(_make_trace())
        span = _make_span()
        session.add(span)
        session.commit()
        assert span.processed_at is None


def test_trace_spans_relationship(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))

    with Session() as session:
        trace = _make_trace()
        session.add(trace)
        session.add(_make_span(span_id="span-1"))
        session.add(_make_span(span_id="span-2"))
        session.commit()
        trace_id = trace.trace_id

    with Session() as session:
        reloaded = session.get(Trace, trace_id)
        assert reloaded is not None
        assert {span.span_id for span in reloaded.spans} == {"span-1", "span-2"}
