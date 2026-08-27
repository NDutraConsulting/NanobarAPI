from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nanobar_api.dynamic_taxonomy import (
    connect,
    full_nanobar_type,
    get_entry,
    get_or_create_entry,
    list_entries,
    split_dynamic_nanobar_type,
)
from nanobar_api.taxonomy import ExpectedScenario, NanobarTypeEntry

_WORKER_DEFAULT = NanobarTypeEntry(
    expected_scenarios={
        "success": ExpectedScenario(weight=1.0, required=True, synthesizable=False),
        "server_error": ExpectedScenario(weight=0.3, required=True, synthesizable=False),
    }
)


def test_full_nanobar_type_joins_key_and_key_name() -> None:
    assert full_nanobar_type("worker", "domain.appointments") == "worker-domain.appointments"


def test_split_dynamic_nanobar_type_matches_a_known_key() -> None:
    assert split_dynamic_nanobar_type("worker-domain.appointments", known_keys=["worker"]) == (
        "worker",
        "domain.appointments",
    )


def test_split_dynamic_nanobar_type_returns_none_for_an_unrecognized_prefix() -> None:
    assert split_dynamic_nanobar_type("controller-request-response", known_keys=["worker"]) is None


def test_split_dynamic_nanobar_type_prefers_the_longest_matching_key() -> None:
    # "worker-batch" and "worker" both prefix-match "worker-batch-domain.x" -- the longer,
    # more specific key must win, not whichever happens first in an unsorted scan.
    result = split_dynamic_nanobar_type("worker-batch-domain.x", known_keys=["worker", "worker-batch"])
    assert result == ("worker-batch", "domain.x")


def test_get_entry_returns_none_when_absent(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "nanobar_type_system.db"))
    try:
        assert get_entry(conn, "worker", "domain.appointments") is None
    finally:
        conn.close()


def test_get_or_create_entry_creates_on_first_sight_and_reuses_after(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "nanobar_type_system.db"))
    try:
        entry, created = get_or_create_entry(
            conn, "worker", "domain.appointments", default_entry=_WORKER_DEFAULT, created_by="test"
        )
        assert created is True
        assert entry == _WORKER_DEFAULT

        again, created_again = get_or_create_entry(
            conn, "worker", "domain.appointments", default_entry=_WORKER_DEFAULT, created_by="test"
        )
        assert created_again is False
        assert again == _WORKER_DEFAULT
    finally:
        conn.close()


def test_get_or_create_entry_persists_across_connections(tmp_path: Path) -> None:
    db_path = str(tmp_path / "nanobar_type_system.db")
    conn1 = connect(db_path)
    try:
        get_or_create_entry(conn1, "worker", "domain.appointments", default_entry=_WORKER_DEFAULT, created_by="test")
    finally:
        conn1.close()

    conn2 = connect(db_path)
    try:
        assert get_entry(conn2, "worker", "domain.appointments") == _WORKER_DEFAULT
    finally:
        conn2.close()


def test_get_or_create_entry_keeps_different_key_names_independent(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "nanobar_type_system.db"))
    try:
        other_default = NanobarTypeEntry(
            expected_scenarios={"success": ExpectedScenario(weight=1.0, required=True, synthesizable=False)}
        )
        get_or_create_entry(conn, "worker", "domain.appointments", default_entry=_WORKER_DEFAULT, created_by="test")
        get_or_create_entry(conn, "worker", "domain.orders", default_entry=other_default, created_by="test")

        assert get_entry(conn, "worker", "domain.appointments") == _WORKER_DEFAULT
        assert get_entry(conn, "worker", "domain.orders") == other_default
    finally:
        conn.close()


def test_list_entries_returns_everything_sorted_and_filters_by_key(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "nanobar_type_system.db"))
    try:
        get_or_create_entry(conn, "worker", "domain.orders", default_entry=_WORKER_DEFAULT, created_by="test")
        get_or_create_entry(conn, "worker", "domain.appointments", default_entry=_WORKER_DEFAULT, created_by="test")
        get_or_create_entry(
            conn, "replay", "controller-request-response", default_entry=_WORKER_DEFAULT, created_by="test"
        )

        all_entries = list_entries(conn)
        assert [(key, key_name) for key, key_name, _ in all_entries] == [
            ("replay", "controller-request-response"),
            ("worker", "domain.appointments"),
            ("worker", "domain.orders"),
        ]

        worker_only = list_entries(conn, key="worker")
        assert [(key, key_name) for key, key_name, _ in worker_only] == [
            ("worker", "domain.appointments"),
            ("worker", "domain.orders"),
        ]
    finally:
        conn.close()


class _InsertFailingConnection:
    """Duck-typed `sqlite3.Connection` stand-in that fails the `INSERT INTO nanobar_type_keys`
    statement specifically -- exercises `get_or_create_entry`'s rollback-on-failure path without
    relying on a specific real SQLite error. Same shape as `test_bricks_binding.py`'s own
    `_InsertFailingConnection`, for the analogous `get_or_create_nanobar_by_route_key`."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real
        self.rolled_back = False

    def execute(self, sql: str, *args: object, **kwargs: object) -> sqlite3.Cursor:
        if "INSERT INTO nanobar_type_keys" in sql:
            raise RuntimeError("boom")
        return self._real.execute(sql, *args, **kwargs)  # type: ignore[arg-type]

    def commit(self) -> None:
        self._real.commit()

    def rollback(self) -> None:
        self.rolled_back = True
        self._real.rollback()


def test_get_or_create_entry_rolls_back_on_failure(tmp_path: Path) -> None:
    real_conn = connect(str(tmp_path / "nanobar_type_system.db"))
    try:
        proxy = _InsertFailingConnection(real_conn)

        with pytest.raises(RuntimeError, match="boom"):
            get_or_create_entry(
                proxy,  # type: ignore[arg-type]
                "worker",
                "domain.appointments",
                default_entry=_WORKER_DEFAULT,
                created_by="test",
            )

        assert proxy.rolled_back is True
        assert get_entry(real_conn, "worker", "domain.appointments") is None
    finally:
        real_conn.close()


def test_connect_creates_schema_idempotently(tmp_path: Path) -> None:
    db_path = str(tmp_path / "nanobar_type_system.db")

    conn1 = connect(db_path)
    conn1.close()
    conn2 = connect(db_path)  # must not error on second call
    conn2.close()
