# Crypto Premarket Alert Bot

Async-first toolkit for monitoring premarket crypto venues and forwarding alerts
to Telegram.

## Quickstart

### Option A: Poetry
1. Install dependencies:
   ```bash
   poetry install
   ```
2. Run the placeholder entry point:
   ```bash
   poetry run alert-bot
   ```

### Option B: Pip + virtual environment
1. Install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. Run the placeholder entry point:
   ```bash
   python -m common.logging
   ```

Configuration lives in `config.yaml`. Secrets such as the Telegram token should
reside in a local `.env` (see `.env.example`).
