from __future__ import annotations

import json
from pathlib import Path

from nanobar_api.eventbus.store import connect
from nanobar_api.worker_utils import WorkerLogEntry, get_worker_log, log_worker_failure


def test_log_worker_failure_writes_db_row(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "events.db"))
    entry = WorkerLogEntry(worker_id="w-1", event_id="evt-1", error="boom", logged_at="2026-08-25 12:00:00")

    log_worker_failure(conn, entry, log_dir=str(tmp_path / "logs"))

    logged = get_worker_log(conn, "w-1")
    assert logged == [entry]


def test_log_worker_failure_writes_append_only_file(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "events.db"))
    log_dir = tmp_path / "logs"
    entry_a = WorkerLogEntry(worker_id="w-1", event_id="evt-1", error="boom", logged_at="2026-08-25 12:00:00")
    entry_b = WorkerLogEntry(worker_id="w-1", event_id="evt-2", error="also boom", logged_at="2026-08-25 12:05:00")

    log_worker_failure(conn, entry_a, log_dir=str(log_dir))
    log_worker_failure(conn, entry_b, log_dir=str(log_dir))

    log_file = log_dir / "2026-08-25-worker-failures.log"
    lines = log_file.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event_id"] == "evt-1"
    assert json.loads(lines[1])["event_id"] == "evt-2"


def test_log_worker_failure_uses_date_from_logged_at_for_separate_files(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "events.db"))
    log_dir = tmp_path / "logs"

    log_worker_failure(
        conn,
        WorkerLogEntry(worker_id="w-1", event_id=None, error="day one", logged_at="2026-08-25 09:00:00"),
        log_dir=str(log_dir),
    )
    log_worker_failure(
        conn,
        WorkerLogEntry(worker_id="w-1", event_id=None, error="day two", logged_at="2026-08-26 09:00:00"),
        log_dir=str(log_dir),
    )

    assert (log_dir / "2026-08-25-worker-failures.log").exists()
    assert (log_dir / "2026-08-26-worker-failures.log").exists()


def test_get_worker_log_filters_by_worker_id(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "events.db"))
    log_worker_failure(
        conn,
        WorkerLogEntry(worker_id="w-1", event_id=None, error="a", logged_at="2026-08-25 09:00:00"),
        log_dir=str(tmp_path / "logs"),
    )
    log_worker_failure(
        conn,
        WorkerLogEntry(worker_id="w-2", event_id=None, error="b", logged_at="2026-08-25 09:00:01"),
        log_dir=str(tmp_path / "logs"),
    )

    assert [e.worker_id for e in get_worker_log(conn, "w-1")] == ["w-1"]
    assert {e.worker_id for e in get_worker_log(conn)} == {"w-1", "w-2"}


def test_get_worker_log_respects_limit(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "events.db"))
    for i in range(5):
        log_worker_failure(
            conn,
            WorkerLogEntry(worker_id="w-1", event_id=None, error=f"e{i}", logged_at=f"2026-08-25 09:00:0{i}"),
            log_dir=str(tmp_path / "logs"),
        )

    assert len(get_worker_log(conn, "w-1", limit=2)) == 2
