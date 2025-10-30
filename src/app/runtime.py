"""Async runtime harness that ties ingest, rules, and alert delivery together."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from contextlib import suppress
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping

from dotenv import load_dotenv

from alerts import TelegramAlertBot, TelegramAlertState
from common import EventBus, MarketEvent, load_config, setup_logging
from ingest import (
    BinanceFuturesTickerClient,
    BybitTickerClient,
    HyperliquidTickerClient,
    MexcBookTickerClient,
    MexcListingPoller,
    WhalesConfig,
    WhalesMarketClient,
)
from rules import (
    HedgedPair,
    HedgedSpreadAlert,
    HedgedSpreadConfig,
    HedgedSpreadEngine,
    SpreadAlert,
    SpreadConfig,
    SpreadEngine,
    VenuePair,
)

AlertItem = SpreadAlert | HedgedSpreadAlert

logger = logging.getLogger(__name__)

# Basic venue URLs used when formatting Telegram messages.
VENUE_LINKS: dict[str, str] = {
    "MEXC": "https://www.mexc.com/exchange",
    "WHALES": "https://www.whales.market/",
    "BYBIT": "https://www.bybit.com/trade/usdt",
    "HYPERLIQUID": "https://app.hyperliquid.xyz/",
    "BINANCE": "https://www.binance.com/en/futures",
}


def _as_mapping(value: object) -> MutableMapping[str, Any]:
    if isinstance(value, MutableMapping):
        return value
    return {}


def _as_list(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        return list(value)
    if value is None:
        return []
    return [value]


def _as_float(value: object, default: float) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _build_spread_config(config: Mapping[str, Any]) -> SpreadConfig | None:
    rules = _as_mapping(config.get("rules"))
    spread_cfg = _as_mapping(rules.get("spread"))
    pairs_raw = _as_list(spread_cfg.get("venue_pairs"))
    venue_pairs: list[VenuePair] = []
    for raw in pairs_raw:
        if isinstance(raw, Iterable) and not isinstance(raw, (str, bytes)):
            items = [str(item).upper() for item in raw if str(item).strip()]
            if len(items) >= 2:
                venue_pairs.append(VenuePair(tuple(items[:2])))
    if not venue_pairs:
        return None

    fee_raw = _as_mapping(spread_cfg.get("fee_bps"))
    fee_bps = {str(key).upper(): _as_float(value, 0.0) for key, value in fee_raw.items()}

    return SpreadConfig(
        venue_pairs=tuple(venue_pairs),
        min_spread_percent=_as_float(spread_cfg.get("min_spread_percent"), 0.0),
        min_notional_usdt=_as_float(spread_cfg.get("min_notional_usdt"), 0.0),
        min_improvement_percent=_as_float(
            spread_cfg.get("min_improvement_percent"), 0.0
        ),
        debounce_seconds=_as_float(spread_cfg.get("debounce_seconds"), 0.0),
        slippage_bps=_as_float(spread_cfg.get("slippage_bps"), 0.0),
        fee_bps=fee_bps,
    )


def _build_hedged_config(config: Mapping[str, Any]) -> tuple[bool, HedgedSpreadConfig | None]:
    rules = _as_mapping(config.get("rules"))
    hedged_cfg = _as_mapping(rules.get("hedged_spread"))
    enabled = bool(hedged_cfg.get("enabled"))
    if not enabled:
        return False, None

    pairs_raw = _as_list(hedged_cfg.get("pairs"))
    pairs: list[HedgedPair] = []
    for raw in pairs_raw:
        if isinstance(raw, Mapping):
            order = str(raw.get("order", "")).upper()
            perp = str(raw.get("perp", "")).upper()
            if order and perp:
                pairs.append(HedgedPair(order, perp))
        elif isinstance(raw, Iterable) and not isinstance(raw, (str, bytes)):
            items = [str(item).upper() for item in raw if str(item).strip()]
            if len(items) >= 2:
                pairs.append(HedgedPair(items[0], items[1]))
    if not pairs:
        logger.warning("Hedged spreads enabled but no valid pairs supplied; disabling")
        return False, None

    fee_raw = _as_mapping(hedged_cfg.get("fee_bps"))
    fee_bps = {str(key).upper(): _as_float(value, 0.0) for key, value in fee_raw.items()}

    config_obj = HedgedSpreadConfig(
        pairs=tuple(pairs),
        min_spread_percent=_as_float(hedged_cfg.get("min_spread_percent"), 0.0),
        min_notional_usdt=_as_float(hedged_cfg.get("min_notional_usdt"), 0.0),
        min_improvement_percent=_as_float(
            hedged_cfg.get("min_improvement_percent"), 0.0
        ),
        debounce_seconds=_as_float(hedged_cfg.get("debounce_seconds"), 0.0),
        slippage_bps=_as_float(hedged_cfg.get("slippage_bps"), 0.0),
        fee_bps=fee_bps,
    )
    return True, config_obj


def _log_level_from_config(config: Mapping[str, Any]) -> tuple[int, Path | None]:
    logging_cfg = _as_mapping(config.get("logging"))
    level_name = str(logging_cfg.get("level", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    file_value = logging_cfg.get("file")
    log_file = Path(file_value) if isinstance(file_value, (str, Path)) else None
    return level, log_file


def _build_telegram_bot(
    config: Mapping[str, Any], *, telegram_dry_run: bool
) -> TelegramAlertBot | None:
    telegram_cfg = _as_mapping(config.get("telegram"))
    enabled = bool(telegram_cfg.get("enabled")) or telegram_dry_run

    token = str(telegram_cfg.get("bot_token", ""))
    chat_id_raw = telegram_cfg.get("chat_id")
    chat_id = str(chat_id_raw) if chat_id_raw is not None else ""
    prefix = str(telegram_cfg.get("alert_prefix", ""))

    if not enabled:
        logger.info("Telegram delivery disabled in configuration")
        return None
    if not token:
        logger.warning("Telegram enabled but bot token missing; disabling Telegram alerts")
        return None
    if not chat_id:
        logger.warning("Telegram enabled but chat id missing; disabling Telegram alerts")
        return None

    try:
        chat_id_int = int(chat_id)
    except ValueError:
        logger.warning("Invalid Telegram chat id %s; disabling Telegram alerts", chat_id)
        return None

    state = TelegramAlertState(chat_id=chat_id_int)
    return TelegramAlertBot(
        token=token,
        state=state,
        alert_prefix=prefix,
        dry_run=telegram_dry_run,
    )


class BotRuntime:
    """Coordinate ingest clients, rule engines, and alert delivery."""

    def __init__(self, config_path: Path, telegram_dry_run: bool = False) -> None:
        self.config_path = config_path
        self.telegram_dry_run = telegram_dry_run
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        load_dotenv()
        config = load_config(self.config_path)

        level, log_file = _log_level_from_config(config)
        setup_logging(level, log_file)
        logger.info("Starting bot runtime", extra={"config": str(self.config_path)})

        bus: EventBus[MarketEvent] = EventBus()
        clients = self._build_ingestors(config, bus)

        alerts_queue: asyncio.Queue[AlertItem] = asyncio.Queue()

        async def _queue_spread(alert: SpreadAlert) -> None:
            await alerts_queue.put(alert)

        async def _queue_hedged(alert: HedgedSpreadAlert) -> None:
            await alerts_queue.put(alert)

        spread_config = _build_spread_config(config)
        spread_engine: SpreadEngine | None = None
        if spread_config is not None:
            spread_engine = SpreadEngine(
                bus=bus,
                config=spread_config,
                alert_callback=_queue_spread,
            )
            spread_task = spread_engine.start()
        else:
            spread_task = None
            logger.warning("No spread venue pairs configured; spread alerts disabled")

        hedged_enabled, hedged_config = _build_hedged_config(config)
        hedged_engine: HedgedSpreadEngine | None = None
        if hedged_enabled and hedged_config is not None:
            hedged_engine = HedgedSpreadEngine(
                bus=bus,
                config=hedged_config,
                alert_callback=_queue_hedged,
            )
            hedged_task = hedged_engine.start()
        else:
            hedged_task = None
            if hedged_enabled:
                logger.warning("Hedged spread configuration missing pairs; alerts disabled")

        telegram_bot = _build_telegram_bot(config, telegram_dry_run=self.telegram_dry_run)
        if telegram_bot:
            await telegram_bot.start()

        if not clients:
            logger.warning(
                "No ingest clients enabled; runtime will idle until configuration changes"
            )

        for client in clients:
            await client.start()

        dispatcher_task = asyncio.create_task(
            self._dispatch_alerts(alerts_queue, telegram_bot),
            name="alert-dispatcher",
        )

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, self._stop_event.set)

        try:
            await self._stop_event.wait()
        finally:
            dispatcher_task.cancel()
            with suppress(asyncio.CancelledError):
                await dispatcher_task

            if hedged_engine and hedged_task:
                hedged_task.cancel()
                await hedged_engine.stop()
            if spread_engine and spread_task:
                spread_task.cancel()
                await spread_engine.stop()

            for client in clients:
                await client.stop()

            if telegram_bot:
                await telegram_bot.stop()

            await bus.close()
            logger.info("Bot runtime stopped")

    async def _dispatch_alerts(
        self, queue: asyncio.Queue[AlertItem], bot: TelegramAlertBot | None
    ) -> None:
        try:
            while True:
                alert = await queue.get()
                try:
                    if bot:
                        await bot.handle_alert(alert, venue_links=VENUE_LINKS)
                    else:
                        logger.info(
                            "Alert ready", extra={"token": alert.token, "net": alert.net_spread_percent}
                        )
                finally:
                    queue.task_done()
        except asyncio.CancelledError:  # pragma: no cover - cooperative shutdown
            raise

    def stop(self) -> None:
        self._stop_event.set()

    def _build_ingestors(
        self, config: Mapping[str, Any], bus: EventBus[MarketEvent]
    ) -> list[Any]:
        venues_cfg = _as_mapping(config.get("venues"))
        app_cfg = _as_mapping(config.get("app"))
        clients: list[Any] = []

        mexc_cfg = _as_mapping(venues_cfg.get("mexc"))
        if mexc_cfg.get("enabled"):
            symbols = [str(sym) for sym in _as_list(mexc_cfg.get("symbols")) if str(sym)]
            clients.append(MexcBookTickerClient(bus=bus, symbols=symbols))
            poll_interval = _as_float(app_cfg.get("venue_poll_interval_seconds"), 60.0)
            if poll_interval > 0:
                clients.append(MexcListingPoller(bus=bus, poll_interval=poll_interval))

        whales_cfg = _as_mapping(venues_cfg.get("whales_market"))
        if whales_cfg.get("enabled"):
            tokens = [str(token) for token in _as_list(whales_cfg.get("symbols")) if str(token)]
            whales_config = WhalesConfig(tokens=tokens or None)
            clients.append(WhalesMarketClient(bus=bus, config=whales_config))

        bitget_cfg = _as_mapping(venues_cfg.get("bitget"))
        if bitget_cfg.get("enabled"):
            logger.warning(
                "Bitget venue enabled but dedicated ingest client not implemented yet"
            )

        bybit_cfg = _as_mapping(venues_cfg.get("bybit"))
        if bybit_cfg.get("enabled"):
            symbols = [str(sym) for sym in _as_list(bybit_cfg.get("symbols")) if str(sym)]
            clients.append(BybitTickerClient(bus=bus, symbols=symbols))

        hyper_cfg = _as_mapping(venues_cfg.get("hyperliquid"))
        if hyper_cfg.get("enabled"):
            symbols = [str(sym) for sym in _as_list(hyper_cfg.get("symbols")) if str(sym)]
            clients.append(HyperliquidTickerClient(bus=bus, symbols=symbols))

        binance_cfg = _as_mapping(venues_cfg.get("binance"))
        if binance_cfg.get("enabled"):
            symbols = [str(sym) for sym in _as_list(binance_cfg.get("symbols")) if str(sym)]
            clients.append(BinanceFuturesTickerClient(bus=bus, symbols=symbols))

        return clients


async def run_bot(config_path: str, telegram_dry_run: bool = False) -> None:
    runtime = BotRuntime(Path(config_path), telegram_dry_run=telegram_dry_run)
    await runtime.run()


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Crypto Premarket Alert Bot")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to YAML configuration file",
    )
    parser.add_argument(
        "--telegram-dry-run",
        action="store_true",
        help="Do not contact Telegram; print messages locally",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> None:
    args = parse_args(argv)
    asyncio.run(run_bot(args.config, telegram_dry_run=args.telegram_dry_run))


if __name__ == "__main__":  # pragma: no cover - CLI execution path
    main()
