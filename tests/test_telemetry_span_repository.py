from __future__ import annotations

from pathlib import Path

from nanobar_api.telemetry.model import Span, Trace
from nanobar_api.telemetry.persistence import build_session_factory
from nanobar_api.telemetry.span_repository import SpanRepository


def _make_trace(trace_id: str = "trace-1", **overrides: object) -> Trace:
    defaults: dict[str, object] = {"trace_id": trace_id, "entry_point": "GET /x"}
    defaults.update(overrides)
    return Trace(**defaults)


def _make_span(span_id: str, trace_id: str = "trace-1", **overrides: object) -> Span:
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
        session.add(_make_trace())
        session.commit()
        repo = SpanRepository(session)
        span = repo.create(_make_span("span-1"))

        assert repo.get(span.span_id) is span


def test_get_returns_none_for_unknown_id(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        repo = SpanRepository(session)
        assert repo.get("does-not-exist") is None


def test_create_many_inserts_all_and_is_a_noop_for_empty_input(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        session.add(_make_trace())
        session.commit()
        repo = SpanRepository(session)

        assert repo.create_many([]) == []
        created = repo.create_many([_make_span("span-1"), _make_span("span-2")])
        assert {s.span_id for s in created} == {"span-1", "span-2"}
        assert session.query(Span).count() == 2


def test_list_by_trace_id_orders_by_monotonic_ns(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        session.add(_make_trace())
        session.commit()
        repo = SpanRepository(session)
        repo.create_many(
            [
                _make_span("span-2", monotonic_ns=20),
                _make_span("span-1", monotonic_ns=10),
            ]
        )

        assert [s.span_id for s in repo.list_by_trace_id("trace-1")] == ["span-1", "span-2"]


def test_list_by_trace_id_filters_by_channel(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        session.add(_make_trace())
        session.commit()
        repo = SpanRepository(session)
        repo.create_many([_make_span("span-1", channel="trace"), _make_span("span-2", channel="snapshot")])

        assert [s.span_id for s in repo.list_by_trace_id("trace-1", channel="snapshot")] == ["span-2"]


def test_list_unprocessed_for_trace_excludes_already_processed_and_other_channels(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        session.add(_make_trace())
        session.commit()
        repo = SpanRepository(session)
        repo.create_many(
            [
                _make_span("span-1", channel="snapshot", recorded_at_ns=1),
                _make_span("span-2", channel="snapshot", recorded_at_ns=2, processed_at="done"),
                _make_span("span-3", channel="other", recorded_at_ns=3),
            ]
        )

        assert [s.span_id for s in repo.list_unprocessed_for_trace("trace-1", "snapshot")] == ["span-1"]


def test_list_unprocessed_for_trace_respects_limit(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        session.add(_make_trace())
        session.commit()
        repo = SpanRepository(session)
        repo.create_many([_make_span(f"span-{i}", channel="snapshot", recorded_at_ns=i) for i in range(3)])

        assert len(repo.list_unprocessed_for_trace("trace-1", "snapshot", limit=2)) == 2


def test_mark_processed_sets_processed_at_and_is_a_noop_for_empty_input(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        session.add(_make_trace())
        session.commit()
        repo = SpanRepository(session)
        repo.create(_make_span("span-1"))

        repo.mark_processed([])
        assert repo.get("span-1").processed_at is None  # type: ignore[union-attr]

        repo.mark_processed(["span-1"])
        session.expire_all()
        assert repo.get("span-1").processed_at is not None  # type: ignore[union-attr]


def test_find_latest_by_nanobar_type_returns_the_most_recent_match(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        session.add(_make_trace())
        session.commit()
        repo = SpanRepository(session)
        repo.create_many(
            [
                _make_span(
                    "span-1", channel="trace", recorded_at_ns=1, payload_json={"nanobar_type": "worker-response"}
                ),
                _make_span(
                    "span-2", channel="trace", recorded_at_ns=5, payload_json={"nanobar_type": "worker-response"}
                ),
                _make_span("span-3", channel="trace", recorded_at_ns=3, payload_json={"nanobar_type": "api-response"}),
            ]
        )

        found = repo.find_latest_by_nanobar_type("trace", "worker-response")
        assert found is not None
        assert found.span_id == "span-2"


def test_find_latest_by_nanobar_type_returns_none_when_no_match(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        repo = SpanRepository(session)
        assert repo.find_latest_by_nanobar_type("trace", "does-not-exist") is None


def test_distinct_facets_returns_sorted_nanobar_types_and_components(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        session.add(_make_trace())
        session.commit()
        repo = SpanRepository(session)
        repo.create_many(
            [
                _make_span(
                    "span-1",
                    channel="trace",
                    recorded_at_ns=1,
                    payload_json={"nanobar_type": "api-response", "name": "GET /x"},
                ),
                _make_span(
                    "span-2",
                    channel="trace",
                    recorded_at_ns=2,
                    payload_json={"nanobar_type": "worker-response", "name": "worker.publish.process"},
                ),
            ]
        )

        nanobar_types, components = repo.distinct_facets("trace")
        assert nanobar_types == ["api-response", "worker-response"]
        assert components == ["api:GET /x", "worker:publish"]


def test_distinct_facets_respects_recorded_at_window(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        session.add(_make_trace())
        session.commit()
        repo = SpanRepository(session)
        repo.create_many(
            [
                _make_span("span-1", channel="trace", recorded_at_ns=1, payload_json={"nanobar_type": "type-a"}),
                _make_span("span-2", channel="trace", recorded_at_ns=100, payload_json={"nanobar_type": "type-b"}),
            ]
        )

        nanobar_types, _ = repo.distinct_facets("trace", created_after_ns=50)
        assert nanobar_types == ["type-b"]
        nanobar_types, _ = repo.distinct_facets("trace", created_before_ns=50)
        assert nanobar_types == ["type-a"]


def test_distinct_facets_empty_when_no_spans(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))
    with Session() as session:
        repo = SpanRepository(session)
        assert repo.distinct_facets("trace") == ([], [])
