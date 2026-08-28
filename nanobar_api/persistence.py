"""Shared SQLAlchemy declarative base + session-factory builder for the `RegressionBrick` and
`Nanobar` entities (`nanobar_api/regression_brick/`, `nanobar_api/nanobar/`) -- framework-level
shared infrastructure, same category as `nanobar_api/eventbus/`/`nanobar_api/capture/` (see
`.focusari/regression-brick-refactor-plan-with-tasks.md`'s "Cross-cutting, stays
framework-level" section), not owned by either entity.

**One shared `Base`/engine/session, not two independent ones.** The two entities' tables have
real foreign-key relationships to each other (`nanobar_regression_bricks` references both
`nanobars.nanobar_id` and `regression_bricks.regression_brick_id`) -- SQLite only enforces those
constraints within one engine whose connections all set `PRAGMA foreign_keys = ON`, so both
tables must be registered against the same metadata and created in the same database file.
"Independent entities, independent stacks" (per the refactor plan) means independent
repository/service/controller/validator_gate layers, not necessarily independent physical
database files -- the same way `app/models/blog_model.py` imports SQLAlchemy's
`DeclarativeBase` from a shared library dependency without that making
`Post`/`Appointment`/`Notification` any less independently owned. Deletion between the two is
soft, not FK-cascaded/restricted (`.focusari/complete/adr/data-retention-adr.md` §4; see
`nanobar_api/nanobar/model.py`'s `NanobarBrickBinding` docstring for why the old `ON DELETE
CASCADE`/`ON DELETE RESTRICT` actions were removed).

**The `regression_bricks_are_immutable` trigger** (`nanobar_api/bricks/schema.py`'s old
`TRIGGER_SQL`) has no first-class SQLAlchemy equivalent -- SQLAlchemy has no declarative trigger
API, so this still runs as raw DDL, executed once against the engine right after
`Base.metadata.create_all()`, same as it always was under raw `sqlite3`. This preserves the
exact same DB-level enforcement (a real `sqlite3.IntegrityError`/SQLAlchemy `IntegrityError` on
any `UPDATE regression_bricks`), not merely an ORM-level convention that a caller could bypass
with a raw connection.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from nanobar_api.eventbus.queue_repository import EventQueueRepository
from nanobar_api.orm import NanobarORMWrapper, build_engine_url


class Base(DeclarativeBase):
    pass


_IMMUTABILITY_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS regression_bricks_are_immutable
BEFORE UPDATE ON regression_bricks
BEGIN
    SELECT RAISE(ABORT, 'RegressionBricks are immutable; fork a new brick');
END;
"""


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection: object, connection_record: object) -> None:
    """SQLite disables foreign-key enforcement by default, and unlike most other PRAGMAs it must
    be set on every new connection, not once per database file -- a connection-pool event
    listener (not a one-time call after `create_engine()`) is the only way to guarantee this for
    every connection SQLAlchemy ever opens, including ones opened later from the pool. Scoped to
    the `Engine` class globally (not one specific engine instance) since every engine this
    process creates is SQLite-backed and wants this; harmless no-op cost for any that isn't --
    unlike `_install_atomic_write_transactions` below, this never emits a statement through
    SQLAlchemy's own execution pipeline, so it can't leak into another engine's ORM capture.
    """
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _install_atomic_write_transactions(engine: Engine) -> None:
    """Every transaction on `engine` claims SQLite's write lock up front (`BEGIN IMMEDIATE`), not
    lazily on first write (`sqlite3`'s/SQLAlchemy's default deferred `BEGIN`) -- the same
    atomic-claim discipline `nanobar_api/eventbus/store.py`'s `claim_events()` established
    directly, generalized here so any repository's select-then-insert (e.g.
    `NanobarRepository.get_or_create_by_route_key`'s route-key claim) is race-free against a
    concurrent transaction on this engine without needing its own manual `BEGIN IMMEDIATE` call.

    **Deliberately scoped to this one `engine` instance, not the `Engine` class globally** (unlike
    `_enable_sqlite_foreign_keys` above) -- confirmed live: a class-wide `"begin"` listener that
    emits `BEGIN IMMEDIATE` through SQLAlchemy's normal `Connection.exec_driver_sql` leaks into
    *every* SQLite engine's own `NanobarORMWrapper`/ORM-capture instrumentation as a spurious
    captured statement (broke `tests/test_orm.py`'s exact-statement assertions), not just this
    module's own engine. Scoping to the instance avoids that blast radius entirely.

    Requires pysqlite's own implicit transaction handling disabled (`isolation_level = None`,
    i.e. driver-level autocommit) -- SQLAlchemy's documented recipe for getting real `BEGIN
    IMMEDIATE` transactions out of pysqlite; without it, pysqlite silently opens its own deferred
    transaction on the first DML statement, conflicting with the explicit `BEGIN IMMEDIATE` below.
    The `BEGIN IMMEDIATE` itself is issued through a raw DBAPI cursor obtained straight from the
    pooled connection (same as `_enable_sqlite_foreign_keys`'s PRAGMA above), not
    `Connection.exec_driver_sql` -- bypassing SQLAlchemy's execution pipeline is what keeps it
    out of ORM capture, not just out of other engines.
    """

    @event.listens_for(engine, "connect")
    def _disable_pysqlite_implicit_transactions(dbapi_connection: object, connection_record: object) -> None:
        dbapi_connection.isolation_level = None  # type: ignore[attr-defined]

    @event.listens_for(engine, "begin")
    def _begin_immediate(conn: Any) -> None:
        cursor = conn.connection.dbapi_connection.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        cursor.close()


def _ensure_all_entity_models_registered() -> None:
    """SQLAlchemy's declarative `Base.metadata` only knows about a class once it's actually been
    imported (import is what runs the class body and registers the table) -- `create_all()`
    below would silently create only *some* tables if a caller happened to import just one
    entity's `model` module first (confirmed live: `nanobar_regression_bricks`' FK to
    `regression_bricks` fails with `NoReferencedTableError` if `regression_brick.model` was
    never imported). Deferred import (not a module-level one) since both entity `model.py`
    modules import `Base` from *this* module -- a top-level import here would be circular.
    """
    import nanobar_api.nanobar.model
    import nanobar_api.regression_brick.model  # noqa: F401


def build_session_factory(db_path: str, *, repository: EventQueueRepository) -> sessionmaker[Session]:
    """Creates the engine, installs DB-boundary capture on it, creates the schema idempotently
    (`Base.metadata.create_all` is a no-op against tables that already exist), installs the
    `RegressionBrick` immutability trigger, and returns a `sessionmaker` -- same shape as
    `app.db.blog_session.build_session_factory`, shared by both `regression_brick/` and
    `nanobar/`'s repositories since they persist to the same database file.
    """
    _ensure_all_entity_models_registered()
    engine = create_engine(build_engine_url(db_path))
    _install_atomic_write_transactions(engine)
    NanobarORMWrapper.install(engine, repository)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql(_IMMUTABILITY_TRIGGER_SQL)
    return sessionmaker(bind=engine)
