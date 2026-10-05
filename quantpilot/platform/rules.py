"""Versioned, shared strategy templates and close-price risk rules."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from quantpilot.strategy import SIGNAL_KEYS, signal_warmup


class StrategyRules(BaseModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, validate_default=True
    )
    version: Literal[1] = 1
    strategy_type: Literal[
        "ema_rsi", "ema_cross", "rsi_reversion", "breakout", "bollinger_reversion"
    ] = "ema_rsi"
    fast: int = Field(default=20, ge=2, le=499)
    slow: int = Field(default=60, ge=3, le=500)
    rsi_period: int = Field(default=14, ge=2, le=100)
    rsi_lower: float = Field(default=30, gt=0, lt=100)
    rsi_upper: float = Field(default=70, gt=0, lt=100)
    stop_loss_pct: float = Field(default=0, ge=0, le=100)
    take_profit_pct: float = Field(default=0, ge=0, le=1000)
    max_drawdown_pct: float = Field(default=0, ge=0, le=100)
    breakout_period: int = Field(default=20, ge=5, le=200)
    bollinger_period: int = Field(default=20, ge=5, le=200)
    bollinger_std: float = Field(default=2, ge=0.5, le=5)
    volume_period: int = Field(default=20, ge=5, le=100)
    volume_ratio: float = Field(default=0, ge=0, le=10)
    atr_period: int = Field(default=14, ge=2, le=100)
    atr_pct_max: float = Field(default=0, ge=0, le=100)
    adx_period: int = Field(default=14, ge=2, le=100)
    adx_min: float = Field(default=0, ge=0, le=100)

    @model_validator(mode="after")
    def validate_order(self):
        if self.fast >= self.slow or self.rsi_lower >= self.rsi_upper:
            raise ValueError("快 EMA 必须小于慢 EMA，RSI 下限必须小于上限")
        return self

    @property
    def warmup(self):
        return signal_warmup(self.model_dump())

    @property
    def signal_options(self):
        return {key: getattr(self, key) for key in SIGNAL_KEYS}


TEMPLATES = [
    {
        "id": "ema_rsi",
        "name": "EMA / RSI 趋势",
        "description": "均线交叉，RSI 过滤过热或超卖信号",
        "rules": StrategyRules().model_dump(),
    },
    {
        "id": "ema_cross",
        "name": "EMA 双均线",
        "description": "快慢均线交叉产生多空信号",
        "rules": StrategyRules(strategy_type="ema_cross").model_dump(),
    },
    {
        "id": "rsi_reversion",
        "name": "RSI 均值回归",
        "description": "RSI 从超卖区回升做多，从超买区回落做空",
        "rules": StrategyRules(strategy_type="rsi_reversion").model_dump(),
    },
]
TEMPLATES.extend(
    [
        {
            "id": "breakout",
            "name": "区间突破",
            "description": "突破此前区间高低点，可结合成交量与趋势强度过滤",
            "rules": StrategyRules(strategy_type="breakout").model_dump(),
        },
        {
            "id": "bollinger_reversion",
            "name": "布林带回归",
            "description": "价格从布林带外回到带内，研究偏离后的回归",
            "rules": StrategyRules(strategy_type="bollinger_reversion").model_dump(),
        },
    ]
)


def position_exit(side, entry, price, rules):
    change = (float(price) / float(entry) - 1) * (1 if side == "LONG" else -1) * 100
    if rules.stop_loss_pct and change <= -rules.stop_loss_pct:
        return "止损"
    if rules.take_profit_pct and change >= rules.take_profit_pct:
        return "止盈"
    return None
