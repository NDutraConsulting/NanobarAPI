"""Shadow-mode persistence configuration for regression-brick replay -- reuses the framework's
`ShadowPersistenceProfile`/`resolve_shadow_connection()` mechanism to resolve where a replayed
request's blog-domain writes go instead of the live `blog.db`.

`app/main.py`'s `build_app()` builds both a live and a shadow `blog_session_factory` up front, and
`app/db/blog_session.py`'s `resolve_session_factory()` picks between them per request based on
`nanobar_api.shadow.is_shadow_mode()` -- a `nanobar-mode: shadow` header
`app/admin/nanobar/api.py`'s `replay_brick_action` attaches, read by `nanobar_api.shadow.
ShadowModeMiddleware` (mounted on this same app). Replay dispatches back into this same running
app instance (`app.state.replay_client`, an in-process `TestClient`) rather than a separately-run
process -- superseding the earlier `shadow_server.py`/port-8100 design, which existed only to get
a genuinely separate app's own lifespan (and thus its `TelemetryDrainWorker`) to run for real; the
header-flag mechanism above needs no second lifespan at all, since it's the same already-running
app answering both live and shadow-flagged requests.

Only `blog_db_path` is shadow-routed -- every other db path (`regression_bricks.db`, `events.db`,
both admin dbs, and telemetry) is shared with live traffic unchanged, same scoping the old design
already established: there's only one `regression_bricks.db` for bricks to live in, and telemetry/
trace capture for a replay isn't isolated in this pass (a replayed request's spans land in the
same `nanobar_api_telemetry.db` as organic traffic) -- a smaller, documented scope-down from what
`shadow_server.py` used to also isolate via `TELEMETRY_SHADOW_PROFILE`, not an oversight. Revisit
if a replay's own telemetry ever needs to be filtered out of/kept separate from real traffic.
"""

from __future__ import annotations

from nanobar_api.bricks.shadow_profile import ShadowPersistenceProfile

BLOG_SHADOW_PROFILE = ShadowPersistenceProfile(
    profile_id="postprod-sqlite", connection_secret_ref="NANOBAR_BLOG_SHADOW_DB"
)
