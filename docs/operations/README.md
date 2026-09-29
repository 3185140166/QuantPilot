# Operations

Safe read-only checks:

```powershell
python -m quantpilot.cli connection --env-file .env
python -m quantpilot.cli signal --env-file .env
python -m quantpilot.cli balance --env-file .env
```

`connection` and `signal` use public market data. `balance` signs a private read-only request. Keep these settings while developing:

```env
DRY_RUN=true
TRADING_ENABLED=false
```

A timeout or proxy error is a network problem. A JSON exchange error or HTTP 401 is an API credential, permission, environment, or clock problem.\n