from dataclasses import dataclass

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Route

from nanobar_api.capture.contract import build_contracts_for_routes, build_endpoint_contract
from nanobar_api.openapi import EndpointSchema, endpoint_schema
from nanobar_api.validation import to_json_schema


@dataclass
class Widget:
    name: str
    quantity: int


@dataclass
class WidgetCreated:
    id: int


def test_build_endpoint_contract_with_request_and_response() -> None:
    schema = EndpointSchema(request=Widget, response=WidgetCreated, summary="Create a widget")

    contract = build_endpoint_contract("/widgets", "post", schema)

    assert contract.path == "/widgets"
    assert contract.method == "post"
    assert contract.schema_version == "1.0"
    assert contract.summary == "Create a widget"
    assert contract.request_schema == to_json_schema(Widget)
    assert contract.response_schema == to_json_schema(WidgetCreated)


def test_build_endpoint_contract_with_none_schema() -> None:
    contract = build_endpoint_contract("/undocumented", "get", None)

    assert contract.path == "/undocumented"
    assert contract.method == "get"
    assert contract.request_schema is None
    assert contract.response_schema is None
    assert contract.summary is None


def test_build_endpoint_contract_with_only_request_set() -> None:
    schema = EndpointSchema(request=Widget)

    contract = build_endpoint_contract("/widgets", "post", schema)

    assert contract.request_schema == to_json_schema(Widget)
    assert contract.response_schema is None
    assert contract.summary is None


def test_build_endpoint_contract_with_only_response_set() -> None:
    schema = EndpointSchema(response=Widget)

    contract = build_endpoint_contract("/widgets/{id}", "get", schema)

    assert contract.request_schema is None
    assert contract.response_schema == to_json_schema(Widget)
    assert contract.summary is None


def test_build_endpoint_contract_normalizes_method_to_lowercase() -> None:
    contract = build_endpoint_contract("/widgets", "POST", None)

    assert contract.method == "post"


def test_build_endpoint_contract_custom_schema_version() -> None:
    contract = build_endpoint_contract("/widgets", "get", None, schema_version="2.0")

    assert contract.schema_version == "2.0"


@endpoint_schema(response=Widget, summary="Get a widget")
async def get_widget(request: Request) -> Response:
    return Response(status_code=200)


@endpoint_schema(request=Widget)
async def create_widget(request: Request) -> Response:
    return Response(status_code=201)


async def undocumented(request: Request) -> Response:
    return Response(status_code=200)


def _routes() -> list[BaseRoute]:
    return [
        Route("/widgets/{id:int}", get_widget, methods=["GET"]),
        Route("/widgets", create_widget, methods=["POST"]),
        Route("/undocumented", undocumented, methods=["GET"]),
    ]


def test_build_contracts_for_routes_returns_a_contract_per_endpoint() -> None:
    contracts = build_contracts_for_routes(_routes())

    assert len(contracts) == 3

    by_path = {(c.path, c.method): c for c in contracts}

    get_contract = by_path[("/widgets/{id}", "get")]
    assert get_contract.summary == "Get a widget"
    assert get_contract.response_schema == to_json_schema(Widget)
    assert get_contract.request_schema is None

    post_contract = by_path[("/widgets", "post")]
    assert post_contract.request_schema == to_json_schema(Widget)
    assert post_contract.response_schema is None
    assert post_contract.summary is None

    undocumented_contract = by_path[("/undocumented", "get")]
    assert undocumented_contract.request_schema is None
    assert undocumented_contract.response_schema is None
    assert undocumented_contract.summary is None


def test_build_contracts_for_routes_passes_through_schema_version() -> None:
    contracts = build_contracts_for_routes(_routes(), schema_version="3.1")

    assert all(c.schema_version == "3.1" for c in contracts)
