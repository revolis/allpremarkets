"""Unit tests for the in-memory event bus."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path


def _load_event_bus() -> type:
    root = Path(__file__).resolve().parents[1]
    module_path = root / "src" / "common" / "bus.py"
    spec = importlib.util.spec_from_file_location("common.bus", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[arg-type]
    return module.EventBus


EventBus = _load_event_bus()


def test_publish_fans_out_to_all_subscribers() -> None:
    async def _run() -> None:
        bus: EventBus[int] = EventBus()
        queue_a = bus.subscribe()
        queue_b = bus.subscribe()

        await bus.publish(1)

        assert await asyncio.wait_for(queue_a.get(), timeout=0.1) == 1
        assert await asyncio.wait_for(queue_b.get(), timeout=0.1) == 1

    asyncio.run(_run())


def test_unsubscribe_prevents_future_messages() -> None:
    async def _run() -> None:
        bus: EventBus[int] = EventBus()
        queue = bus.subscribe()
        bus.unsubscribe(queue)

        await bus.publish(2)

        assert queue.empty()

    asyncio.run(_run())
