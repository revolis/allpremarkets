"""MEXC spot websocket/book ingestion client."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Iterable

import aiohttp

from common import EventBus, EventType, MarketEvent

from .base import IngestClient

logger = logging.getLogger(__name__)

BOOK_CHANNEL_TEMPLATE = "spot@public.bookTicker.v3.api@{symbol}"
LISTING_ENDPOINT = "https://www.mexc.com/open/api/v2/market/coin/list"


def _format_symbol(symbol: str) -> str:
    return symbol.replace("/", "_").replace("-", "_").upper()


class MexcBookTickerClient(IngestClient):
    """Subscribe to the public bookTicker stream for a set of symbols."""

    def __init__(
        self,
        bus: EventBus[MarketEvent],
        symbols: Iterable[str],
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        super().__init__(name="mexc-book")
        self.bus = bus
        self.symbols = [_format_symbol(sym) for sym in symbols]
        self._session = session

    async def run_once(self) -> None:
        if not self.symbols:
            logger.warning("MEXC client started without symbols; nothing to do")
            await asyncio.sleep(5)
            return

        session = self._session or aiohttp.ClientSession()
        try:
            async with session.ws_connect("wss://wbs.mexc.com/raw/ws") as ws:
                sub_msg = {
                    "method": "SUBSCRIBE",
                    "params": [
                        BOOK_CHANNEL_TEMPLATE.format(symbol=symbol) for symbol in self.symbols
                    ],
                    "id": int(time.time()),
                }
                await ws.send_json(sub_msg)
                logger.info("Subscribed to %d MEXC channels", len(self.symbols))

                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._handle_message(msg.data, ws)
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        await self._handle_message(msg.data.decode("utf-8"), ws)
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        logger.warning("MEXC websocket closed: %s", msg)
                        break
        finally:
            if self._session is None:
                await session.close()

    async def _handle_message(self, raw_text: str, ws: aiohttp.ClientWebSocketResponse) -> None:
        if raw_text == "pong":
            return
        if raw_text == "ping":
            await ws.send_str("pong")
            return
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.debug("Non JSON message from MEXC: %s", raw_text)
            return

        if payload.get("method") == "PING":
            pong = {"method": "PONG", "params": payload.get("params", [])}
            await ws.send_json(pong)
            return

        if "result" in payload:
            logger.debug("Subscription ack: %s", payload)
            return

        channel = payload.get("c")
        data = payload.get("d")
        if not channel or not data:
            return

        parts = channel.split("@")
        if len(parts) < 4:
            return
        symbol = parts[-1]
        token = symbol.split("_")[0]
        ts = int(float(data.get("t", time.time() * 1000)))

        event = MarketEvent(
            token=token,
            venue="MEXC",
            instrument=symbol,
            event_type=EventType.BOOK,
            best_bid=float(data.get("b")) if data.get("b") else None,
            best_ask=float(data.get("a")) if data.get("a") else None,
            last_price=float(data.get("bp")) if data.get("bp") else None,
            size=float(data.get("B")) if data.get("B") else None,
            timestamp_ms=ts,
            raw=payload,
        )
        await self.bus.publish(event)


class MexcListingPoller(IngestClient):
    """Poll the public listings endpoint and emit listing events."""

    def __init__(
        self,
        bus: EventBus[MarketEvent],
        poll_interval: float = 60.0,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        super().__init__(name="mexc-listings")
        self.bus = bus
        self.poll_interval = poll_interval
        self._session = session
        self._seen_tokens: set[str] = set()

    async def run_once(self) -> None:
        session = self._session or aiohttp.ClientSession()
        try:
            while not self.stopped:
                await self._poll(session)
                await asyncio.sleep(self.poll_interval)
        finally:
            if self._session is None:
                await session.close()

    async def _poll(self, session: aiohttp.ClientSession) -> None:
        async with session.get(LISTING_ENDPOINT, timeout=30) as resp:
            resp.raise_for_status()
            data = await resp.json()

        coins = data.get("data", [])
        now_ms = int(time.time() * 1000)
        for coin in coins:
            symbol = coin.get("symbol") or coin.get("currency")
            if not symbol:
                continue
            state = coin.get("state") or coin.get("status")
            token = symbol.split("_")[0]
            key = f"{symbol}:{state}"
            if key in self._seen_tokens:
                continue
            if state and state.upper() == "ENABLED":
                event = MarketEvent(
                    token=token,
                    venue="MEXC",
                    instrument=symbol,
                    event_type=EventType.LISTING,
                    timestamp_ms=now_ms,
                    listing_info={
                        "state": state,
                        "full": coin,
                    },
                    raw=coin,
                )
                await self.bus.publish(event)
            self._seen_tokens.add(key)


__all__ = [
    "MexcBookTickerClient",
    "MexcListingPoller",
]
