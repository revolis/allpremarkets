import asyncio
import contextlib
from typing import List

import pytest

pytest.importorskip("pydantic")

from common.bus import EventBus
from common.models import EventType, MarketEvent
from rules import SpreadAlert, SpreadConfig, SpreadEngine, VenuePair


@pytest.mark.asyncio
async def test_spread_alert_emitted_when_threshold_passed():
    bus: EventBus[MarketEvent] = EventBus()
    alerts: List[SpreadAlert] = []

    async def capture(alert) -> None:
        alerts.append(alert)

    config = SpreadConfig(
        venue_pairs=[VenuePair(("MEXC", "WHALES"))],
        min_spread_percent=0.5,
        min_notional_usdt=50.0,
        min_improvement_percent=0.1,
        debounce_seconds=30.0,
        slippage_bps=5.0,
        fee_bps={"MEXC": 10.0, "WHALES": 20.0},
    )

    engine = SpreadEngine(bus, config, capture)
    task = engine.start()

    try:
        await bus.publish(
            MarketEvent(
                token="ABC",
                venue="MEXC",
                instrument="ABC_USDT",
                event_type=EventType.BOOK,
                best_bid=0.99,
                best_ask=1.00,
                last_price=None,
                size=None,
                notional=150.0,
                timestamp_ms=1,
                listing_info=None,
                raw={},
            )
        )
        await bus.publish(
            MarketEvent(
                token="ABC",
                venue="WHALES",
                instrument="ABC",
                event_type=EventType.BOOK,
                best_bid=1.05,
                best_ask=1.06,
                last_price=None,
                size=None,
                notional=200.0,
                timestamp_ms=2,
                listing_info=None,
                raw={},
            )
        )

        await asyncio.sleep(0)
    finally:
        await engine.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert alerts, "Expected at least one alert to be generated"
    alert = alerts[0]
    assert alert.buy_venue == "MEXC"
    assert alert.sell_venue == "WHALES"
    assert alert.net_spread_percent > 0.5


@pytest.mark.asyncio
async def test_spread_alert_debounced_without_improvement():
    bus: EventBus[MarketEvent] = EventBus()
    alerts: List[SpreadAlert] = []

    async def capture(alert) -> None:
        alerts.append(alert)

    config = SpreadConfig(
        venue_pairs=[VenuePair(("MEXC", "WHALES"))],
        min_spread_percent=0.1,
        min_notional_usdt=10.0,
        min_improvement_percent=0.2,
        debounce_seconds=60.0,
        slippage_bps=0.0,
        fee_bps={},
    )

    engine = SpreadEngine(bus, config, capture)
    task = engine.start()

    try:
        base_event = MarketEvent(
            token="DEF",
            venue="MEXC",
            instrument="DEF_USDT",
            event_type=EventType.BOOK,
            best_bid=1.0,
            best_ask=1.0,
            last_price=None,
            size=None,
            notional=100.0,
            timestamp_ms=1,
            listing_info=None,
            raw={},
        )
        await bus.publish(base_event)
        await bus.publish(
            MarketEvent(
                token="DEF",
                venue="WHALES",
                instrument="DEF",
                event_type=EventType.BOOK,
                best_bid=1.2,
                best_ask=1.21,
                last_price=None,
                size=None,
                notional=90.0,
                timestamp_ms=2,
                listing_info=None,
                raw={},
            )
        )

        await asyncio.sleep(0)

        await bus.publish(
            base_event.model_copy(update={"timestamp_ms": 3, "best_ask": 0.99})
        )
        await bus.publish(
            MarketEvent(
                token="DEF",
                venue="WHALES",
                instrument="DEF",
                event_type=EventType.BOOK,
                best_bid=1.21,
                best_ask=1.22,
                last_price=None,
                size=None,
                notional=90.0,
                timestamp_ms=4,
                listing_info=None,
                raw={},
            )
        )

        await asyncio.sleep(0)
    finally:
        await engine.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert len(alerts) == 1, "Debounce should suppress duplicate alerts without improvement"


@pytest.mark.asyncio
async def test_spread_alert_skips_without_notional():
    bus: EventBus[MarketEvent] = EventBus()
    alerts: List[SpreadAlert] = []

    async def capture(alert) -> None:
        alerts.append(alert)

    config = SpreadConfig(
        venue_pairs=[VenuePair(("MEXC", "WHALES"))],
        min_spread_percent=0.1,
        min_notional_usdt=10.0,
        min_improvement_percent=0.0,
        debounce_seconds=10.0,
        slippage_bps=0.0,
        fee_bps={},
    )

    engine = SpreadEngine(bus, config, capture)
    task = engine.start()

    try:
        await bus.publish(
            MarketEvent(
                token="XYZ",
                venue="MEXC",
                instrument="XYZ_USDT",
                event_type=EventType.BOOK,
                best_bid=0.5,
                best_ask=0.5,
                last_price=None,
                size=None,
                notional=None,
                timestamp_ms=1,
                listing_info=None,
                raw={},
            )
        )

        await bus.publish(
            MarketEvent(
                token="XYZ",
                venue="WHALES",
                instrument="XYZ",
                event_type=EventType.BOOK,
                best_bid=0.6,
                best_ask=0.61,
                last_price=None,
                size=None,
                notional=None,
                timestamp_ms=2,
                listing_info=None,
                raw={},
            )
        )

        await asyncio.sleep(0)
    finally:
        await engine.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert not alerts, "Engine should skip alerts when notional is unknown"
