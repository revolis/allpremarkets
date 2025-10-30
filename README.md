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

### Running unit tests

The repository ships with lightweight unit tests that validate the ingest
normalisation logic **and** the spread engine math/debounce rules. After
installing dependencies you can execute:

```bash
pytest
```

Configuration lives in `config.yaml`. Secrets such as the Telegram token should
reside in a local `.env` (see `.env.example`).
