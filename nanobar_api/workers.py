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

from nanobar_api.dynamic_taxonomy import get_or_create_entry
from nanobar_api.eventbus.events import Event
from nanobar_api.eventbus.store import ack_event, claim_events, fail_event, heartbeat, register_worker
from nanobar_api.taxonomy import ExpectedScenario, NanobarTypeEntry
from nanobar_api.telemetry import NanobarProps, NanobarTelemetry
from nanobar_api.worker_utils import WorkerLogEntry, log_worker_failure


@dataclass(frozen=True)
class WorkerConfig:
    channels: tuple[str, ...]
    mode: Literal["cron", "listening"] = "listening"
    schedule: str | None = None  # required when mode="cron"
    poll_interval_s: float = 1.0
    #: This worker's own expected-scenario coverage rules for the `f"worker-{channel}"`
    #: `nanobar_type` its spans get tagged with (see `_process_one()`) -- declared here, on the
    #: class, so a dynamic per-channel taxonomy entry is *generated from this worker's own code*
    #: the first time it actually processes an event, not something a human later configures
    #: through a UI. `None` (the default) means this worker doesn't opt in to that -- its
    #: channel falls back to whatever generic default the caller resolving its taxonomy uses,
    #: same as before this field existed.
    expected_scenarios: dict[str, ExpectedScenario] | None = None


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
        dynamic_taxonomy_conn: sqlite3.Connection | None = None,
    ) -> None:
        self.worker_id = worker_id
        self.conn = conn
        self.telemetry = telemetry
        self.claim_limit = claim_limit
        self.lease_seconds = lease_seconds
        self.log_dir = log_dir
        #: Connection to the dynamic nanobar-type-system database (`dynamic_taxonomy.py`) --
        #: optional, `None` by default so existing callers/tests are unaffected. When given
        #: *and* `self.config.expected_scenarios` is declared, `_process_one()` registers this
        #: worker's own per-channel taxonomy entry the first time it processes an event on a
        #: given channel, generated from the worker's own code rather than configured later.
        self.dynamic_taxonomy_conn = dynamic_taxonomy_conn

    @abstractmethod
    def process(self, event: Event) -> None:
        """Must be idempotent — processing the same event twice must produce the same end
        state, never a duplicate side effect. This is a contract on the implementation; nothing
        here verifies it mechanically."""

    def compensate(self, event: Event, exc: Exception) -> None:
        """Default: no-op. Optional saga-style rollback hook for workers whose `process()`
        can't be made naturally idempotent (e.g. "charge a card")."""

    def run_once(self) -> None:
        """Registers this worker's own liveness + configuration snapshot (`register_worker()`)
        and refreshes its heartbeat on every call -- not just from `run_forever()`'s loop.
        `mode="cron"` workers never call `run_forever()` at all (their lifecycle is owned by an
        external scheduler calling this directly), so registering only inside `run_forever()`
        would leave every cron-mode worker invisible to `store.list_workers()`'s "review
        configurations and monitor lifecycles" view -- registering here instead means any
        invocation of `run_once()`, by either mode, proves this worker is alive.
        """
        register_worker(
            self.conn,
            self.worker_id,
            self.config.channels,
            mode=self.config.mode,
            schedule=self.config.schedule,
            poll_interval_s=self.config.poll_interval_s,
            claim_limit=self.claim_limit,
            lease_seconds=self.lease_seconds,
        )
        for channel in self.config.channels:
            for event in claim_events(self.conn, channel, self.worker_id, self.claim_limit, self.lease_seconds):
                self._process_one(event)
        heartbeat(self.conn, self.worker_id)

    def _register_dynamic_taxonomy_entry(self, channel: str) -> None:
        """Registers this worker's own `("worker", channel)` taxonomy entry from
        `self.config.expected_scenarios` -- a no-op unless both a `dynamic_taxonomy_conn` and
        `expected_scenarios` were actually given, so a worker that doesn't opt in behaves
        exactly as it did before this existed. Called on every processed event rather than
        cached/throttled: `get_or_create_entry()` is already one indexed SELECT before any
        write, cheap enough for this project's local-beta SQLite scale (same "just call it"
        posture `run_forever()`'s own per-loop `heartbeat()` already takes)."""
        if self.dynamic_taxonomy_conn is None or self.config.expected_scenarios is None:
            return
        get_or_create_entry(
            self.dynamic_taxonomy_conn,
            "worker",
            channel,
            default_entry=NanobarTypeEntry(expected_scenarios=self.config.expected_scenarios),
            created_by=self.worker_id,
        )

    def _process_one(self, event: Event) -> None:
        self._register_dynamic_taxonomy_entry(event.channel)
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
        instead of this loop. Registration/heartbeat happen inside `run_once()` itself now (see
        its own docstring), so this loop is just the sleep timer."""
        if self.config.mode != "listening":
            raise ValueError(f"run_forever() is only valid for mode='listening', got {self.config.mode!r}")

        while True:
            self.run_once()
            time.sleep(self.config.poll_interval_s)
