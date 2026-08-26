"""`NanobarORMWrapper` — the class-based entry point onto boundary 5 (`regression-brick-system-
plan.md` §4): a single SQLAlchemy `Engine`-level event hook, not per-repository manual
instrumentation ("capture no longer depends on every repository correctly emitting events by
hand" — the design doc's own words).

Per `.focusari/nanobar_ServiceDomain_abstract_class_buildplan-with-tasks.md` §4.
"""

from __future__ import annotations

import weakref
from typing import TYPE_CHECKING, Any

from sqlalchemy import event

from nanobar_api.capture.layer_capture import capture_layer
from nanobar_api.middleware.trace import current_route_key

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.engine.interfaces import ExceptionContext

    from nanobar_api.eventbus.queue_repository import EventQueueRepository

#: Guards against double-registering listeners on the same `Engine` (e.g. `install()` called
#: twice for an app-factory pattern invoked more than once) -- `event.listens_for` has no
#: built-in dedup, and a double-registration would silently double-emit every ORM brick.
#: A `WeakSet` so an `Engine` that's since been discarded doesn't keep this alive forever.
_installed_engines: weakref.WeakSet[Engine] = weakref.WeakSet()


class NanobarORMWrapper:
    @staticmethod
    def install(engine: Engine, repository: EventQueueRepository) -> None:
        """Registers `after_cursor_execute`/`handle_error` listeners on `engine`. Each reads
        `current_route_key` (`nanobar_api.middleware.trace`, set by `NanobarController.handle()`
        for the duration of a controller call) and emits a `capture_layer(layer="orm")` event
        tagged `nanobar_type="orm-request-response"`.

        Idempotent per `engine`: a second `install()` call on the same engine is a no-op rather
        than registering duplicate listeners.

        **Bind parameter *values* are deliberately never captured — only the statement text and
        whether it's a bulk (`executemany`) operation.** This project already treats capture as
        allow-list-first, not deny-list (`nanobar_api.capture.policy.CapturePolicy` for HTTP
        headers/query params); a query's bind parameters are exactly where real, arbitrary user
        data (emails, tokens, anything) ends up, and the source spec doesn't address this at all.
        Capturing them by default would silently reverse this project's own established posture.
        Flagged, not solved by inventing a parameter-level allow-list here — a real gap if a
        human reviewing a brick later needs to see the actual values, not just the query shape.

        No `before_cursor_execute` listener — nothing here needs a "the query started" signal on
        its own; `after_cursor_execute` (success) and `handle_error` (failure) are the only two
        outcomes that produce a capture, matching how SQLAlchemy's own event sequence works (a
        failing statement fires `handle_error` in place of `after_cursor_execute`, never both).
        """
        if engine in _installed_engines:
            return
        _installed_engines.add(engine)

        def after_cursor_execute(
            conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
        ) -> None:
            capture_layer(
                repository,
                "orm",
                {"statement": statement, "executemany": executemany},
                {"rowcount": cursor.rowcount, "error_type": None},
                nanobar_type="orm-request-response",
                route_key=current_route_key.get(),
            )

        def handle_error(exception_context: ExceptionContext) -> None:
            error_type = type(exception_context.sqlalchemy_exception).__name__
            capture_layer(
                repository,
                "orm",
                {"statement": exception_context.statement, "executemany": None},
                {"error_type": error_type, "error_message": str(exception_context.sqlalchemy_exception)},
                nanobar_type="orm-request-response",
                error=True,
                route_key=current_route_key.get(),
            )

        event.listens_for(engine, "after_cursor_execute")(after_cursor_execute)
        event.listens_for(engine, "handle_error")(handle_error)
