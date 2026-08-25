from __future__ import annotations

import argparse
import sys
from pathlib import Path

#: Matches `fastapi dev`'s default-to-`main.py` convention, using this project's own
#: established default entrypoint name (`app.py`, already the root demo file) instead.
DEFAULT_APP_FILE = Path("app.py")


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

    if args.path is None:
        if not DEFAULT_APP_FILE.is_file():
            parser.error(f"no {DEFAULT_APP_FILE} found in the current directory — pass a path explicitly")
        args.path = DEFAULT_APP_FILE

    if not args.path.is_file():
        parser.error(f"{args.path} is not a file")

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


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:]) if argv is None else argv
    if not argv or argv[0] != "dev":
        print("Usage: nanobar dev [path] [--app NAME] [--host HOST] [--port PORT]", file=sys.stderr)
        raise SystemExit(1)
    dev(argv[1:])
