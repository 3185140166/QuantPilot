# Exchanges

## Bybit

Use `EXCHANGE=bybit`. `BYBIT_TESTNET=true` requires a Bybit Testnet key and uses `api-testnet.bybit.com`. `BYBIT_TESTNET=false` uses the mainnet endpoint and a mainnet key.

```env
EXCHANGE=bybit
BYBIT_TESTNET=true
BYBIT_API_KEY=
BYBIT_API_SECRET=
```

## Binance USD-M Futures

Use `EXCHANGE=binance`. `BINANCE_TESTNET=true` uses `testnet.binancefuture.com` and requires Binance Futures Testnet credentials.

```env
EXCHANGE=binance
BINANCE_TESTNET=true
BINANCE_API_KEY=
BINANCE_API_SECRET=
```

Credentials are environment-specific and cannot be reused across exchanges or between Testnet and mainnet. Public `connection` checks do not prove private credentials; use `balance` for the signed read-only check.\n