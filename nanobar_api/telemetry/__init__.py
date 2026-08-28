"""`nanobar_api.telemetry` — home of both `NanobarTelemetry`/`NanobarProps` (this package's
original, unmoved content, now in `nanobar_telemetry.py`) and the new trace/span capture domain
being built out per `.focusari/telemetry-domain-refactor-plan-with-tasks.md`.

Re-exports `NanobarProps`/`NanobarTelemetry` here so every existing
`from nanobar_api.telemetry import NanobarTelemetry`-style import across the codebase keeps
working unchanged after the `telemetry.py` module -> `telemetry/` package promotion.
"""

from __future__ import annotations

from nanobar_api.telemetry.nanobar_telemetry import NanobarProps, NanobarTelemetry

__all__ = ["NanobarProps", "NanobarTelemetry"]
