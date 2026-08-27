from __future__ import annotations

import json
import tempfile
from pathlib import Path

from starlette.routing import BaseRoute, Mount, Route
from starlette.staticfiles import StaticFiles

from nanobar_api import NanobarAPI
from nanobar_api.route_manifest import (
    RouteManifestEntry,
    build_route_manifest,
    load_route_manifest,
    write_route_manifest,
)


async def _handler(request: object) -> None:
    return None


def _app(routes: list[BaseRoute]) -> NanobarAPI:
    # openapi_url/docs_url=None keeps the manifest deterministic -- NanobarAPI() otherwise
    # auto-registers /openapi.json and /docs, which aren't relevant to these tests. The
    # unconditional /nanobar-static/design-system Mount (a StaticFiles opaque sub-app)
    # contributes zero manifest entries either way, exercising the opaque-mount-skip path.
    return NanobarAPI(openapi_url=None, docs_url=None, routes=routes)


def test_build_route_manifest_flattens_nested_mounts_and_root_routes() -> None:
    app = _app(
        [
            Mount(
                "/admin/nanobar",
                routes=[
                    Route("/dashboard", _handler, methods=["GET"]),
                    Route("/nanobars/{nanobar_id}", _handler, methods=["GET", "PATCH"]),
                ],
            ),
            Route("/", _handler, methods=["GET"]),
        ]
    )

    entries = build_route_manifest(app)

    by_key = {e.route_key: e for e in entries}
    assert by_key["GET /admin/nanobar/dashboard"] == RouteManifestEntry(
        domain="admin/nanobar", method="GET", path="/admin/nanobar/dashboard", route_key="GET /admin/nanobar/dashboard"
    )
    assert "PATCH /admin/nanobar/nanobars/{nanobar_id}" in by_key
    assert "GET /admin/nanobar/nanobars/{nanobar_id}" in by_key
    assert by_key["GET /"] == RouteManifestEntry(domain="", method="GET", path="/", route_key="GET /")


def test_build_route_manifest_excludes_head_as_a_distinct_entry() -> None:
    app = _app([Route("/ping", _handler, methods=["GET"])])

    entries = build_route_manifest(app)

    assert [e.method for e in entries] == ["GET"]


def test_build_route_manifest_skips_an_opaque_mount() -> None:
    with tempfile.TemporaryDirectory() as static_dir:
        app = _app(
            [
                Route("/", _handler, methods=["GET"]),
                Mount("/extra-static", app=StaticFiles(directory=static_dir)),
            ]
        )

        entries = build_route_manifest(app)

    assert [e.route_key for e in entries] == ["GET /"]


def test_build_route_manifest_empty_app_returns_no_entries() -> None:
    app = _app([])

    assert build_route_manifest(app) == []


def test_write_and_load_route_manifest_round_trips(tmp_path: Path) -> None:
    app = _app([Route("/ping", _handler, methods=["GET"])])
    out_path = tmp_path / "nested" / "nanobar.api-routes.json"

    written = write_route_manifest(app, out_path)
    loaded = load_route_manifest(out_path)

    assert written == loaded
    assert loaded == [RouteManifestEntry(domain="", method="GET", path="/ping", route_key="GET /ping")]


def test_write_route_manifest_document_has_generated_at(tmp_path: Path) -> None:
    app = _app([Route("/ping", _handler, methods=["GET"])])
    out_path = tmp_path / "manifest.json"

    write_route_manifest(app, out_path)

    document = json.loads(out_path.read_text())
    assert "generated_at" in document
    assert len(document["routes"]) == 1
