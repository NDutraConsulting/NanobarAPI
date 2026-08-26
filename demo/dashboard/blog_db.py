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

from nanobar_api.eventbus.queue_repository import EventQueueRepository
from nanobar_api.orm import NanobarORMWrapper

from .blog_models import Base

#: Default location, relative to this file, matching db.py/events_db.py/admin_db.py's own
#: convention (``demo/data/*.db``). ``demo/data/`` is gitignored.
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "blog.db"

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
    """
    engine = create_engine(f"sqlite:///{db_path}")
    NanobarORMWrapper.install(engine, repository)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)
