import asyncio
import contextlib
from typing import List

from common.bus import EventBus
from common.models import EventType, MarketEvent
from rules import (
    HedgedPair,
    HedgedSpreadAlert,
    HedgedSpreadConfig,
    HedgedSpreadEngine,
)


def test_hedged_alert_emitted_for_order_buy_perp_sell() -> None:
    async def _run() -> List[HedgedSpreadAlert]:
        bus: EventBus[MarketEvent] = EventBus()
        alerts: List[HedgedSpreadAlert] = []

        async def capture(alert: HedgedSpreadAlert) -> None:
            alerts.append(alert)

        config = HedgedSpreadConfig(
            pairs=[HedgedPair(order_venue="WHALES", perp_venue="BYBIT")],
            min_spread_percent=0.5,
            min_notional_usdt=50.0,
            min_improvement_percent=0.1,
            debounce_seconds=30.0,
            slippage_bps=5.0,
            fee_bps={"WHALES": 20.0, "BYBIT": 7.0},
        )

        engine = HedgedSpreadEngine(bus, config, capture)
        task = engine.start()
        await asyncio.sleep(0)

        try:
            await bus.publish(
                MarketEvent(
                    token="ABC",
                    venue="WHALES",
                    instrument="ABC",
                    event_type=EventType.BOOK,
                    best_bid=1.05,
                    best_ask=1.10,
                    last_price=None,
                    size=None,
                    bid_size=90.0,
                    ask_size=100.0,
                    notional=110.0,
                    timestamp_ms=1,
                    listing_info=None,
                    raw={},
                )
            )
            await bus.publish(
                MarketEvent(
                    token="ABC",
                    venue="BYBIT",
                    instrument="ABCPERP",
                    event_type=EventType.BOOK,
                    best_bid=1.25,
                    best_ask=1.26,
                    last_price=None,
                    size=None,
                    bid_size=120.0,
                    ask_size=110.0,
                    notional=130.0,
                    timestamp_ms=2,
                    listing_info=None,
                    raw={},
                )
            )

            for _ in range(10):
                if alerts:
                    break
                await asyncio.sleep(0.01)
        finally:
            await engine.stop()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        return alerts

    alerts = asyncio.run(_run())
    assert alerts, "Expected hedged alert"
    alert = alerts[0]
    assert alert.direction == "order_buy_perp_sell"
    assert alert.net_spread_percent > 0.5


def test_hedged_alert_debounced_without_improvement() -> None:
    async def _run() -> List[HedgedSpreadAlert]:
        bus: EventBus[MarketEvent] = EventBus()
        alerts: List[HedgedSpreadAlert] = []

        async def capture(alert: HedgedSpreadAlert) -> None:
            alerts.append(alert)

        config = HedgedSpreadConfig(
            pairs=[HedgedPair(order_venue="MEXC", perp_venue="BINANCE")],
            min_spread_percent=0.2,
            min_notional_usdt=20.0,
            min_improvement_percent=0.3,
            debounce_seconds=60.0,
            slippage_bps=2.0,
            fee_bps={"MEXC": 10.0, "BINANCE": 4.0},
        )

        engine = HedgedSpreadEngine(bus, config, capture)
        task = engine.start()
        await asyncio.sleep(0)

        try:
            await bus.publish(
                MarketEvent(
                    token="XYZ",
                    venue="MEXC",
                    instrument="XYZ_USDT",
                    event_type=EventType.BOOK,
                    best_bid=1.0,
                    best_ask=1.02,
                    last_price=None,
                    size=None,
                    bid_size=80.0,
                    ask_size=85.0,
                    notional=85.0,
                    timestamp_ms=1,
                    listing_info=None,
                    raw={},
                )
            )
            await bus.publish(
                MarketEvent(
                    token="XYZ",
                    venue="BINANCE",
                    instrument="XYZUSDTPERP",
                    event_type=EventType.BOOK,
                    best_bid=1.3,
                    best_ask=1.305,
                    last_price=None,
                    size=None,
                    bid_size=70.0,
                    ask_size=72.0,
                    notional=90.0,
                    timestamp_ms=2,
                    listing_info=None,
                    raw={},
                )
            )

            for _ in range(10):
                if alerts:
                    break
                await asyncio.sleep(0.01)

            await bus.publish(
                MarketEvent(
                    token="XYZ",
                    venue="BINANCE",
                    instrument="XYZUSDTPERP",
                    event_type=EventType.BOOK,
                    best_bid=1.3005,
                    best_ask=1.304,
                    last_price=None,
                    size=None,
                    bid_size=70.0,
                    ask_size=72.0,
                    notional=90.0,
                    timestamp_ms=3,
                    listing_info=None,
                    raw={},
                )
            )

            await asyncio.sleep(0.05)
        finally:
            await engine.stop()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        return alerts

    alerts = asyncio.run(_run())
    assert len(alerts) == 1


def test_hedged_alert_requires_notional() -> None:
    async def _run() -> List[HedgedSpreadAlert]:
        bus: EventBus[MarketEvent] = EventBus()
        alerts: List[HedgedSpreadAlert] = []

        async def capture(alert: HedgedSpreadAlert) -> None:
            alerts.append(alert)

        config = HedgedSpreadConfig(
            pairs=[HedgedPair(order_venue="WHALES", perp_venue="BYBIT")],
            min_spread_percent=0.1,
            min_notional_usdt=10.0,
            min_improvement_percent=0.1,
            debounce_seconds=10.0,
            slippage_bps=1.0,
            fee_bps={},
        )

        engine = HedgedSpreadEngine(bus, config, capture)
        task = engine.start()
        await asyncio.sleep(0)

        try:
            await bus.publish(
                MarketEvent(
                    token="LMN",
                    venue="WHALES",
                    instrument="LMN",
                    event_type=EventType.BOOK,
                    best_bid=1.0,
                    best_ask=1.01,
                    last_price=None,
                    size=None,
                    bid_size=None,
                    ask_size=None,
                    notional=None,
                    timestamp_ms=1,
                    listing_info=None,
                    raw={},
                )
            )
            await bus.publish(
                MarketEvent(
                    token="LMN",
                    venue="BYBIT",
                    instrument="LMNPERP",
                    event_type=EventType.BOOK,
                    best_bid=1.2,
                    best_ask=1.21,
                    last_price=None,
                    size=None,
                    bid_size=None,
                    ask_size=None,
                    notional=None,
                    timestamp_ms=2,
                    listing_info=None,
                    raw={},
                )
            )

            await asyncio.sleep(0.05)
        finally:
            await engine.stop()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        return alerts

    alerts = asyncio.run(_run())
    assert alerts == []
