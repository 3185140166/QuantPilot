# Strategy

The current strategy is a deliberately small EMA/RSI crossover:

- EMA fast: 20
- EMA slow: 60
- RSI: 14
- LONG on a bullish EMA crossover with RSI below 70.
- SHORT on a bearish EMA crossover with RSI above 30.
- HOLD otherwise.

The newest REST kline can still be forming. The strategy receives closed bars only, so signal evaluation does not use the active candle. A signal is not an order; risk and execution checks must run after it.\n