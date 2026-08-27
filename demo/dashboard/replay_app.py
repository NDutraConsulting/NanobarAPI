"""A memoized "shadow" `NanobarAPI` instance for hermetically replaying a bound
`RegressionBrick` from the dashboard's own "Run" button, per
`nanobar-dashboard-search-and-replay-upgrade-plan.md` Design Decision G.

**Not** the full `ShadowRoutingMiddleware`/Shadow-Worker-process/signed-header architecture
`.focusari/regression-brick-system-plan.md` §5 describes (explicitly marked "design only, not
built" there, and sized for a distributed production setting) -- this is the smallest local
mechanism that gets this plan's actual requirement: **a replay must never write into the real
`demo/data/blog.db`**. A bound brick can come from a mutating admin route (create/update a
post, book an appointment, mark a notification read); replaying it against the live app's own
`blog_session_factory` would create/mutate real rows on every click of "Run".

The shadow app shares the live app's `regression_bricks.db`/`events.db`/`admin.db` unchanged --
there's only one `regression_bricks.db` for bricks to live in, and a replay's own trace/span
activity must land in the *same* `events.db` the operator is already browsing, or the Run tab's
"Refresh" button would have nothing to fetch. Only `blog_db_path` is rerouted, to a sibling file
(`blog.db` -> `blog_shadow.db`) -- same shape as every other `demo/data/*.db` file: gitignored,
persists across replays (accumulates like any other demo data), safe to delete to reset it.

**Scoped down further, honestly:** this app's ASGI lifespan is never entered (no `with
TestClient(app):`), so its background threads (the domain event bus, the scheduled-post
publisher) never run. A replay's HTTP response — the thing the Run tab's verdict actually
compares — is fully faithful; a replayed action's *asynchronous* side effects (e.g. a replayed
`book-appointment` won't produce a notification) are not. Keeping one shadow app's lifespan
alive for a live dashboard process's entire runtime is a real, solvable problem, just a
disproportionate one for what a local-beta "Run" button needs today — flagged, not silently
glossed over.
"""

from __future__ import annotations

from pathlib import Path

from nanobar_api import NanobarAPI

_replay_apps: dict[tuple[str, str, str, str], NanobarAPI] = {}


def _shadow_blog_db_path(blog_db_path: str) -> str:
    path = Path(blog_db_path)
    return str(path.with_name(f"{path.stem}_shadow{path.suffix}"))


def get_replay_app(*, db_path: str, events_db_path: str, admin_db_path: str, blog_db_path: str) -> NanobarAPI:
    """Returns a memoized shadow app for this exact `(db_path, events_db_path, admin_db_path,
    blog_db_path)` tuple -- the live app's own four db paths, which is also the shadow app's
    natural cache key: tests build a fresh live app (and thus fresh temp-dir db paths) per
    test, so this naturally scopes to "one shadow app per distinct live app," never leaking
    across tests or reusing a stale shadow app pointed at the wrong live app's databases.
    """
    # Deferred: demo.dashboard.app -> admin_nanobar_routes -> api -> this module, at import
    # time -- a real circular import at module-load, since app.py hasn't finished defining
    # build_app() yet when api.py (transitively) imports this file. By the time this function
    # actually runs (request-handling time), demo.dashboard.app is fully initialized.
    from .app import build_app

    key = (db_path, events_db_path, admin_db_path, blog_db_path)
    app = _replay_apps.get(key)
    if app is None:
        app = build_app(
            db_path=db_path,
            events_db_path=events_db_path,
            admin_db_path=admin_db_path,
            blog_db_path=_shadow_blog_db_path(blog_db_path),
        )
        _replay_apps[key] = app
    return app
