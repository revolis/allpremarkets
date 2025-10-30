"""Hedged spread calculation between order venues and perpetual venues."""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Iterable, Tuple

from common.bus import EventBus
from common.models import EventType, MarketEvent


@dataclass(frozen=True)
class HedgedPair:
    """Pairing of an order venue with a perpetual venue."""

    order_venue: str
    perp_venue: str

    def directions(self) -> Tuple[str, str]:
        return self.order_venue, self.perp_venue


@dataclass
class HedgedSpreadConfig:
    """Configuration for hedged spread detection."""

    pairs: Iterable[HedgedPair]
    min_spread_percent: float
    min_notional_usdt: float
    min_improvement_percent: float
    debounce_seconds: float
    slippage_bps: float
    fee_bps: Dict[str, float]

    def total_cost_percent(self, order_venue: str, perp_venue: str) -> float:
        """Aggregate fees and slippage into a single percentage."""

        order_fee = self.fee_bps.get(order_venue, 0.0)
        perp_fee = self.fee_bps.get(perp_venue, 0.0)
        total_bps = order_fee + perp_fee + self.slippage_bps
        return total_bps / 100.0


@dataclass
class HedgedSpreadAlert:
    """Emitted when an order/perp hedge exceeds the configured threshold."""

    token: str
    order_venue: str
    perp_venue: str
    direction: str
    order_price: float
    perp_price: float
    gross_spread_percent: float
    net_spread_percent: float
    reference_notional: float
    updated_at_ms: int


@dataclass
class _Quote:
    best_bid: float | None = None
    best_ask: float | None = None
    bid_size: float | None = None
    ask_size: float | None = None
    general_notional: float | None = None
    timestamp_ms: int = 0


class HedgedSpreadEngine:
    """Consume events and surface hedged order/perp opportunities."""

    def __init__(
        self,
        bus: EventBus[MarketEvent],
        config: HedgedSpreadConfig,
        alert_callback: Callable[[HedgedSpreadAlert], Awaitable[None]],
    ) -> None:
        self._bus = bus
        self._config = config
        self._alert_callback = alert_callback
        self._pairs: Tuple[HedgedPair, ...] = tuple(config.pairs)
        self._quotes: Dict[str, Dict[str, _Quote]] = {}
        self._last_alert: Dict[Tuple[str, str, str, str], Tuple[float, float]] = {}
        self._task: asyncio.Task[None] | None = None

    def start(self) -> asyncio.Task[None]:
        if self._task is not None:
            raise RuntimeError("HedgedSpreadEngine already running")
        self._task = asyncio.create_task(self._run(), name="hedged-spread-engine")
        return self._task

    async def stop(self) -> None:
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
        except asyncio.CancelledError:  # pragma: no cover - cooperative shutdown
            raise
        finally:
            self._bus.unsubscribe(queue)

    async def _handle_event(self, event: MarketEvent) -> None:
        if event.event_type is not EventType.BOOK:
            return

        venues = self._quotes.setdefault(event.token, {})
        quote = venues.setdefault(event.venue, _Quote())

        if event.best_bid is not None:
            quote.best_bid = event.best_bid
        if event.best_ask is not None:
            quote.best_ask = event.best_ask
        if event.bid_size is not None:
            quote.bid_size = event.bid_size
        elif event.size is not None:
            quote.bid_size = event.size
        if event.ask_size is not None:
            quote.ask_size = event.ask_size
        if event.notional is not None:
            quote.general_notional = event.notional
        quote.timestamp_ms = event.timestamp_ms

        await self._evaluate(event.token)

    async def _evaluate(self, token: str) -> None:
        venue_quotes = self._quotes.get(token)
        if not venue_quotes:
            return

        now = time.monotonic()
        for pair in self._pairs:
            order_quote = venue_quotes.get(pair.order_venue)
            perp_quote = venue_quotes.get(pair.perp_venue)
            if not order_quote or not perp_quote:
                continue

            await self._check_order_buy_perp_sell(token, pair, order_quote, perp_quote, now)
            await self._check_perp_buy_order_sell(token, pair, order_quote, perp_quote, now)

    async def _check_order_buy_perp_sell(
        self,
        token: str,
        pair: HedgedPair,
        order_quote: _Quote,
        perp_quote: _Quote,
        now: float,
    ) -> None:
        if order_quote.best_ask is None or perp_quote.best_bid is None:
            return

        if order_quote.best_ask <= 0:
            return

        gross = ((perp_quote.best_bid - order_quote.best_ask) / order_quote.best_ask) * 100.0
        if math.isnan(gross):
            return

        cost = self._config.total_cost_percent(pair.order_venue, pair.perp_venue)
        net = gross - cost
        if net < self._config.min_spread_percent:
            return

        notional = self._reference_notional(order_quote, perp_quote, "ask", "bid")
        if notional is None or notional < self._config.min_notional_usdt:
            return

        key = (token, pair.order_venue, pair.perp_venue, "order_buy_perp_sell")
        if not self._should_emit(key, now, net):
            return

        alert = HedgedSpreadAlert(
            token=token,
            order_venue=pair.order_venue,
            perp_venue=pair.perp_venue,
            direction="order_buy_perp_sell",
            order_price=order_quote.best_ask,
            perp_price=perp_quote.best_bid,
            gross_spread_percent=gross,
            net_spread_percent=net,
            reference_notional=notional,
            updated_at_ms=max(order_quote.timestamp_ms, perp_quote.timestamp_ms),
        )
        await self._alert_callback(alert)
        self._last_alert[key] = (now, net)

    async def _check_perp_buy_order_sell(
        self,
        token: str,
        pair: HedgedPair,
        order_quote: _Quote,
        perp_quote: _Quote,
        now: float,
    ) -> None:
        if perp_quote.best_ask is None or order_quote.best_bid is None:
            return

        if perp_quote.best_ask <= 0:
            return

        gross = ((order_quote.best_bid - perp_quote.best_ask) / perp_quote.best_ask) * 100.0
        if math.isnan(gross):
            return

        cost = self._config.total_cost_percent(pair.order_venue, pair.perp_venue)
        net = gross - cost
        if net < self._config.min_spread_percent:
            return

        notional = self._reference_notional(order_quote, perp_quote, "bid", "ask")
        if notional is None or notional < self._config.min_notional_usdt:
            return

        key = (token, pair.order_venue, pair.perp_venue, "perp_buy_order_sell")
        if not self._should_emit(key, now, net):
            return

        alert = HedgedSpreadAlert(
            token=token,
            order_venue=pair.order_venue,
            perp_venue=pair.perp_venue,
            direction="perp_buy_order_sell",
            order_price=order_quote.best_bid,
            perp_price=perp_quote.best_ask,
            gross_spread_percent=gross,
            net_spread_percent=net,
            reference_notional=notional,
            updated_at_ms=max(order_quote.timestamp_ms, perp_quote.timestamp_ms),
        )
        await self._alert_callback(alert)
        self._last_alert[key] = (now, net)

    def _reference_notional(
        self,
        order_quote: _Quote,
        perp_quote: _Quote,
        order_side: str,
        perp_side: str,
    ) -> float | None:
        values = []
        order_value = self._side_notional(order_quote, order_side)
        perp_value = self._side_notional(perp_quote, perp_side)
        for value in (order_value, perp_value):
            if value is not None:
                values.append(value)
        if not values:
            return None
        return min(values)

    def _side_notional(self, quote: _Quote, side: str) -> float | None:
        if side == "ask":
            price = quote.best_ask
            size = quote.ask_size
        else:
            price = quote.best_bid
            size = quote.bid_size
        if price is not None and size is not None:
            return price * size
        return quote.general_notional

    def _should_emit(self, key: Tuple[str, str, str, str], now: float, spread: float) -> bool:
        last = self._last_alert.get(key)
        if last is None:
            return True
        last_time, last_spread = last
        if now - last_time < self._config.debounce_seconds:
            improvement = spread - last_spread
            return improvement >= self._config.min_improvement_percent
        return True


__all__ = [
    "HedgedPair",
    "HedgedSpreadAlert",
    "HedgedSpreadConfig",
    "HedgedSpreadEngine",
]

