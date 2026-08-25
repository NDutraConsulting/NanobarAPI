from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nanobar_api.bricks.schema import MonitorTargetRef, Nanobar, NanobarBrickBinding, RegressionBrick
from nanobar_api.bricks.store import (
    add_brick_tag,
    bind_brick_to_nanobar,
    connect,
    get_brick,
    get_brick_by_content_hash,
    get_brick_scenario,
    get_bricks_for_nanobar,
    get_nanobar,
    get_nanobars_for_brick,
    get_review_status,
    get_tags_for_brick,
    insert_brick,
    insert_nanobar,
    list_bricks_by_review_status,
    list_bricks_by_tag,
    list_nanobars,
    remove_brick_tag,
    set_brick_scenario,
    set_review_status,
    update_nanobar,
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
        nanobar_type="api-response",
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


def test_review_status_defaults_to_new(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        insert_brick(conn, _make_brick("rbrick-1"))

        status = get_review_status(conn, "rbrick-1")

        assert status.status == "new"
        assert status.regression_brick_id == "rbrick-1"
    finally:
        conn.close()


def test_set_review_status_updates_and_get_reflects_it(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        insert_brick(conn, _make_brick("rbrick-1"))

        set_review_status(conn, "rbrick-1", "flagged", updated_by="alice")
        status = get_review_status(conn, "rbrick-1")

        assert status.status == "flagged"
        assert status.updated_by == "alice"
    finally:
        conn.close()


def test_set_review_status_can_be_changed_again(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        insert_brick(conn, _make_brick("rbrick-1"))

        set_review_status(conn, "rbrick-1", "flagged", updated_by="alice")
        set_review_status(conn, "rbrick-1", "promoted", updated_by="bob")
        status = get_review_status(conn, "rbrick-1")

        assert status.status == "promoted"
        assert status.updated_by == "bob"
    finally:
        conn.close()


def test_set_review_status_rejects_invalid_status(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        insert_brick(conn, _make_brick("rbrick-1"))

        with pytest.raises(ValueError, match="invalid review status"):
            set_review_status(conn, "rbrick-1", "bogus", updated_by="alice")
    finally:
        conn.close()


def test_list_bricks_by_review_status_treats_unset_as_new(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        insert_brick(conn, _make_brick("rbrick-1", content_hash="sha256:a"))
        insert_brick(conn, _make_brick("rbrick-2", content_hash="sha256:b"))
        set_review_status(conn, "rbrick-2", "flagged", updated_by="alice")

        new_bricks = list_bricks_by_review_status(conn, "new")
        flagged_bricks = list_bricks_by_review_status(conn, "flagged")
        all_bricks = list_bricks_by_review_status(conn)

        assert [b.regression_brick_id for b in new_bricks] == ["rbrick-1"]
        assert [b.regression_brick_id for b in flagged_bricks] == ["rbrick-2"]
        assert {b.regression_brick_id for b in all_bricks} == {"rbrick-1", "rbrick-2"}
    finally:
        conn.close()


def test_list_bricks_by_review_status_rejects_invalid_status(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        with pytest.raises(ValueError, match="invalid review status"):
            list_bricks_by_review_status(conn, "bogus")
    finally:
        conn.close()


def test_review_status_removed_when_brick_deleted_via_cascade(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        insert_brick(conn, _make_brick("rbrick-1"))
        set_review_status(conn, "rbrick-1", "flagged", updated_by="alice")

        # regression_bricks itself can't be deleted while bound to a nanobar (tested elsewhere),
        # but with no binding it's deletable, and review status should cascade with it.
        with conn:
            conn.execute("DELETE FROM regression_bricks WHERE regression_brick_id = 'rbrick-1'")

        row = conn.execute(
            "SELECT 1 FROM regression_brick_review_status WHERE regression_brick_id = 'rbrick-1'"
        ).fetchone()
        assert row is None
    finally:
        conn.close()


# ---------------------------------------------------------------------- nanobar new fields ---


def test_nanobar_type_and_human_navigation_fields_round_trip(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        nanobar = Nanobar(
            nanobar_id="nb-1",
            schema_version="1.0",
            system_name="checkout-service",
            system_version="1.0.0",
            nanobar_type="api-to-db",
            request_object_id="req-1",
            response_object_id="res-1",
            regression_weight=0.5,
            endpoint_scenario_frequency={"state": "unmeasured"},
            created_by="test",
            monitor_target_refs=[MonitorTargetRef(target_type="openapi_operation", stable_name="checkout")],
            label="Order lookup",
            scenario_description="Fetches a single order by id.",
            component_source_description="checkout_service.orders.repository",
            domain="checkout",
            source_info={"code.function.name": "checkout_service.orders.repository.get_order"},
        )
        insert_nanobar(conn, nanobar)

        fetched = get_nanobar(conn, "nb-1")

        assert fetched == nanobar
    finally:
        conn.close()


def test_update_nanobar_overwrites_human_navigation_fields(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        insert_nanobar(conn, _make_nanobar("nb-1"))

        update_nanobar(
            conn,
            "nb-1",
            label="Get order",
            scenario_description="Fetches a single order by id.",
            component_source_description="checkout.repository",
            domain="checkout",
        )
        fetched = get_nanobar(conn, "nb-1")

        assert fetched is not None
        assert fetched.label == "Get order"
        assert fetched.scenario_description == "Fetches a single order by id."
        assert fetched.component_source_description == "checkout.repository"
        assert fetched.domain == "checkout"
    finally:
        conn.close()


def test_update_nanobar_with_no_fields_clears_them(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        insert_nanobar(conn, _make_nanobar("nb-1"))
        update_nanobar(conn, "nb-1", label="X")

        update_nanobar(conn, "nb-1")  # no kwargs -- overwrites everything with None
        fetched = get_nanobar(conn, "nb-1")

        assert fetched is not None
        assert fetched.label is None
    finally:
        conn.close()


def test_nanobar_human_navigation_fields_default_to_none(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        insert_nanobar(conn, _make_nanobar("nb-1"))

        fetched = get_nanobar(conn, "nb-1")

        assert fetched is not None
        assert fetched.label is None
        assert fetched.scenario_description is None
        assert fetched.component_source_description is None
        assert fetched.domain is None
        assert fetched.source_info is None
    finally:
        conn.close()


# ------------------------------------------------------------- brick regression_scenario_type ---


def test_brick_regression_scenario_type_round_trips(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        brick = RegressionBrick(
            regression_brick_id="rbrick-1",
            schema_version="1.0",
            brick_version=1,
            source={},
            request={},
            response={"status_code": 404},
            content_hash="sha256:x",
            created_by="test",
            regression_scenario_type="not_found",
        )
        insert_brick(conn, brick)

        fetched = get_brick(conn, "rbrick-1")

        assert fetched == brick
        assert fetched.regression_scenario_type == "not_found"
    finally:
        conn.close()


def test_brick_regression_scenario_type_defaults_to_none(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        insert_brick(conn, _make_brick("rbrick-1"))

        fetched = get_brick(conn, "rbrick-1")

        assert fetched is not None
        assert fetched.regression_scenario_type is None
    finally:
        conn.close()


# --------------------------------------------------------------------- brick scenario (human) ---


def test_brick_scenario_defaults_to_unset(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        insert_brick(conn, _make_brick("rbrick-1"))

        scenario = get_brick_scenario(conn, "rbrick-1")

        assert scenario.regression_brick_id == "rbrick-1"
        assert scenario.regression_scenario_label is None
        assert scenario.description is None
        assert scenario.updated_by == "system"
    finally:
        conn.close()


def test_set_brick_scenario_updates_and_get_reflects_it(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        insert_brick(conn, _make_brick("rbrick-1"))

        set_brick_scenario(
            conn,
            "rbrick-1",
            regression_scenario_label="Order not found",
            description="Order id does not exist in the system.",
            updated_by="alice",
        )
        scenario = get_brick_scenario(conn, "rbrick-1")

        assert scenario.regression_scenario_label == "Order not found"
        assert scenario.description == "Order id does not exist in the system."
        assert scenario.updated_by == "alice"
    finally:
        conn.close()


def test_set_brick_scenario_can_be_changed_again(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        insert_brick(conn, _make_brick("rbrick-1"))

        set_brick_scenario(conn, "rbrick-1", regression_scenario_label="First", updated_by="alice")
        set_brick_scenario(conn, "rbrick-1", regression_scenario_label="Second", updated_by="bob")
        scenario = get_brick_scenario(conn, "rbrick-1")

        assert scenario.regression_scenario_label == "Second"
        assert scenario.updated_by == "bob"
    finally:
        conn.close()


def test_brick_scenario_removed_when_brick_deleted_via_cascade(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        insert_brick(conn, _make_brick("rbrick-1"))
        set_brick_scenario(conn, "rbrick-1", regression_scenario_label="X", updated_by="alice")

        with conn:
            conn.execute("DELETE FROM regression_bricks WHERE regression_brick_id = 'rbrick-1'")

        row = conn.execute("SELECT 1 FROM regression_brick_scenario WHERE regression_brick_id = 'rbrick-1'").fetchone()
        assert row is None
    finally:
        conn.close()


# -------------------------------------------------------------------------------- brick tags ---


def test_add_and_get_tags_for_brick(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        insert_brick(conn, _make_brick("rbrick-1"))

        add_brick_tag(conn, "rbrick-1", "flaky")
        add_brick_tag(conn, "rbrick-1", "checkout")

        assert get_tags_for_brick(conn, "rbrick-1") == ["checkout", "flaky"]
    finally:
        conn.close()


def test_add_brick_tag_is_idempotent(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        insert_brick(conn, _make_brick("rbrick-1"))

        add_brick_tag(conn, "rbrick-1", "flaky")
        add_brick_tag(conn, "rbrick-1", "flaky")

        assert get_tags_for_brick(conn, "rbrick-1") == ["flaky"]
    finally:
        conn.close()


def test_remove_brick_tag(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        insert_brick(conn, _make_brick("rbrick-1"))
        add_brick_tag(conn, "rbrick-1", "flaky")
        add_brick_tag(conn, "rbrick-1", "checkout")

        remove_brick_tag(conn, "rbrick-1", "flaky")

        assert get_tags_for_brick(conn, "rbrick-1") == ["checkout"]
    finally:
        conn.close()


def test_list_bricks_by_tag(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        insert_brick(conn, _make_brick("rbrick-1", content_hash="sha256:one"))
        insert_brick(conn, _make_brick("rbrick-2", content_hash="sha256:two"))
        add_brick_tag(conn, "rbrick-1", "flaky")
        add_brick_tag(conn, "rbrick-2", "stable")

        flaky = list_bricks_by_tag(conn, "flaky")

        assert [b.regression_brick_id for b in flaky] == ["rbrick-1"]
    finally:
        conn.close()


def test_add_brick_tag_to_unknown_brick_is_rejected_by_foreign_key(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            add_brick_tag(conn, "does-not-exist", "flaky")
    finally:
        conn.close()


def test_brick_tags_removed_when_brick_deleted_via_cascade(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    conn = connect(db_path)
    try:
        insert_brick(conn, _make_brick("rbrick-1"))
        add_brick_tag(conn, "rbrick-1", "flaky")

        with conn:
            conn.execute("DELETE FROM regression_bricks WHERE regression_brick_id = 'rbrick-1'")

        assert get_tags_for_brick(conn, "rbrick-1") == []
    finally:
        conn.close()
