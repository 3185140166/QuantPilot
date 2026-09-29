# QuantPilot

QuantPilot is a testnet-first, multi-exchange quantitative trading research project. The first milestone is a closed-bar EMA/RSI strategy with cost-aware backtesting and safe market-data checks. Live trading is disabled by default.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Fill `.env` with a dedicated Bybit or Binance **Testnet** key. Keep `DRY_RUN=true` and `TRADING_ENABLED=false` while developing. Never commit `.env`, `pat.env`, or any file containing keys.

To try Binance USD-M Futures Testnet, set `EXCHANGE=binance` and fill `BINANCE_API_KEY` and `BINANCE_API_SECRET` with keys created at `testnet.binancefuture.com`. Binance and Bybit credentials are separate.

## Smoke checks

```powershell
python -m pytest
python -m quantpilot.cli connection
python -m quantpilot.cli signal
python -m quantpilot.cli balance
```

`connection` and `signal` read public market data. `balance` checks the private API credentials without placing an order. `quantpilot.execution.ExecutionGate` is available for dry-run order simulations; it only sends an order when both `DRY_RUN=false` and `TRADING_ENABLED=true` are explicitly configured.
