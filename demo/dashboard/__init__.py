"""Nanobar Dashboard demo app.

A small NanobarAPI (Starlette-based) admin app that drills down through the
regression-brick data model: Nanobar Dashboard (grouped by
``monitor_target_refs.target_type``) -> one Nanobar -> its bound
RegressionBricks -> one brick -> a triage view (review status) shown as a
kanban-style board. See ``.focusari/nanobarapi-architecture-rules.md``,
"Nanobar Concept & Dashboard", for the design intent this implements.

Reads the regression-bricks SQLite database whose path is produced by
:func:`nanobar_api.bricks.store.connect` (schema created idempotently if
missing) — see :mod:`demo.dashboard.db` for how that path is resolved.
"""

from __future__ import annotations

from .app import build_app as build_app

__all__ = ["build_app"]
