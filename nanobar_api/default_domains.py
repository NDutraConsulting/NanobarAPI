"""Default domains — `nanobar_default_domains_buildplan-with-tasks.md` Phases B/C: `/api/readme`
and a root landing page. `static`/`access.py`'s tiers were already built in Tier 1; this closes
the remaining two.

**Opt-in, not auto-registered** (that doc's own Design Decision): call `install_default_domains(app)`
explicitly. An always-on surface has security-posture implications for an app that never intended
to expose one — a fresh `NanobarAPI()` still registers nothing beyond `/docs`/`/openapi.json`/the
swagger static mount unless you ask for this too.

**Landing page: a real deviation from the buildplan doc's own literal Design Decision, documented
not silent.** That doc calls for content "generated from already-known app metadata... not a
vendored static HTML file." Taken literally, that means server-rendering HTML from Python
f-strings/templating — exactly the pattern this project's own established convention has already
rejected once, for real, elsewhere (`.focusari/.agent_ignore/2026-08-24/2026-08-24-agent-context.md`
gotcha 8: an earlier version of `demo/dashboard/` was built that way, "explicitly rejected... If
you ever see HTML being generated from Python strings anywhere... that's a regression back to the
rejected approach"). Resolved the same way that convention resolves everywhere else in this
codebase: a real static HTML/CSS/JS bundle (vendored under `nanobar_api/static/nanobar-landing/`,
mirroring the design-system/swagger-ui precedent), whose JS fetches `/openapi.json` (already
carries `info.title`/`info.version` — no new server-side data endpoint needed) and renders it
client-side. Same "app metadata, not a static asset" spirit the buildplan doc wanted, without
reintroducing server-rendered HTML.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from nanobar_api.applications import NanobarAPI
from nanobar_api.framework.nanobar_api_controller import NanobarAPIController
from nanobar_api.framework.nanobar_api_validator_gate import NanobarAPIValidatorGate
from nanobar_api.routing import NanobarRouteRule, NanobarRouteSet, RestRouteAdapter

#: Vendored landing-page static assets -- same "pure static file serving" shape as the
#: swagger-ui/design-system mounts, and (per the review pass that found the design-system
#: mount's own path collided with an app's own `/static` mount) deliberately under
#: `/nanobar-static/...`, never `/static/...`.
LANDING_STATIC_DIR = Path(__file__).resolve().parent / "static" / "nanobar-landing"
LANDING_STATIC_MOUNT = "/nanobar-static/landing"

DEFAULT_README_CONTENT = (
    "# API readme\n\nThis app hasn't configured `/api/readme` content yet. "
    "Pass `readme_content=` to `install_default_domains()` to set it."
)


class ApiReadmeController(NanobarAPIController):
    def load_required_services(self) -> None:
        pass  # nothing to load -- the content is static app state, no per-request I/O

    def load_fallback_services(self) -> None:
        pass

    def run_etl_workflow(self, validated: Any) -> Any:
        content: str = self.request.app.state.nanobar_readme_content
        return content

    def build_response(self, result: Any) -> dict[str, Any]:
        return {"content": result}


class ApiReadmeGate(NanobarAPIValidatorGate):
    controller_cls = ApiReadmeController

    def validate(self, request: Request) -> None:
        return None


class ApiRouteSet(NanobarRouteSet):
    """The natural home for a real app's own `/api/*` business routes too -- `readme` is just
    the first default member of what's otherwise an ordinary domain a developer extends with
    their own rules."""

    domain = "api"
    rules = (NanobarRouteRule(key="GET /readme", gate=ApiReadmeGate),)


async def _landing_page(request: Request) -> Response:
    return FileResponse(LANDING_STATIC_DIR / "landing.html")


def install_default_domains(app: NanobarAPI, *, readme_content: str | None = None) -> None:
    """Registers `/api/readme` and the `/` landing page onto `app` (a `NanobarAPI` instance).

    **Route-registration-order caveat, not silently glossed over:** if `app` already registered
    its own route at `/` (a real app very plausibly will), that earlier route wins -- Starlette's
    routing is first-match-wins in list order, same "an app's own routes can shadow a
    framework-appended one" limitation already documented on `DESIGN_SYSTEM_STATIC_MOUNT`'s own
    mount-path fix. Call this before any app-specific `/` registration if you want the generated
    landing page to actually be reachable, or don't call it at all if your app defines its own
    root page.
    """
    app.state.nanobar_readme_content = readme_content if readme_content is not None else DEFAULT_README_CONTENT
    app.routes.append(RestRouteAdapter.build_mount(ApiRouteSet).mount)
    app.routes.append(Route("/", _landing_page, methods=["GET"]))
    app.routes.append(
        Mount(LANDING_STATIC_MOUNT, app=StaticFiles(directory=LANDING_STATIC_DIR), name="nanobar-landing")
    )
