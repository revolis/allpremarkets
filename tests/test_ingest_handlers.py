"""Unit tests for ingest message normalisation."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

pytest.importorskip("pydantic")

from common import EventBus
from ingest.binance import BinanceFuturesTickerClient
from ingest.bybit import BybitTickerClient
from ingest.hyperliquid import HyperliquidTickerClient
from ingest.mexc import MexcBookTickerClient
from ingest.whales import WhalesConfig, WhalesMarketClient


class _DummyWebSocket:
    async def send_json(self, _: Any) -> None:  # pragma: no cover - not triggered
        return None

    async def send_str(self, _: str) -> None:  # pragma: no cover - not triggered
        return None


def test_mexc_book_message_normalises_event() -> None:
    async def _run() -> None:
        bus = EventBus()
        queue = bus.subscribe()
        client = MexcBookTickerClient(bus=bus, symbols=["TNSR_USDT"])

        payload = {
            "c": "spot@public.bookTicker.v3.api@TNSR_USDT",
            "d": {
                "t": 1712345678901,
                "b": "1.2345",
                "a": "1.2351",
                "bp": "1.2349",
                "B": "532",
            },
        }

        await client._handle_message(json.dumps(payload), _DummyWebSocket())

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event.venue == "MEXC"
        assert event.instrument == "TNSR_USDT"
        assert event.best_bid == 1.2345
        assert event.size == 532.0

    asyncio.run(_run())


def test_whales_orderbook_frame_emits_event() -> None:
    async def _run() -> None:
        bus = EventBus()
        queue = bus.subscribe()
        client = WhalesMarketClient(bus=bus, config=WhalesConfig())

        message = '42["orderbook", {"token": "ABC", "bestBid": "1.5", "bestAsk": "1.7"}]'
        await client._process_message(message)

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event.venue == "WHALES"
        assert event.token == "ABC"
        assert event.best_bid == 1.5
        assert event.best_ask == 1.7

    asyncio.run(_run())


def test_bybit_ticker_message_normalises_event() -> None:
    async def _run() -> None:
        bus = EventBus()
        queue = bus.subscribe()
        client = BybitTickerClient(bus=bus, symbols=["TNSRUSDT"])

        payload = {
            "topic": "tickers.TNSRUSDT",
            "data": {
                "bid1Price": "1.25",
                "ask1Price": "1.3",
                "bid1Size": "500",
                "ask1Size": "400",
                "lastPrice": "1.27",
                "ts": 1712000000000,
            },
        }

        await client._handle_message(json.dumps(payload), _DummyWebSocket())

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event.venue == "BYBIT"
        assert event.instrument == "TNSRUSDTPERP"
        assert event.best_bid == 1.25
        assert event.bid_size == 500.0

    asyncio.run(_run())


def test_hyperliquid_message_normalises_event() -> None:
    async def _run() -> None:
        bus = EventBus()
        queue = bus.subscribe()
        client = HyperliquidTickerClient(bus=bus, symbols=["TNSR"])

        payload = {
            "channel": "l2",
            "data": {
                "coin": "TNSR",
                "bids": [["1.1", "250"]],
                "asks": [["1.2", "200"]],
                "time": 1712000000000,
                "markPx": "1.15",
            },
        }

        await client._handle_message(json.dumps(payload), _DummyWebSocket())

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event.venue == "HYPERLIQUID"
        assert event.best_ask == 1.2
        assert event.ask_size == 200.0

    asyncio.run(_run())


def test_binance_message_normalises_event() -> None:
    async def _run() -> None:
        bus = EventBus()
        queue = bus.subscribe()
        client = BinanceFuturesTickerClient(bus=bus, symbols=["TNSRUSDT"])

        payload = {
            "stream": "tnsrusdt@bookTicker",
            "data": {
                "s": "TNSRUSDT",
                "b": "1.05",
                "B": "300",
                "a": "1.08",
                "A": "280",
                "E": 1712000000000,
            },
        }

        await client._handle_message(json.dumps(payload), _DummyWebSocket())

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event.venue == "BINANCE"
        assert event.best_ask == 1.08
        assert event.bid_size == 300.0

    asyncio.run(_run())
