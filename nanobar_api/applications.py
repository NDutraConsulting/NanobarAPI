from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ExceptionHandler, Lifespan

from nanobar_api.openapi import (
    SWAGGER_STATIC_DIR,
    SWAGGER_STATIC_MOUNT,
    NanobarSchemaGenerator,
    endpoint_schema,
    get_swagger_ui_html,
)
from nanobar_api.routing import RouteHandler, adapt_handler


class NanobarAPI(Starlette):
    def __init__(
        self,
        debug: bool = False,
        routes: Sequence[BaseRoute] | None = None,
        middleware: Sequence[Middleware] | None = None,
        exception_handlers: Mapping[object, ExceptionHandler] | None = None,
        lifespan: Lifespan[Starlette] | None = None,
        *,
        max_body_size: int | None = None,
        title: str = "NanobarAPI",
        version: str = "0.1.0",
        openapi_url: str | None = "/openapi.json",
        docs_url: str | None = "/docs",
    ) -> None:
        self.title = title
        self.version = version
        self.openapi_url = openapi_url
        self.docs_url = docs_url
        self.schema_generator = NanobarSchemaGenerator(title=title, version=version)

        all_routes: list[BaseRoute] = list(routes or [])
        if openapi_url is not None:
            all_routes.append(Route(openapi_url, self._openapi, include_in_schema=False))
        if docs_url is not None and openapi_url is not None:
            all_routes.append(Route(docs_url, self._docs, include_in_schema=False))
            all_routes.append(
                Mount(SWAGGER_STATIC_MOUNT, app=StaticFiles(directory=SWAGGER_STATIC_DIR), name="swagger-ui-static")
            )

        super().__init__(
            debug=debug,
            routes=all_routes,
            middleware=middleware,
            exception_handlers=exception_handlers,
            lifespan=lifespan,
            max_body_size=max_body_size,
        )

    async def _openapi(self, request: Request) -> Response:
        return JSONResponse(self.schema_generator.get_schema(self.routes))

    async def _docs(self, request: Request) -> Response:
        assert self.openapi_url is not None
        return get_swagger_ui_html(openapi_url=self.openapi_url, title=self.title)

    def _endpoint(
        self,
        path: str,
        methods: list[str],
        *,
        request: type | None,
        response: type | None,
        summary: str | None,
        include_in_schema: bool,
    ) -> Callable[[RouteHandler], RouteHandler]:
        def decorator(func: RouteHandler) -> RouteHandler:
            endpoint = adapt_handler(func)
            if request is not None or response is not None or summary is not None:
                endpoint_schema(request=request, response=response, summary=summary)(endpoint)
            self.add_route(path, endpoint, methods=methods, include_in_schema=include_in_schema)
            return func

        return decorator

    def get(
        self,
        path: str,
        *,
        request: type | None = None,
        response: type | None = None,
        summary: str | None = None,
        include_in_schema: bool = True,
    ) -> Callable[[RouteHandler], RouteHandler]:
        return self._endpoint(
            path, ["GET"], request=request, response=response, summary=summary, include_in_schema=include_in_schema
        )

    def post(
        self,
        path: str,
        *,
        request: type | None = None,
        response: type | None = None,
        summary: str | None = None,
        include_in_schema: bool = True,
    ) -> Callable[[RouteHandler], RouteHandler]:
        return self._endpoint(
            path, ["POST"], request=request, response=response, summary=summary, include_in_schema=include_in_schema
        )

    def put(
        self,
        path: str,
        *,
        request: type | None = None,
        response: type | None = None,
        summary: str | None = None,
        include_in_schema: bool = True,
    ) -> Callable[[RouteHandler], RouteHandler]:
        return self._endpoint(
            path, ["PUT"], request=request, response=response, summary=summary, include_in_schema=include_in_schema
        )

    def patch(
        self,
        path: str,
        *,
        request: type | None = None,
        response: type | None = None,
        summary: str | None = None,
        include_in_schema: bool = True,
    ) -> Callable[[RouteHandler], RouteHandler]:
        return self._endpoint(
            path, ["PATCH"], request=request, response=response, summary=summary, include_in_schema=include_in_schema
        )

    def delete(
        self,
        path: str,
        *,
        request: type | None = None,
        response: type | None = None,
        summary: str | None = None,
        include_in_schema: bool = True,
    ) -> Callable[[RouteHandler], RouteHandler]:
        return self._endpoint(
            path, ["DELETE"], request=request, response=response, summary=summary, include_in_schema=include_in_schema
        )
