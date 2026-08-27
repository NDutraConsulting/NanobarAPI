"""Entrypoint for `uv run nanobar dev` — runs the Nanobar Dashboard demo app.

Not named `app.py`: a bare `app.py` can never coexist with the top-level `app/` package (the
whole application root) also living at the repo root -- Python's import system resolves `import
app` to the *package*, permanently shadowing any same-named `app.py` file in the same directory
(confirmed live: `import app` returned the application-root module, not an entrypoint file, when
both existed side by side). `nanobar dev`'s own default target is `./server.py`
(`nanobar_api.cli.DEFAULT_APP_FILE`), so the bare, no-argument `nanobar dev` already finds this
file automatically.

Kept as a separate module, not folded into `app/main.py` itself: `app/main.py`'s `build_app()`
is a factory function, deliberately not instantiated at its own module's import time -- the test
suite does `from app.main import build_app` without ever wanting a real app built (and its real
databases touched) as a side effect of that import.
"""

from app.main import build_app

app = build_app()
