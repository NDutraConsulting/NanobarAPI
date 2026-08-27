from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

#: Matches `fastapi dev`'s default-to-`main.py` convention, using this project's own
#: established default entrypoint name (`server.py`, already the root demo file) instead -- not
#: `app.py`, which would be permanently shadowed by a sibling `app/` package (see `server.py`'s
#: own docstring).
DEFAULT_APP_FILE = Path("server.py")


def _resolve_app_path(parser: argparse.ArgumentParser, path: Path | None) -> Path:
    if path is None:
        if not DEFAULT_APP_FILE.is_file():
            parser.error(f"no {DEFAULT_APP_FILE} found in the current directory — pass a path explicitly")
        path = DEFAULT_APP_FILE

    if not path.is_file():
        parser.error(f"{path} is not a file")

    return path


def dev(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="nanobar dev", description="Run a NanobarAPI app with auto-reload.")
    parser.add_argument(
        "path",
        type=Path,
        nargs="?",
        default=None,
        help=f"path to the Python file defining your app (default: {DEFAULT_APP_FILE} in the current directory)",
    )
    parser.add_argument("--app", default="app", help="name of the NanobarAPI instance in that file (default: app)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    args.path = _resolve_app_path(parser, args.path)

    try:
        import uvicorn
    except ImportError:
        parser.error("uvicorn is required for `nanobar dev` — install the 'serve' extra (NanobarAPI[serve]).")

    module_name = args.path.stem
    app_dir = str(args.path.resolve().parent)
    import_string = f"{module_name}:{args.app}"

    print(f"NanobarAPI dev server: http://{args.host}:{args.port}")
    print(f"Docs:                  http://{args.host}:{args.port}/docs")

    uvicorn.run(import_string, host=args.host, port=args.port, reload=True, app_dir=app_dir)


def _import_module_from_path(path: Path) -> ModuleType:
    """Imports `path` as a standalone module, in-process -- unlike `dev()` above, `routes()`
    below needs the live app object itself (to walk its route tree), not just a dotted string
    to hand to uvicorn (which does its own import, out of process from this CLI's point of
    view)."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive: not reachable for a real .py file
        raise SystemExit(f"could not import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def routes(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="nanobar routes", description="Scan an app's route tree and write a route manifest JSON file."
    )
    parser.add_argument(
        "path",
        type=Path,
        nargs="?",
        default=None,
        help=f"path to the Python file defining your app (default: {DEFAULT_APP_FILE} in the current directory); "
        "ignored if --module is given",
    )
    parser.add_argument(
        "--module",
        default=None,
        help="dotted, importable module path (e.g. mypackage.app) instead of a file path -- required for an "
        "app that lives inside a package and uses relative imports, which a bare file path can't load",
    )
    parser.add_argument(
        "--app",
        default="app",
        help="name of the NanobarAPI instance, or a zero-argument factory returning one, in that module (default: app)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("nanobar.api-routes.json"),
        help="output path for the route manifest (default: ./nanobar.api-routes.json)",
    )
    args = parser.parse_args(argv)

    if args.module:
        import importlib

        try:
            module = importlib.import_module(args.module)
        except ImportError as exc:
            parser.error(f"could not import module {args.module!r}: {exc}")
    else:
        args.path = _resolve_app_path(parser, args.path)
        module = _import_module_from_path(args.path)

    if not hasattr(module, args.app):
        parser.error(f"{args.module or args.path} has no attribute {args.app!r}")
    candidate = getattr(module, args.app)

    from starlette.applications import Starlette

    if isinstance(candidate, Starlette):
        app = candidate
    elif callable(candidate):
        app = candidate()
    else:
        parser.error(f"{args.app!r} in {args.module or args.path} is neither a Starlette app nor a callable factory")

    from nanobar_api.route_manifest import write_route_manifest

    entries = write_route_manifest(app, args.out)
    domains = sorted({entry.domain for entry in entries})
    print(f"Wrote {len(entries)} route(s) across {len(domains)} domain(s) to {args.out}")
    for domain in domains:
        print(f"  {domain or '(root)'}")


_SUBCOMMANDS = {"dev": dev, "routes": routes}


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:]) if argv is None else argv
    if not argv or argv[0] not in _SUBCOMMANDS:
        print(
            "Usage: nanobar dev [path] [--app NAME] [--host HOST] [--port PORT]\n"
            "       nanobar routes [path] [--app NAME] [--out FILE]",
            file=sys.stderr,
        )
        raise SystemExit(1)
    _SUBCOMMANDS[argv[0]](argv[1:])
