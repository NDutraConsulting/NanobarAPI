"""`NanobarAPIStateMachine` is a stub -- see its own module docstring for the design sketch this
is scaffolding for (D2, deferred). No real subclass exists anywhere in this codebase yet, so
this is the only thing exercising it: a trivial concrete subclass proving the base class itself
wires `telemetry` the same way every other framework base class does."""

from __future__ import annotations

from typing import Any

from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.framework.nanobar_api_state_machine import NanobarAPIStateMachine
from nanobar_api.telemetry import NanobarTelemetry


class _NoOpSeeder(NanobarAPIStateMachine):
    channel = "domain.example"

    def __init__(self, telemetry: NanobarTelemetry) -> None:
        super().__init__(telemetry)
        self.seeded: list[dict[str, Any]] = []
        self.torn_down: list[dict[str, Any]] = []

    def seed(self, brick_request: dict[str, Any]) -> None:
        self.seeded.append(brick_request)

    def teardown(self, brick_request: dict[str, Any]) -> None:
        self.torn_down.append(brick_request)


def _telemetry() -> NanobarTelemetry:
    return NanobarTelemetry(EventQueueRepository([ChannelConfig(name="trace")]), channel="trace")


def test_concrete_subclass_wires_telemetry_and_channel() -> None:
    seeder = _NoOpSeeder(_telemetry())

    assert seeder.channel == "domain.example"
    assert seeder.telemetry is not None


def test_seed_and_teardown_are_called_with_the_brick_request() -> None:
    seeder = _NoOpSeeder(_telemetry())
    brick_request = {"appointment_id": "appt-1"}

    seeder.seed(brick_request)
    seeder.teardown(brick_request)

    assert seeder.seeded == [brick_request]
    assert seeder.torn_down == [brick_request]
