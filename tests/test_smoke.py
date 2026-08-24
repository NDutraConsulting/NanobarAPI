from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from nanobar_api import NanobarAPI


async def _ping(request: Request) -> JSONResponse:
    return JSONResponse({"status": "success", "msg": "", "result": {"type": "object", "data": {}}})


def test_smoke() -> None:
    app = NanobarAPI(routes=[Route("/ping", _ping)])
    client = TestClient(app)

    response = client.get("/ping")

    assert response.status_code == 200
    assert response.json() == {"status": "success", "msg": "", "result": {"type": "object", "data": {}}}
