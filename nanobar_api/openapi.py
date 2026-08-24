from __future__ import annotations

import html
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from starlette.responses import HTMLResponse
from starlette.routing import BaseRoute
from starlette.schemas import BaseSchemaGenerator

from nanobar_api.validation import to_json_schema

_ENVELOPE_STATUS_SCHEMA = {"type": "string", "enum": ["success", "error", "timeout"]}
_RESULT_TYPE_SCHEMA = {"type": "string", "enum": ["object", "array", "map"]}

_SWAGGER_JS_URL = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"
_SWAGGER_CSS_URL = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css"


@dataclass(frozen=True)
class EndpointSchema:
    request: type | None = None
    response: type | None = None
    summary: str | None = None


def endpoint_schema[F: Callable[..., Any]](
    *,
    request: type | None = None,
    response: type | None = None,
    summary: str | None = None,
) -> Callable[[F], F]:
    schema = EndpointSchema(request=request, response=response, summary=summary)

    def decorator(func: F) -> F:
        func.__nanobar_schema__ = schema  # type: ignore[attr-defined]
        return func

    return decorator


def _envelope_response_schema(data_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "status": _ENVELOPE_STATUS_SCHEMA,
            "msg": {"type": "string"},
            "result": {
                "type": "object",
                "properties": {"type": _RESULT_TYPE_SCHEMA, "data": data_schema},
            },
        },
    }


class NanobarSchemaGenerator(BaseSchemaGenerator):
    def __init__(self, title: str, version: str) -> None:
        self.title = title
        self.version = version

    def get_schema(self, routes: list[BaseRoute]) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "openapi": "3.1.0",
            "info": {"title": self.title, "version": self.version},
            "paths": {},
        }

        for endpoint in self.get_endpoints(routes):
            info: EndpointSchema | None = getattr(endpoint.func, "__nanobar_schema__", None)
            response_type = info.response if info else None

            operation: dict[str, Any] = {
                "responses": {
                    "200": {
                        "description": "Successful response",
                        "content": {
                            "application/json": {
                                "schema": _envelope_response_schema(
                                    to_json_schema(response_type) if response_type else {}
                                )
                            }
                        },
                    }
                }
            }
            if info and info.summary:
                operation["summary"] = info.summary
            if info and info.request:
                operation["requestBody"] = {"content": {"application/json": {"schema": to_json_schema(info.request)}}}

            schema["paths"].setdefault(endpoint.path, {})[endpoint.http_method] = operation

        return schema


def _html_safe_json(value: Any) -> str:
    return json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def get_swagger_ui_html(*, openapi_url: str, title: str) -> HTMLResponse:
    safe_title = html.escape(title)
    content = f"""<!DOCTYPE html>
<html>
<head>
<link type="text/css" rel="stylesheet" href="{_SWAGGER_CSS_URL}">
<title>{safe_title}</title>
</head>
<body>
<div id="swagger-ui"></div>
<script src="{_SWAGGER_JS_URL}"></script>
<script>
const ui = SwaggerUIBundle({{
    url: {_html_safe_json(openapi_url)},
    presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
}})
</script>
</body>
</html>
"""
    return HTMLResponse(content)
