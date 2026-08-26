from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from nanobar_api.supervisor import NanobarSupervisor, SupervisorConfig


class _StopLoop(Exception):
    pass


def _short_lived_command() -> list[str]:
    return [sys.executable, "-c", "pass"]


def _long_lived_command() -> list[str]:
    return [sys.executable, "-c", "import time; time.sleep(60)"]


@pytest.fixture
def kill_all() -> Iterator[list[subprocess.Popen[bytes]]]:
    processes: list[subprocess.Popen[bytes]] = []
    yield processes
    for process in processes:
        process.kill()


def test_start_spawns_every_configured_worker(kill_all: list[subprocess.Popen[bytes]]) -> None:
    supervisor = NanobarSupervisor(SupervisorConfig(), {"w1": _long_lived_command()})

    supervisor.start()

    kill_all.append(supervisor._processes["w1"])
    assert supervisor._processes["w1"].poll() is None


def test_check_once_leaves_a_healthy_worker_alone_and_resets_failure_count(
    kill_all: list[subprocess.Popen[bytes]],
) -> None:
    supervisor = NanobarSupervisor(SupervisorConfig(), {"w1": _long_lived_command()})
    supervisor.start()
    kill_all.append(supervisor._processes["w1"])
    original_pid = supervisor._processes["w1"].pid
    supervisor._consecutive_failures["w1"] = 2

    supervisor.check_once()

    assert supervisor._processes["w1"].pid == original_pid
    assert supervisor._consecutive_failures["w1"] == 0


def test_check_once_restarts_a_dead_worker(kill_all: list[subprocess.Popen[bytes]]) -> None:
    supervisor = NanobarSupervisor(SupervisorConfig(max_consecutive_failures=99), {"w1": _short_lived_command()})
    supervisor.start()
    time.sleep(0.3)  # let the short-lived process actually exit
    assert supervisor._processes["w1"].poll() is not None
    dead_pid = supervisor._processes["w1"].pid

    supervisor.check_once()

    kill_all.append(supervisor._processes["w1"])
    assert supervisor._processes["w1"].pid != dead_pid  # a genuinely new process was spawned
    assert supervisor._consecutive_failures["w1"] == 1


def test_escalates_and_writes_log_after_max_consecutive_failures(
    tmp_path: Path, kill_all: list[subprocess.Popen[bytes]]
) -> None:
    calls: list[tuple[str, str]] = []
    supervisor = NanobarSupervisor(
        SupervisorConfig(max_consecutive_failures=2, log_dir=str(tmp_path / "logs")),
        {"w1": _short_lived_command()},
        notify=lambda worker_id, message: calls.append((worker_id, message)),
    )
    supervisor.start()

    time.sleep(0.3)
    supervisor.check_once()  # failure 1 -- no escalation yet
    assert calls == []

    time.sleep(0.3)
    supervisor.check_once()  # failure 2 -- escalates

    kill_all.append(supervisor._processes["w1"])
    assert len(calls) == 1
    assert calls[0][0] == "w1"
    assert supervisor._consecutive_failures["w1"] == 0  # reset after escalating, not left at max

    log_files = list((tmp_path / "logs").glob("*-worker-failures.log"))
    assert len(log_files) == 1
    assert "w1" in log_files[0].read_text()


def test_default_notify_logs_via_stdlib_logging(
    tmp_path: Path, kill_all: list[subprocess.Popen[bytes]], caplog: pytest.LogCaptureFixture
) -> None:
    supervisor = NanobarSupervisor(
        SupervisorConfig(max_consecutive_failures=1, log_dir=str(tmp_path / "logs")), {"w1": _short_lived_command()}
    )
    supervisor.start()
    time.sleep(0.3)

    with caplog.at_level("ERROR"):
        supervisor.check_once()

    kill_all.append(supervisor._processes["w1"])
    assert "w1" in caplog.text
    assert "escalated" in caplog.text


def test_run_forever_starts_then_loops_check_once(
    monkeypatch: pytest.MonkeyPatch, kill_all: list[subprocess.Popen[bytes]]
) -> None:
    supervisor = NanobarSupervisor(SupervisorConfig(), {"w1": _long_lived_command()})

    sleep_calls = {"n": 0}

    def fake_sleep(seconds: float) -> None:
        sleep_calls["n"] += 1
        if sleep_calls["n"] >= 2:
            raise _StopLoop

    monkeypatch.setattr("nanobar_api.supervisor.time.sleep", fake_sleep)

    with pytest.raises(_StopLoop):
        supervisor.run_forever()

    kill_all.append(supervisor._processes["w1"])
    assert supervisor._processes["w1"].poll() is None
    assert sleep_calls["n"] == 2
