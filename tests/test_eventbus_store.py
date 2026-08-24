from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from nanobar_api.eventbus.events import Event
from nanobar_api.eventbus.store import (
    connect,
    get_events_by_trace_id,
    get_unprocessed,
    insert_events,
    list_trace_ids,
    mark_processed,
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


def test_list_trace_ids_respects_limit(tmp_path: Path) -> None:
    db_path = str(tmp_path / "events.db")
    conn = connect(db_path)
    try:
        insert_events(conn, [_make_event(f"evt-{i}", "trace", trace_id=f"tr-{i}") for i in range(5)])

        summaries = list_trace_ids(conn, "trace", limit=2)

        assert len(summaries) == 2
    finally:
        conn.close()


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
