"""FastAPI admin panel for editing configuration and restarting workers."""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, Mapping, MutableMapping

import yaml
from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import Environment, select_autoescape

logger = logging.getLogger(__name__)

VENUE_ORDER: tuple[tuple[str, str, str], ...] = (
    ("mexc", "MEXC", "MEXC"),
    ("whales_market", "Whales Market", "WHALES"),
    ("bitget", "Bitget", "BITGET"),
    ("bybit", "Bybit", "BYBIT"),
    ("hyperliquid", "Hyperliquid", "HYPERLIQUID"),
    ("binance", "Binance", "BINANCE"),
)

_FORM_TEMPLATE = """\
<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\" />
    <title>Premarket Admin Panel</title>
    <style>
      body { font-family: sans-serif; margin: 2rem auto; max-width: 960px; color: #222; }
      h1 { margin-bottom: 0.5rem; }
      section { margin-bottom: 2rem; padding-bottom: 1.5rem; border-bottom: 1px solid #ddd; }
      fieldset { border: 1px solid #ccc; padding: 1rem; margin-bottom: 1rem; }
      legend { font-weight: bold; }
      label { display: block; margin-top: 0.5rem; }
      input[type=text], input[type=number], textarea { width: 100%; padding: 0.4rem; box-sizing: border-box; }
      textarea { min-height: 120px; }
      .actions { display: flex; gap: 1rem; }
      .flash { background: #def1d8; padding: 0.75rem; border: 1px solid #9acd7e; margin-bottom: 1rem; }
      .error { background: #f6d5d5; border-color: #e68a8a; }
      button { padding: 0.6rem 1.2rem; font-size: 1rem; cursor: pointer; }
    </style>
  </head>
  <body>
    <h1>Premarket Admin Panel</h1>
    {% if message %}
      <div class=\"flash{{ ' error' if error else '' }}\">{{ message }}</div>
    {% endif %}
    <form method=\"post\" action=\"/update\">
      <section>
        <h2>Venues</h2>
        {% for venue in venues %}
          <fieldset>
            <legend>{{ venue.label }}</legend>
            <label>
              <input type=\"checkbox\" name=\"venue_{{ venue.key }}_enabled\" {% if venue.enabled %}checked{% endif %} />
              Enabled
            </label>
            <label>
              Symbols (comma separated):
              <input type=\"text\" name=\"venue_{{ venue.key }}_symbols\" value=\"{{ venue.symbols }}\" />
            </label>
          </fieldset>
        {% endfor %}
      </section>
      <section>
        <h2>Spread Rules</h2>
        <label>Min spread %
          <input type=\"number\" step=\"0.01\" name=\"min_spread_percent\" value=\"{{ spread.min_spread_percent }}\" />
        </label>
        <label>Min notional (USDT)
          <input type=\"number\" step=\"0.01\" name=\"min_notional_usdt\" value=\"{{ spread.min_notional_usdt }}\" />
        </label>
        <label>Min improvement %
          <input type=\"number\" step=\"0.01\" name=\"min_improvement_percent\" value=\"{{ spread.min_improvement_percent }}\" />
        </label>
        <label>Debounce seconds
          <input type=\"number\" step=\"0.1\" name=\"debounce_seconds\" value=\"{{ spread.debounce_seconds }}\" />
        </label>
        <label>Slippage (bps)
          <input type=\"number\" step=\"0.1\" name=\"slippage_bps\" value=\"{{ spread.slippage_bps }}\" />
        </label>
        <fieldset>
          <legend>Venue fees (bps)</legend>
          {% for fee in spread.fees %}
            <label>{{ fee.label }}
              <input type=\"number\" step=\"0.1\" name=\"fee_{{ fee.key }}\" value=\"{{ fee.value }}\" />
            </label>
          {% endfor %}
        </fieldset>
        <label>Venue pairs (one per line, comma separated e.g. MEXC,WHALES)
          <textarea name=\"venue_pairs\">{{ spread.venue_pairs_text }}</textarea>
        </label>
      </section>
      <section>
        <h2>Hedged Spread Rules</h2>
        <label>
          <input type=\"checkbox\" name=\"hedged_enabled\" {% if hedged.enabled %}checked{% endif %} />
          Enabled
        </label>
        <label>Min spread %
          <input type=\"number\" step=\"0.01\" name=\"hedged_min_spread_percent\" value=\"{{ hedged.min_spread_percent }}\" />
        </label>
        <label>Min notional (USDT)
          <input type=\"number\" step=\"0.01\" name=\"hedged_min_notional_usdt\" value=\"{{ hedged.min_notional_usdt }}\" />
        </label>
        <label>Min improvement %
          <input type=\"number\" step=\"0.01\" name=\"hedged_min_improvement_percent\" value=\"{{ hedged.min_improvement_percent }}\" />
        </label>
        <label>Debounce seconds
          <input type=\"number\" step=\"0.1\" name=\"hedged_debounce_seconds\" value=\"{{ hedged.debounce_seconds }}\" />
        </label>
        <label>Slippage (bps)
          <input type=\"number\" step=\"0.1\" name=\"hedged_slippage_bps\" value=\"{{ hedged.slippage_bps }}\" />
        </label>
        <fieldset>
          <legend>Venue fees (bps)</legend>
          {% for fee in hedged.fees %}
            <label>{{ fee.label }}
              <input type=\"number\" step=\"0.1\" name=\"hedged_fee_{{ fee.key }}\" value=\"{{ fee.value }}\" />
            </label>
          {% endfor %}
        </fieldset>
        <label>Order/Perp pairs (one per line, e.g. WHALES,BYBIT)
          <textarea name=\"hedged_pairs\">{{ hedged.pairs_text }}</textarea>
        </label>
      </section>
      <section>
        <h2>Telegram</h2>
        <label>
          <input type=\"checkbox\" name=\"telegram_enabled\" {% if telegram.enabled %}checked{% endif %} />
          Enabled
        </label>
        <label>Bot token
          <input type=\"text\" name=\"telegram_bot_token\" value=\"{{ telegram.bot_token }}\" />
        </label>
        <label>Chat ID
          <input type=\"text\" name=\"telegram_chat_id\" value=\"{{ telegram.chat_id }}\" />
        </label>
        <label>Alert prefix
          <input type=\"text\" name=\"telegram_alert_prefix\" value=\"{{ telegram.alert_prefix }}\" />
        </label>
      </section>
      <div class=\"actions\">
        <button type=\"submit\">Save changes</button>
      </div>
    </form>
    <form method=\"post\" action=\"/restart\" style=\"margin-top:1rem;\">
      <button type=\"submit\">Restart workers</button>
    </form>
  </body>
</html>
"""


def _ensure_mapping(data: Any) -> MutableMapping[str, Any]:
    if isinstance(data, MutableMapping):
        return data  # type: ignore[return-value]
    raise ValueError("Configuration root must be a mapping")


def _normalise_symbols(raw: str) -> list[str]:
    items = [item.strip() for item in raw.replace("\n", ",").split(",")]
    return [item for item in items if item]


def _parse_float(raw: str | None, default: float) -> float:
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


@dataclass
class AdminPanel:
    """Manage the admin FastAPI app and configuration persistence."""

    config_path: Path
    restart_callback: Callable[[], Awaitable[None]] | None = None

    def __post_init__(self) -> None:
        self.config_path = Path(self.config_path)
        self._lock = asyncio.Lock()
        self._template = Environment(autoescape=select_autoescape(["html", "xml"])).from_string(
            _FORM_TEMPLATE
        )
        self.app = FastAPI(title="Premarket Admin Panel")
        self.app.add_api_route("/", self.index, methods=["GET"], response_class=HTMLResponse)
        self.app.add_api_route("/update", self.update, methods=["POST"])
        self.app.add_api_route("/restart", self.restart, methods=["POST"])
        self.app.add_api_route("/health", self.health, methods=["GET"])

    async def _read_config(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            logger.warning("Config file %s missing; initialising empty config", self.config_path)
            return {}
        text = await asyncio.to_thread(self.config_path.read_text)
        data = yaml.safe_load(text) or {}
        return dict(_ensure_mapping(data))

    async def _write_config(self, config: Mapping[str, Any]) -> None:
        serialised = yaml.safe_dump(config, sort_keys=False)
        await asyncio.to_thread(self.config_path.write_text, serialised)

    async def index(self, request: Request) -> HTMLResponse:
        config = await self._read_config()
        message = None
        error = False
        if request.query_params.get("saved") == "1":
            message = "Configuration saved successfully."
        elif request.query_params.get("error"):
            message = request.query_params["error"]
            error = True

        html = self._template.render(
            message=message,
            error=error,
            venues=self._build_venues(config),
            spread=self._build_spread(config),
            hedged=self._build_hedged(config),
            telegram=self._build_telegram(config),
        )
        return HTMLResponse(html)

    async def update(self, request: Request) -> RedirectResponse:
        form = await request.form()
        async with self._lock:
            config = await self._read_config()
            venues = config.setdefault("venues", {})
            for key, label, _fee_key in VENUE_ORDER:
                venue_cfg = _ensure_mapping(venues.setdefault(key, {}))
                enabled_field = f"venue_{key}_enabled"
                symbols_field = f"venue_{key}_symbols"
                venue_cfg["enabled"] = enabled_field in form
                symbols_raw = form.get(symbols_field, "")
                venue_cfg["symbols"] = _normalise_symbols(str(symbols_raw))

            rules = _ensure_mapping(config.setdefault("rules", {}))
            spread = _ensure_mapping(rules.setdefault("spread", {}))
            spread["min_spread_percent"] = _parse_float(
                form.get("min_spread_percent"), float(spread.get("min_spread_percent", 0.0))
            )
            spread["min_notional_usdt"] = _parse_float(
                form.get("min_notional_usdt"), float(spread.get("min_notional_usdt", 0.0))
            )
            spread["min_improvement_percent"] = _parse_float(
                form.get("min_improvement_percent"), float(spread.get("min_improvement_percent", 0.0))
            )
            spread["debounce_seconds"] = _parse_float(
                form.get("debounce_seconds"), float(spread.get("debounce_seconds", 0.0))
            )
            spread["slippage_bps"] = _parse_float(
                form.get("slippage_bps"), float(spread.get("slippage_bps", 0.0))
            )

            fees = _ensure_mapping(spread.setdefault("fee_bps", {}))
            for key, _label, fee_key in VENUE_ORDER:
                current = float(fees.get(fee_key, 0.0))
                fees[fee_key] = _parse_float(form.get(f"fee_{fee_key}"), current)

            venue_pairs_raw = str(form.get("venue_pairs", "")).splitlines()
            pairs: list[list[str]] = []
            for line in venue_pairs_raw:
                parts = [part.strip().upper() for part in line.split(",") if part.strip()]
                if len(parts) == 2:
                    pairs.append(parts)
            spread["venue_pairs"] = pairs

            hedged = _ensure_mapping(rules.setdefault("hedged_spread", {}))
            hedged["enabled"] = "hedged_enabled" in form
            hedged["min_spread_percent"] = _parse_float(
                form.get("hedged_min_spread_percent"),
                float(hedged.get("min_spread_percent", 0.0)),
            )
            hedged["min_notional_usdt"] = _parse_float(
                form.get("hedged_min_notional_usdt"),
                float(hedged.get("min_notional_usdt", 0.0)),
            )
            hedged["min_improvement_percent"] = _parse_float(
                form.get("hedged_min_improvement_percent"),
                float(hedged.get("min_improvement_percent", 0.0)),
            )
            hedged["debounce_seconds"] = _parse_float(
                form.get("hedged_debounce_seconds"),
                float(hedged.get("debounce_seconds", 0.0)),
            )
            hedged["slippage_bps"] = _parse_float(
                form.get("hedged_slippage_bps"),
                float(hedged.get("slippage_bps", 0.0)),
            )

            hedged_fees = _ensure_mapping(hedged.setdefault("fee_bps", {}))
            for key, _label, fee_key in VENUE_ORDER:
                current = float(hedged_fees.get(fee_key, 0.0))
                hedged_fees[fee_key] = _parse_float(
                    form.get(f"hedged_fee_{fee_key}"), current
                )

            hedged_pairs_raw = str(form.get("hedged_pairs", "")).splitlines()
            hedged_pairs: list[dict[str, str]] = []
            for line in hedged_pairs_raw:
                parts = [part.strip().upper() for part in line.split(",") if part.strip()]
                if len(parts) >= 2:
                    hedged_pairs.append({"order": parts[0], "perp": parts[1]})
            hedged["pairs"] = hedged_pairs

            telegram_cfg = _ensure_mapping(config.setdefault("telegram", {}))
            telegram_cfg["enabled"] = "telegram_enabled" in form
            telegram_cfg["bot_token"] = str(form.get("telegram_bot_token", telegram_cfg.get("bot_token", "")))
            telegram_cfg["chat_id"] = str(form.get("telegram_chat_id", telegram_cfg.get("chat_id", "")))
            telegram_cfg["alert_prefix"] = str(
                form.get("telegram_alert_prefix", telegram_cfg.get("alert_prefix", ""))
            )

            await self._write_config(config)

        return RedirectResponse(url="/?saved=1", status_code=status.HTTP_303_SEE_OTHER)

    async def restart(self, request: Request) -> JSONResponse:
        if self.restart_callback is None:
            logger.info("Restart requested but no callback configured")
            return JSONResponse({"status": "noop", "message": "Restart callback not configured"})

        await self.restart_callback()
        return JSONResponse({"status": "ok", "message": "Restart triggered"})

    async def health(self) -> JSONResponse:
        exists = self.config_path.exists()
        last_modified = None
        if exists:
            stat_result = await asyncio.to_thread(self.config_path.stat)
            last_modified = datetime.fromtimestamp(stat_result.st_mtime, tz=timezone.utc).isoformat()

        return JSONResponse(
            {
                "status": "ok",
                "config_path": str(self.config_path),
                "config_exists": exists,
                "last_modified": last_modified,
            }
        )

    def _build_venues(self, config: Mapping[str, Any]) -> list[Dict[str, Any]]:
        venues_cfg = _ensure_mapping(config.get("venues", {}))
        items: list[Dict[str, Any]] = []
        for key, label, _fee_key in VENUE_ORDER:
            venue_cfg = _ensure_mapping(venues_cfg.get(key, {}))
            symbols = venue_cfg.get("symbols", [])
            if isinstance(symbols, Iterable) and not isinstance(symbols, (str, bytes)):
                symbols_text = ", ".join(str(item) for item in symbols)
            else:
                symbols_text = str(symbols)
            items.append(
                {
                    "key": key,
                    "label": label,
                    "enabled": bool(venue_cfg.get("enabled", False)),
                    "symbols": symbols_text,
                }
            )
        return items

    def _build_spread(self, config: Mapping[str, Any]) -> Dict[str, Any]:
        rules = _ensure_mapping(config.get("rules", {}))
        spread = _ensure_mapping(rules.get("spread", {}))
        fees_cfg = _ensure_mapping(spread.get("fee_bps", {}))
        pairs = spread.get("venue_pairs", [])
        venue_pairs_text = ""
        if isinstance(pairs, Iterable) and not isinstance(pairs, (str, bytes)):
            serialised: list[str] = []
            for pair in pairs:
                if isinstance(pair, Iterable) and not isinstance(pair, (str, bytes)):
                    parts = [str(part) for part in pair]
                    if len(parts) == 2:
                        serialised.append(",".join(parts))
            venue_pairs_text = "\n".join(serialised)
        fees = [
            {
                "key": fee_key,
                "label": label,
                "value": fees_cfg.get(fee_key, 0.0),
            }
            for _key, label, fee_key in VENUE_ORDER
        ]
        return {
            "min_spread_percent": spread.get("min_spread_percent", 0.0),
            "min_notional_usdt": spread.get("min_notional_usdt", 0.0),
            "min_improvement_percent": spread.get("min_improvement_percent", 0.0),
            "debounce_seconds": spread.get("debounce_seconds", 0.0),
            "slippage_bps": spread.get("slippage_bps", 0.0),
            "venue_pairs_text": venue_pairs_text,
            "fees": fees,
        }

    def _build_hedged(self, config: Mapping[str, Any]) -> Dict[str, Any]:
        rules = _ensure_mapping(config.get("rules", {}))
        hedged = _ensure_mapping(rules.get("hedged_spread", {}))
        fees_cfg = _ensure_mapping(hedged.get("fee_bps", {}))
        pairs = hedged.get("pairs", [])
        serialised: list[str] = []
        if isinstance(pairs, Iterable) and not isinstance(pairs, (str, bytes)):
            for pair in pairs:
                if isinstance(pair, Mapping):
                    order = str(pair.get("order", "")).upper()
                    perp = str(pair.get("perp", "")).upper()
                elif isinstance(pair, Iterable) and not isinstance(pair, (str, bytes)):
                    elements = [str(part).upper() for part in pair]
                    if len(elements) >= 2:
                        order, perp = elements[:2]
                    else:
                        continue
                else:
                    continue
                if order and perp:
                    serialised.append(f"{order},{perp}")
        fees = [
            {
                "key": fee_key,
                "label": label,
                "value": fees_cfg.get(fee_key, 0.0),
            }
            for _key, label, fee_key in VENUE_ORDER
        ]
        return {
            "enabled": bool(hedged.get("enabled", False)),
            "min_spread_percent": hedged.get("min_spread_percent", 0.0),
            "min_notional_usdt": hedged.get("min_notional_usdt", 0.0),
            "min_improvement_percent": hedged.get("min_improvement_percent", 0.0),
            "debounce_seconds": hedged.get("debounce_seconds", 0.0),
            "slippage_bps": hedged.get("slippage_bps", 0.0),
            "pairs_text": "\n".join(serialised),
            "fees": fees,
        }

    def _build_telegram(self, config: Mapping[str, Any]) -> Dict[str, Any]:
        telegram = _ensure_mapping(config.get("telegram", {}))
        return {
            "enabled": bool(telegram.get("enabled", False)),
            "bot_token": telegram.get("bot_token", ""),
            "chat_id": telegram.get("chat_id", ""),
            "alert_prefix": telegram.get("alert_prefix", ""),
        }


def create_admin_app(
    config_path: str | Path, restart_callback: Callable[[], Awaitable[None]] | None = None
) -> FastAPI:
    """Convenience helper to build the FastAPI app."""

    panel = AdminPanel(Path(config_path), restart_callback=restart_callback)
    return panel.app


def main() -> None:
    """CLI entry point for running the admin panel with Uvicorn."""

    parser = argparse.ArgumentParser(description="Run the admin web panel")
    parser.add_argument("--config", default="config.yaml", help="Path to config YAML file")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind the HTTP server")
    parser.add_argument("--port", type=int, default=8000, help="Port for the HTTP server")
    args = parser.parse_args()

    panel = AdminPanel(Path(args.config))
    import uvicorn

    uvicorn.run(panel.app, host=args.host, port=args.port)


if __name__ == "__main__":  # pragma: no cover - CLI execution
    main()
