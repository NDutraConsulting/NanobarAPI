from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nanobar_api.cli import dev, main, routes


class _FakeUvicorn:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    def run(self, import_string: str, **kwargs: Any) -> None:
        self.calls.append((import_string, kwargs))


@pytest.fixture
def app_file(tmp_path: Path) -> Path:
    path = tmp_path / "app.py"
    path.write_text("app = object()\n")
    return path


def test_dev_invokes_uvicorn_with_correct_import_string(monkeypatch: pytest.MonkeyPatch, app_file: Path) -> None:
    fake = _FakeUvicorn()
    monkeypatch.setattr("uvicorn.run", fake.run)

    dev([str(app_file)])

    assert len(fake.calls) == 1
    import_string, kwargs = fake.calls[0]
    assert import_string == "app:app"
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 8000
    assert kwargs["reload"] is True
    assert kwargs["app_dir"] == str(app_file.parent)


def test_dev_respects_custom_app_name_host_and_port(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "server.py"
    path.write_text("my_api = object()\n")
    fake = _FakeUvicorn()
    monkeypatch.setattr("uvicorn.run", fake.run)

    dev([str(path), "--app", "my_api", "--host", "0.0.0.0", "--port", "9000"])

    import_string, kwargs = fake.calls[0]
    assert import_string == "server:my_api"
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["port"] == 9000


def test_dev_defaults_to_app_py_in_cwd_when_path_omitted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("app = object()\n")
    monkeypatch.chdir(tmp_path)
    fake = _FakeUvicorn()
    monkeypatch.setattr("uvicorn.run", fake.run)

    dev([])

    assert len(fake.calls) == 1
    import_string, kwargs = fake.calls[0]
    assert import_string == "app:app"
    assert kwargs["app_dir"] == str(tmp_path)


def test_dev_errors_when_path_omitted_and_no_default_app_py(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    fake = _FakeUvicorn()
    monkeypatch.setattr("uvicorn.run", fake.run)

    with pytest.raises(SystemExit):
        dev([])

    assert fake.calls == []


def test_dev_errors_on_missing_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _FakeUvicorn()
    monkeypatch.setattr("uvicorn.run", fake.run)

    with pytest.raises(SystemExit):
        dev([str(tmp_path / "does-not-exist.py")])

    assert fake.calls == []


def test_dev_errors_when_path_is_a_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _FakeUvicorn()
    monkeypatch.setattr("uvicorn.run", fake.run)

    with pytest.raises(SystemExit):
        dev([str(tmp_path)])

    assert fake.calls == []


def test_dev_errors_when_uvicorn_not_installed(monkeypatch: pytest.MonkeyPatch, app_file: Path) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "uvicorn":
            raise ImportError("no module named uvicorn")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(SystemExit):
        dev([str(app_file)])


def test_main_with_no_args_prints_usage_and_exits(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main([])

    assert exc_info.value.code == 1
    assert "Usage: nanobar dev" in capsys.readouterr().err


def test_main_with_unknown_subcommand_prints_usage_and_exits(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["serve"])

    assert "Usage: nanobar dev" in capsys.readouterr().err


def test_main_dispatches_to_dev(monkeypatch: pytest.MonkeyPatch, app_file: Path) -> None:
    fake = _FakeUvicorn()
    monkeypatch.setattr("uvicorn.run", fake.run)

    main(["dev", str(app_file)])

    assert len(fake.calls) == 1


def test_main_reads_sys_argv_when_argv_omitted(monkeypatch: pytest.MonkeyPatch, app_file: Path) -> None:
    fake = _FakeUvicorn()
    monkeypatch.setattr("uvicorn.run", fake.run)
    monkeypatch.setattr("sys.argv", ["nanobar", "dev", str(app_file)])

    main()

    assert len(fake.calls) == 1


# ------------------------------------------------------------------------------- routes ---

_APP_MODULE_SOURCE = """
from starlette.routing import Route, Mount
from nanobar_api import NanobarAPI

async def handler(request):
    return None

app = NanobarAPI(
    openapi_url=None,
    docs_url=None,
    routes=[
        Mount("/admin", routes=[Route("/dashboard", handler, methods=["GET"])]),
        Route("/", handler, methods=["GET"]),
    ],
)
"""

_FACTORY_MODULE_SOURCE = """
from starlette.routing import Route
from nanobar_api import NanobarAPI

async def handler(request):
    return None

def build_app():
    return NanobarAPI(openapi_url=None, docs_url=None, routes=[Route("/ping", handler, methods=["GET"])])
"""


@pytest.fixture
def routes_app_file(tmp_path: Path) -> Path:
    path = tmp_path / "app.py"
    path.write_text(_APP_MODULE_SOURCE)
    return path


def test_routes_writes_manifest_json_with_expected_entries(tmp_path: Path, routes_app_file: Path) -> None:
    out = tmp_path / "nanobar.api-routes.json"

    routes([str(routes_app_file), "--out", str(out)])

    document = json.loads(out.read_text())
    assert "generated_at" in document
    entries = {(e["domain"], e["method"], e["path"]) for e in document["routes"]}
    assert entries == {("admin", "GET", "/admin/dashboard"), ("", "GET", "/")}


def test_routes_supports_a_factory_callable(tmp_path: Path) -> None:
    path = tmp_path / "app.py"
    path.write_text(_FACTORY_MODULE_SOURCE)
    out = tmp_path / "nanobar.api-routes.json"

    routes([str(path), "--app", "build_app", "--out", str(out)])

    document = json.loads(out.read_text())
    assert [e["route_key"] for e in document["routes"]] == ["GET /ping"]


def test_routes_defaults_out_path_to_cwd(monkeypatch: pytest.MonkeyPatch, routes_app_file: Path) -> None:
    monkeypatch.chdir(routes_app_file.parent)

    routes([str(routes_app_file)])

    assert (routes_app_file.parent / "nanobar.api-routes.json").is_file()


def test_routes_errors_on_missing_app_attribute(tmp_path: Path, routes_app_file: Path) -> None:
    with pytest.raises(SystemExit):
        routes([str(routes_app_file), "--app", "does_not_exist", "--out", str(tmp_path / "out.json")])


def test_routes_errors_when_attribute_is_neither_app_nor_callable(tmp_path: Path) -> None:
    path = tmp_path / "app.py"
    path.write_text("app = 42\n")

    with pytest.raises(SystemExit):
        routes([str(path), "--out", str(tmp_path / "out.json")])


def test_routes_errors_on_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        routes([str(tmp_path / "does-not-exist.py")])


def test_main_dispatches_to_routes(tmp_path: Path, routes_app_file: Path) -> None:
    out = tmp_path / "nanobar.api-routes.json"

    main(["routes", str(routes_app_file), "--out", str(out)])

    assert out.is_file()


def test_routes_supports_a_dotted_module_path_for_packages_using_relative_imports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A package whose app.py uses `from . import sibling` can't be loaded via a bare file path
    # (no parent package context for the relative import to resolve against). --module goes
    # through importlib.import_module instead, which resolves relative imports correctly as
    # long as the package's parent directory is on sys.path (as it is here, and as it is for
    # `uv run` from a repo root).
    package_dir = tmp_path / "mypkg"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("")
    (package_dir / "sibling.py").write_text("GREETING = 'hi'\n")
    (package_dir / "app.py").write_text(
        "from . import sibling\n"
        "from starlette.routing import Route\n"
        "from nanobar_api import NanobarAPI\n\n"
        "async def handler(request):\n"
        "    return None\n\n"
        "def build_app():\n"
        "    assert sibling.GREETING == 'hi'\n"
        "    return NanobarAPI(openapi_url=None, docs_url=None, routes=[Route('/ping', handler, methods=['GET'])])\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    out = tmp_path / "nanobar.api-routes.json"

    routes(["--module", "mypkg.app", "--app", "build_app", "--out", str(out)])

    document = json.loads(out.read_text())
    assert [e["route_key"] for e in document["routes"]] == ["GET /ping"]


def test_routes_module_errors_on_unimportable_module(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        routes(["--module", "does.not.exist", "--out", str(tmp_path / "out.json")])


def test_routes_module_errors_on_missing_app_attribute(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    package_dir = tmp_path / "mypkg2"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("")
    (package_dir / "app.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))

    with pytest.raises(SystemExit):
        routes(["--module", "mypkg2.app", "--out", str(tmp_path / "out.json")])
