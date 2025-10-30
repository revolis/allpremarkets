"""Bybit linear perpetual ticker ingestion client."""

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

WS_URL = "wss://stream.bybit.com/v5/public/linear"


def _format_symbol(symbol: str) -> str:
    return symbol.replace("/", "").replace("-", "").upper()


class BybitTickerClient(IngestClient):
    """Subscribe to Bybit ticker updates for USDT-margined perpetuals."""

    def __init__(
        self,
        bus: EventBus[MarketEvent],
        symbols: Iterable[str],
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        super().__init__(name="bybit-ticker")
        self.bus = bus
        self.symbols = [_format_symbol(sym) for sym in symbols]
        self._session = session

    async def run_once(self) -> None:
        if not self.symbols:
            logger.warning("Bybit client started without symbols; sleeping")
            await asyncio.sleep(5)
            return

        session = self._session or aiohttp.ClientSession()
        try:
            async with session.ws_connect(WS_URL, heartbeat=20) as ws:
                sub = {
                    "op": "subscribe",
                    "args": [f"tickers.{symbol}" for symbol in self.symbols],
                }
                await ws.send_json(sub)
                logger.info("Subscribed to %d Bybit tickers", len(self.symbols))

                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._handle_message(msg.data, ws)
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        await self._handle_message(msg.data.decode("utf-8"), ws)
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        logger.warning("Bybit websocket closed: %s", msg)
                        break
        finally:
            if self._session is None:
                await session.close()

    async def _handle_message(
        self, raw: str, ws: aiohttp.ClientWebSocketResponse
    ) -> None:
        if raw == "pong":
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("Non JSON Bybit payload: %s", raw)
            return

        if payload.get("op") == "ping":
            await ws.send_json({"op": "pong"})
            return

        if payload.get("type") == "COMMAND":
            logger.debug("Command acknowledgement: %s", payload)
            return

        topic = payload.get("topic")
        if not topic or not topic.startswith("tickers."):
            return

        data = payload.get("data") or {}
        symbol = topic.split(".", 1)[1]
        token = symbol.replace("USDT", "")

        best_bid = _safe_float(data.get("bid1Price"))
        best_ask = _safe_float(data.get("ask1Price"))
        bid_size = _safe_float(data.get("bid1Size"))
        ask_size = _safe_float(data.get("ask1Size"))
        last_price = _safe_float(data.get("lastPrice"))

        notional_candidates = []
        if best_bid and bid_size:
            notional_candidates.append(best_bid * bid_size)
        if best_ask and ask_size:
            notional_candidates.append(best_ask * ask_size)

        timestamp_ms = int(data.get("ts", time.time() * 1000))

        event = MarketEvent(
            token=token,
            venue="BYBIT",
            instrument=f"{symbol}PERP",
            event_type=EventType.BOOK,
            best_bid=best_bid,
            best_ask=best_ask,
            last_price=last_price,
            size=bid_size,
            bid_size=bid_size,
            ask_size=ask_size,
            notional=min(notional_candidates) if notional_candidates else None,
            timestamp_ms=timestamp_ms,
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


__all__ = ["BybitTickerClient"]

