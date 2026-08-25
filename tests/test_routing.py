from __future__ import annotations

from dataclasses import dataclass

from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from nanobar_api import NanobarAPI
from nanobar_api.routing import _accepts_request


@dataclass
class Pong:
    message: str


def test_get_decorator_wraps_plain_return_in_envelope() -> None:
    app = NanobarAPI(routes=[])

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"message": "pong"}

    client = TestClient(app)
    response = client.get("/ping")

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "msg": "",
        "result": {"type": "object", "data": {"message": "pong"}},
    }


def test_accepts_request_returns_false_when_signature_cannot_be_inspected() -> None:
    # inspect.signature() raises ValueError for some builtins (e.g. builtin types used as
    # callables) — this must degrade to "no request param", not crash route registration.
    assert _accepts_request(int) is False


def test_decorator_returns_original_function_unchanged() -> None:
    app = NanobarAPI(routes=[])

    async def ping() -> dict[str, str]:
        return {"message": "pong"}

    decorated = app.get("/ping")(ping)

    assert decorated is ping


def test_handler_with_request_param_receives_it() -> None:
    app = NanobarAPI(routes=[])

    @app.get("/whoami/{name}")
    async def whoami(request: Request) -> dict[str, str]:
        return {"name": request.path_params["name"]}

    client = TestClient(app)
    response = client.get("/whoami/ada")

    assert response.json()["result"]["data"] == {"name": "ada"}


def test_handler_returning_a_response_directly_is_passed_through_unchanged() -> None:
    app = NanobarAPI(routes=[])

    @app.get("/raw")
    async def raw() -> PlainTextResponse:
        return PlainTextResponse("plain text", status_code=201)

    client = TestClient(app)
    response = client.get("/raw")

    assert response.status_code == 201
    assert response.text == "plain text"


def test_sync_handler_runs_without_blocking_the_event_loop() -> None:
    app = NanobarAPI(routes=[])

    @app.get("/sync")
    def sync_handler() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    response = client.get("/sync")

    assert response.json()["result"]["data"] == {"ok": True}


def test_post_put_patch_delete_register_correct_methods() -> None:
    app = NanobarAPI(routes=[])

    @app.post("/items")
    async def create_item() -> dict[str, str]:
        return {"action": "created"}

    @app.put("/items/{id}")
    async def replace_item() -> dict[str, str]:
        return {"action": "replaced"}

    @app.patch("/items/{id}")
    async def update_item() -> dict[str, str]:
        return {"action": "updated"}

    @app.delete("/items/{id}")
    async def delete_item() -> dict[str, str]:
        return {"action": "deleted"}

    client = TestClient(app)

    assert client.post("/items").json()["result"]["data"] == {"action": "created"}
    assert client.put("/items/1").json()["result"]["data"] == {"action": "replaced"}
    assert client.patch("/items/1").json()["result"]["data"] == {"action": "updated"}
    assert client.delete("/items/1").json()["result"]["data"] == {"action": "deleted"}


def test_decorator_with_response_schema_is_reflected_in_openapi() -> None:
    app = NanobarAPI()

    @app.get("/ping", response=Pong, summary="Ping")
    async def ping() -> dict[str, str]:
        return {"message": "pong"}

    client = TestClient(app)
    schema = client.get("/openapi.json").json()

    operation = schema["paths"]["/ping"]["get"]
    assert operation["summary"] == "Ping"
    data_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["result"][
        "properties"
    ]["data"]
    assert data_schema == {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    }


def test_decorator_without_schema_kwargs_has_no_openapi_detail() -> None:
    app = NanobarAPI()

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"message": "pong"}

    client = TestClient(app)
    schema = client.get("/openapi.json").json()

    operation = schema["paths"]["/ping"]["get"]
    assert "summary" not in operation
    assert "requestBody" not in operation


def test_include_in_schema_false_excludes_from_openapi() -> None:
    app = NanobarAPI()

    @app.get("/hidden", include_in_schema=False)
    async def hidden() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    schema = client.get("/openapi.json").json()

    assert "/hidden" not in schema["paths"]
    assert client.get("/hidden").status_code == 200
