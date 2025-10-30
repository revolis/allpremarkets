"""Base classes and helpers shared by ingest clients."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Optional


logger = logging.getLogger(__name__)


@dataclass
class BackoffConfig:
    """Configuration for exponential backoff between reconnect attempts."""

    initial: float = 1.0
    maximum: float = 30.0
    multiplier: float = 2.0

    def __iter__(self) -> AsyncIterator[float]:  # pragma: no cover - convenience helper
        delay = self.initial
        while True:
            yield delay
            delay = min(delay * self.multiplier, self.maximum)


class IngestClient(ABC):
    """Interface shared by ingest clients."""

    def __init__(self, name: str, backoff: Optional[BackoffConfig] = None) -> None:
        self.name = name
        self._backoff = backoff or BackoffConfig()
        self._stopped = asyncio.Event()
        self._task: Optional[asyncio.Task[None]] = None

    @property
    def stopped(self) -> bool:
        return self._stopped.is_set()

    async def start(self) -> None:
        """Start the ingest client loop."""

        if self._task and not self._task.done():
            logger.debug("%s already running", self.name)
            return

        self._stopped.clear()
        self._task = asyncio.create_task(self._run_with_retries(), name=f"{self.name}-loop")

    async def stop(self) -> None:
        """Signal the client to stop and wait for the background task."""

        self._stopped.set()
        if self._task:
            await self._task

    async def _run_with_retries(self) -> None:
        delay = self._backoff.initial
        while not self._stopped.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:  # pragma: no cover - cooperative shutdown
                raise
            except Exception as exc:  # pragma: no cover - logged for operators
                logger.exception("%s errored: %s", self.name, exc)
            if self._stopped.is_set():
                break
            await asyncio.sleep(delay)
            delay = min(delay * self._backoff.multiplier, self._backoff.maximum)
        logger.info("%s stopped", self.name)

    @abstractmethod
    async def run_once(self) -> None:
        """Implement one full connect/run/disconnect cycle."""


__all__ = ["BackoffConfig", "IngestClient"]
