"""Durable log of shadow-db state a regression-brick replay seeded, independent of whether the
process handling that replay lived long enough to run its own synchronous teardown -- resolves
the one real gap explicit seed()/teardown() pairing (`app/db/blog_seeders.py`) can't cover on its
own: a hard process crash/kill between seed and teardown leaks the seeded row forever, since no
`finally` survives that. This log makes that leak recoverable instead of permanent.

**Swept once, at the next app startup (`build_app()`, before it starts serving traffic) -- not by
a perpetually-running worker thread.** The only way a row can ever be left pending here is a crash
that ends the process outright (the normal path always reaches its own `record_teardown()` call,
synchronously, in the request handler's own `finally`) -- so the very next process's own startup
is already the first, and only, moment that could ever need to notice anything pending. A
background sweep worker would solve nothing a startup sweep doesn't, at the cost of a perpetually-
running thread for what should be a rare occurrence.

Own file/schema, same "small, purpose-specific SQLite file" convention every other db in this
project already follows (`nanobar_admin.db`, `events.db`, ...) -- deliberately not a new column
on `Post` (or any other real domain model): this is bookkeeping about *replay infrastructure*,
not application data, and shouldn't leak into a model real application code reads.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS shadow_seeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    route_key TEXT NOT NULL,
    seeded_at TEXT NOT NULL,
    torn_down_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_shadow_seeds_pending ON shadow_seeds(torn_down_at) WHERE torn_down_at IS NULL;
"""

#: Default location: `app/admin/nanobar/data/shadow_seed_log.db`, alongside the code that owns
#: it -- gitignored; may not exist yet or may be empty, and that's fine.
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "shadow_seed_log.db"

DB_PATH_ENV_VAR = "NANOBAR_SHADOW_SEED_LOG_DB"


def resolve_db_path() -> str:
    db_path = os.environ.get(DB_PATH_ENV_VAR, str(DEFAULT_DB_PATH))
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return db_path


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def record_seed(conn: sqlite3.Connection, *, domain: str, resource_id: str, route_key: str) -> int:
    """Logs one seeded row *before* the caller's own synchronous teardown ever gets a chance to
    run -- called right after the underlying shadow-db write succeeds, so a crash any time after
    this point (including during the replay itself) is what `list_pending()` exists to recover."""
    with conn:
        cursor = conn.execute(
            "INSERT INTO shadow_seeds (domain, resource_id, route_key, seeded_at) VALUES (?, ?, ?, ?)",
            (domain, resource_id, route_key, datetime.now(UTC).isoformat()),
        )
    return int(cursor.lastrowid)  # type: ignore[arg-type]


def record_teardown(conn: sqlite3.Connection, log_id: int) -> None:
    with conn:
        conn.execute("UPDATE shadow_seeds SET torn_down_at = ? WHERE id = ?", (datetime.now(UTC).isoformat(), log_id))


def list_pending(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every seed never torn down -- normally empty (the request-handler's own `finally` already
    tears down its own seed before this could ever observe it); a non-empty result here means a
    prior process crashed mid-replay. Returns full rows (`id`/`domain`/`resource_id`/`route_key`)
    so a caller can both delete the underlying shadow-db row and mark the log entry itself torn
    down."""
    return list(conn.execute("SELECT id, domain, resource_id, route_key FROM shadow_seeds WHERE torn_down_at IS NULL"))
