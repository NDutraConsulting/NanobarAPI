"""Resolves the blog domain's SQLite database path and builds its SQLAlchemy engine/session
factory -- wiring in `NanobarORMWrapper.install()`, which (per `.focusari/2026-08-25.2100-
agent-context.md`'s deferred list) had no production call site anywhere in this codebase before
this domain. DB-boundary capture for every blog query now flows through the same
`EventQueueRepository` the rest of the app already uses for telemetry, exactly as designed.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.blog_model import Base
from nanobar_api.eventbus.queue_repository import EventQueueRepository
from nanobar_api.orm import NanobarORMWrapper, build_engine_url

#: Default location: alongside this module itself (`app/db/blog.db`), not a shared data
#: directory -- gitignored.
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "blog.db"

#: Environment variable used to override DEFAULT_DB_PATH.
DB_PATH_ENV_VAR = "NANOBAR_BLOG_DB"


def resolve_db_path() -> str:
    """Returns the configured blog db path: env var if set, else the default. The parent
    directory is created first, matching every other `resolve_db_path()` in this package."""
    db_path = os.environ.get(DB_PATH_ENV_VAR, str(DEFAULT_DB_PATH))
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return db_path


def build_session_factory(db_path: str, *, repository: EventQueueRepository) -> sessionmaker[Session]:
    """Creates the engine, installs DB-boundary capture on it, creates the schema idempotently
    (`Base.metadata.create_all` is itself a no-op against tables that already exist, matching
    every other `connect()` in this codebase), and returns a `sessionmaker` -- callers open one
    `Session` per request/unit of work, the standard SQLAlchemy pattern, not a shared connection
    held across requests.

    `db_path` may be a bare local file path (the common case) or a full connection URL (e.g. a
    remote `postgresql://...` target from a shadow-persistence profile's `connection_secret_ref`
    -- see `app/admin/nanobar/replay_app.py`) -- `build_engine_url()` normalizes either into a
    real SQLAlchemy engine URL.
    """
    engine = create_engine(build_engine_url(db_path))
    NanobarORMWrapper.install(engine, repository)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)
