import asyncio
from datetime import datetime, timezone

from alerts.telegram import (
    TelegramAlertBot,
    TelegramAlertState,
    build_last5_message,
    build_status_message,
    format_alert_message,
)
from rules import HedgedSpreadAlert, SpreadAlert


def make_alert(token: str = "ABC", net: float = 2.5, ts: int | None = None) -> SpreadAlert:
    return SpreadAlert(
        token=token,
        buy_venue="MEXC",
        sell_venue="WHALES",
        buy_price=1.0,
        sell_price=1.05,
        gross_spread_percent=3.5,
        net_spread_percent=net,
        reference_notional=120.0,
        updated_at_ms=ts
        if ts is not None
        else round(datetime.now(tz=timezone.utc).timestamp() * 1000),
    )


def make_hedged_alert(
    token: str = "ABC",
    direction: str = "order_buy_perp_sell",
    ts: int | None = None,
) -> HedgedSpreadAlert:
    return HedgedSpreadAlert(
        token=token,
        order_venue="WHALES",
        perp_venue="BYBIT",
        direction=direction,
        order_price=1.1,
        perp_price=1.19,
        gross_spread_percent=5.0,
        net_spread_percent=3.0,
        reference_notional=140.0,
        updated_at_ms=ts
        if ts is not None
        else round(datetime.now(tz=timezone.utc).timestamp() * 1000),
    )


def test_format_alert_message_includes_prefix_and_values():
    alert = make_alert()
    message = format_alert_message(alert, prefix="[Alert]")
    assert "[Alert] ABC" in message
    assert "Net 2.50%" in message
    assert "WHALES" in message


def test_format_alert_handles_hedged_direction():
    alert = make_hedged_alert(direction="perp_buy_order_sell")
    message = format_alert_message(alert, prefix="[Hedged]")
    assert "[Hedged] ABC" in message
    assert "BYBIT (perp)" in message
    assert "WHALES (order)" in message


def test_status_and_last5_messages_reflect_state():
    state = TelegramAlertState(chat_id=1)
    status = build_status_message(state)
    assert "Muted tokens: None" in status

    alert = make_alert()
    hedged = make_hedged_alert(ts=alert.updated_at_ms + 1000)
    state.record_alert(alert)
    state.record_alert(hedged)
    status_after = build_status_message(state)
    assert "Recent tokens: ABC" in status_after

    last5 = build_last5_message(state, "abc")
    assert "ABC" in last5
    assert "Net 3.00%" in last5
    assert "(WHALES (order)→BYBIT (perp))" in last5

    muted_added = state.mute("abc")
    assert muted_added is True
    assert state.is_muted("ABC")
    muted_again = state.mute("ABC")
    assert muted_again is False
    unmuted = state.unmute("ABC")
    assert unmuted is True
    assert state.is_muted("ABC") is False


def test_dry_run_bot_records_alerts_and_prints(capsys):
    async def _run() -> None:
        state = TelegramAlertState(chat_id=1)
        bot = TelegramAlertBot(token="dummy", state=state, dry_run=True)
        alert = make_alert()
        hedged = make_hedged_alert()

        await bot.handle_alert(alert)
        await bot.handle_alert(hedged)
        captured = capsys.readouterr()
        assert "[DRY-RUN]" in captured.out
        assert state.recent_alerts["ABC"], "Alert should be recorded in state"

    asyncio.run(_run())


def test_dry_run_bot_honours_mute(capsys):
    async def _run() -> None:
        state = TelegramAlertState(chat_id=1)
        state.mute("ABC")
        bot = TelegramAlertBot(token="dummy", state=state, dry_run=True)
        alert = make_alert()

        await bot.handle_alert(alert)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert not state.recent_alerts["ABC"], "Muted tokens should not record alerts"

    asyncio.run(_run())
