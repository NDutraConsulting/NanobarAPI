from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nanobar_api.cli import dev, main


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
