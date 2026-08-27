"""The application root -- everything except the installed `nanobar_api` framework package
lives here, organized by layer rather than by domain: `validators/`, `controllers/`,
`services/`, `crud/`, `models/`, `libraries/`, `db/` each hold one `{domain}_thing.py` file per
domain, following the pipeline `route -> validator gate -> controller (orchestrates services) ->
service(s) -> [libraries, repositories, models]`. `admin/` holds the two independent admin
surfaces (`admin.nanobar`, `admin.app`); `api/routes/` holds the public-facing route
registrations; `pages/` holds the public-facing static page bundles; `core/` holds
cross-cutting configuration. `main.py` is the composition root (`build_app()`).

Today only the blog/booking domain (`blog_*.py` in each layer directory) is actually built out
this way -- see `.focusari/structure-plan-with-tasks.md` for the phased plan bringing
`admin.nanobar`/`admin.app` in line.
"""
