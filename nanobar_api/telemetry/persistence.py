"""Telemetry domain's own SQLAlchemy declarative base + session-factory builder --
`nanobar_api/telemetry/model.py`'s `Trace`/`Span` tables. Deliberately a **separate** `Base`/
engine/database file from `nanobar_api/persistence.py`'s (`regression_bricks.db`) -- per
`.focusari/telemetry-domain-refactor-plan-with-tasks.md` Decision 6, trace/span capture gets its
own physical database (`nanobar_api_telemetry.db`), not shared with `RegressionBrick`/`Nanobar`,
so it can be archived/retained independently and eventually moved to a genuinely separate DB
instance without touching that data. `RegressionBrick.span_id` (a real column, not a foreign key
-- see `regression_brick/model.py`) references a `Span` row here via a second, separate query,
never a SQL join across the two files.

**Deliberately does NOT install `nanobar_api.persistence`'s `BEGIN IMMEDIATE`-per-transaction
listener, unlike `regression_bricks.db`'s engine.** That listener exists there to make
`NanobarRepository.get_or_create_by_route_key` race-free against *many concurrent HTTP requests*
calling it at once. This domain has no such writer -- Decision 4's whole point is exactly one
designated worker (`telemetry_drain_worker.py`) processing events strictly sequentially, so
`TraceRepository.get_or_create`'s own select-then-insert can never interleave with itself.
Installing it anyway actively hurts a real, expected access pattern this domain *does* have: the
drain worker writing while something else reads concurrently (an admin dashboard route, Phase 6;
a test polling for results) -- confirmed live: a background worker holding a session open while a
second session on the same engine tried to read raised `sqlite3.OperationalError: database is
locked`, because `BEGIN IMMEDIATE` claims SQLite's write lock even for a plain read. Ordinary
SQLAlchemy deferred-`BEGIN` transactions are correct here; forcing every transaction to grab the
write lock up front was solving a race this engine doesn't have, at the cost of one it does.
"""

from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from nanobar_api.orm import build_engine_url


class Base(DeclarativeBase):
    pass


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection: object, connection_record: object) -> None:
    """Same per-connection PRAGMA requirement as `nanobar_api.persistence`'s identically-named
    listener (SQLite disables FK enforcement by default, and it must be set per-connection, not
    once per file) -- duplicated here, not imported, so this module stays independently usable
    even in a process that never imports `nanobar_api.persistence` at all (Decision 6's own
    "distribute the DB instances later" goal implies this domain shouldn't need that module to
    function). Registering the identical PRAGMA twice against the `Engine` class (if both modules
    get imported in the same process) is a harmless no-op, not a conflict -- it just re-runs the
    same idempotent statement on connect.
    """
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def build_session_factory(db_path: str) -> sessionmaker[Session]:
    """Creates the engine, creates the schema idempotently (`Base.metadata.create_all` is a
    no-op against tables that already exist), and returns a `sessionmaker` -- same overall shape
    as `nanobar_api.persistence.build_session_factory`, with one deliberate difference: **no
    `NanobarORMWrapper.install()` call.** That hook (wired on the `regression_bricks.db` engine)
    captures every DB write as a new `"snapshot"`-channel event fed back into the very
    `EventQueueRepository` this domain's own drain worker (`telemetry_drain_worker.py`, Phase 5)
    consumes to produce new `Span` rows. Installing it here would make every `Trace`/`Span` write
    recursively produce another captured event destined to become another `Span` row -- an
    unbounded feedback loop, not a bug in the existing hook (which does exactly its documented
    job for `RegressionBrick`/`Nanobar`, where DB writes really are evidence *of* application
    behavior worth capturing). This domain's own writes are the sink at the end of that pipeline,
    not another source for it.
    """
    engine = create_engine(build_engine_url(db_path))
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)
