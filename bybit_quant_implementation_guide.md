# Bybit 量化交易机器人：桌面端实现方案（Python + Testnet）

> 目标：在 Windows 桌面端从零实现一个可运行、可回测、可模拟交易、可扩展的 Bybit 量化交易项目。  
> 第一阶段只使用 **Bybit Testnet**，不直接接入真实资金。  
> 技术栈：Python 3.10+、Bybit V5 API、官方 `pybit` SDK、pandas、numpy、matplotlib、python-dotenv。

---

## 1. 项目最终要实现什么

第一版项目只做一件事：

**对 BTCUSDT 永续合约进行自动化策略交易。**

完整链路：

```text
Bybit 行情
   ↓
K线获取
   ↓
技术指标计算
   ↓
策略产生信号
   ↓
风控模块校验
   ↓
仓位计算
   ↓
Testnet 下单
   ↓
持仓监控
   ↓
止损 / 止盈 / 平仓
   ↓
交易日志
   ↓
收益统计与回测
```

第一版不要追求复杂策略，重点是把量化交易系统的工程闭环做完整。

---

# 2. 技术路线

建议使用：

```text
Python 3.10+
Bybit V5 API
pybit
pandas
numpy
matplotlib
python-dotenv
pydantic
loguru
```

官方 Python SDK：

```bash
pip install pybit
```

官方 `pybit` 支持：

```python
from pybit.unified_trading import HTTP
```

创建 Testnet Session：

```python
session = HTTP(
    testnet=True,
    api_key="YOUR_API_KEY",
    api_secret="YOUR_API_SECRET",
)
```

本项目第一阶段固定：

```text
交易所：Bybit
环境：Testnet
产品：USDT Perpetual
category：linear
symbol：BTCUSDT
周期：15m
```

---

# 3. 项目目录

建议项目名：

```text
bybit_quant/
```

目录如下：

```text
bybit_quant/
│
├── README.md
├── requirements.txt
├── .env
├── .gitignore
│
├── config/
│   └── settings.py
│
├── core/
│   ├── bybit_client.py
│   ├── market_data.py
│   ├── indicators.py
│   ├── strategy.py
│   ├── risk.py
│   ├── trader.py
│   └── position_manager.py
│
├── backtest/
│   ├── engine.py
│   ├── metrics.py
│   └── run_backtest.py
│
├── live/
│   └── run_bot.py
│
├── storage/
│   ├── trades.csv
│   └── logs/
│
├── scripts/
│   ├── test_connection.py
│   ├── download_kline.py
│   └── test_order.py
│
└── tests/
    ├── test_strategy.py
    └── test_risk.py
```

---

# 4. Windows 环境搭建

## 4.1 创建项目

PowerShell：

```powershell
cd $HOME\Desktop
mkdir bybit_quant
cd bybit_quant
```

---

## 4.2 创建虚拟环境

```powershell
python -m venv .venv
```

激活：

```powershell
.\.venv\Scripts\Activate.ps1
```

如果 PowerShell 拒绝执行：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

---

## 4.3 安装依赖

```bash
pip install pybit pandas numpy matplotlib python-dotenv loguru pydantic
```

生成：

```bash
pip freeze > requirements.txt
```

---

# 5. API Key 配置

不要把 API Key 直接写进 Python。

创建：

```text
.env
```

内容：

```env
BYBIT_API_KEY=xxxxxxxx
BYBIT_API_SECRET=xxxxxxxx
BYBIT_TESTNET=true
```

`.gitignore`：

```gitignore
.env
.venv/
__pycache__/
*.pyc
storage/logs/
```

---

# 6. 配置文件

创建：

```text
config/settings.py
```

```python
import os
from dotenv import load_dotenv

load_dotenv()

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")

TESTNET = os.getenv("BYBIT_TESTNET", "true").lower() == "true"

CATEGORY = "linear"
SYMBOL = "BTCUSDT"
INTERVAL = "15"

EMA_FAST = 20
EMA_SLOW = 60
RSI_PERIOD = 14

RISK_PER_TRADE = 0.01
MAX_LEVERAGE = 2

STOP_LOSS_PCT = 0.015
TAKE_PROFIT_PCT = 0.03
```

---

# 7. Bybit 客户端封装

创建：

```text
core/bybit_client.py
```

```python
from pybit.unified_trading import HTTP

from config.settings import (
    BYBIT_API_KEY,
    BYBIT_API_SECRET,
    TESTNET,
)

class BybitClient:

    def __init__(self):
        self.session = HTTP(
            testnet=TESTNET,
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET,
        )

    def get_server_time(self):
        return self.session.get_server_time()

    def get_balance(self):
        return self.session.get_wallet_balance(
            accountType="UNIFIED"
        )

client = BybitClient()
```

测试：

```text
scripts/test_connection.py
```

```python
from core.bybit_client import client

print(client.get_server_time())
print(client.get_balance())
```

执行：

```bash
python scripts/test_connection.py
```

成功返回：

```text
retCode = 0
retMsg = OK
```

说明连接正常。

---

# 8. 获取 K 线

创建：

```text
core/market_data.py
```

```python
import pandas as pd

from core.bybit_client import client
from config.settings import CATEGORY, SYMBOL, INTERVAL


def get_kline(limit=200):

    res = client.session.get_kline(
        category=CATEGORY,
        symbol=SYMBOL,
        interval=INTERVAL,
        limit=limit,
    )

    rows = res["result"]["list"]

    df = pd.DataFrame(
        rows,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "turnover",
        ]
    )

    numeric_cols = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "turnover",
    ]

    df[numeric_cols] = df[numeric_cols].astype(float)

    df["timestamp"] = pd.to_datetime(
        df["timestamp"].astype("int64"),
        unit="ms"
    )

    df = df.sort_values("timestamp")

    return df.reset_index(drop=True)
```

测试：

```python
from core.market_data import get_kline

df = get_kline()

print(df.tail())
```

---

# 9. 技术指标

创建：

```text
core/indicators.py
```

```python
import pandas as pd


def ema(series, period):
    return series.ewm(
        span=period,
        adjust=False
    ).mean()


def rsi(series, period=14):

    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))
```

---

# 10. 第一版策略

策略不要复杂。

定义：

```text
EMA20 > EMA60
且 RSI < 70
→ LONG

EMA20 < EMA60
且 RSI > 30
→ SHORT

否则：
→ HOLD
```

创建：

```text
core/strategy.py
```

```python
from core.indicators import ema, rsi
from config.settings import (
    EMA_FAST,
    EMA_SLOW,
    RSI_PERIOD,
)


def generate_signal(df):

    df = df.copy()

    df["ema_fast"] = ema(
        df["close"],
        EMA_FAST
    )

    df["ema_slow"] = ema(
        df["close"],
        EMA_SLOW
    )

    df["rsi"] = rsi(
        df["close"],
        RSI_PERIOD
    )

    last = df.iloc[-1]

    if (
        last["ema_fast"] > last["ema_slow"]
        and last["rsi"] < 70
    ):
        return "LONG"

    if (
        last["ema_fast"] < last["ema_slow"]
        and last["rsi"] > 30
    ):
        return "SHORT"

    return "HOLD"
```

---

# 11. 改成“交叉信号”会更合理

上面的版本会连续很多根 K 线都返回 LONG。

更好的方案是只在 EMA 发生交叉时产生信号。

```python
def generate_signal(df):

    df = df.copy()

    df["ema_fast"] = ema(
        df["close"],
        EMA_FAST
    )

    df["ema_slow"] = ema(
        df["close"],
        EMA_SLOW
    )

    df["rsi"] = rsi(
        df["close"],
        RSI_PERIOD
    )

    prev = df.iloc[-2]
    curr = df.iloc[-1]

    bullish_cross = (
        prev["ema_fast"] <= prev["ema_slow"]
        and
        curr["ema_fast"] > curr["ema_slow"]
    )

    bearish_cross = (
        prev["ema_fast"] >= prev["ema_slow"]
        and
        curr["ema_fast"] < curr["ema_slow"]
    )

    if bullish_cross and curr["rsi"] < 70:
        return "LONG"

    if bearish_cross and curr["rsi"] > 30:
        return "SHORT"

    return "HOLD"
```

推荐使用这个版本。

---

# 12. 风控模块

真正的量化交易系统中：

```text
策略 ≠ 下单
```

正确结构：

```text
Strategy
    ↓
Signal
    ↓
Risk Manager
    ↓
Order
```

创建：

```text
core/risk.py
```

```python
def calculate_position_size(
    account_equity,
    entry_price,
    stop_price,
    risk_ratio=0.01,
):

    risk_money = account_equity * risk_ratio

    stop_distance = abs(
        entry_price - stop_price
    )

    if stop_distance <= 0:
        return 0

    qty = risk_money / stop_distance

    return qty
```

例如：

```text
账户：
10,000 USDT

每次风险：
1%

最大亏损：
100 USDT
```

BTC：

```text
entry = 100000
stop = 98500

风险距离：
1500 USDT
```

那么：

```text
qty =
100 / 1500
= 0.0667 BTC
```

如果止损触发：

```text
亏损 ≈ 100 USDT
```

---

# 13. 第一版风控规则

强烈建议固定：

```text
单笔风险 <= 1%

最大杠杆 <= 2x

同时只能持有 1 个仓位

连续亏损 3 次：
暂停交易

每日亏损 >= 3%：
停止当天交易

总回撤 >= 10%：
停止机器人
```

实现：

```python
class RiskManager:

    def __init__(self):

        self.max_daily_loss = 0.03
        self.max_drawdown = 0.10
        self.max_consecutive_losses = 3

    def allow_trade(
        self,
        daily_loss,
        drawdown,
        consecutive_losses,
    ):

        if daily_loss >= self.max_daily_loss:
            return False

        if drawdown >= self.max_drawdown:
            return False

        if consecutive_losses >= self.max_consecutive_losses:
            return False

        return True
```

---

# 14. 下单模块

创建：

```text
core/trader.py
```

```python
from core.bybit_client import client
from config.settings import CATEGORY, SYMBOL


def market_buy(qty):

    return client.session.place_order(
        category=CATEGORY,
        symbol=SYMBOL,
        side="Buy",
        orderType="Market",
        qty=str(qty),
    )


def market_sell(qty):

    return client.session.place_order(
        category=CATEGORY,
        symbol=SYMBOL,
        side="Sell",
        orderType="Market",
        qty=str(qty),
    )
```

---

# 15. 千万不要直接用 float 数量下单

交易所对：

```text
最小下单量
qtyStep
tickSize
```

都有约束。

先查询合约参数：

```python
res = client.session.get_instruments_info(
    category="linear",
    symbol="BTCUSDT",
)

info = res["result"]["list"][0]

print(info["lotSizeFilter"])
print(info["priceFilter"])
```

常见字段：

```text
minOrderQty
qtyStep
tickSize
```

你需要写：

```text
normalize_qty()
normalize_price()
```

例如：

```python
from decimal import Decimal, ROUND_DOWN


def round_step(value, step):

    value = Decimal(str(value))
    step = Decimal(str(step))

    return float(
        (value / step)
        .quantize(
            Decimal("1"),
            rounding=ROUND_DOWN
        )
        * step
    )
```

---

# 16. 查询当前持仓

创建：

```text
core/position_manager.py
```

```python
from core.bybit_client import client
from config.settings import CATEGORY, SYMBOL


def get_position():

    result = client.session.get_positions(
        category=CATEGORY,
        symbol=SYMBOL,
    )

    return result["result"]["list"]
```

判断有没有仓位：

```python
def has_position():

    positions = get_position()

    for p in positions:

        size = float(p["size"])

        if size > 0:
            return True

    return False
```

---

# 17. 止损止盈

第一版可以使用固定百分比：

```text
LONG：

止损：
entry × (1 - 1.5%)

止盈：
entry × (1 + 3%)

SHORT：

止损：
entry × (1 + 1.5%)

止盈：
entry × (1 - 3%)
```

目标：

```text
Risk : Reward
≈
1 : 2
```

函数：

```python
def build_sl_tp(
    side,
    entry,
    stop_loss_pct=0.015,
    take_profit_pct=0.03,
):

    if side == "LONG":

        stop_loss = entry * (
            1 - stop_loss_pct
        )

        take_profit = entry * (
            1 + take_profit_pct
        )

    else:

        stop_loss = entry * (
            1 + stop_loss_pct
        )

        take_profit = entry * (
            1 - take_profit_pct
        )

    return stop_loss, take_profit
```

---

# 18. 第一版主程序

创建：

```text
live/run_bot.py
```

```python
import time

from core.market_data import get_kline
from core.strategy import generate_signal
from core.position_manager import has_position
from core.trader import market_buy, market_sell


def run():

    while True:

        try:

            df = get_kline()

            signal = generate_signal(df)

            print(
                df.iloc[-1]["timestamp"],
                signal
            )

            if not has_position():

                if signal == "LONG":
                    market_buy(0.001)

                elif signal == "SHORT":
                    market_sell(0.001)

        except Exception as e:

            print("ERROR:", e)

        time.sleep(60)


if __name__ == "__main__":
    run()
```

注意：

这只是最初级版本。

不要直接实盘运行。

---

# 19. 更正确的执行方式

如果策略周期是：

```text
15m
```

没有必要：

```text
每秒执行策略
```

更合理：

```text
每分钟轮询一次
```

只在发现：

```text
新的 15 分钟 K 线已经收盘
```

时运行一次策略。

保存：

```python
last_bar_time = None
```

然后：

```python
bar_time = df.iloc[-2]["timestamp"]
```

注意：

实时 K 线最后一根通常还没有完全收盘。

因此策略更安全的方式是使用：

```text
倒数第二根 K 线
```

作为最新闭合 K 线。

---

# 20. 防止重复下单

这是自动交易最重要的问题之一。

不要：

```text
程序重启
→ 再次检测到 LONG
→ 再下一单
```

需要给每次订单生成：

```text
orderLinkId
```

例如：

```python
import uuid

order_id = "quant-" + uuid.uuid4().hex[:20]
```

下单：

```python
session.place_order(
    category="linear",
    symbol="BTCUSDT",
    side="Buy",
    orderType="Market",
    qty="0.001",
    orderLinkId=order_id,
)
```

---

# 21. 交易日志

每次交易记录：

```text
timestamp
symbol
side
entry
stop_loss
take_profit
qty
strategy
signal
order_id
pnl
```

CSV：

```text
storage/trades.csv
```

示例：

```csv
timestamp,symbol,side,entry,sl,tp,qty,pnl
2026-09-29,BTCUSDT,LONG,100000,98500,103000,0.01,0
```

Python：

```python
import csv


def log_trade(row):

    with open(
        "storage/trades.csv",
        "a",
        newline="",
        encoding="utf-8",
    ) as f:

        writer = csv.writer(f)

        writer.writerow(row)
```

---

# 22. 日志系统

推荐：

```python
from loguru import logger

logger.add(
    "storage/logs/bot.log",
    rotation="10 MB",
    retention="30 days",
)
```

用：

```python
logger.info("Signal = LONG")

logger.warning("Risk rejected")

logger.error(str(e))
```

---

# 23. 回测系统

不要直接：

```text
写完策略
→ 上 Testnet
```

先：

```text
历史 K线
↓
回测
↓
模拟
↓
Testnet
↓
实盘
```

---

# 24. 最简单回测逻辑

创建：

```text
backtest/engine.py
```

逻辑：

```text
从第 60 根K线开始

每一根：
计算 signal

如果无仓位：
    LONG → 开多
    SHORT → 开空

如果已有仓位：
    检查：
        Stop Loss
        Take Profit
        反向信号
```

---

# 25. 一个简单回测框架

```python
def backtest(
    df,
    initial_balance=10000
):

    balance = initial_balance

    position = None

    trades = []

    for i in range(
        100,
        len(df)
    ):

        history = df.iloc[: i + 1]

        signal = generate_signal(
            history
        )

        price = df.iloc[i]["close"]

        if position is None:

            if signal == "LONG":

                position = {
                    "side": "LONG",
                    "entry": price,
                }

            elif signal == "SHORT":

                position = {
                    "side": "SHORT",
                    "entry": price,
                }

        else:

            if (
                position["side"] == "LONG"
                and signal == "SHORT"
            ):

                pnl = (
                    price
                    - position["entry"]
                )

                balance += pnl

                trades.append(pnl)

                position = None

            elif (
                position["side"] == "SHORT"
                and signal == "LONG"
            ):

                pnl = (
                    position["entry"]
                    - price
                )

                balance += pnl

                trades.append(pnl)

                position = None

    return balance, trades
```

这只是 MVP。

第二版再加入：

```text
手续费
滑点
资金费率
仓位大小
杠杆
SL/TP
最大回撤
```

---

# 26. 回测必须计算哪些指标

至少：

```text
Total Return
Annualized Return
Win Rate
Profit Factor
Sharpe Ratio
Max Drawdown
Number of Trades
Average Win
Average Loss
Risk / Reward
```

---

# 27. 最大回撤

```python
def max_drawdown(equity):

    peak = equity.cummax()

    drawdown = (
        equity - peak
    ) / peak

    return drawdown.min()
```

---

# 28. Sharpe Ratio

简单版：

```python
import numpy as np


def sharpe_ratio(returns):

    if returns.std() == 0:
        return 0

    return (
        returns.mean()
        / returns.std()
        * np.sqrt(365)
    )
```

实际年化因子要根据策略时间粒度调整。

---

# 29. 第二阶段加入 WebSocket

第一版：

```text
REST API
```

第二版：

```text
WebSocket
```

因为实时交易更适合 WebSocket。

架构：

```text
Bybit WebSocket
       ↓
Ticker
Kline
Order
Position
       ↓
Event Queue
       ↓
Strategy Engine
```

官方 SDK：

```python
from pybit.unified_trading import WebSocket
```

例如：

```python
ws = WebSocket(
    testnet=True,
    channel_type="linear",
)

def handle_message(message):
    print(message)

ws.kline_stream(
    interval=15,
    symbol="BTCUSDT",
    callback=handle_message,
)
```

---

# 30. 第三阶段升级策略

完成基础系统后，再逐渐加入：

```text
EMA
RSI
MACD
ATR
Bollinger Bands
ADX
Volume
Open Interest
Funding Rate
```

比较推荐升级成：

```text
Trend Filter
+
Momentum Filter
+
Volatility Filter
```

例如：

```text
EMA20 > EMA60
+
ADX > 25
+
RSI 50~70
+
Volume > MA20 Volume
```

再做多。

---

# 31. ATR 动态止损

固定百分比止损不适合不同波动阶段。

更合理：

```text
SL = 1.5 × ATR
TP = 3 × ATR
```

这样：

```text
高波动 → 止损自动变宽
低波动 → 止损自动变窄
```

---

# 32. 可以增加资金费率因子

永续合约：

```text
Funding Rate
```

可以作为过滤器。

例如：

```text
Funding Rate 极高
+
价格过热

→ 减少做多
```

但不要单独依赖 Funding Rate 做方向判断。

---

# 33. 可以增加 Open Interest

后续可以研究：

```text
Price ↑
OI ↑
→ 新增资金进入

Price ↑
OI ↓
→ 空头平仓可能较多

Price ↓
OI ↑
→ 新空头可能增加

Price ↓
OI ↓
→ 多头平仓可能较多
```

再和：

```text
Volume
Funding
Price Action
```

联合。

---

# 34. LLM / Agent 扩展

这部分非常适合做成你的项目特色。

不要让 LLM：

```text
直接决定买 1 BTC
```

推荐：

```text
行情
新闻
Funding
OI
Volatility
技术指标
     ↓
Market Agent
     ↓
市场状态分类
```

输出：

```json
{
  "regime": "TRENDING",
  "direction": "BULLISH",
  "volatility": "MEDIUM",
  "risk_level": "LOW"
}
```

然后：

```text
规则策略
+
Agent Market Regime
+
Risk Manager
↓
最终决定是否交易
```

---

# 35. Agent 不直接控制仓位

推荐：

```text
LLM：
负责解释 / 分类 / 新闻理解

确定性代码：
负责：
- 仓位
- 杠杆
- SL
- TP
- 最大亏损
- 下单
```

即：

```text
LLM can advise.

LLM cannot bypass risk rules.
```

---

# 36. Agent 模块设计

未来可以加：

```text
agents/
├── market_agent.py
├── news_agent.py
├── risk_agent.py
└── report_agent.py
```

但第一版不要做。

先把纯量化闭环跑通。

---

# 37. 可以做每日交易报告

每天生成：

```text
Daily Trading Report

BTC Price:
102,430

Trades:
5

Win:
3

Loss:
2

PnL:
+1.42%

Max Drawdown:
-0.71%

Current Position:
None

Market Regime:
Trending Bullish
```

以后可以让 LLM 自动总结：

```text
今天为什么盈利？
今天为什么亏损？
策略在哪种市场状态失效？
```

---

# 38. 数据库存储升级

第一版：

```text
CSV
```

第二版：

```text
SQLite
```

例如：

```text
db/
    quant.db
```

表：

```text
candles
signals
orders
positions
trades
daily_metrics
```

---

# 39. UI 可以最后再做

等核心稳定后，可以做：

```text
Streamlit
```

Dashboard：

```text
账户余额
当前仓位
PnL
BTC 实时价格
策略状态
最近交易
收益曲线
最大回撤
```

安装：

```bash
pip install streamlit
```

运行：

```bash
streamlit run dashboard.py
```

---

# 40. 最终工程架构

成熟版：

```text
                    ┌──────────────┐
                    │ Bybit Market │
                    └───────┬──────┘
                            │
                    ┌───────▼───────┐
                    │ Market Data   │
                    └───────┬───────┘
                            │
               ┌────────────▼────────────┐
               │    Feature Engine      │
               │ EMA RSI ATR OI Funding │
               └────────────┬────────────┘
                            │
                    ┌───────▼───────┐
                    │ Strategy      │
                    └───────┬───────┘
                            │
                        Signal
                            │
                    ┌───────▼───────┐
                    │ Risk Manager  │
                    └───────┬───────┘
                            │
                    ┌───────▼───────┐
                    │ Execution     │
                    └───────┬───────┘
                            │
                    ┌───────▼───────┐
                    │ Bybit Order   │
                    └───────────────┘
```

旁路：

```text
Logger
Database
Backtest
Monitoring
Dashboard
Agent
```

---

# 41. 开发顺序

## Phase 1：API

完成：

```text
[x] API连接
[x] 读取余额
[x] 获取K线
[x] 获取BTC价格
[x] 查询合约参数
```

---

## Phase 2：Strategy

```text
[x] EMA
[x] RSI
[x] Signal
```

---

## Phase 3：Backtest

```text
[x] 读取历史K线
[x] 模拟交易
[x] PnL
[x] Win Rate
[x] Max Drawdown
[x] Sharpe
```

---

## Phase 4：Risk

```text
[x] Position Size
[x] SL
[x] TP
[x] Max Loss
[x] Max Drawdown Stop
```

---

## Phase 5：Testnet

```text
[x] Testnet Order
[x] Position Query
[x] Close Position
[x] Stop Loss
[x] Take Profit
```

---

## Phase 6：Automation

```text
[x] Scheduler
[x] Logging
[x] Error Recovery
[x] Duplicate Order Prevention
```

---

## Phase 7：Advanced Data

```text
[ ] Funding
[ ] Open Interest
[ ] Orderbook
[ ] WebSocket
```

---

## Phase 8：AI Agent

```text
[ ] Market Regime
[ ] News Classification
[ ] Trading Report
[ ] Strategy Analysis
```

---

# 42. 非常重要的安全规则

真实交易前必须做到：

```text
1. Testnet 至少稳定运行一段时间
2. API Key 不开启提现权限
3. API Key 不提交到 GitHub
4. 单笔风险 <= 1%
5. 杠杆 <= 2x
6. 出现 API 错误时默认“不下单”
7. 无法获取仓位时默认“不下单”
8. 无法获取余额时默认“不下单”
9. 网络异常默认“不下单”
10. 程序重启后先同步真实持仓
```

核心原则：

```text
Fail Closed
```

即：

```text
不知道发生什么
=
不交易
```

而不是：

```text
不知道发生什么
=
继续下单
```

---

# 43. API Key 安全

实盘时：

```text
API 权限：

Read
Trade

不要：
Withdrawal
```

推荐：

```text
绑定 IP
```

并使用：

```text
专门的交易子账户
```

而不是主账户全部资产。

---

# 44. 实盘前验收 Checklist

只有全部满足才考虑小资金：

```text
[ ] 回测至少覆盖多个市场阶段

[ ] 包含牛市
[ ] 熊市
[ ] 横盘
[ ] 高波动

[ ] 加入手续费
[ ] 加入滑点

[ ] Testnet 稳定运行

[ ] 机器人重启不会重复下单

[ ] 网络断开不会错误下单

[ ] API异常不会错误下单

[ ] 可以正确识别已有仓位

[ ] 止损有效

[ ] 最大亏损机制有效

[ ] 有完整交易日志
```

---

# 45. 推荐的第一周计划

## Day 1

```text
项目搭建
Bybit Testnet API
获取余额
获取BTC行情
```

## Day 2

```text
K线
EMA
RSI
Strategy
```

## Day 3

```text
历史数据
Backtest Engine
```

## Day 4

```text
Win Rate
PnL
Max Drawdown
Sharpe
```

## Day 5

```text
Risk Manager
Position Size
SL/TP
```

## Day 6

```text
Testnet 下单
查询持仓
平仓
```

## Day 7

```text
run_bot
日志
异常处理
```

完成后你就已经有：

```text
一个完整的 Bybit Quant MVP
```

---

# 46. 第二阶段建议

第一版完成后，我建议你不要马上研究几十种指标。

第二阶段优先做：

```text
1. ATR 动态止损
2. Open Interest
3. Funding Rate
4. WebSocket
5. SQLite
6. Dashboard
```

这比继续堆：

```text
MACD
KDJ
CCI
几十种指标
```

更有工程价值。

---

# 47. 第三阶段：做成 AI Quant Agent

这是最适合你继续扩展的方向。

最终：

```text
              ┌──────────────┐
              │ Market Data  │
              └──────┬───────┘
                     │
        ┌────────────▼────────────┐
        │ Quant Feature Engine   │
        └────────────┬────────────┘
                     │
              ┌──────▼──────┐
              │ Strategy    │
              └──────┬──────┘
                     │
       ┌─────────────▼─────────────┐
       │      Market Agent        │
       │ Trend / Vol / News / OI  │
       └─────────────┬─────────────┘
                     │
              ┌──────▼──────┐
              │ Risk Engine │
              └──────┬──────┘
                     │
              ┌──────▼──────┐
              │ Execution   │
              └──────┬──────┘
                     │
                   Bybit
```

这样项目就不只是：

```text
EMA 交叉机器人
```

而是：

```text
Quant Trading
+
Risk Management
+
LLM Agent
+
Market Intelligence
```

---

# 48. README 项目介绍可以这样写

```markdown
# QuantPilot

An AI-enhanced quantitative trading framework for Bybit.

Features:

- Bybit V5 API integration
- Historical backtesting
- EMA/RSI strategy
- ATR-based risk management
- Position sizing
- Stop-loss / take-profit
- Automated Testnet execution
- Trading logs and analytics
- Market regime detection
- LLM-assisted market analysis
```

---

# 49. 官方文档

建议实现过程中以官方文档为准：

Bybit V5 API：

```text
https://bybit-exchange.github.io/docs/v5/intro
```

Market Kline：

```text
https://bybit-exchange.github.io/docs/v5/market/kline
```

Instrument Info：

```text
https://bybit-exchange.github.io/docs/v5/market/instrument
```

Place Order：

```text
https://bybit-exchange.github.io/docs/v5/order/create-order
```

Position：

```text
https://bybit-exchange.github.io/docs/v5/position
```

官方 Python SDK：

```text
https://github.com/bybit-exchange/pybit
```

---

# 50. 最后给你的建议

第一版只完成：

```text
BTCUSDT
15m
EMA20 / EMA60
RSI
1% Risk
SL / TP
Backtest
Testnet
```

不要一开始做：

```text
几十个币
高杠杆
网格
马丁
AI自动决策
真实资金
```

先把：

```text
Data
→ Signal
→ Risk
→ Execution
→ Logging
→ Evaluation
```

这个闭环真正跑通。

这才是一个完整的量化交易系统。

> 本项目仅用于技术学习、策略研究和软件工程实验，不构成任何投资建议。加密资产和衍生品具有较高风险，实盘前应充分评估策略失效、杠杆、流动性、滑点、API异常及交易所风险。
