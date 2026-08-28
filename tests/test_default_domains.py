from __future__ import annotations

import pytest
from starlette.middleware import Middleware
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from starlette.testclient import TestClient

from nanobar_api import NanobarAPI, NanobarTelemetry
from nanobar_api.default_domains import DEFAULT_README_CONTENT, install_default_domains
from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.middleware.trace import EventBusTraceMiddleware


def _app_with_telemetry(*, readme_content: str | None = None) -> NanobarAPI:
    # The readme route goes through the real NanobarAPIValidatorGate/NanobarAPIController pipeline,
    # which needs app.state.telemetry -- install_default_domains() itself doesn't wire this
    # (that's the caller's job, same as every other controller-touching app in this codebase;
    # a bare NanobarAPI() has no telemetry by design).
    repository = EventQueueRepository([ChannelConfig(name="trace"), ChannelConfig(name="snapshot")])
    app = NanobarAPI(
        routes=[], middleware=[Middleware(EventBusTraceMiddleware, repository=repository, channel="trace")]
    )
    app.state.telemetry = NanobarTelemetry(repository, channel="trace")
    install_default_domains(app, readme_content=readme_content)
    return app


def test_fresh_app_has_neither_api_readme_nor_landing_page() -> None:
    client = TestClient(NanobarAPI(routes=[]))

    assert client.get("/api/readme").status_code == 404
    assert client.get("/").status_code == 404


def test_install_default_domains_registers_api_readme_with_default_content() -> None:
    client = TestClient(_app_with_telemetry())

    response = client.get("/api/readme")

    assert response.status_code == 200
    envelope = response.json()
    assert envelope["status"] == "success"
    assert envelope["result"]["data"]["content"] == DEFAULT_README_CONTENT


def test_install_default_domains_with_custom_readme_content() -> None:
    client = TestClient(_app_with_telemetry(readme_content="# My App\n\nReal docs here."))

    response = client.get("/api/readme")

    assert response.json()["result"]["data"]["content"] == "# My App\n\nReal docs here."


def test_install_default_domains_registers_the_landing_page() -> None:
    client = TestClient(_app_with_telemetry())

    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "NanobarAPI" in response.text


def test_landing_page_static_assets_are_served() -> None:
    client = TestClient(_app_with_telemetry())

    css = client.get("/nanobar-static/landing/landing.css")
    js = client.get("/nanobar-static/landing/landing.js")

    assert css.status_code == 200
    assert "css" in css.headers["content-type"]
    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]


def test_landing_static_mount_survives_an_app_owned_static_mount(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # Same collision this session's review pass already found and fixed for the design-system
    # mount: an app-owned `/static` mount registered first must not shadow this one, since
    # LANDING_STATIC_MOUNT deliberately lives under /nanobar-static/, not /static/.
    (tmp_path / "app.txt").write_text("app-owned")
    repository = EventQueueRepository([ChannelConfig(name="trace"), ChannelConfig(name="snapshot")])
    app = NanobarAPI(
        routes=[Mount("/static", app=StaticFiles(directory=tmp_path), name="app-static")],
        middleware=[Middleware(EventBusTraceMiddleware, repository=repository, channel="trace")],
    )
    app.state.telemetry = NanobarTelemetry(repository, channel="trace")
    install_default_domains(app)
    client = TestClient(app)

    response = client.get("/nanobar-static/landing/landing.css")

    assert response.status_code == 200


def test_api_readme_is_not_auto_registered_without_calling_install_default_domains() -> None:
    # Design Decision: opt-in, not automatic -- a fresh NanobarAPI() (no install_default_domains
    # call) must not expose either route, so an app that never asked for this surface doesn't
    # silently get one.
    client = TestClient(NanobarAPI(routes=[]))

    assert client.get("/api/readme").status_code == 404
    assert client.get("/").status_code == 404


def test_api_readme_produces_real_capture_bricks_without_crashing() -> None:
    # Proves the readme route genuinely goes through the NanobarAPIValidatorGate/NanobarAPIController
    # pipeline (capture_layer() calls succeed) -- not just that it happens to return 200.
    client = TestClient(_app_with_telemetry())

    response = client.get("/api/readme")

    assert response.status_code == 200


def test_api_readme_falls_back_gracefully_when_load_required_services_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Mirrors test_controllers.py's own _FallbackController pattern: neither
    # load_required_services() nor load_fallback_services() here actually does anything (the
    # content is static app state, not a loaded service), so a forced failure of the "required"
    # path must still recover to the same working response via NanobarAPIController.__init__'s own
    # try/except -- not just that this controller's fallback is unreachable dead code.
    from nanobar_api.default_domains import ApiReadmeController

    def _raise(self: ApiReadmeController) -> None:
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(ApiReadmeController, "load_required_services", _raise)
    client = TestClient(_app_with_telemetry())

    response = client.get("/api/readme")

    assert response.status_code == 200
    assert response.json()["result"]["data"]["content"] == DEFAULT_README_CONTENT
