"""Simple demo CLI for streaming ingest events to stdout."""

from __future__ import annotations

import argparse
import asyncio
import signal

from common import EventBus, MarketEvent

from .binance import BinanceFuturesTickerClient
from .bybit import BybitTickerClient
from .hyperliquid import HyperliquidTickerClient
from .mexc import MexcBookTickerClient, MexcListingPoller
from .whales import WhalesConfig, WhalesMarketClient


async def _consume(queue: asyncio.Queue[MarketEvent]) -> None:
    while True:
        event = await queue.get()
        print(event.json())


async def run_demo(args: argparse.Namespace) -> None:
    bus: EventBus[MarketEvent] = EventBus()
    demo_queue = bus.subscribe()
    clients = []

    if args.mexc_symbol:
        clients.append(MexcBookTickerClient(bus=bus, symbols=[args.mexc_symbol]))
    if args.mexc_listings:
        clients.append(MexcListingPoller(bus=bus, poll_interval=args.poll_interval))
    if args.whales:
        config = WhalesConfig(tokens=args.whales_tokens or None, headless=not args.debug)
        clients.append(WhalesMarketClient(bus=bus, config=config))

    if args.bybit_symbol:
        clients.append(BybitTickerClient(bus=bus, symbols=[args.bybit_symbol]))
    if args.hyperliquid_symbol:
        clients.append(
            HyperliquidTickerClient(bus=bus, symbols=[args.hyperliquid_symbol])
        )
    if args.binance_symbol:
        clients.append(
            BinanceFuturesTickerClient(bus=bus, symbols=[args.binance_symbol])
        )

    if not clients:
        raise SystemExit(
            "No ingest clients enabled. Use --mexc-symbol, --whales, or a perp flag."
        )
    if not clients:
        raise SystemExit("No ingest clients enabled. Use --mexc-symbol or --whales.")

    for client in clients:
        await client.start()

    consumer_task = asyncio.create_task(_consume(demo_queue), name="demo-consumer")

    stop_event = asyncio.Event()

    def _signal_handler(*_: object) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    await stop_event.wait()

    consumer_task.cancel()
    for client in clients:
        await client.stop()

    bus.unsubscribe(demo_queue)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Demo ingest stream printer")
    parser.add_argument(
        "--mexc-symbol",
        help="Subscribe to a MEXC spot symbol (e.g. TNSR_USDT)",
    )
    parser.add_argument(
        "--mexc-listings",
        action="store_true",
        help="Enable the MEXC listings poller",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=60.0,
        help="Listing poll interval in seconds",
    )
    parser.add_argument(
        "--whales",
        action="store_true",
        help="Enable Whales Market websocket capture",
    )
    parser.add_argument(
        "--whales-tokens",
        nargs="*",
        help="Optional list of whales.market token tickers to open directly",
    )
    parser.add_argument(
        "--bybit-symbol",
        help="Subscribe to a Bybit perp symbol (e.g. TNSRUSDT)",
    )
    parser.add_argument(
        "--hyperliquid-symbol",
        help="Subscribe to a Hyperliquid coin (e.g. TNSR)",
    )
    parser.add_argument(
        "--binance-symbol",
        help="Subscribe to a Binance futures symbol (e.g. TNSRUSDT)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Disable headless mode for troubleshooting",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    asyncio.run(run_demo(args))


if __name__ == "__main__":
    main()
