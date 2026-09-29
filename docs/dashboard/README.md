# Dashboard

Install the project and start the local read-only research page:

```powershell
python -m pip install -e .
streamlit run quantpilot/dashboard.py
```

The page reads recent market data, displays the latest closed-bar price and signal, and shows a small backtest summary. It has no order controls and does not change `DRY_RUN` or `TRADING_ENABLED`.\n