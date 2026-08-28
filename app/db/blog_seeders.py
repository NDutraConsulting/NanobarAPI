"""Regression-brick replay-time shadow-state seeders for the blog domain -- resolves the gap
`nanobar_api/framework/nanobar_api_state_machine.py`'s `NanobarAPIStateMachine` already named for
worker/event-to-subscriber replay ("the shadow db often needs to already look like it did at the
moment the original trigger fired... app-specific enough it shouldn't be guessed at"), just for
HTTP-shaped CRUD replay instead: a replayed "update"-style request depends on a row that only
exists in live data, which `blog_shadow_session_factory`'s shadow replica -- deliberately empty,
by design, so a replay never touches real local data -- never has.

A registered seeder (see `app/admin/nanobar/replay_seeders.py`) ensures that row exists in the
shadow db before dispatch, using only data already captured on the brick itself. Seeded content
never needs to be historically accurate -- the replay's own request immediately overwrites
whatever fields it's actually testing (see each seeder's own docstring for exactly which fields
that leaves undetermined, and why those happen to already match the model's own defaults).

**Every seeder returns a `SeedResult` pairing what it seeded with how to tear it down, matching
`NanobarAPIStateMachine.seed()`/`.teardown()`'s own paired contract** -- confirmed live, not
hypothetical: without a teardown, replaying N distinct bricks over time leaves N permanent rows
behind in what's supposed to be a disposable replica (a real bloat/leak, not just a naming
mismatch with "shadow"). `domain`/`resource_id` on the result are what
`app/admin/nanobar/replay_seeders.py` durably logs (`shadow_seed_log.py`) before running the
replay, so a crash between seeding and teardown is recoverable at next startup instead of a
permanent leak -- this module itself stays unaware that log exists; it only reports what it did.
A seeder that finds the row *already exists* (idempotent no-op) returns `None` -- it isn't this
call's to clean up, whether that row came from an earlier seed call or a real create that
happened to land in this same shadow db.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.crud.blog_crud import PostRepository

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session, sessionmaker

    from nanobar_api.regression_brick.model import RegressionBrick


@dataclass(frozen=True)
class SeedResult:
    #: A stable identifier for *what kind* of thing was seeded -- doubles as the lookup key into
    #: `app/admin/nanobar/replay_seeders.py`'s `SWEEP_HANDLERS`, so a startup sweep recovering a
    #: crash-leaked row (which only has this string + `resource_id` to go on, read back out of
    #: `shadow_seed_log.db`) knows which repository/table to delete it from.
    domain: str
    resource_id: str
    teardown: Callable[[], None]


def seed_post_for_update(brick: RegressionBrick, session_factory: sessionmaker[Session]) -> SeedResult | None:
    """Ensures a `Post` row with the brick's own captured `post_id` exists in the shadow db
    before an update-post replay dispatches, and returns a `SeedResult` pairing that id with a
    zero-arg callable that removes it again afterward -- `None` if this call found the row
    already present (nothing to tear down).

    A controller/service-layer brick's `.request` already embeds `post_id` under that exact
    field name -- this project's own validator convention (`UpdatePostGate.validate()`:
    `UpdatePostRequest(post_id=request.path_params["post_id"], ...)`,
    `app/validators/blog_validator_gateway.py`) -- the same convention
    `regression_brick_analysis_service.py`'s `_substitute_path_params()` already relies on to fix
    the *matching* replay-dispatch-side bug (the URL template's own `{post_id}` placeholder).

    Seeded `title`/`body` content is a placeholder, not a reconstruction of history -- the
    replay's own request immediately overwrites both via `UpdatePostService.handle()` ->
    `PostRepository.update_content()`. Every other field `evaluate_verdict()` compares
    (`status`/`scheduled_at`/`published_at`) stays at `Post`'s own column defaults
    (`"draft"`/`None`/`None`) either way, matching `UpdatePostService.handle()`'s own documented
    behavior ("editing content doesn't change `status`") -- so a seeded row and the brick's
    originally-captured response agree on those fields without this seeder needing to know or
    reconstruct what they actually were at capture time. `id`/`created_at` are both in
    `evaluate_verdict()`'s own `DEFAULT_VOLATILE_FIELDS` (masked in the diff), so a seeded row's
    fresh `created_at` never causes a spurious mismatch either.
    """
    post_id = brick.request.get("post_id") if isinstance(brick.request, dict) else None
    if not isinstance(post_id, str):
        return None

    session = session_factory()
    try:
        repository = PostRepository(session)
        if repository.get(post_id) is not None:
            return None  # already present -- not ours to clean up
        repository.seed(id=post_id, title="(seeded for replay)", body="(seeded for replay)")
    finally:
        session.close()

    def _teardown() -> None:
        cleanup_session = session_factory()
        try:
            PostRepository(cleanup_session).delete(post_id)
        finally:
            cleanup_session.close()

    return SeedResult(domain="blog_post", resource_id=post_id, teardown=_teardown)


def delete_blog_post(resource_id: str, session_factory: sessionmaker[Session]) -> None:
    """The `"blog_post"` domain's own sweep handler (see `SeedResult.domain`'s own docstring) --
    used by `app/admin/nanobar/replay_seeders.py`'s startup sweep to recover a crash-leaked
    `Post` row it otherwise only knows about as `(domain="blog_post", resource_id=<post_id>)`
    read back out of `shadow_seed_log.db`. Same delete `seed_post_for_update()`'s own teardown
    calls -- factored out here so both share one implementation."""
    session = session_factory()
    try:
        PostRepository(session).delete(resource_id)
    finally:
        session.close()
