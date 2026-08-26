from __future__ import annotations

import json
from pathlib import Path

import pytest

from nanobar_api.bricks.schema import Nanobar, RegressionBrick
from nanobar_api.taxonomy import (
    VENDORED_TAXONOMY_PATH,
    ExpectedScenario,
    NanobarTypeEntry,
    NanobarTypeTaxonomy,
    compute_regression_weight,
    detect_coverage_gaps,
    load_taxonomy,
)


def test_vendored_taxonomy_file_exists_and_is_valid_json() -> None:
    assert VENDORED_TAXONOMY_PATH.exists()
    json.loads(VENDORED_TAXONOMY_PATH.read_text())


def test_load_taxonomy_with_no_overrides_returns_vendored_defaults() -> None:
    taxonomy = load_taxonomy()

    assert "api-response" in taxonomy
    success = taxonomy["api-response"].expected_scenarios["success"]
    assert success.weight == 1.0
    assert success.required is True
    assert success.synthesizable is False


def test_load_taxonomy_vendored_scenario_keys_match_classify_scenario_type_vocabulary() -> None:
    known_vocabulary = {
        "success",
        "invalid_input",
        "unauthorized",
        "forbidden",
        "not_found",
        "conflict",
        "validation_error",
        "server_error",
    }
    taxonomy = load_taxonomy()

    for entry in taxonomy.values():
        assert set(entry.expected_scenarios) <= known_vocabulary


def test_load_taxonomy_merges_new_nanobar_type_from_override_file(tmp_path: Path) -> None:
    override_path = tmp_path / "app_taxonomy.json"
    override_path.write_text(
        json.dumps(
            {
                "checkout-to-payment-gateway": {
                    "expected_scenarios": {
                        "success": {"weight": 1.0, "required": True, "synthesizable": False},
                        "server_error": {"weight": 0.3, "required": True, "synthesizable": False},
                    }
                }
            }
        )
    )

    taxonomy = load_taxonomy([override_path])

    assert "checkout-to-payment-gateway" in taxonomy
    assert "api-response" in taxonomy  # vendored defaults still present


def test_load_taxonomy_override_merges_scenarios_onto_existing_type(tmp_path: Path) -> None:
    override_path = tmp_path / "app_taxonomy.json"
    override_path.write_text(
        json.dumps(
            {
                "api-response": {
                    "expected_scenarios": {
                        "success": {"weight": 1.0, "required": True, "synthesizable": False},
                        "rate_limited": {"weight": 0.4, "required": False, "synthesizable": True},
                    }
                }
            }
        )
    )

    taxonomy = load_taxonomy([override_path])

    api_response = taxonomy["api-response"].expected_scenarios
    assert "rate_limited" in api_response  # new scenario added
    assert "invalid_input" in api_response  # vendored scenario for this type kept, not replaced


def test_load_taxonomy_applies_overrides_in_order(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(
        json.dumps(
            {
                "api-response": {
                    "expected_scenarios": {"success": {"weight": 0.1, "required": True, "synthesizable": False}}
                }
            }
        )
    )
    second.write_text(
        json.dumps(
            {
                "api-response": {
                    "expected_scenarios": {"success": {"weight": 0.9, "required": True, "synthesizable": False}}
                }
            }
        )
    )

    taxonomy = load_taxonomy([first, second])

    assert taxonomy["api-response"].expected_scenarios["success"].weight == 0.9


# ------------------------------------------------- compute_regression_weight() / detect_coverage_gaps()

_TEST_TAXONOMY: NanobarTypeTaxonomy = {
    "widget-request-response": NanobarTypeEntry(
        expected_scenarios={
            "success": ExpectedScenario(weight=1.0, required=True, synthesizable=False),
            "not_found": ExpectedScenario(weight=0.5, required=True, synthesizable=True),
            "server_error": ExpectedScenario(weight=0.3, required=True, synthesizable=False),
            "conflict": ExpectedScenario(weight=0.4, required=False, synthesizable=True),
        }
    ),
    "no-required-scenarios-type": NanobarTypeEntry(
        expected_scenarios={
            "success": ExpectedScenario(weight=1.0, required=False, synthesizable=False),
        }
    ),
}


def _make_nanobar(*, nanobar_type: str = "widget-request-response", criticality: float = 0.8) -> Nanobar:
    return Nanobar(
        nanobar_id="nb-1",
        schema_version="1.0",
        system_name="test",
        system_version="1.0.0",
        nanobar_type=nanobar_type,
        request_object_id="req-1",
        response_object_id="res-1",
        regression_weight=0.5,
        endpoint_scenario_frequency={"state": "unmeasured"},
        created_by="test",
        criticality=criticality,
    )


def _make_brick(regression_scenario_type: str | None) -> RegressionBrick:
    return RegressionBrick(
        regression_brick_id="rbrick-1",
        schema_version="1.0",
        brick_version=1,
        source={},
        request={},
        response={},
        content_hash="sha256:x",
        created_by="test",
        regression_scenario_type=regression_scenario_type,
    )


def test_load_taxonomy_override_with_non_dict_entry_raises_value_error(tmp_path: Path) -> None:
    override_path = tmp_path / "app_taxonomy.json"
    override_path.write_text(json.dumps({"checkout-to-payment-gateway": "not-an-object"}))

    with pytest.raises(ValueError, match="checkout-to-payment-gateway"):
        load_taxonomy([override_path])


def test_load_taxonomy_override_missing_expected_scenarios_raises_value_error(tmp_path: Path) -> None:
    override_path = tmp_path / "app_taxonomy.json"
    override_path.write_text(json.dumps({"checkout-to-payment-gateway": {}}))

    with pytest.raises(ValueError, match="expected_scenarios"):
        load_taxonomy([override_path])


def test_load_taxonomy_override_with_non_dict_scenario_raises_value_error(tmp_path: Path) -> None:
    override_path = tmp_path / "app_taxonomy.json"
    override_path.write_text(
        json.dumps({"checkout-to-payment-gateway": {"expected_scenarios": {"success": "not-an-object"}}})
    )

    with pytest.raises(ValueError, match="success"):
        load_taxonomy([override_path])


def test_load_taxonomy_override_scenario_missing_field_raises_value_error(tmp_path: Path) -> None:
    override_path = tmp_path / "app_taxonomy.json"
    override_path.write_text(
        json.dumps(
            {"checkout-to-payment-gateway": {"expected_scenarios": {"success": {"weight": 1.0, "required": True}}}}
        )
    )

    with pytest.raises(ValueError, match="synthesizable"):
        load_taxonomy([override_path])


def test_compute_regression_weight_full_coverage_equals_criticality() -> None:
    nanobar = _make_nanobar(criticality=0.8)
    bricks = [_make_brick("success"), _make_brick("not_found"), _make_brick("server_error")]

    weight = compute_regression_weight(nanobar, bricks, _TEST_TAXONOMY)

    assert weight == pytest.approx(0.8)


def test_compute_regression_weight_partial_coverage_is_scaled_by_criticality() -> None:
    nanobar = _make_nanobar(criticality=1.0)
    # Only "success" (weight 1.0) covered, out of total required weight 1.0+0.5+0.3=1.8.
    bricks = [_make_brick("success")]

    weight = compute_regression_weight(nanobar, bricks, _TEST_TAXONOMY)

    assert weight == pytest.approx((1.0 / 1.8) * 1.0)


def test_compute_regression_weight_ignores_non_required_coverage() -> None:
    nanobar = _make_nanobar(criticality=1.0)
    bricks = [_make_brick("conflict")]  # not required -- doesn't count toward completeness

    weight = compute_regression_weight(nanobar, bricks, _TEST_TAXONOMY)

    assert weight == pytest.approx(0.0)


def test_compute_regression_weight_zero_coverage_is_zero() -> None:
    nanobar = _make_nanobar(criticality=0.9)

    weight = compute_regression_weight(nanobar, [], _TEST_TAXONOMY)

    assert weight == pytest.approx(0.0)


def test_compute_regression_weight_unknown_nanobar_type_returns_unchanged() -> None:
    nanobar = Nanobar(
        nanobar_id="nb-1",
        schema_version="1.0",
        system_name="test",
        system_version="1.0.0",
        nanobar_type="totally-unknown-type",
        request_object_id="req-1",
        response_object_id="res-1",
        regression_weight=0.42,
        endpoint_scenario_frequency={},
        created_by="test",
    )

    weight = compute_regression_weight(nanobar, [_make_brick("success")], _TEST_TAXONOMY)

    assert weight == 0.42


def test_compute_regression_weight_zero_required_scenarios_returns_criticality() -> None:
    nanobar = _make_nanobar(nanobar_type="no-required-scenarios-type", criticality=0.65)

    weight = compute_regression_weight(nanobar, [], _TEST_TAXONOMY)

    assert weight == 0.65


def test_detect_coverage_gaps_full_coverage_is_empty() -> None:
    nanobar = _make_nanobar()
    bricks = [_make_brick("success"), _make_brick("not_found"), _make_brick("server_error")]

    assert detect_coverage_gaps(nanobar, bricks, _TEST_TAXONOMY) == []


def test_detect_coverage_gaps_lists_missing_required_scenarios_only() -> None:
    nanobar = _make_nanobar()
    bricks = [_make_brick("success")]

    gaps = detect_coverage_gaps(nanobar, bricks, _TEST_TAXONOMY)

    assert set(gaps) == {"not_found", "server_error"}
    assert "conflict" not in gaps  # not required -- not a "gap"


def test_detect_coverage_gaps_unknown_nanobar_type_is_empty() -> None:
    nanobar = Nanobar(
        nanobar_id="nb-1",
        schema_version="1.0",
        system_name="test",
        system_version="1.0.0",
        nanobar_type="totally-unknown-type",
        request_object_id="req-1",
        response_object_id="res-1",
        regression_weight=0.5,
        endpoint_scenario_frequency={},
        created_by="test",
    )

    assert detect_coverage_gaps(nanobar, [], _TEST_TAXONOMY) == []
