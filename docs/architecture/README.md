# Architecture

QuantPilot separates exchange access from research logic:

```text
exchange adapter -> normalized market data -> indicators -> strategy -> risk -> backtest or execution gate
```

## Code map

- `quantpilot/config.py`: typed environment settings and safety gates.
- `quantpilot/exchange/`: Bybit V5 and Binance USD-M REST adapters.
- `quantpilot/market_data.py`: common kline schema and closed-bar filtering.
- `quantpilot/indicators.py`: pure EMA and RSI calculations.
- `quantpilot/strategy.py`: pure EMA/RSI crossover signal generation.
- `quantpilot/risk.py`: Decimal step rounding and position sizing.
- `quantpilot/backtest.py`: next-bar-open, fee/slippage-aware research engine.
- `quantpilot/execution.py`: dry-run gate; live order submission remains opt-in.
- `quantpilot/dashboard.py`: read-only Streamlit view.

The backtest and dashboard must not create private API clients implicitly. Live execution should be added only behind an explicit exchange adapter and safety gate.\n