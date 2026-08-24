from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable

import anyio


async def run_until_satisfied[T, R](
    items: Iterable[T],
    fetch: Callable[[T], Awaitable[R]],
    on_event: Callable[[R], None],
    is_satisfied: Callable[[], bool],
) -> None:
    async with anyio.create_task_group() as task_group:

        async def run_one(item: T) -> None:
            result = await fetch(item)
            on_event(result)
            if is_satisfied():
                task_group.cancel_scope.cancel()

        for item in items:
            task_group.start_soon(run_one, item)
