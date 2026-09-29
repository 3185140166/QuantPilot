# Backtesting

The current engine is a research MVP. It evaluates a signal on available history and enters on the next bar open, with fee and slippage parameters.

Run the test suite with:

```powershell
python -m pytest
```

Before trusting results, add historical data storage, funding costs, stop-loss/take-profit simulation, quantity and leverage rules, equity curves, and out-of-sample validation. A profitable backtest is not evidence that live trading is safe.\n