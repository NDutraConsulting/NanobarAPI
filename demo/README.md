# demo/

Demo/seed tooling for NanobarAPI. Nothing here is part of the installed `nanobar_api`
package or its test suite — this is example/dev tooling only.

## seed_kahnban_bricks.py

Drives real, varied HTTP traffic through [focusari_kahnban](../../focusari_kahnban)
(a real, separately-built kanban app used here purely as a realistic traffic source),
captures it through this project's own regression-brick pipeline
(`SnapshotMiddleware` -> eventbus -> `generate_bricks`), and creates/binds `Nanobar`s for
what it captured.

Run from this repo's root:

```sh
uv run python demo/seed_kahnban_bricks.py
```

It creates three persistent (not temp-dir) SQLite databases under `demo/data/`
(gitignored):

- `demo/data/kahnban.db` — kahnban's own data (boards/lists/cards created by the seeded
  traffic), pointed there via `focusari_kahnban.db.configure()` so this script never
  touches kahnban's own dev database.
- `demo/data/events.db` — the raw captured request/response snapshot events.
- `demo/data/regression_bricks.db` — the generated `RegressionBrick`s, plus the
  `Nanobar`s and `nanobar_regression_bricks` bindings created from them. This is also
  what [`demo/dashboard`](dashboard/) reads by default.

All three are real files you can open afterward, e.g. `sqlite3 demo/data/regression_bricks.db`.

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

## demo/dashboard

A real NanobarAPI application (`demo/dashboard/app.py`'s `build_app()`) that doubles as a
worked "how do I build an app on NanobarAPI" reference — two files in particular,
`admin_app_routes.py` and `admin_nanobar_routes.py`, are meant to be read start to finish
for that. It bundles two things:

- **The regression-brick admin dashboard**, mounted at `/admin/nanobar/*` — browse/search
  nanobars, review and replay regression bricks, inspect traces/spans, and monitor worker
  lifecycles.
- **A blog + appointment-booking demo domain** (`blog_*.py`, mounted at `/` and
  `/admin/app/*`) — a small WordPress-*like* post scheduler plus a booking flow that
  publishes a domain event a background worker turns into a notification. This is the
  actual "real app built on the framework" example: full
  `NanobarValidatorGate` → `NanobarController` → `NanobarService` → `NanobarRepository`
  pipeline, `NanobarEventBus` pub/sub, and a `NanobarWorker` subclass, all wired together
  in `app.py`.

Everything under `/admin/*` is session-gated (`nanobar_api.admin_auth`), backed by its own
SQLite-backed session store — not the in-memory default.

### Running it

```sh
uv run uvicorn demo.dashboard.app:build_app --factory --host 127.0.0.1 --port 8001
```

Trace capture is **on by default** here (`configure_tracing(enabled=True)` in `app.py`) —
unlike the framework's own opt-in-only default (`NANOBAR_TRACING_ENABLED`, see
`nanobar_api.middleware.trace`'s docstring, which stays off unless that env var is set,
so a production app never gets silently-active instrumentation), this is a dev/
observability tool where you'd always want it. The real runtime on/off switch is the
**Trace capture** toggle on `/admin/nanobar/dashboard/settings` — it persists across
restarts (`SQLiteTraceCaptureToggle`, stored in `admin.db`) and takes effect immediately,
no restart needed, since it's checked fresh on every request.

Then open `http://127.0.0.1:8001/admin/login` — seeded credentials are
`admin` / `changeme123` (`nanobar_api.admin_auth.DEFAULT_ADMIN_USERNAME`/
`DEFAULT_ADMIN_PASSWORD`; change them via `SQLiteAdminUserStore` once a real
credential-rotation flow exists). The public blog (`/`, `/posts/{id}`,
`/book-appointment`) needs no login.

### Pages

| Path                             | What it is |
| --------------------------------- | ---------- |
| `/admin/login`                    | Session + CSRF login |
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

Six files under `demo/data/` (gitignored), each with its own env-var override — see
`build_app()`'s docstring in `demo/dashboard/app.py` for the exact variable names and
defaults: `regression_bricks.db`, `events.db` (traces/spans — this app instruments its own
requests), `admin.db` (sessions + admin users + the refresh log below), `blog.db`
(posts/appointments/notifications), `nanobar_type_system.db` (the runtime-writable half of
the `nanobar_type` taxonomy — per-`(key, key_name)` coverage rules, e.g. one entry per
worker channel, that the static checked-in `nanobar_api/nanobar.types.lock` can't hold; see
that module's own docstring), and `nanobar.api-routes.json` (below).

### Refresh cycles

Three independent, on-demand actions, each with its own button (and "last run" status) on
`/admin/nanobar/dashboard/settings`, plus its own equivalent from a terminal:

- **API routes** — statically scans the app's live route tree and (re)writes
  `demo/data/nanobar.api-routes.json` (a `{domain, method, path, route_key}` entry per
  route — see `nanobar_api/route_manifest.py`). Runs automatically on every launch already
  ("built on launch"); the button just re-runs it without restarting. From a terminal, this
  is the general framework CLI, not a demo-specific script:

  ```sh
  uv run nanobar routes --module demo.dashboard.app --app build_app
  ```

  (`nanobar routes path/to/app.py` also works for a flat script with no relative imports;
  this demo's `app.py` lives inside a package, so `--module` is required here.)

- **Nanobars** — reconciles `Nanobar` rows against that manifest: creates an
  `unclassified` placeholder (renders as "needs classification" on the nanobar detail page)
  for any declared route with zero real traffic yet, so the dashboard reflects 100% of the
  app's surface, not just whatever's been exercised; and backfills/corrects `domain` on
  existing route-keyed nanobars. See `demo/dashboard/nanobar_refresh.py`.

- **Regression bricks** — the same "drain captured traffic into bricks" step that
  pre-dates this feature (nothing else in the live app turns captured traffic into bricks on
  its own):

  ```sh
  uv run python demo/generate_dashboard_bricks.py
  ```

  Or click **"Generate bricks"** on the nanobar dashboard (also on the Settings page, as
  the "Regression bricks" row — same endpoint either way).
