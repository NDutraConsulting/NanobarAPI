from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from nanobar_api.telemetry.model import Span, Trace
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


def test_build_session_factory_creates_schema(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))

    with Session() as session:
        session.add(_make_trace())
        session.commit()
        assert session.query(Trace).count() == 1


def test_foreign_key_integrity_enforced_on_insert(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))

    with Session() as session:
        session.add(_make_span(trace_id="trace-does-not-exist"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_span_cascade_deletes_when_its_trace_is_deleted(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"))

    with Session() as session:
        trace = _make_trace()
        session.add(trace)
        session.add(_make_span())
        session.commit()

        session.delete(trace)
        session.commit()

        assert session.query(Span).count() == 0


def test_schema_creation_is_idempotent(tmp_path: Path) -> None:
    """Calling `build_session_factory` twice against the same file (a second process/worker, a
    test re-running) must not fail -- `Base.metadata.create_all` is a no-op against an
    already-initialized database."""
    db_path = str(tmp_path / "test.db")
    build_session_factory(db_path)
    Session = build_session_factory(db_path)

    with Session() as session:
        session.add(_make_trace())
        session.commit()
        assert session.query(Trace).count() == 1


def test_a_second_session_can_read_while_the_first_holds_an_open_write_session(tmp_path: Path) -> None:
    """Regression test for a real deadlock this module's own `BEGIN IMMEDIATE`-per-transaction
    listener (since removed -- see the module docstring) caused: with that listener installed, a
    long-lived writer session (e.g. `telemetry_drain_worker.py`'s own worker-thread session) left
    open while a second session tried to read raised `sqlite3.OperationalError: database is
    locked`, because `BEGIN IMMEDIATE` claims SQLite's write lock even for a plain read. Ordinary
    deferred-`BEGIN` transactions don't have this problem."""
    Session = build_session_factory(str(tmp_path / "test.db"))

    writer_session = Session()
    writer_session.add(_make_trace())
    writer_session.commit()

    reader_session = Session()
    try:
        assert reader_session.query(Trace).count() == 1
    finally:
        reader_session.close()
        writer_session.close()
