"""Layered verdict — replaces a raw byte-diff replay comparison.

Per the regression-brick system plan (`.focusari/regression-brick-system-plan.md` §7,
"Layered Verdict"), a byte-diff verdict drowns in false positives immediately: `Date`
headers, generated ids, timestamps, trace ids, and JSON key ordering are all legitimate
nondeterminism that must not fail a replay. Three layers, evaluated in order, cheapest and
highest-signal first:

1. Status/envelope-status match.
2. Schema validation against the (optional) induced/committed contract.
3. Pinned-field equality, with per-brick volatile-field masking.

`overall_passed` is True only if all three layers pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nanobar_api.bricks.schema import RegressionBrick

DEFAULT_VOLATILE_FIELDS = frozenset({"timestamp", "created_at", "updated_at", "id", "request_id", "trace_id"})

_ENVELOPE_STATUSES = frozenset({"success", "error", "timeout"})

_SKIPPED_DETAIL = "skipped: status layer already failed"


@dataclass(frozen=True)
class LayerResult:
    passed: bool
    detail: str


@dataclass(frozen=True)
class Verdict:
    overall_passed: bool
    status_layer: LayerResult
    schema_layer: LayerResult
    pinned_field_layer: LayerResult


def _looks_like_envelope(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("status") in _ENVELOPE_STATUSES


def _evaluate_status_layer(brick: RegressionBrick, replayed_response: dict[str, Any]) -> LayerResult:
    original_status_code = brick.response.get("status_code")
    replayed_status_code = replayed_response.get("status_code")

    if original_status_code != replayed_status_code:
        return LayerResult(
            passed=False,
            detail=f"status_code mismatch: brick={original_status_code!r} replayed={replayed_status_code!r}",
        )

    original_payload = brick.response.get("payload")
    replayed_payload = replayed_response.get("payload")
    # The design doc groups "status/envelope-status match" as one first layer, not a
    # separate layer — only compared when both payloads actually look like this project's
    # service envelope (nanobar_api.envelope.Envelope): a dict with a "status" key whose
    # value is one of "success"/"error"/"timeout". A non-envelope payload (or one brick that
    # is an envelope and one that isn't) skips this half of the check silently rather than
    # failing on a shape this layer isn't meant to judge — that's the schema/pinned-field
    # layers' job.
    if _looks_like_envelope(original_payload) and _looks_like_envelope(replayed_payload):
        assert isinstance(original_payload, dict)  # narrows for mypy; already checked above
        assert isinstance(replayed_payload, dict)
        original_envelope_status = original_payload["status"]
        replayed_envelope_status = replayed_payload["status"]
        if original_envelope_status != replayed_envelope_status:
            return LayerResult(
                passed=False,
                detail=(
                    f"envelope status mismatch: brick={original_envelope_status!r} "
                    f"replayed={replayed_envelope_status!r}"
                ),
            )

    return LayerResult(passed=True, detail="status_code and envelope status (if present) match")


def _schema_type_matches(value: Any, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        # bool is a subclass of int in Python; this project's own validation.py makes the
        # same distinction, so a bool must never satisfy an "integer" schema.
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    # Unknown/unhandled type keyword: treat as non-restrictive rather than raising, since
    # this checker is deliberately a small, honest subset of JSON Schema sufficient for
    # nanobar_api.validation.to_json_schema's own output, not a general-purpose validator.
    return True


def _validate_against_schema(value: Any, schema: dict[str, Any], path: str) -> str | None:
    """Return None on match, or a human-readable mismatch description naming `path`."""
    # Checked before anyOf: to_json_schema emits {"anyOf": [...], "nullable": True} for a
    # multi-type Optional (e.g. `int | str | None`) — none of the anyOf alternatives
    # themselves accept None, so nullable must be consulted first or a legitimately null
    # value for such a field would fail every alternative and be misreported as a mismatch.
    if schema.get("nullable") and value is None:
        return None

    if "anyOf" in schema:
        alternatives = schema["anyOf"]
        for alternative in alternatives:
            if _validate_against_schema(value, alternative, path) is None:
                return None
        return f"{path}: value does not match any alternative in anyOf"

    expected_type = schema.get("type")
    if expected_type is None:
        # No type constraint to check (e.g. an empty schema) — nothing to fail on.
        return None

    if not _schema_type_matches(value, expected_type):
        return f"{path}: expected type {expected_type!r}, got {type(value).__name__}"

    if expected_type == "object":
        properties: dict[str, Any] = schema.get("properties", {})
        required: list[str] = schema.get("required", [])
        for key in required:
            if key not in value:
                return f"{path}.{key}: required field missing"
        for key, sub_schema in properties.items():
            if key not in value:
                continue
            mismatch = _validate_against_schema(value[key], sub_schema, f"{path}.{key}")
            if mismatch is not None:
                return mismatch
        return None

    if expected_type == "array":
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                mismatch = _validate_against_schema(item, item_schema, f"{path}[{index}]")
                if mismatch is not None:
                    return mismatch
        return None

    return None


def _evaluate_schema_layer(replayed_response: dict[str, Any], response_schema: dict[str, Any] | None) -> LayerResult:
    if response_schema is None:
        return LayerResult(passed=True, detail="no schema provided, skipped")

    mismatch = _validate_against_schema(replayed_response.get("payload"), response_schema, "response.payload")
    if mismatch is not None:
        return LayerResult(passed=False, detail=mismatch)

    return LayerResult(passed=True, detail="replayed payload matches response_schema")


def _diff_paths(original: Any, replayed: Any, path: str, volatile_fields: frozenset[str], diffs: list[str]) -> None:
    if isinstance(original, dict) and isinstance(replayed, dict):
        all_keys = set(original) | set(replayed)
        for key in sorted(all_keys, key=str):
            child_path = f"{path}.{key}"
            in_original = key in original
            in_replayed = key in replayed
            if key in volatile_fields:
                # Masked: value is never compared, but presence/absence still must match —
                # a volatile field present in one side and missing in the other is still a
                # real, reportable difference (masking is scoped to *value*, not existence).
                if in_original != in_replayed:
                    diffs.append(f"{child_path}: present in {'brick' if in_original else 'replayed'} only")
                continue
            if not in_original:
                diffs.append(f"{child_path}: present in replayed only")
                continue
            if not in_replayed:
                diffs.append(f"{child_path}: present in brick only")
                continue
            _diff_paths(original[key], replayed[key], child_path, volatile_fields, diffs)
        return

    if isinstance(original, list) and isinstance(replayed, list):
        if len(original) != len(replayed):
            diffs.append(f"{path}: list length mismatch, brick={len(original)} replayed={len(replayed)}")
            return
        for index, (original_item, replayed_item) in enumerate(zip(original, replayed, strict=True)):
            _diff_paths(original_item, replayed_item, f"{path}[{index}]", volatile_fields, diffs)
        return

    if original != replayed:
        diffs.append(f"{path}: brick={original!r} replayed={replayed!r}")


def _evaluate_pinned_field_layer(
    brick: RegressionBrick, replayed_response: dict[str, Any], volatile_fields: frozenset[str]
) -> LayerResult:
    original_payload = brick.response.get("payload")
    replayed_payload = replayed_response.get("payload")

    diffs: list[str] = []
    _diff_paths(original_payload, replayed_payload, "response.payload", volatile_fields, diffs)

    if diffs:
        return LayerResult(passed=False, detail="; ".join(diffs))

    return LayerResult(passed=True, detail="payload matches after volatile-field masking")


def evaluate_verdict(
    brick: RegressionBrick,
    replayed_response: dict[str, Any],
    response_schema: dict[str, Any] | None = None,
    volatile_fields: frozenset[str] = DEFAULT_VOLATILE_FIELDS,
) -> Verdict:
    status_layer = _evaluate_status_layer(brick, replayed_response)

    if not status_layer.passed:
        # No point schema-validating or diffing a response already known not to match at
        # the status level — layers 2/3 are reported as skipped rather than run.
        skipped = LayerResult(passed=False, detail=_SKIPPED_DETAIL)
        return Verdict(
            overall_passed=False,
            status_layer=status_layer,
            schema_layer=skipped,
            pinned_field_layer=skipped,
        )

    schema_layer = _evaluate_schema_layer(replayed_response, response_schema)
    pinned_field_layer = _evaluate_pinned_field_layer(brick, replayed_response, volatile_fields)

    overall_passed = status_layer.passed and schema_layer.passed and pinned_field_layer.passed

    return Verdict(
        overall_passed=overall_passed,
        status_layer=status_layer,
        schema_layer=schema_layer,
        pinned_field_layer=pinned_field_layer,
    )
