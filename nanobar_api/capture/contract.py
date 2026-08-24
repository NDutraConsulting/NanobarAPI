from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from starlette.routing import BaseRoute

from nanobar_api.openapi import EndpointSchema, NanobarSchemaGenerator
from nanobar_api.validation import to_json_schema


@dataclass(frozen=True)
class EndpointContract:
    path: str
    method: str
    schema_version: str
    request_schema: dict[str, Any] | None
    response_schema: dict[str, Any] | None
    summary: str | None


def build_endpoint_contract(
    path: str,
    method: str,
    endpoint_schema: EndpointSchema | None,
    schema_version: str = "1.0",
) -> EndpointContract:
    request_schema = to_json_schema(endpoint_schema.request) if endpoint_schema and endpoint_schema.request else None
    response_schema = to_json_schema(endpoint_schema.response) if endpoint_schema and endpoint_schema.response else None
    summary = endpoint_schema.summary if endpoint_schema else None

    return EndpointContract(
        path=path,
        method=method.lower(),
        schema_version=schema_version,
        request_schema=request_schema,
        response_schema=response_schema,
        summary=summary,
    )


def build_contracts_for_routes(routes: list[BaseRoute], schema_version: str = "1.0") -> list[EndpointContract]:
    generator = NanobarSchemaGenerator(title="", version="")
    contracts: list[EndpointContract] = []

    for endpoint in generator.get_endpoints(routes):
        info: EndpointSchema | None = getattr(endpoint.func, "__nanobar_schema__", None)
        contracts.append(
            build_endpoint_contract(endpoint.path, endpoint.http_method, info, schema_version=schema_version)
        )

    return contracts
