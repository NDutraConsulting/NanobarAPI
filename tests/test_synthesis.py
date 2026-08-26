from __future__ import annotations

from nanobar_api.capture.contract import EndpointContract
from nanobar_api.synthesis import (
    SYNTHESIS_STRATEGIES,
    is_expected_outcome,
    synthesize_not_found_request,
    synthesize_unauthorized_request,
    synthesize_validation_error_request,
)


def _contract(
    *,
    path: str = "/items",
    method: str = "POST",
    request_schema: dict[str, object] | None = None,
) -> EndpointContract:
    return EndpointContract(
        path=path,
        method=method,
        schema_version="1.0",
        request_schema=request_schema,
        response_schema=None,
        summary=None,
    )


# --------------------------------------------------------- synthesize_validation_error_request


def test_validation_error_synthesis_returns_none_without_a_request_schema() -> None:
    assert synthesize_validation_error_request(_contract(request_schema=None)) is None


def test_validation_error_synthesis_returns_none_without_required_fields() -> None:
    contract = _contract(request_schema={"type": "object", "properties": {"note": {"type": "string"}}})

    assert synthesize_validation_error_request(contract) is None


def test_validation_error_synthesis_sends_an_empty_body_when_fields_are_required() -> None:
    contract = _contract(
        method="post", request_schema={"type": "object", "properties": {"title": {}}, "required": ["title"]}
    )

    result = synthesize_validation_error_request(contract)

    assert result == {"method": "POST", "path": "/items", "json": {}}


# ------------------------------------------------------------------- synthesize_not_found_request


def test_not_found_synthesis_returns_none_without_a_path_param() -> None:
    assert synthesize_not_found_request(_contract(path="/items")) is None


def test_not_found_synthesis_substitutes_a_single_path_param() -> None:
    contract = _contract(path="/items/{item_id}", method="get")

    result = synthesize_not_found_request(contract)

    assert result is not None
    assert result["method"] == "GET"
    assert result["path"].startswith("/items/synthetic-does-not-exist-")
    assert "{item_id}" not in result["path"]


def test_not_found_synthesis_substitutes_every_path_param_keeping_the_url_well_formed() -> None:
    contract = _contract(path="/orgs/{org_id}/items/{item_id}", method="get")

    result = synthesize_not_found_request(contract)

    assert result is not None
    assert "{" not in result["path"]
    assert "}" not in result["path"]
    assert result["path"].startswith("/orgs/synthetic-does-not-exist-")
    assert "/items/synthetic-does-not-exist-" in result["path"]


# --------------------------------------------------------------- synthesize_unauthorized_request


def test_unauthorized_synthesis_always_returns_a_bare_request() -> None:
    contract = _contract(path="/admin/app/api/posts", method="get")

    result = synthesize_unauthorized_request(contract)

    assert result == {"method": "GET", "path": "/admin/app/api/posts"}


# ---------------------------------------------------------------------------- is_expected_outcome


def test_validation_error_outcome_accepts_either_400_or_422() -> None:
    assert is_expected_outcome("validation_error", 400) is True
    assert is_expected_outcome("validation_error", 422) is True
    assert is_expected_outcome("validation_error", 200) is False


def test_not_found_outcome_requires_404() -> None:
    assert is_expected_outcome("not_found", 404) is True
    assert is_expected_outcome("not_found", 200) is False


def test_unauthorized_outcome_requires_401() -> None:
    assert is_expected_outcome("unauthorized", 401) is True
    assert is_expected_outcome("unauthorized", 200) is False


def test_unknown_scenario_type_outcome_is_false() -> None:
    assert is_expected_outcome("forbidden", 403) is False
    assert is_expected_outcome("something-else", 200) is False


# ------------------------------------------------------------------------- SYNTHESIS_STRATEGIES


def test_synthesis_strategies_registry_covers_the_three_built_scenario_types() -> None:
    assert set(SYNTHESIS_STRATEGIES.keys()) == {"validation_error", "not_found", "unauthorized"}
    assert "forbidden" not in SYNTHESIS_STRATEGIES  # a real, flagged gap -- not silently guessed at
