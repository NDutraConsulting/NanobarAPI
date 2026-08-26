"""`NanobarSupervisor`/`SupervisorConfig` — Worker-Domain plan Phase C. A simple check/restart/
escalate loop over real `subprocess.Popen` handles, resolving
`regression-brick-system-plan.md` §9's remaining `local`/`local-multicore` worker-supervision
gap. Verbatim pseudocode from that doc's §2:

```
loop:
    sleep(check_interval_s)
    if worker healthy: consecutive_failures = 0; continue
    spawn a fresh process for this worker_id           # the actual restart
    consecutive_failures += 1
    if consecutive_failures >= max_consecutive_failures:
        append {date}-worker-failures.log with worker_id/channels/failure count/last heartbeat
        notify — synchronously, directly (SMTP/webhook), NOT through the eventbus's own
          notifications channel
        consecutive_failures = 0
```

**Deliberate deviation from the Worker-Domain buildplan doc's own signature, documented not
silent:** that doc writes `NanobarSupervisor(config, workers: Mapping[str, WorkerConfig])` — but
`WorkerConfig` (`channels`/`mode`/`schedule`/`poll_interval_s`) carries no executable command,
and "spawn a fresh process for this worker_id" needs one. `workers` here instead maps
`worker_id -> subprocess.Popen` argv (e.g. `[sys.executable, "-m", "myapp.workers.email_worker"]`)
— what the supervisor actually needs to do its one real job.

**Scoped exactly as honestly as the source doc scopes it — `local`/`local-multicore` only.**
The supervisor and its workers share a machine, so it can hold each `subprocess.Popen` handle
directly and poll it (`.poll()` — a precise "check the environment," not heartbeat-staleness
inference) and spawn a replacement locally. A truly `distributed` worker on another machine
can't be restarted this way; that remains open (`regression-brick-system-plan.md` §9), not
re-solved here.
"""

from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SupervisorConfig:
    check_interval_s: float = 10.0
    max_consecutive_failures: int = 3  # silent restarts allowed before escalating
    log_dir: str = "logs"  # escalation writes {date}-worker-failures.log here


def _log_only_notify(worker_id: str, message: str) -> None:
    """Default `notify` — logs via stdlib `logging`, matching every other "default: log,
    override for real delivery" hook already established in this codebase
    (`NanobarCallback.on_failure`). A real deployment passes its own SMTP/webhook callable."""
    logger.error("worker %s escalated: %s", worker_id, message)


class NanobarSupervisor:
    def __init__(
        self,
        config: SupervisorConfig,
        workers: Mapping[str, Sequence[str]],
        *,
        notify: Callable[[str, str], None] | None = None,
    ) -> None:
        self.config = config
        self._commands = dict(workers)
        self._notify = notify if notify is not None else _log_only_notify
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._consecutive_failures: dict[str, int] = dict.fromkeys(workers, 0)

    def start(self) -> None:
        """Spawns every configured worker once, up front — `run_forever()`'s own restart loop
        only reacts to a death after the fact, so an initial spawn is needed before there's
        anything to check."""
        for worker_id, command in self._commands.items():
            self._processes[worker_id] = subprocess.Popen(command)

    def check_once(self) -> None:
        """One pass over every supervised worker — resets the healthy ones' failure counters,
        restarts the rest. Split out from `run_forever()` so a test (or a real one-shot health
        check) can drive exactly one pass without an infinite loop."""
        for worker_id, process in self._processes.items():
            if process.poll() is None:
                self._consecutive_failures[worker_id] = 0
                continue

            self._processes[worker_id] = subprocess.Popen(self._commands[worker_id])
            self._consecutive_failures[worker_id] += 1

            if self._consecutive_failures[worker_id] >= self.config.max_consecutive_failures:
                self._escalate(worker_id)
                self._consecutive_failures[worker_id] = 0  # avoid re-notifying every pass while still down

    def _escalate(self, worker_id: str) -> None:
        now = datetime.now(UTC)
        message = (
            f"{now.isoformat()} worker_id={worker_id} channels={self._commands[worker_id]!r} "
            f"consecutive_failures={self.config.max_consecutive_failures}"
        )
        log_path = Path(self.config.log_dir) / f"{now.strftime('%Y-%m-%d')}-worker-failures.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a") as f:
            f.write(message + "\n")
        self._notify(worker_id, message)

    def run_forever(self) -> None:
        self.start()
        while True:
            time.sleep(self.config.check_interval_s)
            self.check_once()
