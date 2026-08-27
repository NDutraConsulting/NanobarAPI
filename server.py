"""Entrypoint for `uv run nanobar dev server.py` — runs the Nanobar Dashboard demo app.

Not named `app.py`: `nanobar dev`'s own default target is `./app.py`
(`nanobar_api.cli.DEFAULT_APP_FILE`), but a bare `app.py` can never coexist with the top-level
`app/` package (the whole application root) also living at the repo root -- Python's import
system resolves `import app` to the *package*, permanently shadowing any same-named `app.py`
file in the same directory (confirmed live: `import app` returned the application-root module,
not an entrypoint file, when both existed side by side). Run this demo with `nanobar dev
server.py` (or `uvicorn server:app`) instead of the bare, no-argument `nanobar dev`.

Kept as a separate module, not folded into `app/main.py` itself: `app/main.py`'s `build_app()`
is a factory function, deliberately not instantiated at its own module's import time -- the test
suite does `from app.main import build_app` without ever wanting a real app built (and its real
databases touched) as a side effect of that import.
"""

from app.main import build_app

app = build_app()
