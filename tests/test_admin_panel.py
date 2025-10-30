from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")

from fastapi.testclient import TestClient

from admin.web import AdminPanel


def _write_config(path: Path) -> None:
    sample = {
        "venues": {
            "mexc": {"enabled": True, "symbols": ["TNSR_USDT"]},
            "whales_market": {"enabled": True, "symbols": ["TNSR"]},
        },
        "rules": {
            "spread": {
                "venue_pairs": [["MEXC", "WHALES"]],
                "min_spread_percent": 2.0,
                "min_notional_usdt": 100.0,
                "min_improvement_percent": 0.25,
                "debounce_seconds": 30.0,
                "slippage_bps": 10.0,
                "fee_bps": {"MEXC": 10.0, "WHALES": 20.0},
            },
            "hedged_spread": {
                "enabled": False,
                "pairs": [{"order": "WHALES", "perp": "BYBIT"}],
                "min_spread_percent": 1.0,
                "min_notional_usdt": 120.0,
                "min_improvement_percent": 0.2,
                "debounce_seconds": 40.0,
                "slippage_bps": 12.0,
                "fee_bps": {"WHALES": 20.0, "BYBIT": 7.5},
            },
        },
        "telegram": {
            "enabled": False,
            "bot_token": "${TELEGRAM_BOT_TOKEN}",
            "chat_id": "${TELEGRAM_CHAT_ID}",
            "alert_prefix": "[Premarket Alert]",
        },
    }
    path.write_text(yaml.safe_dump(sample, sort_keys=False))


def test_admin_panel_renders(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    panel = AdminPanel(config_path)

    with TestClient(panel.app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "Premarket Admin Panel" in response.text
    assert "MEXC" in response.text
    assert "Whales Market" in response.text


def test_admin_panel_updates_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    panel = AdminPanel(config_path)

    data = {
        "venue_mexc_enabled": "on",
        "venue_mexc_symbols": "ABC_USDT, DEF_USDT",
        "venue_whales_market_symbols": "",
        "min_spread_percent": "3.5",
        "min_notional_usdt": "200",
        "min_improvement_percent": "0.5",
        "debounce_seconds": "45",
        "slippage_bps": "8",
        "fee_MEXC": "12",
        "fee_WHALES": "22",
        "fee_BITGET": "5",
        "fee_BYBIT": "6",
        "fee_HYPERLIQUID": "7",
        "fee_BINANCE": "8",
        "venue_pairs": "MEXC,WHALES\nBYBIT,BINANCE",
        "hedged_enabled": "on",
        "hedged_min_spread_percent": "1.2",
        "hedged_min_notional_usdt": "180",
        "hedged_min_improvement_percent": "0.3",
        "hedged_debounce_seconds": "55",
        "hedged_slippage_bps": "18",
        "hedged_fee_MEXC": "11",
        "hedged_fee_WHALES": "21",
        "hedged_fee_BITGET": "4",
        "hedged_fee_BYBIT": "7",
        "hedged_fee_HYPERLIQUID": "6",
        "hedged_fee_BINANCE": "5",
        "hedged_pairs": "WHALES,BYBIT\nMEXC,BINANCE",
        "telegram_enabled": "on",
        "telegram_bot_token": "token",
        "telegram_chat_id": "123",
        "telegram_alert_prefix": "[Alert]",
    }

    with TestClient(panel.app) as client:
        response = client.post("/update", data=data, allow_redirects=False)
    assert response.status_code == 303

    updated = yaml.safe_load(config_path.read_text())
    assert updated["venues"]["mexc"]["enabled"] is True
    assert updated["venues"]["mexc"]["symbols"] == ["ABC_USDT", "DEF_USDT"]
    assert updated["venues"]["whales_market"]["symbols"] == []
    spread = updated["rules"]["spread"]
    assert spread["min_spread_percent"] == 3.5
    assert spread["min_notional_usdt"] == 200.0
    assert spread["min_improvement_percent"] == 0.5
    assert spread["debounce_seconds"] == 45.0
    assert spread["slippage_bps"] == 8.0
    assert spread["venue_pairs"] == [["MEXC", "WHALES"], ["BYBIT", "BINANCE"]]
    assert spread["fee_bps"]["MEXC"] == 12.0
    assert spread["fee_bps"]["WHALES"] == 22.0
    hedged = updated["rules"]["hedged_spread"]
    assert hedged["enabled"] is True
    assert hedged["min_spread_percent"] == 1.2
    assert hedged["min_notional_usdt"] == 180.0
    assert hedged["min_improvement_percent"] == 0.3
    assert hedged["debounce_seconds"] == 55.0
    assert hedged["slippage_bps"] == 18.0
    assert hedged["fee_bps"]["BYBIT"] == 7.0
    assert hedged["fee_bps"]["BINANCE"] == 5.0
    assert hedged["pairs"] == [
        {"order": "WHALES", "perp": "BYBIT"},
        {"order": "MEXC", "perp": "BINANCE"},
    ]
    assert updated["telegram"]["enabled"] is True
    assert updated["telegram"]["chat_id"] == "123"
    assert updated["telegram"]["alert_prefix"] == "[Alert]"


def test_restart_endpoint_invokes_callback(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    called = asyncio.Event()

    async def restart_cb() -> None:
        called.set()

    panel = AdminPanel(config_path, restart_callback=restart_cb)
    with TestClient(panel.app) as client:
        response = client.post("/restart")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert called.is_set()


def test_restart_endpoint_noop(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    panel = AdminPanel(config_path)

    with TestClient(panel.app) as client:
        response = client.post("/restart")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "noop"
    assert "not configured" in payload["message"].lower()


def test_health_endpoint_reports_status(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    panel = AdminPanel(config_path)

    with TestClient(panel.app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["config_exists"] is True
    assert payload["config_path"].endswith("config.yaml")
    assert payload["last_modified"]
