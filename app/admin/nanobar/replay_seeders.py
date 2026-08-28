"""Registry of replay-time shadow-state seeders, keyed by `route_key` -- see
`app/db/blog_seeders.py`'s own module docstring for why these exist and the paired seed/teardown
contract each one must uphold. Lives under `app/admin/nanobar/` (not `app/db/`, where the
concrete seeders themselves live) since this is dispatch wiring for `replay_brick_action`
(`app/admin/nanobar/api.py`), the one real per-replay call site, plus `build_app()`'s own startup
sweep call -- same "small, single-purpose module" convention as this package's own
`generate_bricks.py`/`nanobar_refresh.py`.

`seed_for_replay()` durably logs every seed it makes (`shadow_seed_log.py`) before returning
control to its caller, and logs the matching teardown once *that* runs -- transparent to
individual seeders (`app/db/blog_seeders.py`), which only ever report what they did via
`SeedResult`, never touch the log themselves. This is what makes `sweep_stale_shadow_seeds()`
possible: a crash between a real seed and its own synchronous teardown leaves a `shadow_seeds`
row with `torn_down_at IS NULL`, which the next app startup (not a background worker -- see
`shadow_seed_log.py`'s own module docstring for why startup is sufficient) can find and clean up
using nothing but `domain`/`resource_id`, resolved back to an actual delete via
`SWEEP_HANDLERS`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from app.admin.nanobar import shadow_seed_log
from app.db.blog_seeders import delete_blog_post, seed_post_for_update

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from app.db.blog_seeders import SeedResult
    from nanobar_api.regression_brick.model import RegressionBrick

Seeder = Callable[["RegressionBrick", "sessionmaker[Session]"], "SeedResult | None"]

#: `route_key` (the exact `NanobarRouteRule.key`/`request_type` string every capture_layer()-
#: sourced brick stamps onto `brick.source["route_key"]`) -> the seeder that ensures this
#: brick's replay has whatever pre-existing shadow-db state it depends on. A route_key with no
#: entry here is assumed to need no seeding (the common case -- most routes, and every
#: SnapshotMiddleware-sourced brick, either create their own state or don't depend on any).
REPLAY_SEEDERS: dict[str, Seeder] = {
    "POST /admin/app/api/posts/{post_id}": seed_post_for_update,
}

#: `SeedResult.domain` -> a handler that deletes a row of that kind, given its `resource_id` --
#: consulted only by `sweep_stale_shadow_seeds()` below, to recover a crash-leaked seed using
#: nothing but what `shadow_seed_log.db` itself stored (a seeder function object can't be
#: serialized into a log row; a stable domain string can).
SWEEP_HANDLERS: dict[str, Callable[[str, sessionmaker[Session]], None]] = {
    "blog_post": delete_blog_post,
}


def seed_for_replay(
    brick: RegressionBrick, blog_shadow_session_factory: sessionmaker[Session]
) -> Callable[[], None] | None:
    """Returns a zero-arg teardown callable if seeding actually created something (the caller
    must call it once the replay is done, in a `finally`), or `None` if there's nothing to tear
    down: either the brick's `route_key` has no registered seeder, it has no `route_key` at all
    (a `SnapshotMiddleware`-sourced brick never stamps one), or the seeder found its target state
    already present (not this call's to clean up).

    Every real seed is durably logged before this returns, and its teardown logged once the
    caller actually runs it -- see this module's own docstring for why."""
    route_key = brick.source.get("route_key")
    if not route_key:
        return None
    seeder = REPLAY_SEEDERS.get(route_key)
    if seeder is None:
        return None

    seed_result = seeder(brick, blog_shadow_session_factory)
    if seed_result is None:
        return None

    log_conn = shadow_seed_log.connect(shadow_seed_log.resolve_db_path())
    try:
        log_id = shadow_seed_log.record_seed(
            log_conn, domain=seed_result.domain, resource_id=seed_result.resource_id, route_key=route_key
        )
    finally:
        log_conn.close()

    def _logged_teardown() -> None:
        seed_result.teardown()
        conn = shadow_seed_log.connect(shadow_seed_log.resolve_db_path())
        try:
            shadow_seed_log.record_teardown(conn, log_id)
        finally:
            conn.close()

    return _logged_teardown


def sweep_stale_shadow_seeds(blog_shadow_session_factory: sessionmaker[Session]) -> int:
    """Called once, at app startup (`build_app()`) -- recovers any shadow-db row a prior
    process's crash left seeded (see `shadow_seed_log.py`'s own module docstring for why startup,
    not a background worker, is sufficient here). A `domain` with no `SWEEP_HANDLERS` entry is
    skipped (its underlying row is left in place -- nothing safe to do without knowing how to
    delete it) but still marked torn down in the log, so a startup sweep can't loop forever
    re-discovering the same unhandled entry on every future launch. Returns how many pending
    entries were found, for `build_app()`'s own startup reporting.
    """
    log_conn = shadow_seed_log.connect(shadow_seed_log.resolve_db_path())
    try:
        pending = shadow_seed_log.list_pending(log_conn)
        for row in pending:
            handler = SWEEP_HANDLERS.get(row["domain"])
            if handler is not None:
                handler(row["resource_id"], blog_shadow_session_factory)
            shadow_seed_log.record_teardown(log_conn, row["id"])
        return len(pending)
    finally:
        log_conn.close()
