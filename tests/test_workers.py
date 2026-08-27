from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Literal

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider

from nanobar_api.dynamic_taxonomy import connect as connect_dynamic_taxonomy, get_entry, get_or_create_entry
from nanobar_api.eventbus.events import Event
from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.eventbus.store import connect, get_unprocessed, insert_events
from nanobar_api.taxonomy import ExpectedScenario, NanobarTypeEntry
from nanobar_api.telemetry import NanobarTelemetry
from nanobar_api.worker_utils import get_worker_log
from nanobar_api.workers import NanobarWorker, WorkerConfig

# A real SDK TracerProvider so spans carry real, non-NoOp trace/span ids -- matches
# test_telemetry.py's own bootstrap, the one place per test process this is set.
if isinstance(otel_trace.get_tracer_provider(), otel_trace.NoOpTracerProvider | otel_trace.ProxyTracerProvider):
    otel_trace.set_tracer_provider(TracerProvider())


class _StopLoop(Exception):
    pass


class _RecordingWorker(NanobarWorker):
    def __init__(
        self,
        worker_id: str,
        conn: sqlite3.Connection,
        telemetry: NanobarTelemetry,
        *,
        claim_limit: int = 10,
        lease_seconds: float = 30.0,
        log_dir: str = "logs",
        dynamic_taxonomy_conn: sqlite3.Connection | None = None,
    ) -> None:
        super().__init__(
            worker_id,
            conn,
            telemetry,
            claim_limit=claim_limit,
            lease_seconds=lease_seconds,
            log_dir=log_dir,
            dynamic_taxonomy_conn=dynamic_taxonomy_conn,
        )
        self.processed: list[str] = []
        self.compensated: list[str] = []

    def process(self, event: Event) -> None:
        if event.payload.get("fail"):
            raise RuntimeError(f"processing failed for {event.event_id}")
        self.processed.append(event.event_id)

    def compensate(self, event: Event, exc: Exception) -> None:
        self.compensated.append(event.event_id)


def _telemetry() -> NanobarTelemetry:
    return NanobarTelemetry(EventQueueRepository([ChannelConfig(name="trace")]))


def _make_worker(
    tmp_path: Path,
    channels: tuple[str, ...] = ("work",),
    mode: Literal["cron", "listening"] = "listening",
    *,
    expected_scenarios: dict[str, ExpectedScenario] | None = None,
    dynamic_taxonomy_conn: sqlite3.Connection | None = None,
) -> _RecordingWorker:
    conn = connect(str(tmp_path / "events.db"))

    class _Worker(_RecordingWorker):
        pass

    _Worker.config = WorkerConfig(channels=channels, mode=mode, expected_scenarios=expected_scenarios)
    return _Worker(
        "worker-1",
        conn,
        _telemetry(),
        claim_limit=10,
        lease_seconds=30.0,
        log_dir=str(tmp_path / "logs"),
        dynamic_taxonomy_conn=dynamic_taxonomy_conn,
    )


def _insert(
    conn: sqlite3.Connection, event_id: str, channel: str = "work", payload: dict[str, object] | None = None
) -> None:
    insert_events(
        conn,
        [Event(event_id=event_id, channel=channel, recorded_at_ns=1, monotonic_ns=1, payload=payload or {})],
    )


def test_run_once_claims_processes_and_acks(tmp_path: Path) -> None:
    worker = _make_worker(tmp_path)
    _insert(worker.conn, "evt-1")

    worker.run_once()

    assert worker.processed == ["evt-1"]
    assert get_unprocessed(worker.conn, "work") == []


def test_run_once_registers_and_heartbeats_even_in_cron_mode(tmp_path: Path) -> None:
    # mode="cron" workers never call run_forever() -- their lifecycle is owned by an external
    # scheduler calling run_once() directly. Registration/heartbeat must still happen, or a
    # cron-mode worker would never show up in store.list_workers()'s lifecycle view at all.
    worker = _make_worker(tmp_path, channels=("work",), mode="cron")
    _insert(worker.conn, "evt-1")

    worker.run_once()

    row = worker.conn.execute(
        "SELECT worker_id, mode, poll_interval_s FROM workers WHERE worker_id = ?", ("worker-1",)
    ).fetchone()
    assert row is not None
    assert row[0] == "worker-1"
    assert row[1] == "cron"


def test_run_once_processes_multiple_channels(tmp_path: Path) -> None:
    worker = _make_worker(tmp_path, channels=("a", "b"))
    _insert(worker.conn, "evt-a", channel="a")
    _insert(worker.conn, "evt-b", channel="b")

    worker.run_once()

    assert sorted(worker.processed) == ["evt-a", "evt-b"]


def test_run_once_on_failure_compensates_fails_event_and_logs(tmp_path: Path) -> None:
    worker = _make_worker(tmp_path)
    _insert(worker.conn, "evt-bad", payload={"fail": True})

    worker.run_once()

    assert worker.processed == []
    assert worker.compensated == ["evt-bad"]

    row = worker.conn.execute(
        "SELECT attempt_count, last_error, claimed_by FROM events WHERE event_id = ?", ("evt-bad",)
    ).fetchone()
    assert row[0] == 1
    assert "evt-bad" in row[1]
    assert row[2] is None

    logged = get_worker_log(worker.conn, "worker-1")
    assert len(logged) == 1
    assert logged[0].event_id == "evt-bad"


def test_run_once_leaves_failed_event_reclaimable(tmp_path: Path) -> None:
    worker = _make_worker(tmp_path)
    _insert(worker.conn, "evt-bad", payload={"fail": True})
    worker.run_once()

    # A failed event's claim is released early -- a second run_once() picks it straight back up.
    worker.run_once()

    assert worker.compensated == ["evt-bad", "evt-bad"]


def test_run_forever_rejects_cron_mode(tmp_path: Path) -> None:
    worker = _make_worker(tmp_path, mode="cron")

    with pytest.raises(ValueError, match="listening"):
        worker.run_forever()


def test_run_forever_registers_worker_processes_and_heartbeats(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    worker = _make_worker(tmp_path)
    _insert(worker.conn, "evt-1")

    sleep_calls = {"n": 0}

    def fake_sleep(seconds: float) -> None:
        sleep_calls["n"] += 1
        if sleep_calls["n"] >= 2:
            raise _StopLoop

    monkeypatch.setattr("nanobar_api.workers.time.sleep", fake_sleep)

    with pytest.raises(_StopLoop):
        worker.run_forever()

    assert worker.processed == ["evt-1"]
    row = worker.conn.execute(
        "SELECT worker_id, channels, last_heartbeat_at FROM workers WHERE worker_id = ?", ("worker-1",)
    ).fetchone()
    assert row is not None
    assert row[0] == "worker-1"
    assert sleep_calls["n"] == 2


# ------------------------------------------------------ dynamic taxonomy self-registration ---


_EXPECTED_SCENARIOS = {
    "success": ExpectedScenario(weight=1.0, required=True, synthesizable=False),
    "conflict": ExpectedScenario(weight=0.5, required=True, synthesizable=True),
}


def test_process_one_registers_its_own_dynamic_taxonomy_entry_from_config(tmp_path: Path) -> None:
    dynamic_conn = connect_dynamic_taxonomy(str(tmp_path / "nanobar_type_system.db"))
    worker = _make_worker(
        tmp_path, channels=("work",), expected_scenarios=_EXPECTED_SCENARIOS, dynamic_taxonomy_conn=dynamic_conn
    )
    _insert(worker.conn, "evt-1", channel="work")

    worker.run_once()

    entry = get_entry(dynamic_conn, "worker", "work")
    assert entry == NanobarTypeEntry(expected_scenarios=_EXPECTED_SCENARIOS)


def test_process_one_does_not_touch_dynamic_taxonomy_when_expected_scenarios_is_unset(tmp_path: Path) -> None:
    dynamic_conn = connect_dynamic_taxonomy(str(tmp_path / "nanobar_type_system.db"))
    # No expected_scenarios given -- this worker doesn't opt in, same behavior as before this
    # feature existed even though a dynamic_taxonomy_conn is available.
    worker = _make_worker(tmp_path, channels=("work",), dynamic_taxonomy_conn=dynamic_conn)
    _insert(worker.conn, "evt-1", channel="work")

    worker.run_once()

    assert get_entry(dynamic_conn, "worker", "work") is None


def test_process_one_does_not_touch_dynamic_taxonomy_when_no_connection_given(tmp_path: Path) -> None:
    # expected_scenarios declared, but no dynamic_taxonomy_conn wired -- must not crash, and
    # must not silently create a database nobody asked for.
    worker = _make_worker(tmp_path, channels=("work",), expected_scenarios=_EXPECTED_SCENARIOS)
    _insert(worker.conn, "evt-1", channel="work")

    worker.run_once()  # must not raise

    assert not (tmp_path / "nanobar_type_system.db").exists()


def test_process_one_does_not_overwrite_an_already_registered_entry(tmp_path: Path) -> None:
    dynamic_conn = connect_dynamic_taxonomy(str(tmp_path / "nanobar_type_system.db"))
    pre_existing = NanobarTypeEntry(
        expected_scenarios={"success": ExpectedScenario(weight=1.0, required=True, synthesizable=False)}
    )
    get_or_create_entry(dynamic_conn, "worker", "work", default_entry=pre_existing, created_by="someone-else")

    worker = _make_worker(
        tmp_path, channels=("work",), expected_scenarios=_EXPECTED_SCENARIOS, dynamic_taxonomy_conn=dynamic_conn
    )
    _insert(worker.conn, "evt-1", channel="work")

    worker.run_once()

    # The worker's own (different) expected_scenarios did not clobber what was already there.
    assert get_entry(dynamic_conn, "worker", "work") == pre_existing
