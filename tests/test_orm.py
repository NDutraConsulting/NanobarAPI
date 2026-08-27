from __future__ import annotations

from sqlalchemy import create_engine, text

from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.middleware.trace import current_route_key
from nanobar_api.orm import NanobarORMWrapper, build_engine_url


def _repository() -> EventQueueRepository:
    return EventQueueRepository([ChannelConfig(name="snapshot")])


def test_successful_query_captures_orm_brick_with_route_key() -> None:
    repository = _repository()
    engine = create_engine("sqlite://")
    NanobarORMWrapper.install(engine, repository)

    token = current_route_key.set("POST /orders")
    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY)"))
            conn.commit()
    finally:
        current_route_key.reset(token)

    event = repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None
    assert event.payload["nanobar_type"] == "orm-request-response"
    assert event.payload["route_key"] == "POST /orders"
    assert event.payload["error"] is False
    assert event.payload["request"]["statement"] == "CREATE TABLE t (id INTEGER PRIMARY KEY)"
    assert event.payload["response"]["error_type"] is None


def test_query_without_ambient_route_key_omits_it() -> None:
    repository = _repository()
    engine = create_engine("sqlite://")
    NanobarORMWrapper.install(engine, repository)

    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY)"))
        conn.commit()

    event = repository.get_any(["snapshot"], timeout=1.0)
    assert event is not None
    assert "route_key" not in event.payload


def test_bind_parameter_values_are_never_captured() -> None:
    repository = _repository()
    engine = create_engine("sqlite://")
    NanobarORMWrapper.install(engine, repository)

    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE t (id INTEGER, secret TEXT)"))
        conn.execute(text("INSERT INTO t VALUES (:id, :secret)"), {"id": 1, "secret": "sensitive-value"})
        conn.commit()

    events = []
    while (event := repository.get_any(["snapshot"], timeout=0.2)) is not None:
        events.append(event)

    for event in events:
        assert "parameters" not in event.payload["request"]
        assert "sensitive-value" not in str(event.payload)


def test_install_twice_on_the_same_engine_does_not_double_register_listeners() -> None:
    repository = _repository()
    engine = create_engine("sqlite://")
    NanobarORMWrapper.install(engine, repository)
    NanobarORMWrapper.install(engine, repository)  # must be a no-op, not a second listener pair

    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY)"))
        conn.commit()

    events = []
    while (event := repository.get_any(["snapshot"], timeout=0.2)) is not None:
        events.append(event)

    # One statement executed -> exactly one captured event, not two.
    assert len(events) == 1


def test_failed_query_captures_error_brick_classified_by_exception_type() -> None:
    repository = _repository()
    engine = create_engine("sqlite://")
    NanobarORMWrapper.install(engine, repository)

    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY)"))
        conn.execute(text("INSERT INTO t VALUES (1)"))
        conn.commit()
        try:
            conn.execute(text("INSERT INTO t VALUES (1)"))
        except Exception:
            pass

    events = []
    while (event := repository.get_any(["snapshot"], timeout=0.2)) is not None:
        events.append(event)

    error_events = [e for e in events if e.payload["error"] is True]
    assert len(error_events) == 1
    assert error_events[0].payload["response"]["error_type"] == "IntegrityError"
    assert error_events[0].payload["request"]["statement"] == "INSERT INTO t VALUES (1)"


def test_build_engine_url_wraps_a_bare_path_as_sqlite() -> None:
    assert build_engine_url("/tmp/blog.db") == "sqlite:////tmp/blog.db"


def test_build_engine_url_passes_through_an_existing_url_unchanged() -> None:
    assert build_engine_url("postgresql://user:pass@host/db") == "postgresql://user:pass@host/db"
