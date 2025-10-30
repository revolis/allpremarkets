# Crypto Premarket Alert Bot

Async-first toolkit for monitoring premarket crypto venues and forwarding alerts
to Telegram.

## Quickstart

### Option A: Poetry
1. Install dependencies:
   ```bash
   poetry install
   ```
2. Try the ingest demo (requires network access):
   ```bash
   poetry run python -m ingest.demo --mexc-symbol TNSR_USDT --mexc-listings
   ```
   To watch a perpetual venue alongside spot/orders you can add flags such as:
   ```bash
   poetry run python -m ingest.demo --whales --bybit-symbol TNSRUSDT
   ```

### Option B: Pip + virtual environment
1. Install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. Install the package in editable mode and run the ingest demo via Python:
   ```bash
   pip install -e .
   python -m ingest.demo --mexc-symbol TNSR_USDT --mexc-listings
   ```
   Perpetual feeds are available with:
   ```bash
   python -m ingest.demo --binance-symbol TNSRUSDT --hyperliquid-symbol TNSR
   ```

To capture Whales Market data you must install the optional Playwright extras:

```bash
poetry run playwright install chromium
```

or with pip environments:

```bash
playwright install chromium
```

Then run the demo with Whales Market capture enabled:

   ```bash
   python -m ingest.demo --whales --whales-tokens TNSR
   ```

### Running the full bot runtime

After populating `.env` and `config.yaml`, start the orchestrator:

```bash
python -m app.runtime --config config.yaml
```

Append `--telegram-dry-run` to verify formatting without contacting Telegram.

### Running unit tests

The repository ships with lightweight unit tests that validate the ingest
normalisation logic **and** the spread engine math/debounce rules. After
installing dependencies you can execute:

```bash
pytest
```

Configuration lives in `config.yaml`. Secrets such as the Telegram token should
reside in a local `.env` (see `.env.example`). The `venues` section now supports
symbols for Bybit, Hyperliquid, and Binance perpetual feeds. The
`rules.hedged_spread` block controls cross-venue hedged alerts – enable it and
list `{order, perp}` pairs (e.g. `WHALES` vs `BYBIT`) when you are ready to
compare OTC/spot pricing with futures quotes.

### Telegram alert dry-run

After configuring your `.env` (or using the provided example token) you can
preview the Telegram messaging without contacting Telegram:

```bash
python -m alerts.telegram --dry-run
```

The command prints a sample alert plus the responses for `/status` and
`/last5` so you can confirm formatting before enabling the bot.

### Admin web panel

Phase 5 introduces a lightweight FastAPI admin panel for editing
`config.yaml` from a browser and requesting worker restarts. Launch it via:

```bash
python -m admin.web --config config.yaml --port 8080
```

Then visit `http://localhost:8080` to toggle venues, adjust spread thresholds,
configure hedged spread pairs/fees, and trigger the restart endpoint after
saving changes. The UI writes the updated configuration back to disk
immediately.

### VPS bootstrap & operations

* **Provision dependencies:** run `sudo ./setup_vps.sh` on a fresh Ubuntu host.
  Environment variables let you customise the install (e.g. `APP_DIR` or
  `BOT_USER`). The script copies the repository, creates a Python virtualenv,
  installs requirements (including optional Playwright support), and prepares
  the log directory.
* **Systemd services:** copy the unit files from `deploy/systemd/` to
  `/etc/systemd/system/`, adjusting the paths if you changed `APP_DIR` or the
  service user. Enable them via:

  ```bash
  sudo systemctl daemon-reload
  sudo systemctl enable --now premarket-bot.service
  sudo systemctl enable --now premarket-admin.service
  ```

  The bot service launches `python -m app.runtime`, while the admin panel keeps
  the FastAPI UI reachable on port 8080.
* **Health check:** FastAPI now exposes `GET /health` returning JSON with the
  config file path and last modification timestamp. Point your VPS monitoring
  or load balancer at `http://<host>:8080/health` for lightweight liveness
  checks.
* **Log rotation:** import the example logrotate stanza from
  `deploy/logrotate/premarket-bot` into `/etc/logrotate.d/` to rotate
  `/opt/crypto-premarket/logs/*.log` daily with compression and seven retained
  archives.
* **Backups:** snapshot `config.yaml`, `.env`, and the `logs/` directory. A
  simple tarball created by `tar czf premarket-backup.tgz config.yaml .env
  logs/` is sufficient; restore by extracting into the application directory and
  restarting the systemd units.

