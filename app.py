"""Entrypoint for `uv run nanobar dev` — runs the Nanobar Dashboard demo app."""

from demo.dashboard.app import build_app

app = build_app()
