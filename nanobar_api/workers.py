"""`WorkerConfig`/`NanobarWorker` — implements the worker claiming/processing lifecycle
`regression-brick-system-plan.md` §2 already specifies as pseudocode, on top of
`eventbus/store.py`'s atomic `claim_events`/`ack_event`/`fail_event`/`heartbeat`.
"""

from __future__ import annotations

import sqlite3
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar, Literal

from nanobar_api.eventbus.events import Event
from nanobar_api.eventbus.store import ack_event, claim_events, fail_event, heartbeat, register_worker
from nanobar_api.telemetry import NanobarProps, NanobarTelemetry
from nanobar_api.worker_utils import WorkerLogEntry, log_worker_failure


@dataclass(frozen=True)
class WorkerConfig:
    channels: tuple[str, ...]
    mode: Literal["cron", "listening"] = "listening"
    schedule: str | None = None  # required when mode="cron"
    poll_interval_s: float = 1.0


class NanobarWorker(ABC):
    config: ClassVar[WorkerConfig]

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
        self.worker_id = worker_id
        self.conn = conn
        self.telemetry = telemetry
        self.claim_limit = claim_limit
        self.lease_seconds = lease_seconds
        self.log_dir = log_dir

    @abstractmethod
    def process(self, event: Event) -> None:
        """Must be idempotent — processing the same event twice must produce the same end
        state, never a duplicate side effect. This is a contract on the implementation; nothing
        here verifies it mechanically."""

    def compensate(self, event: Event, exc: Exception) -> None:
        """Default: no-op. Optional saga-style rollback hook for workers whose `process()`
        can't be made naturally idempotent (e.g. "charge a card")."""

    def run_once(self) -> None:
        for channel in self.config.channels:
            for event in claim_events(self.conn, channel, self.worker_id, self.claim_limit, self.lease_seconds):
                self._process_one(event)

    def _process_one(self, event: Event) -> None:
        try:
            with self.telemetry.trace(
                f"worker.{self.worker_id}.process", nanobar=NanobarProps(type=f"worker-{event.channel}")
            ):
                self.process(event)
        except Exception as exc:
            self.compensate(event, exc)
            fail_event(self.conn, event.event_id, str(exc))
            log_worker_failure(
                self.conn,
                WorkerLogEntry(
                    worker_id=self.worker_id,
                    event_id=event.event_id,
                    error=str(exc),
                    logged_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
                ),
                log_dir=self.log_dir,
            )
            return
        ack_event(self.conn, event.event_id)

    def run_forever(self) -> None:
        """Only valid for `mode="listening"` — a `mode="cron"` worker's lifecycle is owned by
        an external scheduler, which calls `run_once()` directly, once per process invocation,
        instead of this loop."""
        if self.config.mode != "listening":
            raise ValueError(f"run_forever() is only valid for mode='listening', got {self.config.mode!r}")

        register_worker(self.conn, self.worker_id, self.config.channels)
        while True:
            self.run_once()
            heartbeat(self.conn, self.worker_id)
            time.sleep(self.config.poll_interval_s)
