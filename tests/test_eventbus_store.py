from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from nanobar_api.eventbus.events import Event
from nanobar_api.eventbus.store import (
    ack_event,
    claim_events,
    component_span_name,
    connect,
    count_trace_ids,
    derive_component,
    fail_event,
    find_latest_span_by_nanobar_type,
    get_events_by_trace_id,
    get_trace_facets,
    get_unprocessed,
    heartbeat,
    insert_events,
    list_stale_workers,
    list_trace_ids,
    list_workers,
    mark_processed,
    register_worker,
)


def _make_event(
    event_id: str,
    channel: str = "ch1",
    trace_id: str | None = "trace-1",
    span_id: str = "span-1",
    recorded_at_ns: int = 1_000,
    monotonic_ns: int = 2_000,
    payload: dict[str, object] | None = None,
) -> Event:
    return Event(
        event_id=event_id,
        channel=channel,
        recorded_at_ns=recorded_at_ns,
        monotonic_ns=monotonic_ns,
        payload=payload if payload is not None else {"key": "value", "event_id": event_id},
        trace_id=trace_id,
        span_id=span_id,
    )


def test_connect_creates_schema_idempotently(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")

    conn1 = connect(db_path)
    conn1.close()
    conn2 = connect(db_path)  # must not error on second call
    conn2.close()


def test_connect_sets_wal_mode(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")

    conn = connect(db_path)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
    finally:
        conn.close()


def test_insert_events_persists_rows(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        events = [_make_event("evt-1"), _make_event("evt-2"), _make_event("evt-3")]

        insert_events(conn, events)

        rows = conn.execute(
            "SELECT event_id, channel, trace_id, span_id, recorded_at_ns, monotonic_ns, payload_json "
            "FROM events ORDER BY event_id"
        ).fetchall()
        assert [row[0] for row in rows] == ["evt-1", "evt-2", "evt-3"]
        for row, event in zip(rows, events, strict=True):
            event_id, channel, trace_id, span_id, recorded_at_ns, monotonic_ns, payload_json = row
            assert event_id == event.event_id
            assert channel == event.channel
            assert trace_id == event.trace_id
            assert span_id == event.span_id
            assert recorded_at_ns == event.recorded_at_ns
            assert monotonic_ns == event.monotonic_ns
            assert json.loads(payload_json) == event.payload

        # Defaults for columns this function does not touch.
        defaults = conn.execute(
            "SELECT processed_at, attempt_count, last_error, claimed_by, lease_expires_at FROM events"
        ).fetchall()
        for processed_at, attempt_count, last_error, claimed_by, lease_expires_at in defaults:
            assert processed_at is None
            assert attempt_count == 0
            assert last_error is None
            assert claimed_by is None
            assert lease_expires_at is None
    finally:
        conn.close()


def test_insert_events_empty_list_is_noop(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(conn, [])

        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert count == 0
    finally:
        conn.close()


def test_insert_events_rolls_back_whole_batch_on_failure(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        duplicate_id_events = [_make_event("evt-a"), _make_event("evt-a")]

        with pytest.raises(sqlite3.IntegrityError):
            insert_events(conn, duplicate_id_events)

        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert count == 0
    finally:
        conn.close()


def test_two_connections_on_same_path_can_read_and_write(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")

    writer_conn = connect(db_path)
    reader_conn = connect(db_path)
    try:
        insert_events(writer_conn, [_make_event("evt-writer")])

        # A second connection opened via connect() on the same path can see the write.
        row = reader_conn.execute("SELECT event_id FROM events WHERE event_id = ?", ("evt-writer",)).fetchone()
        assert row is not None
        assert row[0] == "evt-writer"

        # And can also write itself.
        insert_events(reader_conn, [_make_event("evt-reader")])
        row2 = writer_conn.execute("SELECT event_id FROM events WHERE event_id = ?", ("evt-reader",)).fetchone()
        assert row2 is not None
        assert row2[0] == "evt-reader"
    finally:
        writer_conn.close()
        reader_conn.close()


def test_get_unprocessed_returns_only_unprocessed_rows_in_channel(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(conn, [_make_event("evt-1", "snapshot"), _make_event("evt-2", "snapshot")])
        insert_events(conn, [_make_event("evt-other", "trace")])
        mark_processed(conn, ["evt-1"])

        unprocessed = get_unprocessed(conn, "snapshot")

        assert [event.event_id for event in unprocessed] == ["evt-2"]
        assert unprocessed[0].payload == {"key": "value", "event_id": "evt-2"}
    finally:
        conn.close()


def test_get_unprocessed_respects_limit(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(conn, [_make_event(f"evt-{i}", "snapshot") for i in range(5)])

        unprocessed = get_unprocessed(conn, "snapshot", limit=2)

        assert len(unprocessed) == 2
    finally:
        conn.close()


def test_mark_processed_empty_list_is_noop(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(conn, [_make_event("evt-1", "snapshot")])

        mark_processed(conn, [])

        assert get_unprocessed(conn, "snapshot") != []
    finally:
        conn.close()


def test_list_trace_ids_groups_by_trace_and_orders_by_recency(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(
            conn,
            [
                _make_event("evt-1a", "trace", trace_id="tr-1", recorded_at_ns=100),
                _make_event("evt-1b", "trace", trace_id="tr-1", recorded_at_ns=200),
                _make_event("evt-2a", "trace", trace_id="tr-2", recorded_at_ns=150),
            ],
        )

        summaries = list_trace_ids(conn, "trace")

        assert [s.trace_id for s in summaries] == ["tr-1", "tr-2"]
        tr1 = summaries[0]
        assert tr1.span_count == 2
        assert tr1.first_recorded_at_ns == 100
        assert tr1.last_recorded_at_ns == 200
        assert tr1.any_error is False
    finally:
        conn.close()


def test_list_trace_ids_excludes_null_trace_id_and_other_channels(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(
            conn,
            [
                _make_event("evt-1", "trace", trace_id="tr-1"),
                _make_event("evt-2", "trace", trace_id=None),
                _make_event("evt-3", "snapshot", trace_id="tr-other"),
            ],
        )

        summaries = list_trace_ids(conn, "trace")

        assert [s.trace_id for s in summaries] == ["tr-1"]
    finally:
        conn.close()


def test_list_trace_ids_reports_any_error(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(
            conn,
            [
                _make_event("evt-1", "trace", trace_id="tr-err", payload={"error": False}),
                _make_event("evt-2", "trace", trace_id="tr-err", payload={"error": True}),
                _make_event("evt-3", "trace", trace_id="tr-ok", payload={"error": False}),
            ],
        )

        summaries = {s.trace_id: s for s in list_trace_ids(conn, "trace")}

        assert summaries["tr-err"].any_error is True
        assert summaries["tr-ok"].any_error is False
    finally:
        conn.close()


def test_list_trace_ids_respects_page_size(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(conn, [_make_event(f"evt-{i}", "trace", trace_id=f"tr-{i}") for i in range(5)])

        summaries = list_trace_ids(conn, "trace", page_size=2)

        assert len(summaries) == 2
    finally:
        conn.close()


def test_list_trace_ids_second_page_returns_the_next_slice(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        # recorded_at_ns ascending -> default ORDER BY last_recorded_at_ns DESC means tr-4 is
        # newest (page 1) and tr-0 is oldest (page 2, with page_size=4).
        insert_events(conn, [_make_event(f"evt-{i}", "trace", trace_id=f"tr-{i}", recorded_at_ns=i) for i in range(5)])

        page_1 = list_trace_ids(conn, "trace", page=1, page_size=4)
        page_2 = list_trace_ids(conn, "trace", page=2, page_size=4)

        assert [s.trace_id for s in page_1] == ["tr-4", "tr-3", "tr-2", "tr-1"]
        assert [s.trace_id for s in page_2] == ["tr-0"]
    finally:
        conn.close()


def test_list_trace_ids_filters_by_date_range(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(
            conn,
            [
                _make_event("evt-early", "trace", trace_id="tr-early", recorded_at_ns=100),
                _make_event("evt-mid", "trace", trace_id="tr-mid", recorded_at_ns=500),
                _make_event("evt-late", "trace", trace_id="tr-late", recorded_at_ns=900),
            ],
        )

        after_only = list_trace_ids(conn, "trace", created_after_ns=400)
        before_only = list_trace_ids(conn, "trace", created_before_ns=600)
        between = list_trace_ids(conn, "trace", created_after_ns=400, created_before_ns=600)

        assert {s.trace_id for s in after_only} == {"tr-mid", "tr-late"}
        assert {s.trace_id for s in before_only} == {"tr-early", "tr-mid"}
        assert {s.trace_id for s in between} == {"tr-mid"}
    finally:
        conn.close()


def test_list_trace_ids_filters_by_nanobar_type(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(
            conn,
            [
                _make_event("evt-a", "trace", trace_id="tr-a", payload={"nanobar_type": "controller-request-response"}),
                _make_event("evt-b", "trace", trace_id="tr-b", payload={"nanobar_type": "worker-snapshot"}),
                _make_event("evt-c", "trace", trace_id="tr-c", payload={}),
            ],
        )

        matched = list_trace_ids(conn, "trace", nanobar_types=["controller-request-response"])

        assert [s.trace_id for s in matched] == ["tr-a"]
    finally:
        conn.close()


def test_list_trace_ids_filters_by_component(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(
            conn,
            [
                _make_event(
                    "evt-a", "trace", trace_id="tr-a", payload={"name": "controller.POST /admin/app/api/posts"}
                ),
                _make_event("evt-b", "trace", trace_id="tr-b", payload={"name": "worker.worker-1.process"}),
            ],
        )

        matched = list_trace_ids(conn, "trace", components=["controller:POST /admin/app/api/posts"])

        assert [s.trace_id for s in matched] == ["tr-a"]
    finally:
        conn.close()


def test_count_trace_ids_matches_list_trace_ids_total_ignoring_page_size(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(conn, [_make_event(f"evt-{i}", "trace", trace_id=f"tr-{i}") for i in range(5)])

        total = count_trace_ids(conn, "trace")
        page = list_trace_ids(conn, "trace", page_size=2)

        assert total == 5
        assert len(page) == 2
    finally:
        conn.close()


def test_get_trace_facets_returns_distinct_nanobar_types_and_components(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(
            conn,
            [
                _make_event(
                    "evt-a",
                    "trace",
                    trace_id="tr-a",
                    payload={"name": "controller.POST /x", "nanobar_type": "controller-request-response"},
                ),
                _make_event(
                    "evt-b",
                    "trace",
                    trace_id="tr-a",
                    payload={"name": "controller.POST /x", "nanobar_type": "controller-request-response"},
                ),
                _make_event("evt-c", "trace", trace_id="tr-b", payload={"name": "worker.w1.process"}),
            ],
        )

        nanobar_types, components = get_trace_facets(conn, "trace")

        assert nanobar_types == ["controller-request-response"]
        assert components == ["controller:POST /x", "worker:w1"]
    finally:
        conn.close()


def test_list_trace_ids_ignores_a_component_filter_with_no_reconstructible_span_name(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(conn, [_make_event("evt-a", "trace", trace_id="tr-a", payload={"name": "controller.POST /x"})])

        matched = list_trace_ids(conn, "trace", components=["not-a-real-kind:whatever"])

        assert [s.trace_id for s in matched] == ["tr-a"]
    finally:
        conn.close()


def test_component_span_name_returns_none_for_an_unknown_kind() -> None:
    assert component_span_name("not-a-real-kind", "whatever") is None


def test_get_trace_facets_respects_a_date_window(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(
            conn,
            [
                _make_event("evt-early", "trace", trace_id="tr-early", recorded_at_ns=100, payload={"name": "a"}),
                _make_event("evt-late", "trace", trace_id="tr-late", recorded_at_ns=900, payload={"name": "b"}),
            ],
        )

        after_only = get_trace_facets(conn, "trace", created_after_ns=500)
        before_only = get_trace_facets(conn, "trace", created_before_ns=500)

        assert after_only[1] == ["other:b"]
        assert before_only[1] == ["other:a"]
    finally:
        conn.close()


def test_derive_component_and_component_span_name_round_trip() -> None:
    cases = [
        "GET /admin/app/dashboard",
        "controller.POST /admin/app/api/posts",
        "validator.POST /admin/app/api/posts",
        "service",
        "worker.worker-1.process",
        "event-callback.domain.appointments",
        "dashboard.nanobars.list",
    ]
    for span_name in cases:
        kind, name = derive_component(span_name)
        assert component_span_name(kind, name) == span_name


def test_get_events_by_trace_id_orders_by_monotonic_ns(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(
            conn,
            [
                _make_event("evt-late", "trace", trace_id="tr-1", monotonic_ns=300),
                _make_event("evt-early", "trace", trace_id="tr-1", monotonic_ns=100),
                _make_event("evt-mid", "trace", trace_id="tr-1", monotonic_ns=200),
            ],
        )

        events = get_events_by_trace_id(conn, "tr-1")

        assert [e.event_id for e in events] == ["evt-early", "evt-mid", "evt-late"]
    finally:
        conn.close()


def test_get_events_by_trace_id_filters_by_channel(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(
            conn,
            [
                _make_event("evt-trace", "trace", trace_id="tr-1"),
                _make_event("evt-snapshot", "snapshot", trace_id="tr-1"),
            ],
        )

        all_events = get_events_by_trace_id(conn, "tr-1")
        trace_only = get_events_by_trace_id(conn, "tr-1", channel="trace")

        assert {e.event_id for e in all_events} == {"evt-trace", "evt-snapshot"}
        assert [e.event_id for e in trace_only] == ["evt-trace"]
    finally:
        conn.close()


def test_get_events_by_trace_id_unknown_returns_empty(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        assert get_events_by_trace_id(conn, "does-not-exist") == []
    finally:
        conn.close()


def test_find_latest_span_by_nanobar_type_returns_the_most_recent_match(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(
            conn,
            [
                _make_event(
                    "evt-old",
                    channel="trace",
                    recorded_at_ns=1_000,
                    payload={"name": "old", "nanobar_type": "worker-domain.appointments"},
                ),
                _make_event(
                    "evt-new",
                    channel="trace",
                    recorded_at_ns=2_000,
                    payload={"name": "new", "nanobar_type": "worker-domain.appointments"},
                ),
                _make_event(
                    "evt-other-type",
                    channel="trace",
                    recorded_at_ns=3_000,
                    payload={"name": "other", "nanobar_type": "api-response"},
                ),
            ],
        )

        span = find_latest_span_by_nanobar_type(conn, "trace", "worker-domain.appointments")

        assert span is not None
        assert span.event_id == "evt-new"
    finally:
        conn.close()


def test_find_latest_span_by_nanobar_type_respects_channel(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(
            conn,
            [_make_event("evt-1", channel="other-channel", payload={"nanobar_type": "worker-x"})],
        )

        assert find_latest_span_by_nanobar_type(conn, "trace", "worker-x") is None
    finally:
        conn.close()


def test_find_latest_span_by_nanobar_type_returns_none_when_no_match(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(conn, [_make_event("evt-1", channel="trace", payload={"nanobar_type": "api-response"})])

        assert find_latest_span_by_nanobar_type(conn, "trace", "worker-unseen") is None
    finally:
        conn.close()


def test_claim_events_marks_rows_claimed_and_orders_by_recorded_at(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(
            conn,
            [
                _make_event("evt-2", recorded_at_ns=200),
                _make_event("evt-1", recorded_at_ns=100),
                _make_event("evt-3", recorded_at_ns=300),
            ],
        )

        claimed = claim_events(conn, "ch1", "worker-a", limit=2, lease_seconds=60.0)

        assert [e.event_id for e in claimed] == ["evt-1", "evt-2"]
        rows = conn.execute("SELECT event_id, claimed_by FROM events WHERE claimed_by IS NOT NULL").fetchall()
        assert {row[0] for row in rows} == {"evt-1", "evt-2"}
        assert all(row[1] == "worker-a" for row in rows)
    finally:
        conn.close()


def test_claim_events_does_not_reclaim_unexpired_lease(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(conn, [_make_event("evt-1")])
        claim_events(conn, "ch1", "worker-a", limit=10, lease_seconds=60.0)

        second_claim = claim_events(conn, "ch1", "worker-b", limit=10, lease_seconds=60.0)

        assert second_claim == []
    finally:
        conn.close()


def test_claim_events_reclaims_after_lease_expires(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(conn, [_make_event("evt-1")])
        claim_events(conn, "ch1", "worker-a", limit=10, lease_seconds=-1.0)

        reclaimed = claim_events(conn, "ch1", "worker-b", limit=10, lease_seconds=60.0)

        assert [e.event_id for e in reclaimed] == ["evt-1"]
    finally:
        conn.close()


def test_claim_events_skips_processed_rows(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(conn, [_make_event("evt-1")])
        mark_processed(conn, ["evt-1"])

        claimed = claim_events(conn, "ch1", "worker-a", limit=10, lease_seconds=60.0)

        assert claimed == []
    finally:
        conn.close()


def test_ack_event_sets_processed_and_clears_claim(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(conn, [_make_event("evt-1")])
        claim_events(conn, "ch1", "worker-a", limit=10, lease_seconds=60.0)

        ack_event(conn, "evt-1")

        row = conn.execute(
            "SELECT processed_at, claimed_by, lease_expires_at FROM events WHERE event_id = ?", ("evt-1",)
        ).fetchone()
        assert row[0] is not None
        assert row[1] is None
        assert row[2] is None
    finally:
        conn.close()


def test_fail_event_increments_attempt_count_and_releases_claim(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(conn, [_make_event("evt-1")])
        claim_events(conn, "ch1", "worker-a", limit=10, lease_seconds=60.0)

        fail_event(conn, "evt-1", "boom")

        row = conn.execute(
            "SELECT attempt_count, last_error, claimed_by, lease_expires_at FROM events WHERE event_id = ?",
            ("evt-1",),
        ).fetchone()
        assert row[0] == 1
        assert row[1] == "boom"
        assert row[2] is None
        assert row[3] is None

        reclaimable = claim_events(conn, "ch1", "worker-b", limit=10, lease_seconds=60.0)
        assert [e.event_id for e in reclaimable] == ["evt-1"]
    finally:
        conn.close()


def test_register_worker_inserts_then_upserts(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        register_worker(conn, "worker-a", ["ch1", "ch2"])
        row = conn.execute("SELECT channels FROM workers WHERE worker_id = ?", ("worker-a",)).fetchone()
        assert json.loads(row[0]) == ["ch1", "ch2"]

        register_worker(conn, "worker-a", ["ch3"])  # re-register under the same id
        rows = conn.execute("SELECT channels FROM workers WHERE worker_id = ?", ("worker-a",)).fetchall()
        assert len(rows) == 1
        assert json.loads(rows[0][0]) == ["ch3"]
    finally:
        conn.close()


def test_heartbeat_updates_last_heartbeat_at(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        register_worker(conn, "worker-a", ["ch1"])
        before = conn.execute("SELECT last_heartbeat_at FROM workers WHERE worker_id = ?", ("worker-a",)).fetchone()[0]

        heartbeat(conn, "worker-a")

        after = conn.execute("SELECT last_heartbeat_at FROM workers WHERE worker_id = ?", ("worker-a",)).fetchone()[0]
        assert after >= before
    finally:
        conn.close()


def test_register_worker_persists_optional_configuration_fields(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        register_worker(
            conn,
            "worker-a",
            ["ch1"],
            mode="cron",
            schedule="*/5 * * * *",
            poll_interval_s=2.5,
            claim_limit=20,
            lease_seconds=45.0,
        )

        row = conn.execute(
            "SELECT mode, schedule, poll_interval_s, claim_limit, lease_seconds FROM workers WHERE worker_id = ?",
            ("worker-a",),
        ).fetchone()

        assert tuple(row) == ("cron", "*/5 * * * *", 2.5, 20, 45.0)
    finally:
        conn.close()


def test_register_worker_configuration_fields_default_to_none(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        register_worker(conn, "worker-a", ["ch1"])

        row = conn.execute(
            "SELECT mode, schedule, poll_interval_s, claim_limit, lease_seconds FROM workers WHERE worker_id = ?",
            ("worker-a",),
        ).fetchone()

        assert tuple(row) == (None, None, None, None, None)
    finally:
        conn.close()


def test_list_workers_returns_everything_most_recently_alive_first(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        register_worker(conn, "worker-a", ["ch1"], mode="listening", poll_interval_s=1.0)
        register_worker(conn, "worker-b", ["ch2", "ch3"], mode="cron", schedule="0 * * * *")
        conn.execute(
            "UPDATE workers SET last_heartbeat_at = datetime('now', '-1 hour') WHERE worker_id = ?", ("worker-a",)
        )
        conn.commit()

        workers = list_workers(conn)

        assert [w.worker_id for w in workers] == ["worker-b", "worker-a"]
        worker_b = workers[0]
        assert worker_b.channels == ["ch2", "ch3"]
        assert worker_b.mode == "cron"
        assert worker_b.schedule == "0 * * * *"
        worker_a = workers[1]
        assert worker_a.poll_interval_s == 1.0
        assert worker_a.schedule is None
    finally:
        conn.close()


def test_list_stale_workers_returns_only_workers_past_staleness_threshold(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        register_worker(conn, "worker-fresh", ["ch1"])
        register_worker(conn, "worker-stale", ["ch1"])
        conn.execute(
            "UPDATE workers SET last_heartbeat_at = datetime('now', '-1 hour') WHERE worker_id = ?",
            ("worker-stale",),
        )
        conn.commit()

        stale = list_stale_workers(conn, staleness_seconds=60.0)

        assert stale == ["worker-stale"]
    finally:
        conn.close()


def test_claim_events_concurrent_workers_never_double_claim(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    setup_conn = connect(db_path)
    try:
        insert_events(setup_conn, [_make_event(f"evt-{i}") for i in range(20)])
    finally:
        setup_conn.close()

    claimed_ids: list[str] = []
    claimed_lock = threading.Lock()
    errors: list[BaseException] = []

    def _worker(worker_id: str) -> None:
        conn = connect(db_path)
        try:
            while True:
                claimed = claim_events(conn, "ch1", worker_id, limit=1, lease_seconds=60.0)
                if not claimed:
                    return
                with claimed_lock:
                    claimed_ids.extend(event.event_id for event in claimed)
        except BaseException as exc:  # pragma: no cover - surfaced via `errors` assertion below
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=_worker, args=(f"worker-{i}",)) for i in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert sorted(claimed_ids) == sorted(f"evt-{i}" for i in range(20))
    assert len(claimed_ids) == len(set(claimed_ids))


class _CommitFailingConnection:
    """Duck-typed `sqlite3.Connection` stand-in that fails `commit()` — used to exercise
    `claim_events`'s rollback-on-failure path without relying on a specific real SQLite error."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real
        self.rolled_back = False

    def execute(self, *args: object, **kwargs: object) -> sqlite3.Cursor:
        return self._real.execute(*args, **kwargs)  # type: ignore[arg-type]

    def commit(self) -> None:
        raise RuntimeError("boom")

    def rollback(self) -> None:
        self.rolled_back = True
        self._real.rollback()


def test_claim_events_rolls_back_on_failure(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    real_conn = connect(db_path)
    try:
        insert_events(real_conn, [_make_event("evt-1")])
        proxy = _CommitFailingConnection(real_conn)

        with pytest.raises(RuntimeError, match="boom"):
            claim_events(proxy, "ch1", "worker-a", limit=10, lease_seconds=60.0)  # type: ignore[arg-type]

        assert proxy.rolled_back is True
        row = real_conn.execute("SELECT claimed_by FROM events WHERE event_id = ?", ("evt-1",)).fetchone()
        assert row[0] is None
    finally:
        real_conn.close()
