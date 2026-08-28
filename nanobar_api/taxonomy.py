"""Loads the `nanobar_type` taxonomy — per `.focusari/
nanobar_type_taxonomy_and_expected_coverage_buildplan-with-tasks.md` §2: which
`regression_scenario_type`s (`bricks/generate.py`'s `_classify_scenario_type` vocabulary) are
expected for a given `nanobar_type`, how much each matters (`weight`), whether its absence is a
real coverage gap (`required`), and whether it can be auto-synthesized (`synthesizable`).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from nanobar_api.nanobar.model import Nanobar
from nanobar_api.regression_brick.model import RegressionBrick

#: The framework's own pinned baseline taxonomy -- a "lock file" in the same spirit as
#: `uv.lock`: a static, versioned, checked-in default. Dynamic, runtime-registered entries
#: (per-channel `"worker-{channel}"` coverage rules, etc.) live in a separate SQLite database
#: instead -- see `nanobar_api/dynamic_taxonomy.py` -- since a lock file is deliberately *not*
#: writable at runtime, and per-channel rules genuinely need to be.
VENDORED_TAXONOMY_PATH = Path(__file__).resolve().parent / "nanobar.types.lock"


@dataclass(frozen=True)
class ExpectedScenario:
    weight: float
    required: bool
    synthesizable: bool


@dataclass(frozen=True)
class NanobarTypeEntry:
    expected_scenarios: dict[str, ExpectedScenario]


NanobarTypeTaxonomy = dict[str, NanobarTypeEntry]


def _parse_taxonomy(raw: dict[str, object], *, source: str) -> NanobarTypeTaxonomy:
    """Parses a taxonomy JSON document already loaded via `json.loads`.

    Raises `ValueError` (not a bare `AssertionError`/`KeyError`) on any shape mismatch, naming
    `source` and the offending `nanobar_type`/scenario — unlike the vendored file, `paths` in
    `load_taxonomy()` below are meant to be hand-authored by app authors, so a malformed one
    deserves an error a human can act on, not an assert (silently stripped under `python -O`)
    or an unqualified `KeyError`.
    """
    taxonomy: NanobarTypeTaxonomy = {}
    for nanobar_type, entry in raw.items():
        if not isinstance(entry, dict):
            raise ValueError(f"{source}: nanobar_type {nanobar_type!r} must map to an object")
        scenarios = entry.get("expected_scenarios")
        if not isinstance(scenarios, dict):
            raise ValueError(f"{source}: nanobar_type {nanobar_type!r} is missing an 'expected_scenarios' object")
        parsed_scenarios: dict[str, ExpectedScenario] = {}
        for scenario_type, scenario in scenarios.items():
            if not isinstance(scenario, dict):
                raise ValueError(f"{source}: {nanobar_type!r}.{scenario_type!r} must be an object")
            try:
                parsed_scenarios[scenario_type] = ExpectedScenario(
                    weight=scenario["weight"],
                    required=scenario["required"],
                    synthesizable=scenario["synthesizable"],
                )
            except KeyError as exc:
                raise ValueError(f"{source}: {nanobar_type!r}.{scenario_type!r} is missing field {exc}") from exc
        taxonomy[nanobar_type] = NanobarTypeEntry(expected_scenarios=parsed_scenarios)
    return taxonomy


def load_taxonomy(paths: Sequence[Path] = ()) -> NanobarTypeTaxonomy:
    """Load the vendored default taxonomy, then merge each of `paths` on top, in order.

    Merging is per `nanobar_type`, then per scenario: a file in `paths` can add a wholly new
    `nanobar_type` an app defines beyond the vendored catalog, or override/add individual
    `expected_scenarios` entries on an existing `nanobar_type` without needing to repeat the
    ones it isn't changing.
    """
    taxonomy = _parse_taxonomy(json.loads(VENDORED_TAXONOMY_PATH.read_text()), source=str(VENDORED_TAXONOMY_PATH))

    for path in paths:
        overrides = _parse_taxonomy(json.loads(path.read_text()), source=str(path))
        for nanobar_type, entry in overrides.items():
            existing = taxonomy.get(nanobar_type)
            if existing is None:
                taxonomy[nanobar_type] = entry
            else:
                taxonomy[nanobar_type] = NanobarTypeEntry(
                    expected_scenarios={**existing.expected_scenarios, **entry.expected_scenarios}
                )

    return taxonomy


def resolve_taxonomy_entry(taxonomy: NanobarTypeTaxonomy, nanobar_type: str) -> NanobarTypeEntry | None:
    """Exact match first; then two dynamic-suffix conventions this project's own runtime
    produces (`nanobar_api/telemetry.py`'s `NanobarProps.type` call sites), neither of which can
    be pre-enumerated as literal keys in the vendored taxonomy JSON:

    - `f"replay-{original_nanobar_type}"` (the dashboard's Run tab, `admin/nanobar/api.py`) --
      resolved by stripping the prefix and resolving the *original* type instead (recursively,
      so a replay of a worker nanobar still falls through to the `"worker-"` case below).
      Replaying doesn't change what "covered" means for that original layer.
    - `f"worker-{channel}"` (`NanobarWorker._process_one`) -- `channel` varies per app/
      deployment, so this falls back to one channel-agnostic `"worker"` entry rather than
      requiring one entry per channel (which would go stale the moment a new channel appears).

    An unrecognized type matching neither convention still resolves to `None` — this project's
    own documented Open Decision (`nanobar_type_taxonomy_and_expected_coverage_buildplan-with-
    tasks.md`): "keeping the taxonomy in sync is an ongoing dependency," left visible rather
    than papered over with a guess.
    """
    entry = taxonomy.get(nanobar_type)
    if entry is not None:
        return entry
    if nanobar_type.startswith("replay-"):
        return resolve_taxonomy_entry(taxonomy, nanobar_type[len("replay-") :])
    if nanobar_type.startswith("worker-"):
        return taxonomy.get("worker")
    return None


def compute_regression_weight(
    nanobar: Nanobar, bricks: Sequence[RegressionBrick], taxonomy: NanobarTypeTaxonomy
) -> float:
    """Coverage-completeness score, scaled by criticality: sum of covered required-scenario
    weights (from `resolve_taxonomy_entry(taxonomy, nanobar.nanobar_type)`) over sum of all
    required-scenario weights defined for that type, times `nanobar.criticality`. `bricks` must
    already be filtered to the ones actually bound to `nanobar` (e.g.
    `bricks_store.get_bricks_for_nanobar`'s return value) — this function does no DB access
    itself, to stay unit-testable without one.

    Two edge cases, both deliberate, not oversights:
    - **Unresolvable `nanobar_type` (no exact taxonomy entry, and not a recognized
      `"replay-"`/`"worker-"` dynamic form — see `resolve_taxonomy_entry`) — returns
      `nanobar.regression_weight` unchanged**, not a guessed formula. A real `nanobar_type` with
      no taxonomy entry is exactly this doc's own Open Decision 1 ("keeping the taxonomy in sync
      is an ongoing dependency"); inventing a fallback here would silently paper over that gap
      instead of leaving it visible.
    - **Zero required scenarios defined for the type — returns `nanobar.criticality` alone.**
      Coverage-completeness is undefined with nothing to be complete *about*; criticality is
      still a real, human-set signal worth returning rather than an arbitrary `0.0`.
    """
    entry = resolve_taxonomy_entry(taxonomy, nanobar.nanobar_type)
    if entry is None:
        return nanobar.regression_weight

    required = {name: scenario for name, scenario in entry.expected_scenarios.items() if scenario.required}
    total_weight = sum(scenario.weight for scenario in required.values())
    if total_weight == 0.0:
        return nanobar.criticality

    covered = {brick.regression_scenario_type for brick in bricks if brick.regression_scenario_type is not None}
    covered_weight = sum(scenario.weight for name, scenario in required.items() if name in covered)

    return (covered_weight / total_weight) * nanobar.criticality


def detect_coverage_gaps(
    nanobar: Nanobar, bricks: Sequence[RegressionBrick], taxonomy: NanobarTypeTaxonomy
) -> list[str]:
    """Returns the required scenario types from `resolve_taxonomy_entry(taxonomy,
    nanobar.nanobar_type)` with no corresponding bound brick — the literal "what's missing"
    list. `bricks` must already be filtered to the ones bound to `nanobar`, same contract as
    `compute_regression_weight` above. Empty (not guessed at) for an unresolvable
    `nanobar_type` — same Open Decision 1 reasoning.
    """
    entry = resolve_taxonomy_entry(taxonomy, nanobar.nanobar_type)
    if entry is None:
        return []

    covered = {brick.regression_scenario_type for brick in bricks if brick.regression_scenario_type is not None}
    return [name for name, scenario in entry.expected_scenarios.items() if scenario.required and name not in covered]
