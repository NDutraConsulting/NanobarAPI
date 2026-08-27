from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Event:
    event_id: str
    channel: str
    recorded_at_ns: int
    monotonic_ns: int
    payload: dict[str, Any]
    trace_id: str | None = None
    span_id: str | None = None


@dataclass(frozen=True)
class TraceSummary:
    trace_id: str
    span_count: int
    first_recorded_at_ns: int
    last_recorded_at_ns: int
    any_error: bool


@dataclass(frozen=True)
class WorkerRecord:
    """A registered worker's own most-recently-seen liveness + configuration snapshot -- what
    `store.list_workers()` returns, for "reviewing configurations and monitoring lifecycles"
    (an app/dashboard consumer, e.g. `admin/nanobar/api.py`'s workers routes). `mode`/
    `schedule`/`poll_interval_s`/`claim_limit`/`lease_seconds` are `None` for a worker
    registered before these columns existed (or a caller that never supplied them) -- not
    every registration is guaranteed to carry full configuration."""

    worker_id: str
    channels: list[str]
    started_at: str
    last_heartbeat_at: str
    mode: str | None = None
    schedule: str | None = None
    poll_interval_s: float | None = None
    claim_limit: int | None = None
    lease_seconds: float | None = None
