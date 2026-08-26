from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Literal

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider

from nanobar_api.eventbus.events import Event
from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.eventbus.store import connect, get_unprocessed, insert_events
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
    ) -> None:
        super().__init__(
            worker_id, conn, telemetry, claim_limit=claim_limit, lease_seconds=lease_seconds, log_dir=log_dir
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
    tmp_path: Path, channels: tuple[str, ...] = ("work",), mode: Literal["cron", "listening"] = "listening"
) -> _RecordingWorker:
    conn = connect(str(tmp_path / "events.db"))

    class _Worker(_RecordingWorker):
        pass

    _Worker.config = WorkerConfig(channels=channels, mode=mode)
    return _Worker("worker-1", conn, _telemetry(), claim_limit=10, lease_seconds=30.0, log_dir=str(tmp_path / "logs"))


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
