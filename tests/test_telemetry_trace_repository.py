from __future__ import annotations

from pathlib import Path

from nanobar_api.telemetry.model import SourceActivityInfo, Span, Trace
from nanobar_api.telemetry.persistence import build_session_factory
from nanobar_api.telemetry.trace_repository import TraceRepository


def _make_trace(trace_id: str = "trace-1", **overrides: object) -> Trace:
    defaults: dict[str, object] = {"trace_id": trace_id, "entry_point": "GET /x"}
    defaults.update(overrides)
    return Trace(**defaults)


def _make_span(span_id: str, trace_id: str, **overrides: object) -> Span:
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


def test_create_and_get_round_trip(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        repo = TraceRepository(session)
        trace = repo.create(_make_trace())

        assert repo.get(trace.trace_id) is trace


def test_get_returns_none_for_unknown_id(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        repo = TraceRepository(session)
        assert repo.get("does-not-exist") is None


def test_get_uses_the_cache_on_second_call(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        repo = TraceRepository(session)
        trace = repo.create(_make_trace())

        repo.get(trace.trace_id)
        assert repo.get_cached(trace.trace_id) is trace
        assert repo.get(trace.trace_id) is trace


def test_get_or_create_creates_on_first_call(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        repo = TraceRepository(session)
        trace, created = repo.get_or_create("trace-1", entry_point="GET /x", app_box="api")

        assert created is True
        assert trace.entry_point == "GET /x"
        assert trace.app_box == "api"


def test_get_or_create_returns_existing_on_second_call_without_overwriting(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        repo = TraceRepository(session)
        first, _ = repo.get_or_create("trace-1", entry_point="GET /x", app_box="api")
        second, created = repo.get_or_create("trace-1", entry_point="GET /different", app_box="workers")

        assert created is False
        assert second.trace_id == first.trace_id
        assert second.entry_point == "GET /x"
        assert second.app_box == "api"


def test_get_or_create_stamps_source_activity_info_only_on_creation(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    info = SourceActivityInfo(
        source_name="cron", source_url="internal://cron", created_at="2026-08-27T00:00:00+00:00", created_by="sched"
    )
    with Session() as session:
        repo = TraceRepository(session)
        trace, _ = repo.get_or_create("trace-1", entry_point="worker-domain.appointments", source_activity_info=info)
        assert trace.source_activity_info == info

        second, created = repo.get_or_create("trace-1", entry_point="worker-domain.appointments")
        assert created is False
        assert second.source_activity_info == info


def test_list_traces_filters_by_entry_point_and_app_box(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        repo = TraceRepository(session)
        repo.create(_make_trace("trace-1", entry_point="GET /a", app_box="api"))
        repo.create(_make_trace("trace-2", entry_point="GET /b", app_box="workers"))
        repo.create(_make_trace("trace-3", entry_point="GET /a", app_box="workers"))

        assert {t.trace_id for t in repo.list_traces(entry_point="GET /a")} == {"trace-1", "trace-3"}
        assert {t.trace_id for t in repo.list_traces(app_box="workers")} == {"trace-2", "trace-3"}
        assert {t.trace_id for t in repo.list_traces(entry_point="GET /a", app_box="workers")} == {"trace-3"}


def test_list_traces_filters_by_created_at_range(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        repo = TraceRepository(session)
        repo.create(_make_trace("trace-1", created_at="2026-08-01T00:00:00+00:00"))
        repo.create(_make_trace("trace-2", created_at="2026-08-15T00:00:00+00:00"))
        repo.create(_make_trace("trace-3", created_at="2026-08-30T00:00:00+00:00"))

        result = repo.list_traces(created_after="2026-08-10T00:00:00+00:00", created_before="2026-08-20T00:00:00+00:00")
        assert [t.trace_id for t in result] == ["trace-2"]


def test_list_traces_orders_newest_first_and_paginates(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        repo = TraceRepository(session)
        repo.create(_make_trace("trace-1", created_at="2026-08-01T00:00:00+00:00"))
        repo.create(_make_trace("trace-2", created_at="2026-08-15T00:00:00+00:00"))
        repo.create(_make_trace("trace-3", created_at="2026-08-30T00:00:00+00:00"))

        assert [t.trace_id for t in repo.list_traces(page_size=2)] == ["trace-3", "trace-2"]
        assert [t.trace_id for t in repo.list_traces(page=2, page_size=2)] == ["trace-1"]


def test_count_traces_matches_filtered_list(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        repo = TraceRepository(session)
        repo.create(_make_trace("trace-1", app_box="api"))
        repo.create(_make_trace("trace-2", app_box="workers"))

        assert repo.count_traces() == 2
        assert repo.count_traces(app_box="api") == 1


def test_list_with_unprocessed_spans_returns_only_traces_with_unprocessed_spans_on_channel(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        repo = TraceRepository(session)
        repo.create(_make_trace("trace-1"))
        repo.create(_make_trace("trace-2"))
        repo.create(_make_trace("trace-3"))
        session.add(_make_span("span-1", "trace-1", channel="snapshot", recorded_at_ns=10))
        session.add(_make_span("span-2", "trace-2", channel="snapshot", recorded_at_ns=5, processed_at="done"))
        session.add(_make_span("span-3", "trace-3", channel="other", recorded_at_ns=1))
        session.commit()

        result = repo.list_with_unprocessed_spans("snapshot")
        assert [t.trace_id for t in result] == ["trace-1"]


def test_list_with_unprocessed_spans_orders_oldest_unprocessed_span_first(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        repo = TraceRepository(session)
        repo.create(_make_trace("trace-1"))
        repo.create(_make_trace("trace-2"))
        session.add(_make_span("span-1", "trace-1", channel="snapshot", recorded_at_ns=100))
        session.add(_make_span("span-2", "trace-2", channel="snapshot", recorded_at_ns=1))
        session.commit()

        result = repo.list_with_unprocessed_spans("snapshot")
        assert [t.trace_id for t in result] == ["trace-2", "trace-1"]


def test_list_trace_summaries_computes_aggregates(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        repo = TraceRepository(session)
        repo.create(_make_trace("trace-1"))
        session.add(_make_span("span-1", "trace-1", channel="trace", recorded_at_ns=10, payload_json={"name": "a"}))
        session.add(
            _make_span(
                "span-2", "trace-1", channel="trace", recorded_at_ns=20, payload_json={"name": "b", "error": True}
            )
        )
        session.commit()

        summaries = repo.list_trace_summaries("trace")
        assert len(summaries) == 1
        summary = summaries[0]
        assert summary.trace_id == "trace-1"
        assert summary.span_count == 2
        assert summary.first_recorded_at_ns == 10
        assert summary.last_recorded_at_ns == 20
        assert summary.any_error is True


def test_list_trace_summaries_filters_by_channel(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        repo = TraceRepository(session)
        repo.create(_make_trace("trace-1"))
        repo.create(_make_trace("trace-2"))
        session.add(_make_span("span-1", "trace-1", channel="trace", recorded_at_ns=1))
        session.add(_make_span("span-2", "trace-2", channel="snapshot", recorded_at_ns=1))
        session.commit()

        assert [s.trace_id for s in repo.list_trace_summaries("trace")] == ["trace-1"]


def test_list_trace_summaries_filters_by_date_range(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        repo = TraceRepository(session)
        repo.create(_make_trace("trace-1"))
        repo.create(_make_trace("trace-2"))
        session.add(_make_span("span-1", "trace-1", channel="trace", recorded_at_ns=10))
        session.add(_make_span("span-2", "trace-2", channel="trace", recorded_at_ns=100))
        session.commit()

        assert [s.trace_id for s in repo.list_trace_summaries("trace", created_after_ns=50)] == ["trace-2"]
        assert [s.trace_id for s in repo.list_trace_summaries("trace", created_before_ns=50)] == ["trace-1"]


def test_list_trace_summaries_filters_by_nanobar_types(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        repo = TraceRepository(session)
        repo.create(_make_trace("trace-1"))
        repo.create(_make_trace("trace-2"))
        session.add(
            _make_span("span-1", "trace-1", channel="trace", recorded_at_ns=1, payload_json={"nanobar_type": "api"})
        )
        session.add(
            _make_span("span-2", "trace-2", channel="trace", recorded_at_ns=1, payload_json={"nanobar_type": "worker"})
        )
        session.commit()

        result = repo.list_trace_summaries("trace", nanobar_types=["api"])
        assert [s.trace_id for s in result] == ["trace-1"]


def test_list_trace_summaries_filters_by_components(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        repo = TraceRepository(session)
        repo.create(_make_trace("trace-1"))
        repo.create(_make_trace("trace-2"))
        session.add(
            _make_span("span-1", "trace-1", channel="trace", recorded_at_ns=1, payload_json={"name": "service"})
        )
        session.add(
            _make_span(
                "span-2", "trace-2", channel="trace", recorded_at_ns=1, payload_json={"name": "worker.x.process"}
            )
        )
        session.commit()

        result = repo.list_trace_summaries("trace", components=["service:service"])
        assert [s.trace_id for s in result] == ["trace-1"]


def test_list_trace_summaries_ignores_unreconstructible_components(tmp_path: Path) -> None:
    """A `component` value with no reconstructible exact span name (`component_span_name`
    returning `None`) is silently skipped from the filter, not an error -- matches the old
    `_trace_where`'s own documented behavior for unvalidated input."""
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        repo = TraceRepository(session)
        repo.create(_make_trace("trace-1"))
        session.add(_make_span("span-1", "trace-1", channel="trace", recorded_at_ns=1))
        session.commit()

        result = repo.list_trace_summaries("trace", components=["not-a-real-kind:whatever"])
        assert [s.trace_id for s in result] == ["trace-1"]


def test_list_trace_summaries_orders_newest_first_and_paginates(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        repo = TraceRepository(session)
        repo.create(_make_trace("trace-1"))
        repo.create(_make_trace("trace-2"))
        repo.create(_make_trace("trace-3"))
        session.add(_make_span("span-1", "trace-1", channel="trace", recorded_at_ns=1))
        session.add(_make_span("span-2", "trace-2", channel="trace", recorded_at_ns=2))
        session.add(_make_span("span-3", "trace-3", channel="trace", recorded_at_ns=3))
        session.commit()

        assert [s.trace_id for s in repo.list_trace_summaries("trace", page_size=2)] == ["trace-3", "trace-2"]
        assert [s.trace_id for s in repo.list_trace_summaries("trace", page=2, page_size=2)] == ["trace-1"]


def test_count_trace_summaries_matches_filtered_list(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        repo = TraceRepository(session)
        repo.create(_make_trace("trace-1"))
        repo.create(_make_trace("trace-2"))
        session.add(_make_span("span-1", "trace-1", channel="trace", recorded_at_ns=1))
        session.add(_make_span("span-2", "trace-2", channel="snapshot", recorded_at_ns=1))
        session.commit()

        assert repo.count_trace_summaries("trace") == 1


def test_list_with_unprocessed_spans_respects_limit(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        repo = TraceRepository(session)
        for i in range(3):
            repo.create(_make_trace(f"trace-{i}"))
            session.add(_make_span(f"span-{i}", f"trace-{i}", channel="snapshot", recorded_at_ns=i))
        session.commit()

        assert len(repo.list_with_unprocessed_spans("snapshot", limit=2)) == 2
