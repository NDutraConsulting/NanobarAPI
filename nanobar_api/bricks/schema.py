from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS regression_bricks (
    regression_brick_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    brick_version INTEGER NOT NULL CHECK (brick_version > 0),
    forked_from_regression_brick_id TEXT,
    source_json TEXT NOT NULL CHECK (json_valid(source_json)),
    request_json TEXT NOT NULL CHECK (json_valid(request_json)),
    response_json TEXT NOT NULL CHECK (json_valid(response_json)),
    trace_refs_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(trace_refs_json)),
    capture_policy_id TEXT,
    content_hash TEXT NOT NULL UNIQUE,
    regression_scenario_type TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT NOT NULL,
    FOREIGN KEY (forked_from_regression_brick_id) REFERENCES regression_bricks(regression_brick_id)
);

CREATE TABLE IF NOT EXISTS nanobars (
    nanobar_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    system_name TEXT NOT NULL,
    system_version TEXT NOT NULL,
    nanobar_type TEXT NOT NULL,
    request_object_id TEXT NOT NULL,
    response_object_id TEXT NOT NULL,
    regression_weight REAL NOT NULL CHECK (regression_weight BETWEEN 0.0 AND 1.0),
    endpoint_scenario_frequency_json TEXT NOT NULL CHECK (json_valid(endpoint_scenario_frequency_json)),
    monitor_target_refs_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(monitor_target_refs_json)),
    label TEXT,
    scenario_description TEXT,
    component_source_description TEXT,
    domain TEXT,
    source_info_json TEXT CHECK (source_info_json IS NULL OR json_valid(source_info_json)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nanobar_regression_bricks (
    nanobar_id TEXT NOT NULL,
    regression_brick_id TEXT NOT NULL,
    match_method TEXT NOT NULL CHECK (match_method IN ('exact', 'regex', 'fuzzy', 'trace', 'manual')),
    match_rule TEXT,
    confidence REAL CHECK (confidence BETWEEN 0.0 AND 1.0),
    matcher_version TEXT NOT NULL,
    matched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    matched_by TEXT NOT NULL,
    PRIMARY KEY (nanobar_id, regression_brick_id),
    FOREIGN KEY (nanobar_id) REFERENCES nanobars(nanobar_id) ON DELETE CASCADE,
    FOREIGN KEY (regression_brick_id) REFERENCES regression_bricks(regression_brick_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS regression_brick_review_status (
    regression_brick_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('new', 'reviewed', 'flagged', 'promoted')) DEFAULT 'new',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by TEXT NOT NULL,
    FOREIGN KEY (regression_brick_id) REFERENCES regression_bricks(regression_brick_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS regression_brick_scenario (
    regression_brick_id TEXT PRIMARY KEY,
    regression_scenario_label TEXT,
    description TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by TEXT NOT NULL,
    FOREIGN KEY (regression_brick_id) REFERENCES regression_bricks(regression_brick_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS regression_brick_tags (
    regression_brick_id TEXT NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (regression_brick_id, tag),
    FOREIGN KEY (regression_brick_id) REFERENCES regression_bricks(regression_brick_id) ON DELETE CASCADE
);
"""

TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS regression_bricks_are_immutable
BEFORE UPDATE ON regression_bricks
BEGIN
    SELECT RAISE(ABORT, 'RegressionBricks are immutable; fork a new brick');
END;
"""


@dataclass(frozen=True)
class RegressionBrick:
    regression_brick_id: str
    schema_version: str
    brick_version: int
    source: dict[str, Any]
    request: dict[str, Any]
    response: dict[str, Any]
    content_hash: str
    created_by: str
    trace_refs: list[dict[str, Any]] = field(default_factory=list)
    capture_policy_id: str | None = None
    forked_from_regression_brick_id: str | None = None
    regression_scenario_type: str | None = None


@dataclass(frozen=True)
class MonitorTargetRef:
    target_type: str
    stable_name: str


@dataclass(frozen=True)
class Nanobar:
    nanobar_id: str
    schema_version: str
    system_name: str
    system_version: str
    nanobar_type: str
    request_object_id: str
    response_object_id: str
    regression_weight: float
    endpoint_scenario_frequency: dict[str, Any]
    created_by: str
    monitor_target_refs: list[MonitorTargetRef] = field(default_factory=list)
    label: str | None = None
    scenario_description: str | None = None
    component_source_description: str | None = None
    domain: str | None = None
    source_info: dict[str, Any] | None = None


@dataclass(frozen=True)
class NanobarBrickBinding:
    nanobar_id: str
    regression_brick_id: str
    match_method: str
    matcher_version: str
    matched_by: str
    match_rule: str | None = None
    confidence: float | None = None


REVIEW_STATUSES = ("new", "reviewed", "flagged", "promoted")


@dataclass(frozen=True)
class BrickReviewStatus:
    regression_brick_id: str
    status: str
    updated_by: str


@dataclass(frozen=True)
class BrickScenario:
    regression_brick_id: str
    regression_scenario_label: str | None
    description: str | None
    updated_by: str
