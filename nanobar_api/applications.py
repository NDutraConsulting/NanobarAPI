from __future__ import annotations

from collections.abc import Mapping, Sequence

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Route
from starlette.types import ExceptionHandler, Lifespan

from nanobar_api.openapi import NanobarSchemaGenerator, get_swagger_ui_html


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
