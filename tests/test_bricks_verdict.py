"""Tests for the verdict — the most important test file in this system so far.

Per the user's own correction (2026-08-27,
`.focusari/2026-08-27-regression-brick-clarification.md` Part 1): "run it, then diff it. If they
match, show a pass. If they don't, show the diff. That is all." No more separately-gated
layers — one diff pass over `{"status_code", "payload"}` as a single structure, plus an optional
schema check that contributes diff entries rather than gating anything.

Proves both directions the design doc demands (`.focusari/regression-brick-system-plan.md`
§7): no false positives on an unchanged endpoint (including under volatile-field
nondeterminism), and real regressions are actually caught, with the diff naming what changed.
"""

from __future__ import annotations

from typing import Any

import pytest

from nanobar_api.bricks.verdict import DEFAULT_VOLATILE_FIELDS, Verdict, evaluate_verdict
from nanobar_api.regression_brick.model import RegressionBrick


def _brick(response: dict[str, Any], status_code: int = 200) -> RegressionBrick:
    return RegressionBrick(
        regression_brick_id="rbrick-1",
        schema_version="1.0",
        brick_version=1,
        source={"trace_id": "t-1", "span_id": "s-1", "channel": "snapshot"},
        request={"method": "GET", "path": "/items", "headers": {}, "payload": {}},
        response={"status_code": status_code, "payload": response},
        content_hash="sha256:abc",
        created_by="test",
    )


def _replayed(payload: dict[str, Any], status_code: int = 200) -> dict[str, Any]:
    return {"status_code": status_code, "payload": payload}


# ---------------------------------------------------------------------------
# No false positive
# ---------------------------------------------------------------------------


def test_identical_response_passes() -> None:
    payload = {
        "status": "success",
        "msg": "",
        "result": {"type": "array", "data": [{"id": 1, "name": "widget"}]},
    }
    brick = _brick(payload)

    verdict = evaluate_verdict(brick, _replayed(payload))

    assert verdict.overall_passed is True
    assert verdict.diffs == []


def test_identical_response_passes_with_a_matching_schema() -> None:
    payload = {"status": "success", "msg": "", "result": {"type": "object", "data": {"count": 3}}}
    brick = _brick(payload)
    schema = {
        "type": "object",
        "required": ["status", "msg", "result"],
        "properties": {
            "status": {"type": "string"},
            "msg": {"type": "string"},
            "result": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "data": {"type": "object", "properties": {"count": {"type": "integer"}}},
                },
            },
        },
    }

    verdict = evaluate_verdict(brick, _replayed(payload), response_schema=schema)

    assert verdict.overall_passed is True
    assert verdict.diffs == []


# ---------------------------------------------------------------------------
# Real regressions are caught
# ---------------------------------------------------------------------------


def test_different_status_code_is_reported_as_a_diff() -> None:
    brick = _brick({"status": "success"}, status_code=200)

    verdict = evaluate_verdict(brick, _replayed({"status": "success"}, status_code=500))

    assert verdict.overall_passed is False
    assert any("200" in d and "500" in d for d in verdict.diffs)


def test_status_code_and_payload_differences_are_both_reported_not_hidden_by_each_other() -> None:
    """The old layered model would skip the payload diff entirely once status_code differed --
    the whole point of "just diff it" is that both show up together."""
    brick = _brick({"price": 10}, status_code=200)

    verdict = evaluate_verdict(brick, _replayed({"price": 999}, status_code=500))

    assert verdict.overall_passed is False
    assert any("status_code" in d for d in verdict.diffs)
    assert any("price" in d for d in verdict.diffs)


def test_different_envelope_status_is_reported_even_with_matching_http_status() -> None:
    brick = _brick({"status": "success", "msg": "", "result": {"type": "object", "data": {}}})

    verdict = evaluate_verdict(
        brick, _replayed({"status": "error", "msg": "", "result": {"type": "object", "data": {}}})
    )

    assert verdict.overall_passed is False
    assert any("success" in d and "error" in d for d in verdict.diffs)


def test_missing_required_field_is_reported_as_a_schema_diff() -> None:
    brick = _brick({"status": "success", "result": {}})
    schema = {"type": "object", "required": ["status", "msg"], "properties": {"status": {"type": "string"}}}

    verdict = evaluate_verdict(brick, _replayed({"status": "success", "result": {}}), response_schema=schema)

    assert verdict.overall_passed is False
    assert any("msg" in d and "required" in d for d in verdict.diffs)


def test_wrong_type_is_reported_as_a_schema_diff() -> None:
    brick = _brick({"count": "not-a-number"})
    schema = {"type": "object", "properties": {"count": {"type": "integer"}}}

    verdict = evaluate_verdict(brick, _replayed({"count": "not-a-number"}), response_schema=schema)

    assert verdict.overall_passed is False
    assert any("count" in d for d in verdict.diffs)


def test_non_volatile_field_value_change_is_reported() -> None:
    original = {"status": "success", "result": {"type": "object", "data": {"price": 10}}}
    replayed = {"status": "success", "result": {"type": "object", "data": {"price": 999}}}
    brick = _brick(original)

    verdict = evaluate_verdict(brick, _replayed(replayed))

    assert verdict.overall_passed is False
    assert any("price" in d and "10" in d and "999" in d for d in verdict.diffs)


def test_missing_required_key_in_replayed_is_reported() -> None:
    original = {"status": "success", "extra_field": "value"}
    replayed = {"status": "success"}
    brick = _brick(original)

    verdict = evaluate_verdict(brick, _replayed(replayed))

    assert verdict.overall_passed is False
    assert any("extra_field" in d for d in verdict.diffs)


def test_extra_key_in_replayed_is_reported() -> None:
    original = {"status": "success"}
    replayed = {"status": "success", "unexpected_new_field": "surprise"}
    brick = _brick(original)

    verdict = evaluate_verdict(brick, _replayed(replayed))

    assert verdict.overall_passed is False
    assert any("unexpected_new_field" in d for d in verdict.diffs)


# ---------------------------------------------------------------------------
# Volatile-field masking: doesn't cause false positives, doesn't hide real diffs
# ---------------------------------------------------------------------------


def test_volatile_field_value_difference_alone_still_passes() -> None:
    original = {"status": "success", "created_at": "2026-01-01T00:00:00Z", "result": {"type": "object", "data": {}}}
    replayed = {"status": "success", "created_at": "2026-08-24T12:34:56Z", "result": {"type": "object", "data": {}}}
    brick = _brick(original)

    verdict = evaluate_verdict(brick, _replayed(replayed))

    assert verdict.overall_passed is True
    assert verdict.diffs == []


def test_volatile_field_difference_plus_a_real_difference_still_fails() -> None:
    original = {"created_at": "2026-01-01T00:00:00Z", "price": 10}
    replayed = {"created_at": "2026-08-24T12:34:56Z", "price": 999}
    brick = _brick(original)

    verdict = evaluate_verdict(brick, _replayed(replayed))

    assert verdict.overall_passed is False
    # The real (non-volatile) diff is reported...
    assert any("price" in d for d in verdict.diffs)
    # ...and the volatile field's value difference is not reported as a diff.
    assert not any("created_at" in d for d in verdict.diffs)


def test_all_default_volatile_fields_are_masked() -> None:
    original = {field: "original" for field in DEFAULT_VOLATILE_FIELDS}
    replayed = {field: "different" for field in DEFAULT_VOLATILE_FIELDS}
    brick = _brick(original)

    verdict = evaluate_verdict(brick, _replayed(replayed))

    assert verdict.overall_passed is True


def test_volatile_field_masking_applies_at_nested_dict_depth() -> None:
    original = {"result": {"data": {"id": "abc-111", "name": "widget"}}}
    replayed = {"result": {"data": {"id": "xyz-999", "name": "widget"}}}
    brick = _brick(original)

    verdict = evaluate_verdict(brick, _replayed(replayed))

    assert verdict.overall_passed is True


def test_volatile_field_masking_applies_inside_list_items() -> None:
    original = {"items": [{"id": "1", "price": 10}, {"id": "2", "price": 20}]}
    replayed = {"items": [{"id": "aaa", "price": 10}, {"id": "bbb", "price": 20}]}
    brick = _brick(original)

    verdict = evaluate_verdict(brick, _replayed(replayed))

    assert verdict.overall_passed is True


def test_volatile_field_masking_inside_list_items_does_not_hide_real_diff() -> None:
    original = {"items": [{"id": "1", "price": 10}]}
    replayed = {"items": [{"id": "aaa", "price": 12345}]}
    brick = _brick(original)

    verdict = evaluate_verdict(brick, _replayed(replayed))

    assert verdict.overall_passed is False
    assert any("price" in d for d in verdict.diffs)
    assert not any("id" in d for d in verdict.diffs)


def test_list_length_mismatch_is_reported_as_a_diff() -> None:
    original = {"items": [1, 2, 3]}
    replayed = {"items": [1, 2]}
    brick = _brick(original)

    verdict = evaluate_verdict(brick, _replayed(replayed))

    assert verdict.overall_passed is False
    assert any("items" in d and "length" in d for d in verdict.diffs)


def test_volatile_field_present_in_only_one_side_is_still_a_reported_diff() -> None:
    original = {"status": "success", "request_id": "req-1"}
    replayed = {"status": "success"}
    brick = _brick(original)

    verdict = evaluate_verdict(brick, _replayed(replayed))

    assert verdict.overall_passed is False
    assert any("request_id" in d for d in verdict.diffs)


def test_custom_volatile_fields_override_the_default_set() -> None:
    original = {"status": "success", "widget_name": "sprocket"}
    replayed = {"status": "success", "widget_name": "cog"}
    brick = _brick(original)

    verdict = evaluate_verdict(brick, _replayed(replayed), volatile_fields=frozenset({"widget_name"}))

    assert verdict.overall_passed is True


def test_custom_volatile_fields_no_longer_mask_the_old_defaults() -> None:
    original = {"created_at": "2026-01-01T00:00:00Z"}
    replayed = {"created_at": "2026-08-24T12:34:56Z"}
    brick = _brick(original)

    # With a volatile_fields override that does NOT include "created_at", it's no longer
    # masked and a value difference must be reported as a real diff.
    verdict = evaluate_verdict(brick, _replayed(replayed), volatile_fields=frozenset({"unrelated_field"}))

    assert verdict.overall_passed is False
    assert any("created_at" in d for d in verdict.diffs)


# ---------------------------------------------------------------------------
# Schema check specifics: anyOf / nullable
# ---------------------------------------------------------------------------


def test_no_schema_given_skips_the_schema_check_entirely() -> None:
    brick = _brick({"anything": "goes"})

    verdict = evaluate_verdict(brick, _replayed({"anything": "goes"}), response_schema=None)

    assert verdict.overall_passed is True
    assert verdict.diffs == []


def test_schema_nullable_accepts_none() -> None:
    brick = _brick({"maybe": None})
    schema = {"type": "object", "properties": {"maybe": {"type": "string", "nullable": True}}}

    verdict = evaluate_verdict(brick, _replayed({"maybe": None}), response_schema=schema)

    assert verdict.overall_passed is True


def test_schema_nullable_still_accepts_the_base_type() -> None:
    brick = _brick({"maybe": "a string"})
    schema = {"type": "object", "properties": {"maybe": {"type": "string", "nullable": True}}}

    verdict = evaluate_verdict(brick, _replayed({"maybe": "a string"}), response_schema=schema)

    assert verdict.overall_passed is True


def test_schema_without_nullable_rejects_none() -> None:
    brick = _brick({"required_string": None})
    schema = {"type": "object", "properties": {"required_string": {"type": "string"}}}

    verdict = evaluate_verdict(brick, _replayed({"required_string": None}), response_schema=schema)

    assert verdict.overall_passed is False


def test_schema_any_of_passes_when_one_alternative_matches() -> None:
    brick = _brick({"value": 42})
    schema = {"type": "object", "properties": {"value": {"anyOf": [{"type": "string"}, {"type": "integer"}]}}}

    verdict = evaluate_verdict(brick, _replayed({"value": 42}), response_schema=schema)

    assert verdict.overall_passed is True


def test_schema_any_of_fails_when_no_alternative_matches() -> None:
    brick = _brick({"value": [1, 2, 3]})
    schema = {"type": "object", "properties": {"value": {"anyOf": [{"type": "string"}, {"type": "integer"}]}}}

    verdict = evaluate_verdict(brick, _replayed({"value": [1, 2, 3]}), response_schema=schema)

    assert verdict.overall_passed is False


def test_schema_nullable_anyof_accepts_none() -> None:
    # Regression test: to_json_schema emits {"anyOf": [...], "nullable": True} for a
    # multi-type Optional field (e.g. `int | str | None`) — none of the anyOf alternatives
    # themselves accept None, so nullable must be checked before anyOf or a legitimately
    # null value is wrongly reported as matching no alternative.
    brick = _brick({"value": None})
    schema = {
        "type": "object",
        "properties": {"value": {"anyOf": [{"type": "string"}, {"type": "integer"}], "nullable": True}},
    }

    verdict = evaluate_verdict(brick, _replayed({"value": None}), response_schema=schema)

    assert verdict.overall_passed is True


def test_schema_nullable_anyof_still_validates_non_none_values() -> None:
    brick = _brick({"value": "hello"})
    schema = {
        "type": "object",
        "properties": {"value": {"anyOf": [{"type": "string"}, {"type": "integer"}], "nullable": True}},
    }

    verdict = evaluate_verdict(brick, _replayed({"value": [1, 2]}), response_schema=schema)

    assert verdict.overall_passed is False


def test_schema_bool_is_not_accepted_as_integer() -> None:
    # Python bool is an int subclass; this project's own validation.py explicitly rejects
    # bool where an integer is expected, and the shape-checker must match that.
    brick = _brick({"count": True})
    schema = {"type": "object", "properties": {"count": {"type": "integer"}}}

    verdict = evaluate_verdict(brick, _replayed({"count": True}), response_schema=schema)

    assert verdict.overall_passed is False


def test_schema_bool_is_not_accepted_as_number() -> None:
    brick = _brick({"count": False})
    schema = {"type": "object", "properties": {"count": {"type": "number"}}}

    verdict = evaluate_verdict(brick, _replayed({"count": False}), response_schema=schema)

    assert verdict.overall_passed is False


def test_schema_actual_bool_passes_boolean_type() -> None:
    brick = _brick({"flag": True})
    schema = {"type": "object", "properties": {"flag": {"type": "boolean"}}}

    verdict = evaluate_verdict(brick, _replayed({"flag": True}), response_schema=schema)

    assert verdict.overall_passed is True


def test_schema_array_items_validated_recursively() -> None:
    brick = _brick({"items": [{"id": 1}, {"id": 2}]})
    schema = {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "integer"}}}}
        },
    }

    verdict = evaluate_verdict(brick, _replayed({"items": [{"id": 1}, {"id": 2}]}), response_schema=schema)

    assert verdict.overall_passed is True


def test_schema_array_items_failure_is_reported() -> None:
    brick = _brick({"items": [{"id": 1}, {"id": "oops"}]})
    schema = {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "integer"}}}}
        },
    }

    verdict = evaluate_verdict(brick, _replayed({"items": [{"id": 1}, {"id": "oops"}]}), response_schema=schema)

    assert verdict.overall_passed is False
    assert any("items[1]" in d for d in verdict.diffs)


def test_schema_unknown_type_keyword_is_non_restrictive() -> None:
    # An unrecognized "type" value is treated as non-restrictive rather than raising --
    # this shape-checker is a small, honest subset of JSON Schema, not a general validator.
    brick = _brick({"value": "anything"})
    schema = {"type": "object", "properties": {"value": {"type": "some-future-keyword"}}}

    verdict = evaluate_verdict(brick, _replayed({"value": "anything"}), response_schema=schema)

    assert verdict.overall_passed is True


def test_schema_property_with_no_type_constraint_passes() -> None:
    brick = _brick({"value": {"anything": "goes"}})
    schema = {"type": "object", "properties": {"value": {}}}

    verdict = evaluate_verdict(brick, _replayed({"value": {"anything": "goes"}}), response_schema=schema)

    assert verdict.overall_passed is True


def test_schema_optional_property_absent_from_payload_is_fine() -> None:
    brick = _brick({"status": "success"})
    schema = {
        "type": "object",
        "properties": {"status": {"type": "string"}, "optional_field": {"type": "string"}},
    }

    verdict = evaluate_verdict(brick, _replayed({"status": "success"}), response_schema=schema)

    assert verdict.overall_passed is True


def test_schema_array_type_without_items_schema_is_unconstrained() -> None:
    brick = _brick({"values": [1, "two", {"three": 3}]})
    schema = {"type": "object", "properties": {"values": {"type": "array"}}}

    verdict = evaluate_verdict(brick, _replayed({"values": [1, "two", {"three": 3}]}), response_schema=schema)

    assert verdict.overall_passed is True


def test_schema_does_not_raise_on_mismatch() -> None:
    # Exceptions from evaluate_verdict should only happen on genuinely unexpected/malformed
    # input, never as the mechanism for reporting "the schema didn't validate."
    brick = _brick({"count": "nope"})
    schema = {"type": "object", "properties": {"count": {"type": "integer"}}}

    try:
        verdict = evaluate_verdict(brick, _replayed({"count": "nope"}), response_schema=schema)
    except Exception as exc:  # pragma: no cover - the whole point is this must not happen
        pytest.fail(f"evaluate_verdict raised on a schema mismatch instead of returning a failed verdict: {exc}")

    assert verdict.overall_passed is False


# ---------------------------------------------------------------------------
# Dataclass sanity
# ---------------------------------------------------------------------------


def test_verdict_is_frozen_dataclass_with_expected_fields() -> None:
    brick = _brick({"status": "success"})
    verdict = evaluate_verdict(brick, _replayed({"status": "success"}))

    assert isinstance(verdict, Verdict)
    with pytest.raises(AttributeError):
        verdict.overall_passed = False  # type: ignore[misc]
