# QuantPilot

QuantPilot is a testnet-first Bybit quantitative trading research project. The
first milestone is a closed-bar EMA/RSI strategy with cost-aware backtesting
and safe market-data checks. Live trading is disabled by default.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Fill `.env` with a dedicated Bybit **Testnet** key. Keep `DRY_RUN=true` and
`TRADING_ENABLED=false` while developing. Never commit `.env`, `pat.env`, or
any file containing keys.

## Smoke checks

```powershell
python -m pytest
python -m quantpilot.cli connection
python -m quantpilot.cli signal
```

The CLI performs read-only market-data calls. `quantpilot.execution.ExecutionGate`
is available for dry-run order simulations; it only sends an order when both
`DRY_RUN=false` and `TRADING_ENABLED=true` are explicitly configured.
