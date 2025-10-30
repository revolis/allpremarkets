"""Whales Market ingestion using Playwright Socket.IO interception."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from typing import TYPE_CHECKING

try:  # Playwright is optional for unit testing without browser support.
    from playwright.async_api import async_playwright
except ImportError:  # pragma: no cover - exercised when Playwright missing.
    async_playwright = None  # type: ignore[assignment]

if TYPE_CHECKING:  # pragma: no cover - type checker only.
    from playwright.async_api import Browser, Page, Playwright, WebSocket
else:  # pragma: no cover - runtime when Playwright isn't installed.
    Browser = Page = Playwright = WebSocket = object  # type: ignore

from common import EventBus, EventType, MarketEvent

from .base import IngestClient

logger = logging.getLogger(__name__)


SOCKET_EVENT_MAP = {
    "orderbook": EventType.BOOK,
    "listing": EventType.LISTING,
    "trade": EventType.TRADE,
}


def _parse_socketio_message(message: str) -> tuple[str | None, Any | None]:
    """Parse a Socket.IO textual frame and return ``(event, data)``."""

    if not message:
        return None, None

    if message in {"40", "3", "2"}:  # handshake/ping frames
        return None, None

    if not message.startswith("42"):
        return None, None

    try:
        payload = json.loads(message[2:])
    except json.JSONDecodeError:
        return None, None

    if isinstance(payload, list) and len(payload) >= 1:
        event = payload[0]
        data = payload[1] if len(payload) > 1 else None
        return str(event), data
    return None, None


@dataclass
class WhalesConfig:
    tokens: Optional[list[str]] = None
    headless: bool = True
    homepage: str = "https://www.whales.market/"


class WhalesMarketClient(IngestClient):
    """Intercept WebSocket traffic from whales.market using Playwright."""

    def __init__(self, bus: EventBus[MarketEvent], config: WhalesConfig | None = None):
        super().__init__(name="whales-market")
        self.bus = bus
        self.config = config or WhalesConfig()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None

    async def run_once(self) -> None:
        if async_playwright is None:
            raise RuntimeError(
                "Playwright dependency not installed. Install extras 'playwright' to "
                "enable the Whales Market ingestor."
            )
        await self._ensure_browser()
        assert self._page is not None

        # Keep the page alive while we listen for websocket frames.
        while not self.stopped:
            await asyncio.sleep(1)

    async def _ensure_browser(self) -> None:
        if self._playwright:
            return

        assert async_playwright is not None  # for type checkers
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.config.headless)
        self._page = await self._browser.new_page()
        self._page.on("websocket", self._on_websocket)
        await self._page.goto(self.config.homepage, wait_until="domcontentloaded")

        if self.config.tokens:
            for token in self.config.tokens:
                await self._page.goto(f"https://www.whales.market/token/{token}")

    async def stop(self) -> None:
        await super().stop()
        if self._page:
            await self._page.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._page = None
        self._browser = None
        self._playwright = None

    def _on_websocket(self, websocket: WebSocket) -> None:
        if "socket.io" not in websocket.url:
            return

        logger.info("Capturing Whales Market websocket: %s", websocket.url)
        websocket.on("framereceived", self._frame_handler())

    def _frame_handler(self) -> Callable[[str], None]:
        def handler(message: str) -> None:
            asyncio.create_task(self._process_message(message))

        return handler

    async def _process_message(self, message: str) -> None:
        event, data = _parse_socketio_message(message)
        if event is None:
            return

        event_type = SOCKET_EVENT_MAP.get(event)
        if event_type is None:
            logger.debug("Unhandled Whales event: %s", event)
            return

        now_ms = int(time.time() * 1000)

        if isinstance(data, dict):
            await self._enqueue_dict(event_type, data, now_ms)
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    await self._enqueue_dict(event_type, item, now_ms)

    async def _enqueue_dict(self, event_type: EventType, payload: dict[str, Any], ts: int) -> None:
        token = str(payload.get("token") or payload.get("symbol") or payload.get("ticker") or "")
        if not token:
            return

        instrument = payload.get("pair") or f"{token}_USDT"
        best_bid = payload.get("bestBid") or payload.get("best_bid")
        best_ask = payload.get("bestAsk") or payload.get("best_ask")
        size = payload.get("size") or payload.get("amount") or payload.get("quantity")
        price = payload.get("price") or payload.get("last_price") or payload.get("mid")
        notional = None
        try:
            if price is not None and size is not None:
                notional = float(price) * float(size)
        except (ValueError, TypeError):
            notional = None

        event = MarketEvent(
            token=token.upper(),
            venue="WHALES",
            instrument=str(instrument),
            event_type=event_type,
            best_bid=float(best_bid) if best_bid is not None else None,
            best_ask=float(best_ask) if best_ask is not None else None,
            last_price=float(price) if price is not None else None,
            size=float(size) if size is not None else None,
            notional=notional,
            timestamp_ms=ts,
            listing_info=payload if event_type is EventType.LISTING else None,
            raw=payload,
        )
        await self.bus.publish(event)


__all__ = ["WhalesMarketClient", "WhalesConfig"]
