"""Hyperliquid websocket ingestion for perpetual markets."""

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

WS_URL = "wss://api.hyperliquid.xyz/ws"


class HyperliquidTickerClient(IngestClient):
    """Subscribe to Hyperliquid level 2 streams and emit top-of-book events."""

    def __init__(
        self,
        bus: EventBus[MarketEvent],
        symbols: Iterable[str],
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        super().__init__(name="hyperliquid-ticker")
        self.bus = bus
        self.symbols = [symbol.upper() for symbol in symbols]
        self._session = session

    async def run_once(self) -> None:
        if not self.symbols:
            logger.warning("Hyperliquid client started without symbols; sleeping")
            await asyncio.sleep(5)
            return

        session = self._session or aiohttp.ClientSession()
        try:
            async with session.ws_connect(WS_URL) as ws:
                for symbol in self.symbols:
                    sub = {
                        "method": "subscribe",
                        "subscription": {"type": "l2", "coin": symbol},
                    }
                    await ws.send_json(sub)
                logger.info(
                    "Subscribed to %d Hyperliquid markets", len(self.symbols)
                )

                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._handle_message(msg.data, ws)
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        await self._handle_message(msg.data.decode("utf-8"), ws)
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        logger.warning("Hyperliquid websocket closed: %s", msg)
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
            logger.debug("Non JSON Hyperliquid payload: %s", raw)
            return

        if payload.get("type") == "ping":
            await ws.send_json({"type": "pong"})
            return

        channel = payload.get("channel")
        if channel not in {"l2", "l2Book"}:
            return

        data = payload.get("data") or {}
        coin = data.get("coin") or data.get("symbol")
        if not coin or coin.upper() not in self.symbols:
            return

        best_bid, bid_size = _top_of_book(data, side="bid")
        best_ask, ask_size = _top_of_book(data, side="ask")

        notional_candidates = []
        if best_bid and bid_size:
            notional_candidates.append(best_bid * bid_size)
        if best_ask and ask_size:
            notional_candidates.append(best_ask * ask_size)

        timestamp = data.get("time") or data.get("ts") or time.time() * 1000

        event = MarketEvent(
            token=coin.upper(),
            venue="HYPERLIQUID",
            instrument=f"{coin.upper()}PERP",
            event_type=EventType.BOOK,
            best_bid=best_bid,
            best_ask=best_ask,
            last_price=_safe_float(data.get("markPx")) or _safe_float(data.get("mid")),
            size=bid_size,
            bid_size=bid_size,
            ask_size=ask_size,
            notional=min(notional_candidates) if notional_candidates else None,
            timestamp_ms=int(float(timestamp)),
            raw=payload,
        )
        await self.bus.publish(event)


def _top_of_book(data: dict[str, object], side: str) -> tuple[float | None, float | None]:
    side_key = "bids" if side == "bid" else "asks"
    levels = data.get(side_key) or []
    if not levels and "levels" in data:
        # Alternate format: list of dicts with "side" key
        desired = "BID" if side == "bid" else "ASK"
        for level in data.get("levels", []):
            if str(level.get("side")).upper() == desired:
                price = _safe_float(level.get("px"))
                size = _safe_float(level.get("sz"))
                return price, size
        return None, None

    if levels:
        first = levels[0]
        if isinstance(first, (list, tuple)) and len(first) >= 2:
            price = _safe_float(first[0])
            size = _safe_float(first[1])
            return price, size
        if isinstance(first, dict):
            price = _safe_float(first.get("px"))
            size = _safe_float(first.get("sz"))
            return price, size
    return None, None


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["HyperliquidTickerClient"]

