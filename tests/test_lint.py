from __future__ import annotations

from pathlib import Path

import pytest

from nanobar_api.lint import check_service_boundaries, main


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content)
    return path


def test_clean_service_module_has_no_violations(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "clean_service.py",
        """
from nanobar_api.services import NanobarService

class OrderService(NanobarService):
    def handle(self, request):
        return request
""",
    )

    assert check_service_boundaries([path]) == []


def test_module_not_defining_a_service_is_skipped_even_with_a_construction_call(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "not_a_service.py",
        """
def build():
    return NanobarController()
""",
    )

    assert check_service_boundaries([path]) == []


def test_service_module_constructing_another_service_is_a_violation(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "bad_service.py",
        """
from nanobar_api.services import NanobarService

class OrderService(NanobarService):
    def handle(self, request):
        other = PaymentService()
        return other.handle(request)

class PaymentService(NanobarService):
    def handle(self, request):
        return request
""",
    )

    violations = check_service_boundaries([path])

    assert len(violations) == 1
    assert "PaymentService" in violations[0]
    assert str(path) in violations[0]


def test_service_module_constructing_a_controller_is_a_violation(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "bad_service2.py",
        """
from nanobar_api.services import NanobarService

class OrderService(NanobarService):
    def handle(self, request):
        controller = NanobarController()
        return controller
""",
    )

    violations = check_service_boundaries([path])

    assert len(violations) == 1
    assert "NanobarController" in violations[0]


def test_main_succeeds_silently_for_clean_paths(tmp_path: Path) -> None:
    path = _write(tmp_path, "clean.py", "x = 1\n")

    main([str(path)])  # does not raise


def test_main_raises_system_exit_on_violations(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "bad.py",
        """
from nanobar_api.services import NanobarService

class OrderService(NanobarService):
    def handle(self, request):
        return NanobarController()
""",
    )

    with pytest.raises(SystemExit) as exc_info:
        main([str(path)])

    assert exc_info.value.code == 1


def test_main_expands_a_directory_argument(tmp_path: Path) -> None:
    sub = tmp_path / "pkg"
    sub.mkdir()
    _write(sub, "a.py", "x = 1\n")
    _write(sub, "b.py", "y = 2\n")

    main([str(tmp_path)])  # does not raise


def test_main_defaults_argv_from_sys_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(tmp_path, "clean.py", "x = 1\n")
    monkeypatch.setattr("sys.argv", ["prog", str(path)])

    main()  # does not raise


def test_dotted_base_class_name_is_recognized(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "dotted_base.py",
        """
import nanobar_api.services as services

class OrderService(services.NanobarService):
    def handle(self, request):
        return services.NanobarController()
""",
    )

    violations = check_service_boundaries([path])

    assert len(violations) == 1
    assert "NanobarController" in violations[0]


def test_non_name_non_attribute_base_and_call_are_ignored(tmp_path: Path) -> None:
    # A subscripted base (e.g. a generic alias, `Sequence[int]`) and a call through a subscript
    # expression -- neither an `ast.Name` nor an `ast.Attribute` -- must not crash the scan,
    # just be ignored.
    path = _write(
        tmp_path,
        "odd_shapes.py",
        """
from collections.abc import Sequence
from nanobar_api.services import NanobarService

class OrderService(NanobarService, Sequence[int]):
    handlers = {}

    def handle(self, request):
        return self.handlers["x"](request)
""",
    )

    assert check_service_boundaries([path]) == []
