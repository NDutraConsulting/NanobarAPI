from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from nanobar_api.bricks.binding import (
    bind_composite_nanobars,
    bind_new_bricks_to_nanobars,
    get_or_create_nanobar_by_route_key,
)
from nanobar_api.bricks.schema import RegressionBrick
from nanobar_api.bricks.store import (
    connect,
    get_bricks_for_nanobar,
    get_nanobars_for_brick,
    insert_brick,
    list_nanobars,
)
from nanobar_api.taxonomy import ExpectedScenario, NanobarTypeEntry, NanobarTypeTaxonomy


def _make_brick(
    brick_id: str,
    content_hash: str,
    *,
    nanobar_type: str | None = "validator-request-response",
    route_key: str | None = "GET /health",
    request: dict[str, object] | None = None,
    regression_scenario_type: str | None = None,
) -> RegressionBrick:
    source: dict[str, object] = {"trace_id": "trace-1", "span_id": "span-1", "channel": "snapshot"}
    if nanobar_type is not None:
        source["nanobar_type"] = nanobar_type
    if route_key is not None:
        source["route_key"] = route_key
    return RegressionBrick(
        regression_brick_id=brick_id,
        schema_version="1.0",
        brick_version=1,
        source=source,
        request=request if request is not None else {"body": {}},
        response={"ok": True},
        content_hash=content_hash,
        regression_scenario_type=regression_scenario_type,
        created_by="test",
    )


def test_get_or_create_creates_then_reuses(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "regression_bricks.db"))
    try:
        first, created_first = get_or_create_nanobar_by_route_key(
            conn, nanobar_type="validator-request-response", route_key="GET /health"
        )
        second, created_second = get_or_create_nanobar_by_route_key(
            conn, nanobar_type="validator-request-response", route_key="GET /health"
        )

        assert created_first is True
        assert created_second is False
        assert first.nanobar_id == second.nanobar_id
        assert first.nanobar_type == "validator-request-response"
        assert first.monitor_target_refs[0].stable_name == "GET /health"
        assert first.monitor_target_refs[0].target_type == "route"
    finally:
        conn.close()


def test_get_or_create_distinguishes_by_nanobar_type(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "regression_bricks.db"))
    try:
        validator_nanobar, _ = get_or_create_nanobar_by_route_key(
            conn, nanobar_type="validator-request-response", route_key="GET /health"
        )
        controller_nanobar, _ = get_or_create_nanobar_by_route_key(
            conn, nanobar_type="controller-request-response", route_key="GET /health"
        )

        assert validator_nanobar.nanobar_id != controller_nanobar.nanobar_id
    finally:
        conn.close()


def test_get_or_create_placeholder_metadata_matches_seed_script_convention(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "regression_bricks.db"))
    try:
        nanobar, _ = get_or_create_nanobar_by_route_key(
            conn, nanobar_type="validator-request-response", route_key="GET /health"
        )

        assert nanobar.regression_weight == 0.5
        assert nanobar.endpoint_scenario_frequency == {"state": "unmeasured"}
        assert nanobar.request_object_id == "req-GET /health"
        assert nanobar.response_object_id == "res-GET /health"
    finally:
        conn.close()


def test_get_or_create_concurrent_first_sight_creates_exactly_one_row(tmp_path: Path) -> None:
    db_path = str(tmp_path / "regression_bricks.db")
    setup_conn = connect(db_path)
    setup_conn.close()

    created_flags: list[bool] = []
    lock = threading.Lock()
    errors: list[BaseException] = []

    def _worker() -> None:
        conn = connect(db_path)
        try:
            _, was_created = get_or_create_nanobar_by_route_key(
                conn, nanobar_type="validator-request-response", route_key="GET /health"
            )
            with lock:
                created_flags.append(was_created)
        except BaseException as exc:  # pragma: no cover - surfaced via `errors` assertion below
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=_worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert created_flags.count(True) == 1
    assert created_flags.count(False) == 4

    conn = connect(db_path)
    try:
        nanobars = list_nanobars(conn)
        assert len(nanobars) == 1
    finally:
        conn.close()


def test_bind_new_bricks_creates_nanobars_and_bindings(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "regression_bricks.db"))
    try:
        bricks = [
            _make_brick("rbrick-1", "sha256:one", nanobar_type="validator-request-response"),
            _make_brick("rbrick-2", "sha256:two", nanobar_type="controller-request-response"),
        ]
        for brick in bricks:
            insert_brick(conn, brick)

        result = bind_new_bricks_to_nanobars(conn, bricks)

        assert result.nanobars_created == 2
        assert result.bindings_created == 2
        assert result.skipped == 0

        nanobars = list_nanobars(conn)
        assert len(nanobars) == 2
        for brick in bricks:
            bound_nanobars = get_nanobars_for_brick(conn, brick.regression_brick_id)
            assert len(bound_nanobars) == 1
            assert bound_nanobars[0].nanobar_type == brick.source["nanobar_type"]
    finally:
        conn.close()


def test_bind_new_bricks_reuses_nanobar_for_bricks_sharing_route_key_and_type(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "regression_bricks.db"))
    try:
        bricks = [
            _make_brick("rbrick-1", "sha256:one"),
            _make_brick("rbrick-2", "sha256:two"),
        ]
        for brick in bricks:
            insert_brick(conn, brick)

        result = bind_new_bricks_to_nanobars(conn, bricks)

        assert result.nanobars_created == 1
        assert result.bindings_created == 2
        nanobars = list_nanobars(conn)
        assert len(nanobars) == 1
        bricks_bound = get_bricks_for_nanobar(conn, nanobars[0].nanobar_id)
        assert {b.regression_brick_id for b in bricks_bound} == {"rbrick-1", "rbrick-2"}
    finally:
        conn.close()


def test_bind_new_bricks_skips_bricks_without_nanobar_type_or_route_key(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "regression_bricks.db"))
    try:
        bricks = [
            _make_brick("rbrick-1", "sha256:one", nanobar_type=None),
            _make_brick("rbrick-2", "sha256:two", route_key=None),
        ]

        result = bind_new_bricks_to_nanobars(conn, bricks)

        assert result.nanobars_created == 0
        assert result.bindings_created == 0
        assert result.skipped == 2
        assert list_nanobars(conn) == []
    finally:
        conn.close()


def test_bind_new_bricks_uses_match_method_exact(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "regression_bricks.db"))
    try:
        brick = _make_brick("rbrick-1", "sha256:one")
        insert_brick(conn, brick)

        bind_new_bricks_to_nanobars(conn, [brick])

        row = conn.execute(
            "SELECT match_method, matcher_version, matched_by, confidence FROM nanobar_regression_bricks"
        ).fetchone()
        assert row[0] == "exact"
        assert row[1] == "v1"
        assert row[2] == "auto-registration"
        assert row[3] == 1.0
    finally:
        conn.close()


def test_get_or_create_finds_match_among_multiple_nanobars_of_same_type(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "regression_bricks.db"))
    try:
        get_or_create_nanobar_by_route_key(conn, nanobar_type="validator-request-response", route_key="GET /health")
        get_or_create_nanobar_by_route_key(conn, nanobar_type="validator-request-response", route_key="POST /orders")

        found, was_created = get_or_create_nanobar_by_route_key(
            conn, nanobar_type="validator-request-response", route_key="POST /orders"
        )

        assert was_created is False
        assert found.monitor_target_refs[0].stable_name == "POST /orders"
    finally:
        conn.close()


class _InsertFailingConnection:
    """Duck-typed `sqlite3.Connection` stand-in that fails the `INSERT INTO nanobars` statement
    specifically — exercises `get_or_create_nanobar_by_route_key`'s rollback-on-failure path
    without relying on a specific real SQLite error. Supports the context-manager protocol too,
    since `insert_nanobar` uses `with conn:` internally (never reached here, since the failure
    happens one statement earlier, inside `execute()`)."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real
        self.rolled_back = False

    def __enter__(self) -> _InsertFailingConnection:
        self._real.__enter__()
        return self

    def __exit__(self, *exc_info: object) -> bool | None:
        return self._real.__exit__(*exc_info)  # type: ignore[arg-type]

    def execute(self, sql: str, *args: object, **kwargs: object) -> sqlite3.Cursor:
        if "INSERT INTO nanobars" in sql:
            raise RuntimeError("boom")
        return self._real.execute(sql, *args, **kwargs)  # type: ignore[arg-type]

    def commit(self) -> None:
        self._real.commit()

    def rollback(self) -> None:
        self.rolled_back = True
        self._real.rollback()


def test_get_or_create_rolls_back_on_failure(tmp_path: Path) -> None:
    real_conn = connect(str(tmp_path / "regression_bricks.db"))
    try:
        proxy = _InsertFailingConnection(real_conn)

        with pytest.raises(RuntimeError, match="boom"):
            get_or_create_nanobar_by_route_key(
                proxy,  # type: ignore[arg-type]
                nanobar_type="validator-request-response",
                route_key="GET /health",
            )

        assert proxy.rolled_back is True
        assert list_nanobars(real_conn) == []
    finally:
        real_conn.close()


def test_bind_composite_creates_composite_nanobar_when_all_member_types_present(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "regression_bricks.db"))
    try:
        controller_brick = _make_brick("rbrick-1", "sha256:one", nanobar_type="controller-request-response")
        orm_brick = _make_brick("rbrick-2", "sha256:two", nanobar_type="orm-request-response")
        for brick in (controller_brick, orm_brick):
            insert_brick(conn, brick)

        result = bind_composite_nanobars(
            conn,
            [controller_brick, orm_brick],
            composite_nanobar_type="controller-to-db",
            member_nanobar_types=("controller-request-response", "orm-request-response"),
        )

        assert result.nanobars_created == 1
        assert result.bindings_created == 2
        assert result.skipped == 0

        nanobars = list_nanobars(conn)
        composite = next(n for n in nanobars if n.nanobar_type == "controller-to-db")
        bound = get_bricks_for_nanobar(conn, composite.nanobar_id)
        assert {b.regression_brick_id for b in bound} == {"rbrick-1", "rbrick-2"}
    finally:
        conn.close()


def test_bind_composite_skips_route_key_missing_a_member_type(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "regression_bricks.db"))
    try:
        controller_brick = _make_brick(
            "rbrick-1", "sha256:one", nanobar_type="controller-request-response", route_key="GET /health"
        )
        # orm brick for a DIFFERENT route -- no composite should form for either route.
        orm_brick = _make_brick("rbrick-2", "sha256:two", nanobar_type="orm-request-response", route_key="POST /other")
        for brick in (controller_brick, orm_brick):
            insert_brick(conn, brick)

        result = bind_composite_nanobars(
            conn,
            [controller_brick, orm_brick],
            composite_nanobar_type="controller-to-db",
            member_nanobar_types=("controller-request-response", "orm-request-response"),
        )

        assert result.nanobars_created == 0
        assert result.bindings_created == 0
        assert result.skipped == 2
        assert list_nanobars(conn) == []
    finally:
        conn.close()


def test_bind_composite_derives_route_key_for_untagged_api_brick(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "regression_bricks.db"))
    try:
        api_brick = _make_brick(
            "rbrick-1",
            "sha256:one",
            nanobar_type=None,
            route_key=None,
            request={"method": "POST", "path": "/orders"},
        )
        orm_brick = _make_brick("rbrick-2", "sha256:two", nanobar_type="orm-request-response", route_key="POST /orders")
        for brick in (api_brick, orm_brick):
            insert_brick(conn, brick)

        result = bind_composite_nanobars(
            conn,
            [api_brick, orm_brick],
            composite_nanobar_type="api-to-db",
            member_nanobar_types=("api-request-response", "orm-request-response"),
        )

        assert result.nanobars_created == 1
        assert result.bindings_created == 2
        nanobars = list_nanobars(conn)
        composite = next(n for n in nanobars if n.nanobar_type == "api-to-db")
        assert composite.monitor_target_refs[0].stable_name == "POST /orders"
    finally:
        conn.close()


def test_bind_composite_reuses_existing_composite_nanobar_across_calls(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "regression_bricks.db"))
    try:
        first_pair = [
            _make_brick("rbrick-1", "sha256:one", nanobar_type="controller-request-response"),
            _make_brick("rbrick-2", "sha256:two", nanobar_type="orm-request-response"),
        ]
        second_pair = [
            _make_brick("rbrick-3", "sha256:three", nanobar_type="controller-request-response"),
            _make_brick("rbrick-4", "sha256:four", nanobar_type="orm-request-response"),
        ]
        for brick in [*first_pair, *second_pair]:
            insert_brick(conn, brick)

        bind_composite_nanobars(
            conn,
            first_pair,
            composite_nanobar_type="controller-to-db",
            member_nanobar_types=("controller-request-response", "orm-request-response"),
        )
        result = bind_composite_nanobars(
            conn,
            second_pair,
            composite_nanobar_type="controller-to-db",
            member_nanobar_types=("controller-request-response", "orm-request-response"),
        )

        assert result.nanobars_created == 0  # reused the nanobar created for the first pair
        nanobars = [n for n in list_nanobars(conn) if n.nanobar_type == "controller-to-db"]
        assert len(nanobars) == 1
    finally:
        conn.close()


def test_bind_composite_ignores_brick_with_no_derivable_route_key(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "regression_bricks.db"))
    try:
        no_route_key_brick = _make_brick(
            "rbrick-1", "sha256:one", nanobar_type=None, route_key=None, request={"body": {}}
        )
        orm_brick = _make_brick("rbrick-2", "sha256:two", nanobar_type="orm-request-response")
        for brick in (no_route_key_brick, orm_brick):
            insert_brick(conn, brick)

        result = bind_composite_nanobars(
            conn,
            [no_route_key_brick, orm_brick],
            composite_nanobar_type="api-to-db",
            member_nanobar_types=("api-request-response", "orm-request-response"),
        )

        assert result.bindings_created == 0
        assert list_nanobars(conn) == []
    finally:
        conn.close()


def test_bind_composite_treats_empty_string_route_key_as_absent(tmp_path: Path) -> None:
    # `_effective_route_key()` must use truthiness, not `is not None` -- an empty-string
    # `route_key` (or empty method/path) is not a real route key. Before that fix, an empty
    # string passed `is not None` and was used as-is, so any two otherwise-unrelated
    # malformed/untagged bricks of different member types would collide under one degenerate
    # "" route key and get spuriously bound into the same composite nanobar. Both bricks here
    # carry empty route info specifically so they *would* collide under the old logic --
    # distinct-but-both-missing route keys (as in the "no derivable route key" test above)
    # wouldn't actually exercise the bug, since they'd never land in the same bucket anyway.
    conn = connect(str(tmp_path / "regression_bricks.db"))
    try:
        empty_route_key_api_brick = _make_brick(
            "rbrick-1", "sha256:one", nanobar_type=None, route_key="", request={"method": "", "path": ""}
        )
        empty_route_key_orm_brick = _make_brick(
            "rbrick-2",
            "sha256:two",
            nanobar_type="orm-request-response",
            route_key="",
            request={"method": "", "path": ""},
        )
        for brick in (empty_route_key_api_brick, empty_route_key_orm_brick):
            insert_brick(conn, brick)

        result = bind_composite_nanobars(
            conn,
            [empty_route_key_api_brick, empty_route_key_orm_brick],
            composite_nanobar_type="api-to-db",
            member_nanobar_types=("api-request-response", "orm-request-response"),
        )

        assert result.bindings_created == 0
        assert list_nanobars(conn) == []
    finally:
        conn.close()


def test_bind_composite_ignores_bricks_of_uninvolved_nanobar_types(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "regression_bricks.db"))
    try:
        validator_brick = _make_brick("rbrick-1", "sha256:one", nanobar_type="validator-request-response")
        controller_brick = _make_brick("rbrick-2", "sha256:two", nanobar_type="controller-request-response")
        orm_brick = _make_brick("rbrick-3", "sha256:three", nanobar_type="orm-request-response")
        for brick in (validator_brick, controller_brick, orm_brick):
            insert_brick(conn, brick)

        result = bind_composite_nanobars(
            conn,
            [validator_brick, controller_brick, orm_brick],
            composite_nanobar_type="controller-to-db",
            member_nanobar_types=("controller-request-response", "orm-request-response"),
        )

        assert result.bindings_created == 2
        composite = next(n for n in list_nanobars(conn) if n.nanobar_type == "controller-to-db")
        bound = get_bricks_for_nanobar(conn, composite.nanobar_id)
        assert {b.regression_brick_id for b in bound} == {"rbrick-2", "rbrick-3"}
    finally:
        conn.close()


# ------------------------------------------------- taxonomy-driven regression_weight recompute

_WEIGHT_TEST_TAXONOMY: NanobarTypeTaxonomy = {
    "validator-request-response": NanobarTypeEntry(
        expected_scenarios={
            "success": ExpectedScenario(weight=1.0, required=True, synthesizable=False),
            "invalid_input": ExpectedScenario(weight=0.6, required=True, synthesizable=True),
        }
    ),
    "controller-request-response": NanobarTypeEntry(
        expected_scenarios={"success": ExpectedScenario(weight=1.0, required=True, synthesizable=False)}
    ),
    "orm-request-response": NanobarTypeEntry(
        expected_scenarios={"success": ExpectedScenario(weight=1.0, required=True, synthesizable=False)}
    ),
}


def test_bind_new_bricks_with_taxonomy_recomputes_weight(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "regression_bricks.db"))
    try:
        brick = _make_brick(
            "rbrick-1", "sha256:one", nanobar_type="validator-request-response", regression_scenario_type="success"
        )
        insert_brick(conn, brick)

        bind_new_bricks_to_nanobars(conn, [brick], taxonomy=_WEIGHT_TEST_TAXONOMY)

        nanobar = list_nanobars(conn)[0]
        # 1 of 2 required scenarios covered (success, weight 1.0, out of total 1.6) * default criticality 0.5
        assert nanobar.regression_weight == pytest.approx((1.0 / 1.6) * 0.5)
    finally:
        conn.close()


def test_bind_new_bricks_without_taxonomy_leaves_weight_as_placeholder(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "regression_bricks.db"))
    try:
        brick = _make_brick(
            "rbrick-1", "sha256:one", nanobar_type="validator-request-response", regression_scenario_type="success"
        )
        insert_brick(conn, brick)

        bind_new_bricks_to_nanobars(conn, [brick])  # no taxonomy -- unchanged behavior

        nanobar = list_nanobars(conn)[0]
        assert nanobar.regression_weight == 0.5  # the placeholder default, never recomputed
    finally:
        conn.close()


def test_bind_new_bricks_recomputes_weight_once_per_nanobar_using_all_its_bricks(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "regression_bricks.db"))
    try:
        first = _make_brick(
            "rbrick-1", "sha256:one", nanobar_type="validator-request-response", regression_scenario_type="success"
        )
        second = _make_brick(
            "rbrick-2",
            "sha256:two",
            nanobar_type="validator-request-response",
            regression_scenario_type="invalid_input",
        )
        insert_brick(conn, first)
        insert_brick(conn, second)

        bind_new_bricks_to_nanobars(conn, [first, second], taxonomy=_WEIGHT_TEST_TAXONOMY)

        nanobar = list_nanobars(conn)[0]
        # Both required scenarios now covered across both bricks bound to the same nanobar.
        assert nanobar.regression_weight == pytest.approx(0.5)
    finally:
        conn.close()


def test_bind_composite_with_taxonomy_recomputes_composite_weight(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "regression_bricks.db"))
    try:
        controller_brick = _make_brick(
            "rbrick-1",
            "sha256:one",
            nanobar_type="controller-request-response",
            regression_scenario_type="success",
        )
        orm_brick = _make_brick(
            "rbrick-2", "sha256:two", nanobar_type="orm-request-response", regression_scenario_type="success"
        )
        for brick in (controller_brick, orm_brick):
            insert_brick(conn, brick)

        # "controller-to-db" isn't itself a taxonomy entry -- unknown-type fallback applies
        # (returns the placeholder unchanged), proving the wiring runs without crashing even
        # when the composite type has no taxonomy entry of its own.
        bind_composite_nanobars(
            conn,
            [controller_brick, orm_brick],
            composite_nanobar_type="controller-to-db",
            member_nanobar_types=("controller-request-response", "orm-request-response"),
            taxonomy=_WEIGHT_TEST_TAXONOMY,
        )

        composite = next(n for n in list_nanobars(conn) if n.nanobar_type == "controller-to-db")
        assert composite.regression_weight == 0.5  # unknown type to the taxonomy -- unchanged
    finally:
        conn.close()
