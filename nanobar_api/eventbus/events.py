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
