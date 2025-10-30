"""Simple in-memory event bus for fan-out of market events."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Generic, TypeVar

T = TypeVar("T")


class EventBus(Generic[T]):
    """Fan-out publisher that feeds each event to all subscribers."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[T]] = set()
        self._lock = asyncio.Lock()

    async def publish(self, item: T) -> None:
        """Broadcast ``item`` to every active subscriber queue."""

        async with self._lock:
            queues: Iterable[asyncio.Queue[T]] = tuple(self._subscribers)

        if not queues:
            return

        await asyncio.gather(*(queue.put(item) for queue in queues))

    def subscribe(self, maxsize: int = 0) -> asyncio.Queue[T]:
        """Create and register a new subscriber queue."""

        queue: asyncio.Queue[T] = asyncio.Queue(maxsize=maxsize)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[T]) -> None:
        """Remove a subscriber queue."""

        self._subscribers.discard(queue)

    async def close(self) -> None:
        """Remove all subscribers and drain any pending events."""

        async with self._lock:
            queues = tuple(self._subscribers)
            self._subscribers.clear()

        for queue in queues:
            while not queue.empty():
                queue.get_nowait()
                queue.task_done()


__all__ = ["EventBus"]

