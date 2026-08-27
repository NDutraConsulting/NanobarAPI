# examples/

Demo/seed tooling for NanobarAPI. Nothing here is part of the installed `nanobar_api`
package or its test suite — this is example/dev tooling only. Runtime data (gitignored
SQLite/JSON files these scripts and the dashboard app read/write) lives alongside the domain
code that owns it under `app/` -- `app/db/*.db` (cross-domain: blog, events, kahnban),
`app/admin/app/data/app_admin.db`, `app/admin/nanobar/data/*.db` -- not in one shared
directory. Only the example/seed *scripts* live here.

## seed_kahnban_bricks.py

Drives real, varied HTTP traffic through [focusari_kahnban](../../focusari_kahnban)
(a real, separately-built kanban app used here purely as a realistic traffic source),
captures it through this project's own regression-brick pipeline
(`SnapshotMiddleware` -> eventbus -> `generate_bricks`), and creates/binds `Nanobar`s for
what it captured.

Run from this repo's root:

```sh
uv run python examples/seed_kahnban_bricks.py
```

It creates three persistent (not temp-dir) SQLite databases (gitignored):

- `app/db/kahnban.db` — kahnban's own data (boards/lists/cards created by the seeded
  traffic), pointed there via `focusari_kahnban.db.configure()` so this script never
  touches kahnban's own dev database.
- `app/db/events.db` — the raw captured request/response snapshot events.
- `app/admin/nanobar/data/regression_bricks.db` — the generated `RegressionBrick`s, plus the
  `Nanobar`s and `nanobar_regression_bricks` bindings created from them. This is also
  what [the dashboard app](../app/main.py) reads by default.

All three are real files you can open afterward, e.g.
`sqlite3 app/admin/nanobar/data/regression_bricks.db`.

The traffic driven covers 2 boards, 2-3 lists per board, 5 cards spread across lists, an
in-list reorder (position-race territory), a cross-board move *attempt* (kahnban's own
`card_service` rejects this by design — the capture is the rejection response, which is
still a legitimate regression brick), a card delete (gap-closing position logic), and a
couple of read-only GETs.

**Safe to re-run.** Nanobars are keyed by a stable `(method, path-template)` identity
that doesn't depend on any particular board/list/card id, so re-running never creates
duplicate Nanobars. Regression bricks dedupe by content-hash of the exact captured
request+response and are only ever generated once per event — but since each run creates
fresh boards/lists/cards with new random ids, the captured content differs run to run, so
brick *counts* will typically keep growing across runs rather than staying flat. See the
module's own docstring for the full explanation.

## The dashboard app

A real NanobarAPI application (`app/main.py`'s `build_app()`, at the repo root) that doubles
as a worked "how do I build an app on NanobarAPI" reference. `app/` is the whole application
root, organized **by layer** rather than by domain (see
`.focusari/structure-plan-with-tasks.md` for the reasoning) -- each layer directory holds one
`{domain}_thing.py` file per domain, following the pipeline
`route -> validator gate -> controller (orchestrates services) -> service(s) -> [libraries,
repositories, models]`:

- **[`app/validators/`](../app/validators/)**, **[`app/controllers/`](../app/controllers/)**,
  **[`app/services/`](../app/services/)**, **[`app/crud/`](../app/crud/)**,
  **[`app/models/`](../app/models/)**, **[`app/libraries/`](../app/libraries/)**,
  **[`app/db/`](../app/db/)** — the blog/booking domain's own business logic (`blog_*.py` in
  each), used by both `app/api/routes/blog.py` and `app/admin/app/` below. This is the actual
  "real app built on the framework" example: full `NanobarValidatorGate` → `NanobarController`
  → `NanobarService` → `NanobarRepository` pipeline, `NanobarEventBus` pub/sub, and a
  `NanobarWorker`-adjacent background sweep.
- **[`app/api/routes/blog.py`](../app/api/routes/blog.py)** — the public-facing routes on top
  of it (`/`, `/posts/{slug}`, `/book-appointment`), no session gate.
- **[`app/admin/app/`](../app/admin/app/)** — the admin surface for managing that same
  blog/booking data (`/admin/app/*`): a small WordPress-*like* post scheduler plus a
  notification feed the booking flow's domain event ends up on.
- **[`app/admin/nanobar/`](../app/admin/nanobar/)** — the regression-brick admin dashboard
  (`/admin/nanobar/*`), this project's own self-instrumentation surface — browse/search
  nanobars, review and replay regression bricks, inspect traces/spans, monitor worker
  lifecycles, and manage the route manifest/refresh cycles below. The largest and most
  self-contained of the four, including its own `api.py`/`routes.py` -- both are meant to be
  read start to finish as the "how do I build an app on NanobarAPI" reference. Not yet split
  across the layer directories above the way the blog domain is -- see
  `.focusari/structure-plan-with-tasks.md`'s Phase 3 for that.
- **[`app/pages/`](../app/pages/)** — every domain's static per-page HTML/CSS/JS bundles
  (public, `admin/app`, and `admin/nanobar` alike, in one flat directory today -- see that
  plan doc's noted deviation from a fully per-surface-nested `pages/` tree).
- **[`app/core/config.py`](../app/core/config.py)** — cross-cutting path configuration
  (`WEB_DIR`, the route-manifest path resolver). Per-domain database paths are resolved
  locally by each domain's own `*_db.py`/`auth_db.py`/`blog_session.py`, not centralized here.

`app/main.py` is the composition root wiring everything together; `server.py` (at the repo
root, outside `app/`) is the `nanobar dev`/`uvicorn` entrypoint (kept separate from
`app/main.py` itself so that merely importing `build_app` — as the test suite does — never
triggers a real `build_app()` call as an import-time side effect; see `server.py`'s own
docstring for why it isn't named `app.py`).

**Two fully independent admin surfaces, not one shared login.** `app/admin/app/` and
`app/admin/nanobar/` each have their own `auth_db.py` (`app_admin.db`/`nanobar_admin.db`),
their own `login_routes.py` (`/admin/app/login`/`/admin/nanobar/login`), and their own
session/CSRF cookies, isolated by cookie path — logging into one never authenticates, or
otherwise disturbs, a session on the other. Everything is wired together in `app/main.py`.

Everything under `/admin/*` is session-gated (`nanobar_api.admin_auth`), backed by its own
SQLite-backed session store per surface — not the in-memory default.

### Running it

Run from this repo's root:

```sh
uv run nanobar dev server.py --port 8001
```

or, equivalently:

```sh
uv run uvicorn server:app --host 127.0.0.1 --port 8001
```

Trace capture is **on by default** here (`configure_tracing(enabled=True)` in `app/main.py`) —
unlike the framework's own opt-in-only default (`NANOBAR_TRACING_ENABLED`, see
`nanobar_api.middleware.trace`'s docstring, which stays off unless that env var is set,
so a production app never gets silently-active instrumentation), this is a dev/
observability tool where you'd always want it. The real runtime on/off switch is the
**Trace capture** toggle on `/admin/nanobar/dashboard/settings` — it persists across
restarts (`SQLiteTraceCaptureToggle`, stored in `nanobar_admin.db`) and takes effect
immediately, no restart needed, since it's checked fresh on every request.

Then open `http://127.0.0.1:8001/admin/nanobar/login` (or `/admin/app/login` for the
blog/booking admin — separate login, separate session) — seeded credentials are
`admin` / `changeme123` for both (`nanobar_api.admin_auth.DEFAULT_ADMIN_USERNAME`/
`DEFAULT_ADMIN_PASSWORD`; change them via `SQLiteAdminUserStore` once a real
credential-rotation flow exists). The public blog (`/`, `/posts/{id}`,
`/book-appointment`) needs no login.

### Pages

| Path                             | What it is |
| --------------------------------- | ---------- |
| `/admin/nanobar/login`            | Nanobar-admin's own session + CSRF login |
| `/admin/app/login`                | App-admin's own session + CSRF login — fully independent of the above |
| `/admin/nanobar/dashboard`        | Nanobar list, filterable by **`nanobar_type`** (the taxonomy layer type — `validator-request-response`, `orm-request-response`, ...) and by **`domain`** (which application this nanobar belongs to — `""` for a root-level route, `admin/app`, `admin/nanobar`, or an unrelated seeded domain like `boards`; see `nanobar.api-routes.json` below). `monitor_target_refs[].target_type` (e.g. an HTTP route) is a lower-level per-request detail, not something worth filtering by here. |
| `/admin/nanobar/nanobars/{id}`    | One nanobar's detail: summary, coverage gaps (or a "needs classification" prompt with a link to the evidencing span, for a `nanobar_type` with no taxonomy entry yet), and its bound regression bricks (Detail/Run tabs — Run replays a brick against a shadow app instance backed by sibling `_shadow`-suffixed databases, never the real ones) |
| `/admin/nanobar/triage`           | Triage board for unreviewed bricks |
| `/admin/nanobar/traces`           | Trace list with date/component/nanobar-type filters |
| `/admin/nanobar/traces/{id}`      | One trace's spans, left pane list + right pane detail |
| `/admin/nanobar/workers`          | `NanobarWorker` lifecycle monitor — config, health, failure log |
| `/`, `/posts/{slug}`              | Public blog |
| `/book-appointment`               | Public booking form |
| `/admin/app/dashboard`            | Admin post list + notification feed |
| `/admin/app/posts/{id}/edit`      | Edit a post |
| `/admin/nanobar/dashboard/settings` | Runtime settings — trace-capture on/off toggle, and the "Refresh cycles" menu below |

### Data

Seven files, each domain-local to the code that owns it (gitignored) rather than in one shared
directory, each with its own env-var override — see `build_app()`'s docstring in `app/main.py`
for the exact variable names and defaults:

- `app/admin/nanobar/data/regression_bricks.db`
- `app/db/events.db` (traces/spans — this app instruments its own requests)
- `app/admin/app/data/app_admin.db` (the blog/booking admin's own sessions + admin users)
- `app/admin/nanobar/data/nanobar_admin.db` (the nanobar-admin's own sessions + admin users,
  plus the trace-capture toggle and the refresh log below)
- `app/db/blog.db` (posts/appointments/notifications)
- `app/admin/nanobar/data/nanobar_type_system.db` (the runtime-writable half of the
  `nanobar_type` taxonomy — per-`(key, key_name)` coverage rules, e.g. one entry per worker
  channel, that the static checked-in `nanobar_api/nanobar.types.lock` can't hold; see that
  module's own docstring)
- `app/nanobar.api-routes.json` (below)

### Refresh cycles

Three independent, on-demand actions, each with its own button (and "last run" status) on
`/admin/nanobar/dashboard/settings`, plus its own equivalent from a terminal:

- **API routes** — statically scans the app's live route tree and (re)writes
  `app/nanobar.api-routes.json` (a `{domain, method, path, route_key}` entry per
  route — see `nanobar_api/route_manifest.py`). Runs automatically on every launch already
  ("built on launch"); the button just re-runs it without restarting. From a terminal, this
  is the general framework CLI, not a demo-specific script:

  ```sh
  uv run nanobar routes app/main.py --app build_app
  ```

  (`app/main.py` has no relative imports of its own, so a bare file path works here — no
  `--module` needed.)

- **Nanobars** — reconciles `Nanobar` rows against that manifest: creates an
  `unclassified` placeholder (renders as "needs classification" on the nanobar detail page)
  for any declared route with zero real traffic yet, so the dashboard reflects 100% of the
  app's surface, not just whatever's been exercised; and backfills/corrects `domain` on
  existing route-keyed nanobars. See `app/admin/nanobar/nanobar_refresh.py`.

- **Regression bricks** — the same "drain captured traffic into bricks" step that
  pre-dates this feature (nothing else in the live app turns captured traffic into bricks on
  its own):

  ```sh
  uv run python examples/generate_dashboard_bricks.py
  ```

  Or click **"Generate bricks"** on the nanobar dashboard (also on the Settings page, as
  the "Regression bricks" row — same endpoint either way).
