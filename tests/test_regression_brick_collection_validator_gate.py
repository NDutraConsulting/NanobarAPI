from __future__ import annotations

from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.routing import Route
from starlette.testclient import TestClient

from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.framework.nanobar_api_validator_gate import NanobarAPIValidatorGate
from nanobar_api.middleware.trace import EventBusTraceMiddleware
from nanobar_api.persistence import build_session_factory
from nanobar_api.regression_brick.model import RegressionBrick
from nanobar_api.regression_brick.regression_brick_collection_validator_gate import (
    AddBrickTagGate,
    RemoveBrickTagGate,
    SetBrickScenarioGate,
    SetReviewStatusGate,
)
from nanobar_api.regression_brick.repository import RegressionBrickRepository
from nanobar_api.routing import adapt_handler
from nanobar_api.telemetry import NanobarTelemetry


def _gate_endpoint(gate_cls: type[NanobarAPIValidatorGate], request_type: str) -> Any:
    async def endpoint(request: Request) -> Any:
        return await gate_cls()(request, request_type)

    return endpoint


def _repository() -> EventQueueRepository:
    return EventQueueRepository([ChannelConfig(name="trace"), ChannelConfig(name="snapshot")])


def _build_app(tmp_path: Path) -> Starlette:
    repository = _repository()
    app = Starlette(
        routes=[
            Route(
                "/bricks/{brick_id}/review-status",
                adapt_handler(_gate_endpoint(SetReviewStatusGate, "PATCH .../review-status")),
                methods=["PATCH", "POST"],
            ),
            Route(
                "/bricks/{brick_id}/scenario",
                adapt_handler(_gate_endpoint(SetBrickScenarioGate, "PATCH .../scenario")),
                methods=["PATCH", "POST"],
            ),
            Route(
                "/bricks/{brick_id}/tags",
                adapt_handler(_gate_endpoint(AddBrickTagGate, "POST .../tags")),
                methods=["POST"],
            ),
            Route(
                "/bricks/{brick_id}/tags/{tag}",
                adapt_handler(_gate_endpoint(RemoveBrickTagGate, "DELETE .../tags/{tag}")),
                methods=["DELETE"],
            ),
        ],
        middleware=[Middleware(EventBusTraceMiddleware, repository=repository, channel="trace")],
    )
    app.state.telemetry = NanobarTelemetry(repository, channel="trace")
    app.state.bricks_session_factory = build_session_factory(str(tmp_path / "bricks.db"), repository=repository)
    return app


def _make_brick(**overrides: object) -> RegressionBrick:
    defaults: dict[str, object] = {
        "schema_version": "1.0",
        "brick_version": 1,
        "source": {},
        "request": {},
        "response": {},
        "content_hash": "h",
        "created_by": "test",
    }
    defaults.update(overrides)
    return RegressionBrick(**defaults)


def _seed_brick(app: Starlette) -> str:
    session = app.state.bricks_session_factory()
    brick = RegressionBrickRepository(session).create(_make_brick())
    session.close()
    return brick.regression_brick_id


def test_set_review_status_valid(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    brick_id = _seed_brick(app)
    client = TestClient(app)

    response = client.post(f"/bricks/{brick_id}/review-status", json={"status": "flagged"})

    assert response.status_code == 200
    assert response.json()["result"]["data"]["status"] == "flagged"


def test_set_review_status_invalid_value(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    brick_id = _seed_brick(app)
    client = TestClient(app)

    response = client.post(f"/bricks/{brick_id}/review-status", json={"status": "bogus"})

    assert response.status_code == 400
    assert "invalid review status" in response.json()["msg"]


def test_set_review_status_body_not_an_object(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    brick_id = _seed_brick(app)
    client = TestClient(app)

    response = client.post(f"/bricks/{brick_id}/review-status", json=["flagged"])

    assert response.status_code == 400
    assert "status" in response.json()["msg"]


def test_set_review_status_malformed_json(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    brick_id = _seed_brick(app)
    client = TestClient(app)

    response = client.post(
        f"/bricks/{brick_id}/review-status", content=b"{not valid json", headers={"content-type": "application/json"}
    )

    assert response.status_code == 400
    assert "JSON" in response.json()["msg"]


def test_set_review_status_brick_not_found(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    client = TestClient(app)

    response = client.post("/bricks/does-not-exist/review-status", json={"status": "flagged"})

    assert response.status_code == 404


def test_set_brick_scenario_valid(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    brick_id = _seed_brick(app)
    client = TestClient(app)

    response = client.post(f"/bricks/{brick_id}/scenario", json={"regression_scenario_label": "timeout"})

    assert response.status_code == 200
    assert response.json()["result"]["data"]["regression_scenario_label"] == "timeout"


def test_set_brick_scenario_body_not_an_object(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    brick_id = _seed_brick(app)
    client = TestClient(app)

    response = client.post(f"/bricks/{brick_id}/scenario", json=["x"])

    assert response.status_code == 400


def test_set_brick_scenario_malformed_json(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    brick_id = _seed_brick(app)
    client = TestClient(app)

    response = client.post(
        f"/bricks/{brick_id}/scenario", content=b"{not valid json", headers={"content-type": "application/json"}
    )

    assert response.status_code == 400
    assert "JSON" in response.json()["msg"]


def test_add_brick_tag_valid(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    brick_id = _seed_brick(app)
    client = TestClient(app)

    response = client.post(f"/bricks/{brick_id}/tags", json={"tag": "flaky"})

    assert response.status_code == 200
    assert response.json()["result"]["data"] == ["flaky"]


def test_add_brick_tag_rejects_empty_tag(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    brick_id = _seed_brick(app)
    client = TestClient(app)

    response = client.post(f"/bricks/{brick_id}/tags", json={"tag": ""})

    assert response.status_code == 400


def test_add_brick_tag_rejects_missing_tag(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    brick_id = _seed_brick(app)
    client = TestClient(app)

    response = client.post(f"/bricks/{brick_id}/tags", json={})

    assert response.status_code == 400
    assert "tag" in response.json()["msg"]


def test_add_brick_tag_body_not_an_object(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    brick_id = _seed_brick(app)
    client = TestClient(app)

    response = client.post(f"/bricks/{brick_id}/tags", json=["flaky"])

    assert response.status_code == 400
    assert "tag" in response.json()["msg"]


def test_add_brick_tag_malformed_json(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    brick_id = _seed_brick(app)
    client = TestClient(app)

    response = client.post(
        f"/bricks/{brick_id}/tags", content=b"{not valid json", headers={"content-type": "application/json"}
    )

    assert response.status_code == 400
    assert "JSON" in response.json()["msg"]


def test_remove_brick_tag_valid(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    brick_id = _seed_brick(app)
    client = TestClient(app)
    client.post(f"/bricks/{brick_id}/tags", json={"tag": "flaky"})

    response = client.delete(f"/bricks/{brick_id}/tags/flaky")

    assert response.status_code == 200
    assert response.json()["result"]["data"] == []


def test_remove_brick_tag_brick_not_found(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    client = TestClient(app)

    response = client.delete("/bricks/does-not-exist/tags/flaky")

    assert response.status_code == 404
