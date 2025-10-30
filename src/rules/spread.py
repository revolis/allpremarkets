"""Spread calculation engine consuming normalised market events."""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Iterable, Tuple

from common.bus import EventBus
from common.models import EventType, MarketEvent


@dataclass(frozen=True)
class VenuePair:
    """Represents a pair of venues to compare for arbitrage opportunities."""

    venues: Tuple[str, str]

    def all_directions(self) -> Iterable[Tuple[str, str]]:
        """Yield both trading directions for the venue pair."""

        a, b = self.venues
        yield a, b
        yield b, a


@dataclass
class SpreadConfig:
    """Configuration for the spread calculation engine."""

    venue_pairs: Iterable[VenuePair]
    min_spread_percent: float
    min_notional_usdt: float
    min_improvement_percent: float
    debounce_seconds: float
    slippage_bps: float
    fee_bps: Dict[str, float]

    def total_cost_percent(self, buy_venue: str, sell_venue: str) -> float:
        """Return the total cost percentage applied to the spread calculation."""

        buy_fee = self.fee_bps.get(buy_venue, 0.0)
        sell_fee = self.fee_bps.get(sell_venue, 0.0)
        total_bps = buy_fee + sell_fee + self.slippage_bps
        return total_bps / 100.0


@dataclass
class SpreadAlert:
    """Alert emitted when a profitable spread is detected."""

    token: str
    buy_venue: str
    sell_venue: str
    buy_price: float
    sell_price: float
    gross_spread_percent: float
    net_spread_percent: float
    reference_notional: float
    updated_at_ms: int


@dataclass
class _Quote:
    best_bid: float | None = None
    best_ask: float | None = None
    notional: float | None = None
    timestamp_ms: int = 0


class SpreadEngine:
    """Consume market events and emit spread alerts when opportunities appear."""

    def __init__(
        self,
        bus: EventBus[MarketEvent],
        config: SpreadConfig,
        alert_callback: Callable[[SpreadAlert], Awaitable[None]],
    ) -> None:
        self._bus = bus
        self._config = config
        self._alert_callback = alert_callback
        self._venue_pairs: Tuple[VenuePair, ...] = tuple(config.venue_pairs)
        self._quotes: Dict[str, Dict[str, _Quote]] = {}
        self._last_alert: Dict[Tuple[str, str, str], Tuple[float, float]] = {}
        self._task: asyncio.Task[None] | None = None

    def start(self) -> asyncio.Task[None]:
        """Begin consuming events from the bus."""

        if self._task is not None:
            raise RuntimeError("SpreadEngine already running")
        self._task = asyncio.create_task(self._run(), name="spread-engine")
        return self._task

    async def stop(self) -> None:
        """Stop consuming events and clean up resources."""

        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _run(self) -> None:
        queue = self._bus.subscribe()
        try:
            while True:
                event = await queue.get()
                try:
                    await self._handle_event(event)
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            raise
        finally:
            self._bus.unsubscribe(queue)

    async def _handle_event(self, event: MarketEvent) -> None:
        if event.event_type is not EventType.BOOK:
            return

        if event.best_bid is None and event.best_ask is None:
            return

        venue_quotes = self._quotes.setdefault(event.token, {})
        quote = venue_quotes.setdefault(event.venue, _Quote())

        if event.best_bid is not None:
            quote.best_bid = event.best_bid
        if event.best_ask is not None:
            quote.best_ask = event.best_ask
        if event.notional is not None:
            quote.notional = event.notional
        quote.timestamp_ms = event.timestamp_ms

        await self._evaluate_spreads(event.token)

    async def _evaluate_spreads(self, token: str) -> None:
        venue_quotes = self._quotes.get(token)
        if not venue_quotes:
            return

        now = time.monotonic()
        for pair in self._venue_pairs:
            for buy_venue, sell_venue in pair.all_directions():
                buy_quote = venue_quotes.get(buy_venue)
                sell_quote = venue_quotes.get(sell_venue)
                if not buy_quote or not sell_quote:
                    continue

                if buy_quote.best_ask is None or sell_quote.best_bid is None:
                    continue

                if buy_quote.best_ask <= 0:
                    continue

                gross_spread = (
                    (sell_quote.best_bid - buy_quote.best_ask) / buy_quote.best_ask
                ) * 100.0

                if math.isnan(gross_spread):
                    continue

                cost = self._config.total_cost_percent(buy_venue, sell_venue)
                net_spread = gross_spread - cost

                if net_spread < self._config.min_spread_percent:
                    continue

                reference_notional = self._reference_notional(buy_quote, sell_quote)
                if reference_notional is None:
                    continue

                if reference_notional < self._config.min_notional_usdt:
                    continue

                key = (token, buy_venue, sell_venue)
                last = self._last_alert.get(key)
                if last is not None:
                    last_time, last_spread = last
                    if (
                        now - last_time < self._config.debounce_seconds
                        and net_spread <= last_spread + self._config.min_improvement_percent
                    ):
                        continue

                alert = SpreadAlert(
                    token=token,
                    buy_venue=buy_venue,
                    sell_venue=sell_venue,
                    buy_price=buy_quote.best_ask,
                    sell_price=sell_quote.best_bid,
                    gross_spread_percent=gross_spread,
                    net_spread_percent=net_spread,
                    reference_notional=reference_notional,
                    updated_at_ms=max(buy_quote.timestamp_ms, sell_quote.timestamp_ms),
                )

                await self._alert_callback(alert)
                self._last_alert[key] = (now, net_spread)

    @staticmethod
    def _reference_notional(
        buy_quote: _Quote, sell_quote: _Quote
    ) -> float | None:
        candidates = [
            value
            for value in (buy_quote.notional, sell_quote.notional)
            if value is not None
        ]
        if not candidates:
            return None
        return min(candidates)


__all__ = [
    "SpreadConfig",
    "SpreadEngine",
    "SpreadAlert",
    "VenuePair",
]

