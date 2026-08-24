from __future__ import annotations

import json
import sqlite3

from nanobar_api.bricks.schema import (
    REVIEW_STATUSES,
    SCHEMA_SQL,
    TRIGGER_SQL,
    BrickReviewStatus,
    MonitorTargetRef,
    Nanobar,
    NanobarBrickBinding,
    RegressionBrick,
)


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_SQL)
    conn.executescript(TRIGGER_SQL)
    conn.commit()
    return conn


def insert_brick(conn: sqlite3.Connection, brick: RegressionBrick) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO regression_bricks (
                regression_brick_id, schema_version, brick_version, forked_from_regression_brick_id,
                source_json, request_json, response_json, trace_refs_json,
                capture_policy_id, content_hash, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                brick.regression_brick_id,
                brick.schema_version,
                brick.brick_version,
                brick.forked_from_regression_brick_id,
                json.dumps(brick.source),
                json.dumps(brick.request),
                json.dumps(brick.response),
                json.dumps(brick.trace_refs),
                brick.capture_policy_id,
                brick.content_hash,
                brick.created_by,
            ),
        )


def get_brick(conn: sqlite3.Connection, regression_brick_id: str) -> RegressionBrick | None:
    row = conn.execute(
        "SELECT * FROM regression_bricks WHERE regression_brick_id = ?", (regression_brick_id,)
    ).fetchone()
    return _row_to_brick(row) if row is not None else None


def get_brick_by_content_hash(conn: sqlite3.Connection, content_hash: str) -> RegressionBrick | None:
    row = conn.execute("SELECT * FROM regression_bricks WHERE content_hash = ?", (content_hash,)).fetchone()
    return _row_to_brick(row) if row is not None else None


def _row_to_brick(row: sqlite3.Row) -> RegressionBrick:
    return RegressionBrick(
        regression_brick_id=row["regression_brick_id"],
        schema_version=row["schema_version"],
        brick_version=row["brick_version"],
        forked_from_regression_brick_id=row["forked_from_regression_brick_id"],
        source=json.loads(row["source_json"]),
        request=json.loads(row["request_json"]),
        response=json.loads(row["response_json"]),
        trace_refs=json.loads(row["trace_refs_json"]),
        capture_policy_id=row["capture_policy_id"],
        content_hash=row["content_hash"],
        created_by=row["created_by"],
    )


def insert_nanobar(conn: sqlite3.Connection, nanobar: Nanobar) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO nanobars (
                nanobar_id, schema_version, system_name, system_version, regression_scenario_type,
                request_object_id, response_object_id, regression_weight,
                endpoint_scenario_frequency_json, monitor_target_refs_json, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                nanobar.nanobar_id,
                nanobar.schema_version,
                nanobar.system_name,
                nanobar.system_version,
                nanobar.regression_scenario_type,
                nanobar.request_object_id,
                nanobar.response_object_id,
                nanobar.regression_weight,
                json.dumps(nanobar.endpoint_scenario_frequency),
                json.dumps(
                    [
                        {"target_type": ref.target_type, "stable_name": ref.stable_name}
                        for ref in nanobar.monitor_target_refs
                    ]
                ),
                nanobar.created_by,
            ),
        )


def get_nanobar(conn: sqlite3.Connection, nanobar_id: str) -> Nanobar | None:
    row = conn.execute("SELECT * FROM nanobars WHERE nanobar_id = ?", (nanobar_id,)).fetchone()
    return _row_to_nanobar(row) if row is not None else None


def list_nanobars(conn: sqlite3.Connection, target_type: str | None = None) -> list[Nanobar]:
    rows = conn.execute("SELECT * FROM nanobars ORDER BY created_at").fetchall()
    nanobars = [_row_to_nanobar(row) for row in rows]
    if target_type is None:
        return nanobars
    return [n for n in nanobars if any(ref.target_type == target_type for ref in n.monitor_target_refs)]


def _row_to_nanobar(row: sqlite3.Row) -> Nanobar:
    return Nanobar(
        nanobar_id=row["nanobar_id"],
        schema_version=row["schema_version"],
        system_name=row["system_name"],
        system_version=row["system_version"],
        regression_scenario_type=row["regression_scenario_type"],
        request_object_id=row["request_object_id"],
        response_object_id=row["response_object_id"],
        regression_weight=row["regression_weight"],
        endpoint_scenario_frequency=json.loads(row["endpoint_scenario_frequency_json"]),
        monitor_target_refs=[
            MonitorTargetRef(target_type=ref["target_type"], stable_name=ref["stable_name"])
            for ref in json.loads(row["monitor_target_refs_json"])
        ],
        created_by=row["created_by"],
    )


def bind_brick_to_nanobar(conn: sqlite3.Connection, binding: NanobarBrickBinding) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO nanobar_regression_bricks (
                nanobar_id, regression_brick_id, match_method, match_rule,
                confidence, matcher_version, matched_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                binding.nanobar_id,
                binding.regression_brick_id,
                binding.match_method,
                binding.match_rule,
                binding.confidence,
                binding.matcher_version,
                binding.matched_by,
            ),
        )


def get_bricks_for_nanobar(conn: sqlite3.Connection, nanobar_id: str) -> list[RegressionBrick]:
    rows = conn.execute(
        """
        SELECT rb.* FROM regression_bricks rb
        JOIN nanobar_regression_bricks nrb ON nrb.regression_brick_id = rb.regression_brick_id
        WHERE nrb.nanobar_id = ?
        ORDER BY rb.created_at
        """,
        (nanobar_id,),
    ).fetchall()
    return [_row_to_brick(row) for row in rows]


def set_review_status(conn: sqlite3.Connection, regression_brick_id: str, status: str, updated_by: str) -> None:
    if status not in REVIEW_STATUSES:
        raise ValueError(f"invalid review status {status!r}, must be one of {REVIEW_STATUSES}")
    with conn:
        conn.execute(
            """
            INSERT INTO regression_brick_review_status (regression_brick_id, status, updated_by)
            VALUES (?, ?, ?)
            ON CONFLICT(regression_brick_id) DO UPDATE SET
                status = excluded.status,
                updated_at = CURRENT_TIMESTAMP,
                updated_by = excluded.updated_by
            """,
            (regression_brick_id, status, updated_by),
        )


def get_review_status(conn: sqlite3.Connection, regression_brick_id: str) -> BrickReviewStatus:
    row = conn.execute(
        "SELECT regression_brick_id, status, updated_by FROM regression_brick_review_status "
        "WHERE regression_brick_id = ?",
        (regression_brick_id,),
    ).fetchone()
    if row is None:
        return BrickReviewStatus(regression_brick_id=regression_brick_id, status="new", updated_by="system")
    return BrickReviewStatus(
        regression_brick_id=row["regression_brick_id"], status=row["status"], updated_by=row["updated_by"]
    )


def list_bricks_by_review_status(conn: sqlite3.Connection, status: str | None = None) -> list[RegressionBrick]:
    rows = conn.execute(
        """
        SELECT rb.*, COALESCE(rs.status, 'new') AS effective_status
        FROM regression_bricks rb
        LEFT JOIN regression_brick_review_status rs ON rs.regression_brick_id = rb.regression_brick_id
        ORDER BY rb.created_at
        """
    ).fetchall()
    if status is not None and status not in REVIEW_STATUSES:
        raise ValueError(f"invalid review status {status!r}, must be one of {REVIEW_STATUSES}")
    return [_row_to_brick(row) for row in rows if status is None or row["effective_status"] == status]


def get_nanobars_for_brick(conn: sqlite3.Connection, regression_brick_id: str) -> list[Nanobar]:
    rows = conn.execute(
        """
        SELECT n.* FROM nanobars n
        JOIN nanobar_regression_bricks nrb ON nrb.nanobar_id = n.nanobar_id
        WHERE nrb.regression_brick_id = ?
        ORDER BY n.created_at
        """,
        (regression_brick_id,),
    ).fetchall()
    return [_row_to_nanobar(row) for row in rows]
