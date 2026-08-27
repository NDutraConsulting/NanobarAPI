"""Shadow persistence profile resolution -- the first real slice of
`.focusari/complete/archive/nanobarapi-architecture-rules.md`'s "Shadow Execution and
Persistence Rerouting" design, scoped down to what's actually needed today: an in-process
shadow database target for `RegressionBrick` replay, resolved via a `connection_secret_ref`
environment variable rather than hardcoded filename-suffix string surgery (which is all
`app/admin/nanobar/replay_app.py`'s `_shadow_blog_db_path()` did before this module existed).

**Not** the separate Shadow Worker *process*, `ShadowRoutingMiddleware` verifying a signed
internal header contract, or the `shadow_execution_runs` audit table that design also
describes -- those remain explicitly deferred, unbuilt design (see
`.focusari/regression-brick-system-plan.md` §5's restatement, still marked "design only, not
built").
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ShadowPersistenceProfile:
    """One named shadow-persistence target, per the architecture doc's Environment Profile JSON
    shape (`profile_id`/`connection_secret_ref` fields) -- scoped down to the one field this
    slice actually resolves (a connection string), not the full profile object
    (`external_boundaries`, `allow_production_writes`, etc.) that doc's fuller design covers.

    `profile_id` is a descriptive/audit label only in this slice (e.g. `"postprod-sqlite"`,
    `"postprod-full"`) -- it doesn't yet drive type-specific behavior like enforcing
    `postprod-readonly`'s read-only-ness. `connection_secret_ref` is an environment variable
    *name*, resolved at call time -- never a literal secret/URL in code, matching the
    architecture doc's own rule that shadow-routing configuration "must never contain a database
    URL, credential... " on the wire; here, the same discipline applies to how it's configured.
    """

    profile_id: str
    connection_secret_ref: str


def resolve_shadow_connection(real_db_path: str, *, profile: ShadowPersistenceProfile) -> str:
    """Resolves `profile`'s shadow connection target: `profile.connection_secret_ref`'s
    environment variable if set -- a full remote connection URL (e.g. `postgresql://...`) for a
    `postprod-full`-style profile, or another local path -- else a local sibling file next to
    `real_db_path` (`blog.db` -> `blog_shadow.db`), preserving the pre-existing zero-config
    default behavior exactly.
    """
    override = os.environ.get(profile.connection_secret_ref)
    if override is not None:
        return override
    path = Path(real_db_path)
    return str(path.with_name(f"{path.stem}_shadow{path.suffix}"))
