from __future__ import annotations

import time

import pytest

from nanobar_api.eventbus.events import Event
from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository


def _make_event(channel: str, event_id: str = "evt-1") -> Event:
    return Event(
        event_id=event_id,
        channel=channel,
        recorded_at_ns=1,
        monotonic_ns=1,
        payload={"k": "v"},
    )


def _repo(*names: str, maxsize: int = 1000) -> EventQueueRepository:
    return EventQueueRepository([ChannelConfig(name=n) for n in names], maxsize=maxsize)


def test_put_then_get_any_happy_path() -> None:
    repo = _repo("orders")
    event = _make_event("orders")

    repo.put("orders", event)
    result = repo.get_any(["orders"], timeout=0.1)

    assert result == event


def test_put_unknown_channel_raises_key_error() -> None:
    repo = _repo("orders")

    with pytest.raises(KeyError):
        repo.put("unknown", _make_event("unknown"))


def test_get_any_unknown_channel_raises_key_error() -> None:
    repo = _repo("orders")

    with pytest.raises(KeyError):
        repo.get_any(["unknown"], timeout=0.1)


def test_get_any_unknown_channel_raises_before_polling() -> None:
    repo = _repo("orders")

    start = time.monotonic()
    with pytest.raises(KeyError):
        repo.get_any(["orders", "unknown"], timeout=5.0)
    elapsed = time.monotonic() - start

    assert elapsed < 1.0


def test_get_any_returns_none_after_timeout() -> None:
    repo = _repo("orders")

    start = time.monotonic()
    result = repo.get_any(["orders"], timeout=0.05)
    elapsed = time.monotonic() - start

    assert result is None
    assert elapsed >= 0.05
    assert elapsed < 1.0


def test_full_queue_drops_event_and_increments_counter() -> None:
    repo = _repo("orders", maxsize=1)
    repo.put("orders", _make_event("orders", "first"))

    # Second put should be silently dropped, not raise, since the queue is full.
    repo.put("orders", _make_event("orders", "second"))

    assert repo.dropped_counts["orders"] == 1
    # The first event is still retrievable; the queue was never corrupted.
    result = repo.get_any(["orders"], timeout=0.1)
    assert result is not None
    assert result.event_id == "first"


def test_put_does_not_raise_or_block_when_full() -> None:
    repo = _repo("orders", maxsize=1)
    repo.put("orders", _make_event("orders", "first"))

    start = time.monotonic()
    repo.put("orders", _make_event("orders", "second"))
    elapsed = time.monotonic() - start

    assert elapsed < 0.5
    assert repo.dropped_counts["orders"] == 1


def test_get_any_checks_multiple_channels_in_order() -> None:
    repo = _repo("a", "b")
    event_b = _make_event("b")
    repo.put("b", event_b)

    result = repo.get_any(["a", "b"], timeout=0.1)

    assert result == event_b


def test_get_any_returns_from_whichever_channel_has_data() -> None:
    repo = _repo("a", "b")
    event_a = _make_event("a")
    repo.put("a", event_a)

    result = repo.get_any(["b", "a"], timeout=0.1)

    assert result == event_a


def test_dropped_counts_is_read_only_view() -> None:
    repo = _repo("orders", maxsize=1)
    repo.put("orders", _make_event("orders", "first"))
    repo.put("orders", _make_event("orders", "second"))  # dropped

    counts = repo.dropped_counts
    with pytest.raises(TypeError):
        counts["orders"] = 999  # type: ignore[index]

    # Mutating the returned mapping (if it were possible) must not affect internal state.
    assert repo.dropped_counts["orders"] == 1


def test_dropped_counts_snapshot_not_live() -> None:
    repo = _repo("orders", maxsize=1)
    repo.put("orders", _make_event("orders", "first"))

    counts_before = repo.dropped_counts
    repo.put("orders", _make_event("orders", "second"))  # dropped after the snapshot was taken

    assert counts_before["orders"] == 0
    assert repo.dropped_counts["orders"] == 1


def test_channel_names_preserves_order_and_is_not_internal_dict() -> None:
    repo = _repo("first", "second", "third")

    assert repo.channel_names == ("first", "second", "third")


def test_dropped_counts_includes_zero_for_untouched_channels() -> None:
    repo = _repo("orders", "shipments")

    assert repo.dropped_counts == {"orders": 0, "shipments": 0}


def test_channel_config_defaults() -> None:
    config = ChannelConfig(name="orders")

    assert config.thread == "shared"
    assert config.priority == 0
