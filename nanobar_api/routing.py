from __future__ import annotations

import functools
import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from nanobar_api.envelope import success
from nanobar_api.validator_gate import NanobarValidatorGate

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


@dataclass(frozen=True)
class NanobarRouteRule:
    """One route declaration: a path/method (REST) or service/method key (gRPC), and the
    `NanobarValidatorGate` subclass that handles it.

    `middleware` is additive over the owning `NanobarRouteSet.middleware`, domain first — real,
    not speculative: a rule that must bypass part of its domain's default middleware stack (e.g.
    a login route that can't itself require being logged in) declares its own list here rather
    than the adapter trying to infer an exemption.
    """

    key: str
    gate: type[NanobarValidatorGate]
    transport: Literal["rest", "grpc"] = "rest"
    label: str | None = None
    domain: str | None = None
    middleware: Sequence[Middleware] = ()


class NanobarRouteSet:
    """Groups `NanobarRouteRule`s that share a domain and, optionally, a dedicated middleware
    stack — subclassed per domain (e.g. a `checkout` domain declaring every `/checkout/*` rule
    plus a `CheckoutAuthMiddleware` that only applies to that group).
    """

    domain: ClassVar[str]
    rules: ClassVar[tuple[NanobarRouteRule, ...]]
    middleware: ClassVar[Sequence[Middleware]] = ()


@dataclass(frozen=True)
class MountedRouteSet:
    """What `RestRouteAdapter.build_mount` produces: the `Mount` itself, plus which middleware
    class names actually applied to each rule — captured at declaration time, since Starlette
    gives no way to read declared middleware back off a constructed `Route`/`Mount`.
    """

    mount: Mount
    rule_middleware_names: dict[str, tuple[str, ...]]


def _parse_rest_route_key(key: str) -> tuple[str, str]:
    method, _, path = key.partition(" ")
    if not method or not path:
        raise ValueError(f"malformed REST route key {key!r}; expected 'METHOD /path'")
    return method.upper(), path


def _gate_endpoint(gate_cls: type[NanobarValidatorGate], request_type: str) -> Callable[[Request], Awaitable[Any]]:
    async def endpoint(request: Request) -> Any:
        return await gate_cls()(request, request_type)

    return endpoint


class RestRouteAdapter:
    """Builds a `starlette.routing.Mount` from a `NanobarRouteSet` — deliberately not via
    `NanobarAPI.get`/`.post`/etc., since `Starlette.add_route` (what those call) has no
    `middleware` parameter and silently drops any domain- or rule-level middleware given to it.
    Every REST rule still dispatches through `adapt_handler`'s sync/async/envelope handling —
    this is a declarative registration path, not a parallel dispatch mechanism.
    """

    @staticmethod
    def build_mount(route_set: type[NanobarRouteSet]) -> MountedRouteSet:
        domain_middleware_names = tuple(getattr(m.cls, "__name__", str(m.cls)) for m in route_set.middleware)
        rule_middleware_names: dict[str, tuple[str, ...]] = {}
        routes: list[Route] = []
        for rule in route_set.rules:
            if rule.transport != "rest":
                continue
            method, path = _parse_rest_route_key(rule.key)
            endpoint = adapt_handler(_gate_endpoint(rule.gate, rule.key))
            routes.append(Route(path, endpoint, methods=[method], middleware=list(rule.middleware)))
            rule_middleware_names[rule.key] = domain_middleware_names + tuple(
                getattr(m.cls, "__name__", str(m.cls)) for m in rule.middleware
            )

        mount = Mount(f"/{route_set.domain}", routes=routes, middleware=list(route_set.middleware))
        return MountedRouteSet(mount=mount, rule_middleware_names=rule_middleware_names)

    @staticmethod
    def register(app: Starlette, route_set: type[NanobarRouteSet]) -> MountedRouteSet:
        mounted = RestRouteAdapter.build_mount(route_set)
        app.routes.append(mounted.mount)
        return mounted
