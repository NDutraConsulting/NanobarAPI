"""Demo/seed script: drives real traffic through focusari_kahnban and captures it as
regression bricks + nanobars through nanobar_api's own (already-built) pipeline.

Pipeline exercised, end to end:

    focusari_kahnban app (real, separate app)
        -> wrapped in SnapshotMiddleware (nanobar_api.middleware.snapshot)
        -> EventQueueRepository "snapshot" channel
        -> eventbus_lifespan's background EventThread
        -> demo/data/events.db
        -> generate_bricks()
        -> demo/data/regression_bricks.db (RegressionBricks + Nanobars + bindings)

focusari_kahnban is a real, already-tested app built on focusari_asgi (not this
project's Starlette base) — used purely as a realistic source of varied HTTP traffic.
ASGI middleware composition doesn't care which framework produced the wrapped app, so
SnapshotMiddleware captures it exactly like it would capture a NanobarAPI app.

Idempotency across repeated runs, honestly stated:

  * Nanobars are keyed by a stable (method, path-template) identity that does *not*
    depend on any particular board/list/card id (see `_stable_path_template` below), so
    running this script many times will always resolve to the same small set of
    Nanobars — `_get_or_create_nanobar` reuses an existing one instead of duplicating it.
  * `generate_bricks()` dedupes by content-hash of the *exact* captured request+response,
    and it also permanently marks every event it looks at as processed (in events.db), so
    a given captured event is never turned into a brick twice, and calling
    `generate_bricks()` twice over the same already-processed events is a safe no-op.
  * That said: this script creates fresh boards/lists/cards with new random ids on every
    run, so the request paths and response bodies genuinely differ run to run. Content
    hashes will therefore typically differ too, and repeated runs will typically add
    *new* bricks rather than being silently skipped — the safety guarantee is "never a
    duplicate of identical content", not "brick count stays constant". What *does* stay
    constant across runs is the Nanobar set and its bindings continuing to grow to cover
    the new bricks, never duplicating a Nanobar.

Run from the nanobar_api repo root with:

    uv run python demo/seed_kahnban_bricks.py
"""

from __future__ import annotations

import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

import anyio
from focusari_kahnban.applications import create_app  # type: ignore[import-untyped]
from focusari_kahnban.db import configure as configure_kahnban_db  # type: ignore[import-untyped]
from starlette.testclient import TestClient

from nanobar_api.bricks import MonitorTargetRef, Nanobar, NanobarBrickBinding, RegressionBrick, generate_bricks
from nanobar_api.bricks.store import bind_brick_to_nanobar, connect as bricks_connect, insert_nanobar, list_nanobars
from nanobar_api.eventbus import ChannelConfig, EventQueueRepository, eventbus_lifespan
from nanobar_api.eventbus.store import connect as events_connect
from nanobar_api.middleware.snapshot import SnapshotMiddleware
from nanobar_api.middleware.trace import EventBusTraceMiddleware, configure_tracing

DATA_DIR = Path(__file__).resolve().parent / "data"
KAHNBAN_DB_PATH = DATA_DIR / "kahnban.db"
EVENTS_DB_PATH = DATA_DIR / "events.db"
BRICKS_DB_PATH = DATA_DIR / "regression_bricks.db"

#: focusari_kahnban's own pyproject.toml [project].version, kept in sync by hand since
#: there's no installed-package metadata lookup wired up here.
SYSTEM_NAME = "focusari_kahnban"
SYSTEM_VERSION = "0.1.0"
CREATED_BY = "seed_kahnban_bricks"

#: kahnban's primary keys are `uuid.uuid4().hex` (32 lowercase hex chars) everywhere
#: (see focusari_kahnban/models/*.py), so any path segment that looks like an opaque hex
#: id is a strong, unambiguous signal that it's a real id, not a static route segment.
#: `>=8` hex chars rather than requiring exactly 32 so the heuristic stays reasonable
#: even if some future id scheme is shorter.
_ID_SEGMENT_RE = re.compile(r"^[0-9a-f]{8,}$")


def _stable_path_template(path: str) -> str:
    """Derive a stable, id-free path template from a literal captured request path.

    e.g. "/api/cards/8f2e1c3d4a5b6c7d8e9f0a1b2c3d4e5f" -> "/api/cards/{card_id}".

    This is a heuristic, not a real route-template lookup: SnapshotMiddleware's captured
    payload only carries the literal `path` (see nanobar_api/middleware/snapshot.py's
    `request_dict`), not a matched route object or path_format the way
    EventBusTraceMiddleware's `http.route` span attribute does (nothing here consumes
    that middleware/channel). Absent that, each id-shaped segment is replaced with a
    `{<singularized preceding segment>_id}` placeholder — verified below to reproduce
    every real route in focusari_kahnban/routes/{boards,lists,cards}.py exactly.
    """
    segments = path.strip("/").split("/")
    templated: list[str] = []
    for index, segment in enumerate(segments):
        if _ID_SEGMENT_RE.match(segment):
            previous = segments[index - 1] if index > 0 else "resource"
            param_name = previous[:-1] if previous.endswith("s") else previous
            templated.append(f"{{{param_name}_id}}")
        else:
            templated.append(segment)
    return "/" + "/".join(templated)


def _stable_name_for_brick(brick: RegressionBrick) -> str:
    method = brick.request.get("method") or "?"
    path = brick.request.get("path") or ""
    return f"{method}:{_stable_path_template(path)}"


def _resource_for_path_template(path_template: str) -> str:
    """The last non-placeholder segment of a path template, e.g.
    "/api/boards/{board_id}/lists" -> "lists". Matches which `focusari_kahnban/routes/*.py`
    module actually owns the route — verified against every real route in
    focusari_kahnban/routes/{boards,lists,cards}.py, including the two nested-creation routes
    (`/api/boards/{board_id}/lists`, `/api/lists/{list_id}/cards`) where the *first* path
    segment names the parent resource, not the module that owns the route.
    """
    segments = [s for s in path_template.strip("/").split("/") if not s.startswith("{")]
    return segments[-1] if segments else "root"


def _label_for(method: str, path_template: str, resource: str) -> str:
    has_id_segment = any(s.startswith("{") for s in path_template.strip("/").split("/"))
    verb = {"GET": "Get" if has_id_segment else "List", "POST": "Create", "PATCH": "Update", "DELETE": "Delete"}.get(
        method, method
    )
    # "List boards" reads fine plural, but "Create board"/"Update card"/"Delete card" (singular,
    # since each acts on exactly one resource) read better than "Create boards" would.
    noun = resource[:-1] if verb != "List" and resource.endswith("s") else resource
    return f"{verb} {noun}"


def _find_nanobar_by_stable_name(conn: sqlite3.Connection, stable_name: str) -> Nanobar | None:
    for nanobar in list_nanobars(conn, target_type="openapi_operation"):
        if any(ref.stable_name == stable_name for ref in nanobar.monitor_target_refs):
            return nanobar
    return None


def _get_or_create_nanobar(conn: sqlite3.Connection, stable_name: str) -> tuple[Nanobar, bool]:
    """Returns (nanobar, was_newly_created)."""
    existing = _find_nanobar_by_stable_name(conn, stable_name)
    if existing is not None:
        return existing, False

    method, path_template = stable_name.split(":", 1)
    resource = _resource_for_path_template(path_template)

    nanobar = Nanobar(
        nanobar_id=f"nb-{uuid.uuid4().hex[:12]}",
        schema_version="1.0",
        system_name=SYSTEM_NAME,
        system_version=SYSTEM_VERSION,
        nanobar_type="api-response",  # all kahnban traffic here goes through the API
        request_object_id=f"req-{stable_name}",
        response_object_id=f"res-{stable_name}",
        regression_weight=0.5,  # neutral placeholder; this project doesn't yet specify a real computation
        endpoint_scenario_frequency={"state": "unmeasured"},  # honest: no real production frequency data exists
        created_by=CREATED_BY,
        monitor_target_refs=[MonitorTargetRef(target_type="openapi_operation", stable_name=stable_name)],
        label=_label_for(method, path_template, resource),
        scenario_description=f"Exercises {method} {path_template} through real focusari_kahnban traffic.",
        component_source_description=f"{SYSTEM_NAME}.routes.{resource}",
        domain=resource,  # e.g. "boards"/"lists"/"cards" -- the natural sub-domain within kahnban
    )
    insert_nanobar(conn, nanobar)
    return nanobar, True


def _process_bricks(bricks_conn: sqlite3.Connection, new_bricks: list[RegressionBrick]) -> tuple[int, int]:
    """Create/reuse one Nanobar per distinct (method, path-template) and bind every new
    brick to its Nanobar. Returns (nanobars_newly_created, bindings_created).
    """
    nanobars_created = 0
    bindings_created = 0
    resolved: dict[str, Nanobar] = {}

    for brick in new_bricks:
        stable_name = _stable_name_for_brick(brick)
        nanobar = resolved.get(stable_name)
        if nanobar is None:
            nanobar, was_created = _get_or_create_nanobar(bricks_conn, stable_name)
            resolved[stable_name] = nanobar
            nanobars_created += int(was_created)

        bind_brick_to_nanobar(
            bricks_conn,
            NanobarBrickBinding(
                nanobar_id=nanobar.nanobar_id,
                regression_brick_id=brick.regression_brick_id,
                match_method="exact",  # the brick was captured from literally this endpoint
                matcher_version="v1",
                matched_by=CREATED_BY,
                confidence=1.0,
            ),
        )
        bindings_created += 1

    return nanobars_created, bindings_created


def _drive_traffic(client: TestClient) -> int:
    """Drive real, meaningfully varied traffic through the wrapped kahnban app.

    Covers: 2 boards, 2-3 lists per board, 5 cards spread across lists, an in-list
    reorder (position race territory), a cross-board move attempt, a delete (gap-closing
    position logic), and a couple of read-only GETs. Returns the number of HTTP requests
    made, so the caller knows how many snapshot events to wait for.
    """
    request_count = 0

    def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        nonlocal request_count
        request_count += 1
        return client.post(path, json=body).json()["result"]["data"]  # type: ignore[no-any-return]

    def patch(path: str, body: dict[str, Any]) -> None:
        nonlocal request_count
        request_count += 1
        client.patch(path, json=body)

    def delete(path: str) -> None:
        nonlocal request_count
        request_count += 1
        client.delete(path)

    def get(path: str) -> None:
        nonlocal request_count
        request_count += 1
        client.get(path)

    # -- 2 boards --
    board_a = post("/api/boards", {"name": "Engineering Roadmap"})
    board_b = post("/api/boards", {"name": "Marketing Launch"})

    # -- 2-3 lists per board --
    list_a_todo = post(f"/api/boards/{board_a['id']}/lists", {"name": "To Do"})
    list_a_progress = post(f"/api/boards/{board_a['id']}/lists", {"name": "In Progress"})
    list_a_done = post(f"/api/boards/{board_a['id']}/lists", {"name": "Done"})
    list_b_backlog = post(f"/api/boards/{board_b['id']}/lists", {"name": "Backlog"})
    post(f"/api/boards/{board_b['id']}/lists", {"name": "Doing"})

    # -- 4-5 cards spread across lists --
    post(f"/api/lists/{list_a_todo['id']}/cards", {"title": "Design schema", "description": "ER diagram + DDL"})
    card_2 = post(f"/api/lists/{list_a_todo['id']}/cards", {"title": "Write integration tests"})
    post(f"/api/lists/{list_a_progress['id']}/cards", {"title": "Implement REST API"})
    card_4 = post(f"/api/lists/{list_a_done['id']}/cards", {"title": "Set up CI pipeline"})
    card_5 = post(f"/api/lists/{list_b_backlog['id']}/cards", {"title": "Draft launch roadmap"})

    # -- Reorder: move card_2 to the front of its own list (position-race territory). --
    patch(f"/api/cards/{card_2['id']}", {"position": 0})

    # -- Cross-board move attempt: card_5 lives on board_b; try to move it onto a list on
    # board_a. focusari_kahnban's card_service explicitly rejects this ("Cannot move a
    # card to a list on a different board" — see services/card_service.py), so this
    # captures a real validated-rejection response rather than a successful move. That's
    # still exactly the kind of business-logic edge case this demo means to exercise: a
    # brick pinning "this must stay rejected" is a legitimate regression brick. --
    patch(f"/api/cards/{card_5['id']}", {"list_id": list_a_todo["id"]})

    # -- Delete: exercises gap-closing position logic in the source list. --
    delete(f"/api/cards/{card_4['id']}")

    # -- Read-only boundary, for contrast with the mutating traffic above. --
    get("/api/boards")
    get(f"/api/boards/{board_a['id']}")

    return request_count


async def _wait_for_events_flushed(db_path: Path, channel: str, expected_count: int, timeout_s: float = 10.0) -> int:
    """Poll events.db until at least `expected_count` events for `channel` have been written
    by the background EventThread, or `timeout_s` elapses. Returns the count actually
    observed. Mirrors the "poll a fresh connection with a timeout" pattern used by
    tests/test_eventbus_integration.py and tests/test_thin_slice_proof.py — the thread
    flushes asynchronously (batched, on a timer), so a fixed sleep would be a guess.
    """
    deadline = time.monotonic() + timeout_s
    observed = 0
    while time.monotonic() < deadline:
        conn = sqlite3.connect(str(db_path))
        try:
            observed = conn.execute("SELECT COUNT(*) FROM events WHERE channel = ?", (channel,)).fetchone()[0]
        except sqlite3.OperationalError:
            observed = 0
        finally:
            conn.close()
        if observed >= expected_count:
            break
        await anyio.sleep(0.05)
    return observed


async def _capture_traffic() -> int:
    """Configures kahnban against the demo db, drives traffic through it wrapped in both
    SnapshotMiddleware (the "snapshot" channel, what generate_bricks consumes) and
    EventBusTraceMiddleware (the "trace" channel, real OTel spans — what the dashboard's
    trace timeline reads), and waits for every captured event on both channels to flush to
    events.db before returning. Returns the number of HTTP requests actually driven.

    EventBusTraceMiddleware wraps SnapshotMiddleware (trace outermost) so the trace span
    covers the whole request, including snapshot capture — the two middlewares use distinct
    reentrancy-guard scope keys ("nanobar.trace" vs "nanobar.snapshot") and don't interfere.

    Tracing is opt-in (never silently active): passing enabled=True forces it on for this
    script explicitly, the same as setting NANOBAR_TRACING_ENABLED=1 in the environment
    would — EventBusTraceMiddleware calls configure_tracing() itself on construction, so
    nothing else needs to be configured here for local capture into events.db to work; no
    external OTel SDK setup or backend is required for that (see configure_tracing's own
    docstring for why).
    """
    configure_tracing(enabled=True)

    configure_kahnban_db("sqlite:///" + str(KAHNBAN_DB_PATH))
    kahnban_app = create_app()

    repository = EventQueueRepository([ChannelConfig(name="snapshot"), ChannelConfig(name="trace")])
    snapshot_app = SnapshotMiddleware(kahnban_app, repository, channel="snapshot")
    wrapped_app = EventBusTraceMiddleware(snapshot_app, repository, channel="trace")

    async with eventbus_lifespan(repository, str(EVENTS_DB_PATH), channels=["snapshot", "trace"]):
        with TestClient(wrapped_app) as client:
            request_count = _drive_traffic(client)

        snapshot_flushed = await _wait_for_events_flushed(EVENTS_DB_PATH, "snapshot", request_count)
        trace_flushed = await _wait_for_events_flushed(EVENTS_DB_PATH, "trace", request_count)
        if snapshot_flushed < request_count or trace_flushed < request_count:
            print(
                f"warning: only {snapshot_flushed}/{request_count} snapshot events and "
                f"{trace_flushed}/{request_count} trace events flushed to events.db before the "
                "timeout; some traffic may be missing from this run's bricks/traces."
            )
    # eventbus_lifespan has now stopped and joined the background thread: events.db is
    # fully flushed and safe to read from a brand-new connection below.
    return request_count


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Reopening these files across runs is intentional and safe: events.db/regression_bricks.db's
    # own connect() functions create their schema idempotently (CREATE TABLE IF NOT EXISTS), and
    # kahnban's own init_db() (run in its lifespan) is likewise idempotent. See this module's
    # docstring for exactly what stays deduped across repeated runs and what doesn't.
    request_count = anyio.run(_capture_traffic)

    events_conn = events_connect(str(EVENTS_DB_PATH))
    bricks_conn = bricks_connect(str(BRICKS_DB_PATH))
    try:
        new_bricks = generate_bricks(events_conn, bricks_conn, channel="snapshot", created_by=CREATED_BY)
        nanobars_created, bindings_created = _process_bricks(bricks_conn, new_bricks)
        total_nanobars = len(list_nanobars(bricks_conn))
    finally:
        events_conn.close()
        bricks_conn.close()

    print("=== seed_kahnban_bricks summary ===")
    print(f"HTTP requests driven through kahnban : {request_count}")
    print(f"New regression bricks generated       : {len(new_bricks)}")
    print(f"New nanobars created this run         : {nanobars_created}")
    print(f"New brick<->nanobar bindings created   : {bindings_created}")
    print(f"Total nanobars in regression_bricks.db: {total_nanobars}")
    print()
    print("Database files (safe to re-run this script; see module docstring for what dedupes):")
    print(f"  kahnban.db          : {KAHNBAN_DB_PATH}")
    print(f"  events.db           : {EVENTS_DB_PATH}")
    print(f"  regression_bricks.db: {BRICKS_DB_PATH}")


if __name__ == "__main__":
    main()
