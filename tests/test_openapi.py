from dataclasses import dataclass

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from starlette.testclient import TestClient

from nanobar_api import NanobarAPI, endpoint_schema


@dataclass
class Widget:
    name: str
    quantity: int


@endpoint_schema(response=Widget, summary="Get a widget")
async def get_widget(request: Request) -> Response:
    return Response(status_code=200)


@endpoint_schema(request=Widget)
async def create_widget(request: Request) -> Response:
    return Response(status_code=201)


async def undocumented(request: Request) -> Response:
    return Response(status_code=200)


def _build_app() -> NanobarAPI:
    return NanobarAPI(
        title="Widgets API",
        version="1.2.3",
        routes=[
            Route("/widgets/{id:int}", get_widget, methods=["GET"]),
            Route("/widgets", create_widget, methods=["POST"]),
            Route("/undocumented", undocumented, methods=["GET"]),
        ],
    )


def test_openapi_json_served_by_default() -> None:
    client = TestClient(_build_app())

    response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["info"] == {"title": "Widgets API", "version": "1.2.3"}


def test_openapi_schema_includes_documented_endpoint_detail() -> None:
    generator = _build_app().schema_generator
    schema = generator.get_schema(_build_app().routes)

    get_op = schema["paths"]["/widgets/{id}"]["get"]
    assert get_op["summary"] == "Get a widget"
    assert get_op["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["result"]["properties"][
        "data"
    ] == {
        "type": "object",
        "properties": {"name": {"type": "string"}, "quantity": {"type": "integer"}},
        "required": ["name", "quantity"],
    }

    post_op = schema["paths"]["/widgets"]["post"]
    assert post_op["requestBody"]["content"]["application/json"]["schema"]["properties"]["name"] == {"type": "string"}


def test_openapi_schema_undocumented_endpoint_has_bare_envelope() -> None:
    generator = _build_app().schema_generator
    schema = generator.get_schema(_build_app().routes)

    op = schema["paths"]["/undocumented"]["get"]
    assert "summary" not in op
    assert "requestBody" not in op
    assert (
        op["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["result"]["properties"]["data"]
        == {}
    )


def test_docs_route_served_by_default() -> None:
    client = TestClient(_build_app())

    response = client.get("/docs")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "swagger-ui" in response.text
    assert "/openapi.json" in response.text


def test_docs_and_openapi_can_be_disabled() -> None:
    app = NanobarAPI(routes=[], openapi_url=None)
    client = TestClient(app)

    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404


def test_docs_disabled_independently_of_openapi() -> None:
    app = NanobarAPI(routes=[], docs_url=None)
    client = TestClient(app)

    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 404
