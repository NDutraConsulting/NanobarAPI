from __future__ import annotations

from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from starlette.testclient import TestClient

from nanobar_api import NanobarAPI


def test_design_system_tokens_are_served() -> None:
    client = TestClient(NanobarAPI(routes=[]))

    response = client.get("/nanobar-static/design-system/design-system.css")

    assert response.status_code == 200
    assert "css" in response.headers["content-type"]
    assert "--color-primary" in response.text


def test_design_system_mount_present_even_with_docs_disabled() -> None:
    client = TestClient(NanobarAPI(routes=[], docs_url=None, openapi_url=None))

    response = client.get("/nanobar-static/design-system/design-system.css")

    assert response.status_code == 200


def test_design_system_mount_survives_an_app_owned_static_mount(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # Found via live verification: a real app (e.g. the demo dashboard) commonly claims
    # `/static` for its own StaticFiles mount. Starlette's Mount matching is prefix-based and
    # first-match-wins in route list order, so the framework's own design-system mount must
    # live outside `/static` entirely -- not merely be registered after it -- or an app-owned
    # `/static` mount registered first would shadow it and this would 404.
    (tmp_path / "app.txt").write_text("app-owned")
    client = TestClient(NanobarAPI(routes=[Mount("/static", app=StaticFiles(directory=tmp_path), name="app-static")]))

    response = client.get("/nanobar-static/design-system/design-system.css")

    assert response.status_code == 200
    assert "--color-primary" in response.text


def test_design_system_missing_asset_is_404() -> None:
    client = TestClient(NanobarAPI(routes=[]))

    response = client.get("/nanobar-static/design-system/does-not-exist.css")

    assert response.status_code == 404
