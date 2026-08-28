"""Verdict — "run it, then diff it. If they match, show a pass. If they don't, show the diff.
That is all." Per the user's own correction (2026-08-27,
`.focusari/2026-08-27-regression-brick-clarification.md` Part 1) — the previous version of this
module (three separately-gated layers: status/envelope-status, optional schema, then pinned-field
diff, with a status-code mismatch skipping the other two entirely) was overcomplicated relative
to what's actually wanted. Resolving D4 (`.focusari/2026-08-27.1800.build_and_update_plan_with_tasks.md`
Phase 6): rewritten to match the simple model directly, not left divergent from it.

**One diff pass, not three gated layers.** `brick.response` and `replayed_response` are both
already shaped `{"status_code": ..., "payload": ...}` (`regression_brick_analysis_service.py`'s
`_verdict_inputs()` guarantees this even for a capture_layer()-sourced brick) — diffed as one
structure, so a status-code mismatch is just one more diff entry, not a special gate that hides
whatever else also differs. This is a real improvement, not just simpler: the old skip-on-
status-failure behavior actively hid useful information (a regression that also changed the
payload would only ever report "status mismatch," never showing *what else* broke). The old
status layer's separate "envelope status" comparison (`nanobar_api.envelope.Envelope`'s own
`status` field, `"success"|"error"|"timeout"`) needed no special-casing to preserve either — it's
just `payload.status`, already covered by the same structural diff.

Schema validation (an optional, per-request `response_schema`) still runs, but as one more
source of diff entries, not a gate — a schema violation is reported alongside any other
differences, not instead of them. `_validate_against_schema`/`_schema_type_matches` are otherwise
unchanged from the previous version of this module.

`volatile_fields` masking is unchanged: `_diff_paths` masks *values* for volatile fields (default:
timestamps, generated ids, trace ids) while still requiring matching *presence* — a masked field
missing from one side but not the other is still a real, reported difference.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nanobar_api.regression_brick.model import RegressionBrick

DEFAULT_VOLATILE_FIELDS = frozenset({"timestamp", "created_at", "updated_at", "id", "request_id", "trace_id"})


@dataclass(frozen=True)
class Verdict:
    overall_passed: bool
    #: Human-readable diff lines -- empty when `overall_passed` is `True`. No per-layer
    #: structure; every discrepancy (status code, payload field, schema violation) is just
    #: another entry in this one flat list.
    diffs: list[str]


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


def evaluate_verdict(
    brick: RegressionBrick,
    replayed_response: dict[str, Any],
    response_schema: dict[str, Any] | None = None,
    volatile_fields: frozenset[str] = DEFAULT_VOLATILE_FIELDS,
) -> Verdict:
    """Run it (the caller already has), diff it, pass or show the diff. `brick.response`/
    `replayed_response` are diffed as one `{"status_code", "payload"}` structure -- a status-code
    mismatch is just one more diff entry, never a gate that hides other differences. A schema
    violation (only checked when `response_schema` is given) is appended the same way.
    """
    diffs: list[str] = []
    _diff_paths(brick.response, replayed_response, "response", volatile_fields, diffs)

    if response_schema is not None:
        mismatch = _validate_against_schema(replayed_response.get("payload"), response_schema, "response.payload")
        if mismatch is not None:
            diffs.append(f"schema: {mismatch}")

    return Verdict(overall_passed=not diffs, diffs=diffs)
