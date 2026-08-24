import anyio
import pytest

from nanobar_api import run_until_satisfied


@pytest.mark.anyio
async def test_stops_at_first_success() -> None:
    events: list[int] = []

    async def fetch(item: int) -> int:
        if item != 2:
            await anyio.sleep(0.05)
        return item

    await run_until_satisfied(
        items=[1, 2, 3],
        fetch=fetch,
        on_event=events.append,
        is_satisfied=lambda: 2 in events,
    )

    assert 2 in events


@pytest.mark.anyio
async def test_runs_all_when_never_satisfied() -> None:
    events: list[int] = []

    async def fetch(item: int) -> int:
        return item

    await run_until_satisfied(
        items=[1, 2, 3],
        fetch=fetch,
        on_event=events.append,
        is_satisfied=lambda: False,
    )

    assert sorted(events) == [1, 2, 3]


@pytest.mark.anyio
async def test_empty_items_completes() -> None:
    events: list[int] = []

    async def fetch(item: int) -> int:
        return item

    await run_until_satisfied(
        items=[],
        fetch=fetch,
        on_event=events.append,
        is_satisfied=lambda: False,
    )

    assert events == []
