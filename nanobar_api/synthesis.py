"""Synthesis strategies — `nanobar_type_taxonomy_and_expected_coverage_buildplan-with-tasks.md`
Phase D. Turns a detected, `synthesizable: true` coverage gap (`taxonomy.detect_coverage_gaps()`)
into a real request to actually fire, reusing `capture/contract.py`'s existing schema derivation
rather than inventing a second one.

Three of the task's four named `regression_scenario_type`s are built; the fourth is a real,
flagged gap, not an oversight:

- **`validation_error`** — an empty `{}` request body against a contract with `required` fields.
  Any one missing required field is enough to trigger the validator layer, so the simplest, most
  robust synthesis works generically without guessing which field or what a "wrong" value would
  even look like for an unknown type. **Naming note, found via live verification, not assumed:**
  the vendored taxonomy (`nanobar_type.json`) lists `invalid_input` (400) and `validation_error`
  (422) as *separate* scenario types (`bricks/generate.py`'s `_classify_scenario_type` maps them
  to those exact status codes) — but `NanobarValidatorGate.__call__`'s own `ValidationError`
  handling always returns 400, never 422. Against an app built on *this* framework's own
  validator gate, this strategy's fired request lands as `invalid_input`, not `validation_error`
  in that stricter sense; `is_expected_outcome()` below accepts either status code so the
  strategy stays genuinely useful regardless of which convention the target app follows, rather
  than silently failing every time against this framework's own real behavior.
- **`not_found`** — a path template's `{param}` segment(s) filled with a value implausible
  enough to not exist (a random, clearly-synthetic id). Every occurrence is replaced, not just
  the first, so the fired path stays well-formed for routes with more than one path parameter.
- **`unauthorized`** — the request as fired, with no credentials attached. Generically
  synthesizable: no identity at all is the one thing every auth mechanism this framework ships
  (session cookies, bearer tokens) agrees should be rejected, regardless of which one an app uses.
- **`forbidden` — not built.** Distinguishing "no valid identity" (401) from "a valid identity
  without sufficient privilege" (403) needs a *specific*, lesser-privileged-but-real credential
  this framework has no generic representation of (no role/permission model exists anywhere in
  this codebase to synthesize one from). Flagged, not guessed at.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from typing import Any

from nanobar_api.capture.contract import EndpointContract

_PATH_PARAM_PATTERN = "{"  # cheap presence check; real substitution below is regex-based
_PATH_PARAM_REGEX = re.compile(r"\{[^}]+\}")


def synthesize_validation_error_request(contract: EndpointContract) -> dict[str, Any] | None:
    """`None` when the contract has no request schema, or no required fields to omit — nothing
    honest to synthesize."""
    if not contract.request_schema or not contract.request_schema.get("required"):
        return None
    return {"method": contract.method.upper(), "path": contract.path, "json": {}}


def synthesize_not_found_request(contract: EndpointContract) -> dict[str, Any] | None:
    """`None` when the contract's path has no `{param}` segment to substitute — there is no
    resource-lookup shape to make "not found" out of."""
    if _PATH_PARAM_PATTERN not in contract.path:
        return None

    def _synthetic_id(_: re.Match[str]) -> str:
        return f"synthetic-does-not-exist-{uuid.uuid4().hex[:8]}"

    path = _PATH_PARAM_REGEX.sub(_synthetic_id, contract.path)
    return {"method": contract.method.upper(), "path": path}


def synthesize_unauthorized_request(contract: EndpointContract) -> dict[str, Any]:
    # Always synthesizable, unconditionally -- no credentials is a valid request shape for any
    # endpoint; whether it's actually rejected (proving the gap is real coverage, not a no-op)
    # is what firing it and checking the response determines, not this function's job.
    return {"method": contract.method.upper(), "path": contract.path}


SYNTHESIS_STRATEGIES: dict[str, Callable[[EndpointContract], dict[str, Any] | None]] = {
    "validation_error": synthesize_validation_error_request,
    "not_found": synthesize_not_found_request,
    "unauthorized": synthesize_unauthorized_request,
}


def is_expected_outcome(regression_scenario_type: str, status_code: int) -> bool:
    """Did firing a synthesized request for `regression_scenario_type` actually land in the
    scenario it was meant to demonstrate? Real new coverage only exists if the answer is yes —
    e.g. a `not_found` synthesis whose synthetic id happened to collide with a real resource, or
    an endpoint that doesn't 404 for missing resources at all, produced a request but not the
    coverage the gap needed."""
    if regression_scenario_type == "validation_error":
        return status_code in (400, 422)  # see this module's docstring for why both are accepted
    if regression_scenario_type == "not_found":
        return status_code == 404
    if regression_scenario_type == "unauthorized":
        return status_code == 401
    return False
