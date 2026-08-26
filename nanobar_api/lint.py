"""Static checks with no cheap runtime equivalent — currently just one: "services can't call
services or controllers" (`.focusari/nanobar_ServiceDomain_abstract_class_buildplan-with-tasks.md`
§1.3). Python has no clean, cheap way to enforce this at runtime without inspecting the call
stack on every call or building a mediating DI container this project doesn't otherwise need, so
it's a static, `ast`-based scan instead: no module that defines a `NanobarService` subclass may
itself construct a `NanobarService`/`NanobarController`, or any *subclass* of either — a
transitive closure computed across every scanned file first, not just the two literal base names,
since real violations construct concrete subclasses (`PaymentService()`), never the abstract base
directly.

Run via `scripts/check` (folded in there, not a separate script — no CI configuration exists
anywhere in this repo yet, confirmed by search, so `scripts/check` is the closest thing this
project has to "wired into CI" today).
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterable
from pathlib import Path

_SEED_BANNED = frozenset({"NanobarService", "NanobarController"})


def _base_name(base: ast.expr) -> str | None:
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return None


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _direct_base_names(node: ast.ClassDef) -> set[str]:
    names = {name for base in node.bases if (name := _base_name(base)) is not None}
    return names


def _defines_nanobar_service_subclass(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.ClassDef) and "NanobarService" in _direct_base_names(node) for node in ast.walk(tree)
    )


def _collect_banned_construction_targets(trees: Iterable[ast.AST]) -> frozenset[str]:
    """Transitive closure of every class name (across all scanned files) that subclasses
    `NanobarService`/`NanobarController`, directly or through intermediate subclasses."""
    class_bases: dict[str, set[str]] = {}
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                class_bases[node.name] = _direct_base_names(node)

    banned = set(_SEED_BANNED)
    changed = True
    while changed:
        changed = False
        for name, bases in class_bases.items():
            if name not in banned and bases & banned:
                banned.add(name)
                changed = True
    return frozenset(banned)


def check_service_boundaries(paths: Iterable[Path]) -> list[str]:
    """Returns one human-readable violation string per disallowed construction found, empty if
    clean. Files that don't define a `NanobarService` subclass at all are skipped when scanning
    for violations — the rule only applies *inside* such a module, not to code that merely
    imports one — but every file is still parsed once to build the cross-file banned-name set.
    """
    paths = list(paths)
    trees = {path: ast.parse(path.read_text(), filename=str(path)) for path in paths}
    banned = _collect_banned_construction_targets(trees.values())

    violations: list[str] = []
    for path, tree in trees.items():
        if not _defines_nanobar_service_subclass(tree):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _call_name(node.func)
                if name in banned:
                    violations.append(f"{path}:{node.lineno}: constructs {name}() inside a NanobarService module")
    return violations


def main(argv: list[str] | None = None) -> None:
    """Matches `nanobar_api.cli.main`'s own convention: defaults `argv` from `sys.argv`, raises
    `SystemExit` on failure rather than returning a code, referenced by a `[project.scripts]`
    entry point rather than a `python -m` / `if __name__ == "__main__":` guard."""
    argv = list(sys.argv[1:]) if argv is None else argv
    paths = [p for arg in argv for p in (Path(arg).rglob("*.py") if Path(arg).is_dir() else [Path(arg)])]
    violations = check_service_boundaries(paths)
    for violation in violations:
        print(violation)
    if violations:
        raise SystemExit(1)
