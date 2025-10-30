"""Telegram integration for delivering spread alerts and handling commands."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Deque, Iterable, Mapping, MutableMapping

from dotenv import load_dotenv

try:  # pragma: no cover - optional dependency during tests
    from telegram import Update
    from telegram.ext import (  # type: ignore
        AIORateLimiter,
        Application,
        ApplicationBuilder,
        CommandHandler,
        ContextTypes,
    )
except Exception:  # pragma: no cover - telegram not available in some test envs
    Update = None  # type: ignore
    Application = None  # type: ignore
    ApplicationBuilder = None  # type: ignore
    AIORateLimiter = None  # type: ignore
    CommandHandler = None  # type: ignore
    ContextTypes = None  # type: ignore

from common import load_config
from rules import HedgedSpreadAlert, SpreadAlert

AlertLike = SpreadAlert | HedgedSpreadAlert


@dataclass
class TradeView:
    buy_label: str
    buy_key: str
    buy_price: float
    sell_label: str
    sell_key: str
    sell_price: float

logger = logging.getLogger(__name__)


@dataclass
class TelegramAlertState:
    """Mutable in-memory state shared between commands and alert delivery."""

    chat_id: int
    muted_tokens: set[str] = field(default_factory=set)
    recent_alerts: MutableMapping[str, Deque[AlertLike]] = field(
        default_factory=lambda: defaultdict(lambda: deque(maxlen=5))
    )
    last_alert_at: datetime | None = None

    def normalise(self, token: str) -> str:
        return token.upper()

    def record_alert(self, alert: AlertLike) -> None:
        token = self.normalise(alert.token)
        bucket = self.recent_alerts[token]
        bucket.append(alert)
        self.last_alert_at = datetime.fromtimestamp(
            alert.updated_at_ms / 1000, tz=timezone.utc
        )

    def is_muted(self, token: str) -> bool:
        return self.normalise(token) in self.muted_tokens

    def mute(self, token: str) -> bool:
        token_norm = self.normalise(token)
        if token_norm in self.muted_tokens:
            return False
        self.muted_tokens.add(token_norm)
        return True

    def unmute(self, token: str) -> bool:
        token_norm = self.normalise(token)
        if token_norm not in self.muted_tokens:
            return False
        self.muted_tokens.remove(token_norm)
        return True


def _resolve_trade(alert: AlertLike) -> TradeView:
    if isinstance(alert, SpreadAlert):
        return TradeView(
            buy_label=alert.buy_venue,
            buy_key=alert.buy_venue,
            buy_price=alert.buy_price,
            sell_label=alert.sell_venue,
            sell_key=alert.sell_venue,
            sell_price=alert.sell_price,
        )

    if alert.direction == "order_buy_perp_sell":
        buy_label = f"{alert.order_venue} (order)"
        buy_key = alert.order_venue
        buy_price = alert.order_price
        sell_label = f"{alert.perp_venue} (perp)"
        sell_key = alert.perp_venue
        sell_price = alert.perp_price
    else:
        buy_label = f"{alert.perp_venue} (perp)"
        buy_key = alert.perp_venue
        buy_price = alert.perp_price
        sell_label = f"{alert.order_venue} (order)"
        sell_key = alert.order_venue
        sell_price = alert.order_price

    return TradeView(
        buy_label=buy_label,
        buy_key=buy_key,
        buy_price=buy_price,
        sell_label=sell_label,
        sell_key=sell_key,
        sell_price=sell_price,
    )


def format_alert_message(
    alert: AlertLike,
    *,
    prefix: str = "",
    venue_links: Mapping[str, str] | None = None,
) -> str:
    """Build a human readable Telegram message for a spread alert."""

    timestamp = datetime.fromtimestamp(alert.updated_at_ms / 1000, tz=timezone.utc)
    header = f"{prefix.strip()} {alert.token}".strip()
    lines = [header]
    trade = _resolve_trade(alert)
    lines.append(
        f"Buy {trade.buy_label} @ {trade.buy_price:.6g} | "
        f"Sell {trade.sell_label} @ {trade.sell_price:.6g}"
    )
    lines.append(
        "Gross {gross:.2f}% | Net {net:.2f}% | Ref ≈ {notional:.2f} USDT".format(
            gross=alert.gross_spread_percent,
            net=alert.net_spread_percent,
            notional=alert.reference_notional,
        )
    )
    lines.append(f"Updated: {timestamp.isoformat()}")

    if venue_links:
        extras: list[str] = []
        buy_link = venue_links.get(trade.buy_key)
        sell_link = venue_links.get(trade.sell_key)
        if buy_link:
            extras.append(f"Buy venue: {buy_link}")
        if sell_link:
            extras.append(f"Sell venue: {sell_link}")
        if extras:
            lines.append(" | ".join(extras))

    return "\n".join(lines)


def build_status_message(state: TelegramAlertState) -> str:
    """Return the /status command body."""

    muted = ", ".join(sorted(state.muted_tokens)) or "None"
    recent = ", ".join(sorted(state.recent_alerts)) or "None"
    last_alert = state.last_alert_at.isoformat() if state.last_alert_at else "Never"
    return (
        "Premarket bot status:\n"
        f"- Muted tokens: {muted}\n"
        f"- Recent tokens: {recent}\n"
        f"- Last alert: {last_alert}"
    )


def build_last5_message(state: TelegramAlertState, token: str) -> str:
    """Return the /last5 response for ``token``."""

    token_norm = state.normalise(token)
    alerts = list(state.recent_alerts.get(token_norm, []))
    if not alerts:
        return f"No alerts recorded for {token_norm} yet."

    lines = [f"Last {len(alerts)} alerts for {token_norm}:"]
    for alert in reversed(alerts):
        timestamp = datetime.fromtimestamp(alert.updated_at_ms / 1000, tz=timezone.utc)
        trade = _resolve_trade(alert)
        lines.append(
            (
                f"{timestamp.isoformat()} | Net {alert.net_spread_percent:.2f}% "
                f"({trade.buy_label}→{trade.sell_label})"
            )
        )
    return "\n".join(lines)


class TelegramAlertBot:
    """Manage Telegram delivery of spread alerts with command support."""

    def __init__(
        self,
        token: str,
        state: TelegramAlertState,
        *,
        alert_prefix: str = "",
        dry_run: bool = False,
    ) -> None:
        self._token = token
        self.state = state
        self.alert_prefix = alert_prefix
        self.dry_run = dry_run
        self._application: Application | None = None
        self._send_lock = asyncio.Lock()

    async def start(self) -> None:
        if self.dry_run:
            logger.info("Telegram bot running in dry-run mode; not starting polling")
            return
        if ApplicationBuilder is None:
            raise RuntimeError("python-telegram-bot is required to start the bot")
        if self._application is not None:
            return

        self._application = (
            ApplicationBuilder()
            .token(self._token)
            .rate_limiter(AIORateLimiter())
            .build()
        )
        self._application.add_handler(CommandHandler("status", self._handle_status))
        self._application.add_handler(CommandHandler("last5", self._handle_last5))
        self._application.add_handler(CommandHandler("mute", self._handle_mute))
        self._application.add_handler(CommandHandler("unmute", self._handle_unmute))

        await self._application.initialize()
        await self._application.start()
        await self._application.updater.start_polling()
        logger.info("Telegram bot polling started")

    async def stop(self) -> None:
        if self._application is None:
            return
        await self._application.updater.stop()
        await self._application.stop()
        await self._application.shutdown()
        self._application = None
        logger.info("Telegram bot stopped")

    async def handle_alert(
        self,
        alert: AlertLike,
        venue_links: Mapping[str, str] | None = None,
    ) -> None:
        """Send or print a formatted alert depending on mode."""

        if self.state.is_muted(alert.token):
            logger.info("Skipping muted token %s", alert.token)
            return

        self.state.record_alert(alert)
        message = format_alert_message(
            alert,
            prefix=self.alert_prefix,
            venue_links=venue_links,
        )

        if self.dry_run:
            print(f"[DRY-RUN] {message}")
            return

        if self._application is None:
            raise RuntimeError("Telegram bot has not been started")

        async with self._send_lock:
            await self._application.bot.send_message(
                chat_id=self.state.chat_id,
                text=message,
            )

    async def _authorised_reply(self, update: Update, text: str) -> None:
        if update.effective_chat is None or update.effective_chat.id != self.state.chat_id:
            logger.debug("Ignoring command from unauthorised chat")
            return
        if self.dry_run:
            print(f"[DRY-RUN] {text}")
            return
        if self._application is None:
            raise RuntimeError("Telegram bot not started")
        await self._application.bot.send_message(chat_id=self.state.chat_id, text=text)

    async def _handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._authorised_reply(update, build_status_message(self.state))

    async def _handle_last5(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        token = context.args[0] if context.args else ""
        if not token:
            await self._authorised_reply(update, "Usage: /last5 <token>")
            return
        await self._authorised_reply(update, build_last5_message(self.state, token))

    async def _handle_mute(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        token = context.args[0] if context.args else ""
        if not token:
            await self._authorised_reply(update, "Usage: /mute <token>")
            return
        added = self.state.mute(token)
        message = (
            f"Muted {self.state.normalise(token)}"
            if added
            else f"{self.state.normalise(token)} already muted"
        )
        await self._authorised_reply(update, message)

    async def _handle_unmute(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        token = context.args[0] if context.args else ""
        if not token:
            await self._authorised_reply(update, "Usage: /unmute <token>")
            return
        removed = self.state.unmute(token)
        message = (
            f"Unmuted {self.state.normalise(token)}"
            if removed
            else f"{self.state.normalise(token)} was not muted"
        )
        await self._authorised_reply(update, message)


async def _run_cli(args: argparse.Namespace) -> None:
    load_dotenv()
    config = load_config(args.config)
    telegram_cfg = config.get("telegram", {})
    token = telegram_cfg.get("bot_token") or args.token
    chat_id = telegram_cfg.get("chat_id") or args.chat_id

    if not token:
        raise SystemExit("Telegram token missing. Provide via config or --token.")
    if not chat_id:
        raise SystemExit("Telegram chat id missing. Provide via config or --chat-id.")

    state = TelegramAlertState(chat_id=int(chat_id))
    bot = TelegramAlertBot(
        token=token,
        state=state,
        alert_prefix=telegram_cfg.get("alert_prefix", ""),
        dry_run=args.dry_run,
    )

    sample_alert = SpreadAlert(
        token="TNSR",
        buy_venue="MEXC",
        sell_venue="WHALES",
        buy_price=1.23,
        sell_price=1.29,
        gross_spread_percent=4.88,
        net_spread_percent=3.58,
        reference_notional=150.0,
        updated_at_ms=round(datetime.now(tz=timezone.utc).timestamp() * 1000),
    )

    hedged_sample = HedgedSpreadAlert(
        token="TNSR",
        order_venue="WHALES",
        perp_venue="BYBIT",
        direction="order_buy_perp_sell",
        order_price=1.18,
        perp_price=1.26,
        gross_spread_percent=6.5,
        net_spread_percent=4.3,
        reference_notional=120.0,
        updated_at_ms=sample_alert.updated_at_ms,
    )

    if args.dry_run:
        await bot.handle_alert(sample_alert)
        await bot.handle_alert(hedged_sample)
        print(build_status_message(state))
        print(build_last5_message(state, sample_alert.token))
        return

    await bot.start()

    queue: asyncio.Queue[AlertLike] = asyncio.Queue()

    async def _process_queue() -> None:
        while True:
            alert = await queue.get()
            try:
                await bot.handle_alert(alert)
            finally:
                queue.task_done()

    consumer = asyncio.create_task(_process_queue(), name="telegram-alert-consumer")

    stop_event = asyncio.Event()

    def _shutdown(*_: object) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:  # pragma: no cover - Windows fallback
            signal.signal(sig, lambda *_: _shutdown())

    print("Telegram bot running. Press Ctrl+C to exit.")
    await stop_event.wait()

    consumer.cancel()
    await bot.stop()


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Telegram alert bot runner")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to YAML config with telegram settings",
    )
    parser.add_argument("--token", help="Override Telegram bot token")
    parser.add_argument("--chat-id", help="Override Telegram chat id")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print example alerts without contacting Telegram",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> None:
    args = parse_args(argv)
    asyncio.run(_run_cli(args))


if __name__ == "__main__":
    main()


__all__ = [
    "TelegramAlertBot",
    "TelegramAlertState",
    "build_last5_message",
    "build_status_message",
    "format_alert_message",
]
