"""Per-request shadow-mode signal -- a lightweight, same-process alternative to routing a
regression-brick replay to an entirely separate shadow deployment/process. An app that builds
more than one persistence target per domain (a live one and a disposable "shadow replica" used
for replaying captured `RegressionBrick`s) can check `is_shadow_mode()` wherever it currently
picks which session factory/engine to use, instead of standing up a second app instance.

Mirrors `nanobar_api.middleware.trace`'s `current_trace_id`/`current_route_key` shape exactly: a
`contextvars.ContextVar` set by a small ASGI middleware at request entry, reset in a `finally`,
read anywhere further down the same request's call stack (works across `await` boundaries within
one task, same as those).

This intentionally does **not** implement `.focusari/regression-brick-system-plan.md` §5's fuller
"Shadow Execution and Persistence Rerouting" design (a signed internal header contract verified by
`ShadowRoutingMiddleware`, an isolated Shadow Worker process with no production write credentials,
fail-closed on any ambiguity) -- that design is for a genuinely multi-tenant production deployment
where an attacker-controlled client could otherwise forge the header to bypass write protections.
This module is for a single local dev/beta instance (see the project's own "not for production
use" status) where the only caller ever setting this header is the app's own replay-dispatch code,
not untrusted public traffic -- a plain, unsigned header is a deliberately honest match for that
threat model, not a shortcut around a decision that hasn't been made. Revisit if this ever needs
to run where an untrusted client can reach the same port.
"""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

#: Lowercase, matching every other header name compared against raw ASGI scope headers in this
#: codebase (`nanobar_api.capture.policy`, `nanobar_api.admin_auth`) -- ASGI header names always
#: arrive lowercased regardless of what the client sent, so comparing against anything else would
#: never match. Named `Nanobar-Mode`/`shadow` to match the header/value already named (but never
#: built) in `.focusari/regression-brick-system-plan.md` §5 -- same vocabulary, smaller build.
SHADOW_MODE_HEADER = b"nanobar-mode"
SHADOW_MODE_VALUE = b"shadow"

current_shadow_mode: contextvars.ContextVar[bool] = contextvars.ContextVar("current_shadow_mode", default=False)


def is_shadow_mode() -> bool:
    return current_shadow_mode.get()


class ShadowModeMiddleware:
    """Sets `current_shadow_mode` for the duration of one request based on whether
    `SHADOW_MODE_HEADER` equals `SHADOW_MODE_VALUE`, reset in a `finally` -- same lifecycle shape
    as `nanobar_api.middleware.trace`'s `current_route_key`."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        shadow = headers.get(SHADOW_MODE_HEADER, b"").lower() == SHADOW_MODE_VALUE

        token = current_shadow_mode.set(shadow)
        try:
            await self.app(scope, receive, send)
        finally:
            current_shadow_mode.reset(token)
