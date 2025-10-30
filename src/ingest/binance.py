"""Binance futures bookTicker ingestion."""

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

WS_URL = "wss://fstream.binance.com/ws"


def _format_symbol(symbol: str) -> str:
    return symbol.replace("/", "").replace("-", "").lower()


class BinanceFuturesTickerClient(IngestClient):
    """Subscribe to Binance USDT-m perpetual bookTicker updates."""

    def __init__(
        self,
        bus: EventBus[MarketEvent],
        symbols: Iterable[str],
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        super().__init__(name="binance-futures")
        self.bus = bus
        self.symbols = [_format_symbol(sym) for sym in symbols]
        self._session = session

    async def run_once(self) -> None:
        if not self.symbols:
            logger.warning("Binance client started without symbols; sleeping")
            await asyncio.sleep(5)
            return

        session = self._session or aiohttp.ClientSession()
        try:
            async with session.ws_connect(WS_URL, heartbeat=15) as ws:
                sub = {
                    "method": "SUBSCRIBE",
                    "params": [f"{symbol}@bookTicker" for symbol in self.symbols],
                    "id": int(time.time()),
                }
                await ws.send_json(sub)
                logger.info(
                    "Subscribed to %d Binance futures bookTicker streams",
                    len(self.symbols),
                )

                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._handle_message(msg.data, ws)
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        await self._handle_message(msg.data.decode("utf-8"), ws)
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        logger.warning("Binance websocket closed: %s", msg)
                        break
        finally:
            if self._session is None:
                await session.close()

    async def _handle_message(
        self, raw: str, ws: aiohttp.ClientWebSocketResponse
    ) -> None:
        if raw.lower() == "pong":
            return

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("Non JSON Binance payload: %s", raw)
            return

        if "result" in payload and payload.get("id") is not None:
            # Subscription acknowledgement
            return

        data = payload.get("data") if "data" in payload else payload
        if not isinstance(data, dict):
            return

        if data.get("e") not in (None, "bookTicker"):
            return

        symbol = data.get("s") or data.get("symbol")
        if not symbol:
            return

        token = symbol.replace("USDT", "")

        best_bid = _safe_float(data.get("b")) or _safe_float(data.get("bidPrice"))
        best_ask = _safe_float(data.get("a")) or _safe_float(data.get("askPrice"))
        bid_size = _safe_float(data.get("B")) or _safe_float(data.get("bidQty"))
        ask_size = _safe_float(data.get("A")) or _safe_float(data.get("askQty"))

        notional_candidates = []
        if best_bid and bid_size:
            notional_candidates.append(best_bid * bid_size)
        if best_ask and ask_size:
            notional_candidates.append(best_ask * ask_size)

        timestamp = data.get("T") or data.get("E") or time.time() * 1000

        event = MarketEvent(
            token=token,
            venue="BINANCE",
            instrument=f"{symbol}PERP",
            event_type=EventType.BOOK,
            best_bid=best_bid,
            best_ask=best_ask,
            last_price=_safe_float(data.get("p")) or _safe_float(data.get("lastPrice")),
            size=bid_size,
            bid_size=bid_size,
            ask_size=ask_size,
            notional=min(notional_candidates) if notional_candidates else None,
            timestamp_ms=int(float(timestamp)),
            raw=payload,
        )
        await self.bus.publish(event)


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["BinanceFuturesTickerClient"]

