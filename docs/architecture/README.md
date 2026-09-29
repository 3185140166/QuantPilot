# 系统架构

QuantPilot 将交易所访问和研究逻辑分开：

```text
交易所适配器 -> 标准化行情 -> 指标 -> 策略 -> 风控 -> 回测或执行门控
```

## 代码对应关系

- `quantpilot/config.py`：环境配置和安全开关。
- `quantpilot/exchange/`：Bybit V5 和 Binance USD-M REST 适配器。
- `quantpilot/market_data.py`：统一 K 线结构和已收盘 K 线过滤。
- `quantpilot/indicators.py`：EMA、RSI 等纯指标计算。
- `quantpilot/strategy.py`：EMA/RSI 交叉信号。
- `quantpilot/risk.py`：Decimal 数量步长处理和仓位计算。
- `quantpilot/backtest.py`：考虑手续费和滑点的基础回测引擎。
- `quantpilot/execution.py`：默认阻止真实下单的执行门控。
- `quantpilot/dashboard.py`：只读 Streamlit 页面。

回测和仪表盘不能隐式创建私有 API 客户端。真实执行必须经过明确的交易所适配器和安全开关。
