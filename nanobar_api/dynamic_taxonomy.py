"""Runtime-writable, per-application taxonomy storage for dynamically-suffixed `nanobar_type`
values -- the SQLite counterpart to `taxonomy.py`'s vendored `nanobar.types.lock` file.

`nanobar.types.lock` is deliberately what its name says: a pinned, checked-in baseline, not
writable at runtime (same spirit as `uv.lock`). But a real dynamic `nanobar_type` like
`f"worker-{channel}"` (`NanobarWorker._process_one`, `nanobar_api/telemetry.py`) can have a
different `channel` for every app, and different channels can genuinely warrant different
expected-scenario coverage rules (a `"domain.appointments"` worker's failure modes aren't
necessarily a `"domain.orders"` worker's) -- a static lock file can't grow to cover that without
a code change and a release. This module is the dynamic layer instead: a dedicated,
per-application SQLite database (`demo/dashboard/dynamic_taxonomy_db.py` resolves its path, the
same `demo/data/*.db` convention every other per-app database here already follows) that a
running app can register new `(key, key_name)` entries into as it actually encounters them --
`get_or_create_entry()` mirrors `bricks/binding.py`'s `get_or_create_nanobar_by_route_key()`
exactly, down to the `BEGIN IMMEDIATE` atomic-claim discipline, for the same reason: two
concurrent first-sights of the same dynamic type must not race into two divergent entries.

The full `nanobar_type` string a `(key, key_name)` pair represents is always
`f"{key}-{key_name}"` (`full_nanobar_type()`) -- the exact same string shape the runtime already
produces (`"worker-domain.appointments"`), so a caller resolving a captured `nanobar_type`
against this store never needs a second, parallel naming scheme.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from typing import Any

from nanobar_api.taxonomy import ExpectedScenario, NanobarTypeEntry

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS nanobar_type_keys (
    key TEXT NOT NULL,
    key_name TEXT NOT NULL,
    expected_scenarios_json TEXT NOT NULL CHECK (json_valid(expected_scenarios_json)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT NOT NULL,
    PRIMARY KEY (key, key_name)
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def full_nanobar_type(key: str, key_name: str) -> str:
    return f"{key}-{key_name}"


def split_dynamic_nanobar_type(nanobar_type: str, *, known_keys: Sequence[str]) -> tuple[str, str] | None:
    """Splits a dynamic `nanobar_type` string (e.g. `"worker-domain.appointments"`) into its
    `(key, key_name)` parts, matched against `known_keys` -- the fixed prefixes this project's
    own runtime actually produces (see `nanobar_api/telemetry.py`'s `NanobarProps.type` call
    sites), not an open-ended guess at every hyphen in the string. Longest key first, so a key
    that happens to be a prefix of another never wins by accident (not a real case today, but a
    correctness trap otherwise)."""
    for key in sorted(known_keys, key=len, reverse=True):
        prefix = f"{key}-"
        if nanobar_type.startswith(prefix):
            return key, nanobar_type[len(prefix) :]
    return None


def _scenarios_from_json(raw: dict[str, Any]) -> dict[str, ExpectedScenario]:
    return {
        name: ExpectedScenario(weight=value["weight"], required=value["required"], synthesizable=value["synthesizable"])
        for name, value in raw.items()
    }


def _scenarios_to_json(scenarios: dict[str, ExpectedScenario]) -> dict[str, Any]:
    return {
        name: {"weight": scenario.weight, "required": scenario.required, "synthesizable": scenario.synthesizable}
        for name, scenario in scenarios.items()
    }


def get_entry(conn: sqlite3.Connection, key: str, key_name: str) -> NanobarTypeEntry | None:
    row = conn.execute(
        "SELECT expected_scenarios_json FROM nanobar_type_keys WHERE key = ? AND key_name = ?", (key, key_name)
    ).fetchone()
    if row is None:
        return None
    return NanobarTypeEntry(expected_scenarios=_scenarios_from_json(json.loads(row["expected_scenarios_json"])))


def get_or_create_entry(
    conn: sqlite3.Connection, key: str, key_name: str, *, default_entry: NanobarTypeEntry, created_by: str
) -> tuple[NanobarTypeEntry, bool]:
    """Atomic get-or-create keyed by `(key, key_name)`. Returns `(entry, was_created)` -- the
    caller (`demo/dashboard/api.py`'s taxonomy-resolution helper) needs to know which, the same
    reporting contract `get_or_create_nanobar_by_route_key()` already established.

    `PRIMARY KEY (key, key_name)` alone would reject a concurrent duplicate insert, but the
    `BEGIN IMMEDIATE` transaction keeps the read-then-write atomic rather than relying on
    catching an `IntegrityError` after the fact -- same reasoning `get_or_create_nanobar_by_
    route_key()` documents for the same shape of race.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = get_entry(conn, key, key_name)
        if existing is not None:
            conn.commit()
            return existing, False

        conn.execute(
            "INSERT INTO nanobar_type_keys (key, key_name, expected_scenarios_json, created_by) VALUES (?, ?, ?, ?)",
            (key, key_name, json.dumps(_scenarios_to_json(default_entry.expected_scenarios)), created_by),
        )
        conn.commit()
        return default_entry, True
    except BaseException:
        conn.rollback()
        raise


def list_entries(conn: sqlite3.Connection, key: str | None = None) -> list[tuple[str, str, NanobarTypeEntry]]:
    """All dynamic entries, optionally filtered to one `key` -- the auditability this module
    exists for: every runtime-registered `(key, key_name)` pair, in one portable, inspectable
    SQLite file, not silently accumulated inside an in-memory dict that vanishes on restart."""
    if key is None:
        rows = conn.execute(
            "SELECT key, key_name, expected_scenarios_json FROM nanobar_type_keys ORDER BY key, key_name"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT key, key_name, expected_scenarios_json FROM nanobar_type_keys WHERE key = ? ORDER BY key_name",
            (key,),
        ).fetchall()
    return [
        (
            row["key"],
            row["key_name"],
            NanobarTypeEntry(expected_scenarios=_scenarios_from_json(json.loads(row["expected_scenarios_json"]))),
        )
        for row in rows
    ]
