# 交易所配置

## Bybit

设置 `EXCHANGE=bybit`。`BYBIT_TESTNET=true` 时使用 Bybit Testnet key 和 `api-testnet.bybit.com`；`BYBIT_TESTNET=false` 时使用主网 endpoint 和主网 key。

```env
EXCHANGE=bybit
BYBIT_TESTNET=true
BYBIT_API_KEY=
BYBIT_API_SECRET=
```

## Binance USD-M Futures

设置 `EXCHANGE=binance`。`BINANCE_TESTNET=true` 时使用 Binance USD-M Futures Testnet 和 `testnet.binancefuture.com`。

```env
EXCHANGE=binance
BINANCE_TESTNET=true
BINANCE_API_KEY=
BINANCE_API_SECRET=
```

不同交易所、不同环境的 key 不能混用。`connection` 只验证公开行情；`balance` 才会验证签名的私有只读请求。
