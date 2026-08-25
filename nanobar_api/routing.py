from __future__ import annotations

import functools
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from nanobar_api.envelope import success

RouteHandler = Callable[..., Any]


def _accepts_request(func: RouteHandler) -> bool:
    try:
        parameters = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False
    return len(parameters) > 0


def adapt_handler(func: RouteHandler) -> Callable[[Request], Awaitable[Response]]:
    """Wraps a plain handler function into a real Starlette ASGI endpoint.

    The wrapped function may be sync or async (sync handlers run in a threadpool, same as
    Starlette's own dispatch, so blocking code never blocks the event loop), and may take a
    `request: Request` parameter or none at all — whichever it declares.

    Its return value is used as follows:
    - a real Starlette `Response` is returned unchanged (a full escape hatch, no wrapping);
    - anything else is wrapped via `nanobar_api.envelope.success()` and returned as JSON —
      the same envelope contract this project's services already return, applied here so a
      route handler doesn't have to build it by hand for the common case.

    Deliberately does not bind path/query parameters to individual function arguments —
    that edges toward the signature-based dependency injection this project has already
    decided against. A handler that needs them takes `request` and reads
    `request.path_params`/`.query_params` itself, same as every handler already written
    against this project does.
    """
    takes_request = _accepts_request(func)
    is_async = inspect.iscoroutinefunction(func)

    @functools.wraps(func)
    async def endpoint(request: Request) -> Response:
        args = (request,) if takes_request else ()
        result = await func(*args) if is_async else await run_in_threadpool(func, *args)
        if isinstance(result, Response):
            return result
        return JSONResponse(success(result))

    return endpoint
