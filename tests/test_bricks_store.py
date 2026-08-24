from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nanobar_api.bricks.schema import MonitorTargetRef, Nanobar, NanobarBrickBinding, RegressionBrick
from nanobar_api.bricks.store import (
    bind_brick_to_nanobar,
    connect,
    get_brick,
    get_brick_by_content_hash,
    get_bricks_for_nanobar,
    get_nanobar,
    get_nanobars_for_brick,
    insert_brick,
    insert_nanobar,
    list_nanobars,
)


def _make_brick(brick_id: str, content_hash: str = "sha256:abc") -> RegressionBrick:
    return RegressionBrick(
        regression_brick_id=brick_id,
        schema_version="1.0",
        brick_version=1,
        source={"host": "test"},
        request={"method": "GET", "headers": {}, "payload": {}},
        response={"status_code": 200, "payload": {}},
        content_hash=content_hash,
        created_by="test",
    )


def _make_nanobar(nanobar_id: str, target_refs: list[MonitorTargetRef] | None = None) -> Nanobar:
    return Nanobar(
        nanobar_id=nanobar_id,
        schema_version="1.0",
        system_name="checkout-service",
        system_version="1.0.0",
        regression_scenario_type="happy_path",
        request_object_id="req-1",
        response_object_id="res-1",
        regression_weight=0.5,
        endpoint_scenario_frequency={"state": "unmeasured"},
        created_by="test",
        monitor_target_refs=target_refs or [MonitorTargetRef(target_type="openapi_operation", stable_name="ping")],
    )


def test_connect_creates_schema_idempotently(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")

    conn1 = connect(db_path)
    conn1.close()
    conn2 = connect(db_path)
    conn2.close()


def test_insert_and_get_brick_round_trips(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        brick = _make_brick("rbrick-1")
        insert_brick(conn, brick)

        fetched = get_brick(conn, "rbrick-1")

        assert fetched == brick
    finally:
        conn.close()


def test_get_brick_missing_returns_none(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        assert get_brick(conn, "does-not-exist") is None
    finally:
        conn.close()


def test_get_brick_by_content_hash(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        brick = _make_brick("rbrick-1", content_hash="sha256:unique")
        insert_brick(conn, brick)

        assert get_brick_by_content_hash(conn, "sha256:unique") == brick
        assert get_brick_by_content_hash(conn, "sha256:missing") is None
    finally:
        conn.close()


def test_content_hash_must_be_unique(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        insert_brick(conn, _make_brick("rbrick-1", content_hash="sha256:dup"))

        with pytest.raises(sqlite3.IntegrityError):
            insert_brick(conn, _make_brick("rbrick-2", content_hash="sha256:dup"))
    finally:
        conn.close()


def test_bricks_are_immutable(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        insert_brick(conn, _make_brick("rbrick-1"))

        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE regression_bricks SET schema_version = '2.0' WHERE regression_brick_id = 'rbrick-1'")
    finally:
        conn.close()


def test_trace_refs_and_optional_fields_round_trip(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        brick = RegressionBrick(
            regression_brick_id="rbrick-1",
            schema_version="1.0",
            brick_version=2,
            source={},
            request={},
            response={},
            content_hash="sha256:x",
            created_by="test",
            trace_refs=[{"trace_id": "tr-1", "span_ids": ["sp-1"]}],
            capture_policy_id="cp-default",
            forked_from_regression_brick_id=None,
        )
        insert_brick(conn, brick)

        fetched = get_brick(conn, "rbrick-1")

        assert fetched == brick
    finally:
        conn.close()


def test_insert_and_get_nanobar_round_trips(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        nanobar = _make_nanobar("nb-1")
        insert_nanobar(conn, nanobar)

        fetched = get_nanobar(conn, "nb-1")

        assert fetched == nanobar
    finally:
        conn.close()


def test_get_nanobar_missing_returns_none(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        assert get_nanobar(conn, "does-not-exist") is None
    finally:
        conn.close()


def test_list_nanobars_filters_by_target_type(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        insert_nanobar(
            conn,
            _make_nanobar("nb-route", [MonitorTargetRef(target_type="openapi_operation", stable_name="ping")]),
        )
        insert_nanobar(
            conn,
            _make_nanobar("nb-service", [MonitorTargetRef(target_type="service", stable_name="billing")]),
        )

        all_nanobars = list_nanobars(conn)
        routes_only = list_nanobars(conn, target_type="openapi_operation")

        assert {n.nanobar_id for n in all_nanobars} == {"nb-route", "nb-service"}
        assert [n.nanobar_id for n in routes_only] == ["nb-route"]
    finally:
        conn.close()


def test_nanobar_with_multiple_monitor_target_refs_round_trips(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        refs = [
            MonitorTargetRef(target_type="openapi_operation", stable_name="createOrder"),
            MonitorTargetRef(target_type="service", stable_name="payment"),
        ]
        insert_nanobar(conn, _make_nanobar("nb-multi", refs))

        fetched = get_nanobar(conn, "nb-multi")

        assert fetched is not None
        assert fetched.monitor_target_refs == refs
    finally:
        conn.close()


def test_bind_brick_to_nanobar_and_query_both_directions(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        insert_nanobar(conn, _make_nanobar("nb-1"))
        insert_brick(conn, _make_brick("rbrick-1", content_hash="sha256:one"))
        insert_brick(conn, _make_brick("rbrick-2", content_hash="sha256:two"))

        bind_brick_to_nanobar(
            conn,
            NanobarBrickBinding(
                nanobar_id="nb-1",
                regression_brick_id="rbrick-1",
                match_method="exact",
                matcher_version="v1",
                matched_by="test",
            ),
        )
        bind_brick_to_nanobar(
            conn,
            NanobarBrickBinding(
                nanobar_id="nb-1",
                regression_brick_id="rbrick-2",
                match_method="trace",
                matcher_version="v1",
                matched_by="test",
                match_rule="trace_id equality",
                confidence=0.9,
            ),
        )

        bricks = get_bricks_for_nanobar(conn, "nb-1")
        nanobars = get_nanobars_for_brick(conn, "rbrick-1")

        assert {b.regression_brick_id for b in bricks} == {"rbrick-1", "rbrick-2"}
        assert [n.nanobar_id for n in nanobars] == ["nb-1"]
    finally:
        conn.close()


def test_binding_to_unknown_brick_is_rejected_by_foreign_key(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        insert_nanobar(conn, _make_nanobar("nb-1"))

        with pytest.raises(sqlite3.IntegrityError):
            bind_brick_to_nanobar(
                conn,
                NanobarBrickBinding(
                    nanobar_id="nb-1",
                    regression_brick_id="does-not-exist",
                    match_method="manual",
                    matcher_version="v1",
                    matched_by="test",
                ),
            )
    finally:
        conn.close()


def test_deleting_nanobar_cascades_to_bindings_but_not_bricks(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        insert_nanobar(conn, _make_nanobar("nb-1"))
        insert_brick(conn, _make_brick("rbrick-1", content_hash="sha256:cascade"))
        bind_brick_to_nanobar(
            conn,
            NanobarBrickBinding(
                nanobar_id="nb-1",
                regression_brick_id="rbrick-1",
                match_method="exact",
                matcher_version="v1",
                matched_by="test",
            ),
        )

        with conn:
            conn.execute("DELETE FROM nanobars WHERE nanobar_id = 'nb-1'")

        assert get_bricks_for_nanobar(conn, "nb-1") == []
        assert get_brick(conn, "rbrick-1") is not None
    finally:
        conn.close()


def test_deleting_brick_still_bound_to_a_nanobar_is_restricted(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        insert_nanobar(conn, _make_nanobar("nb-1"))
        insert_brick(conn, _make_brick("rbrick-1", content_hash="sha256:restrict"))
        bind_brick_to_nanobar(
            conn,
            NanobarBrickBinding(
                nanobar_id="nb-1",
                regression_brick_id="rbrick-1",
                match_method="exact",
                matcher_version="v1",
                matched_by="test",
            ),
        )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM regression_bricks WHERE regression_brick_id = 'rbrick-1'")
    finally:
        conn.close()
