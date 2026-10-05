"""Validated API inputs shared by paper and research services."""

from datetime import datetime, timezone
from decimal import Decimal
from math import isclose
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .rules import StrategyRules


class MarketParameters(BaseModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, validate_default=True
    )
    exchange: Literal["bybit", "binance"] = "bybit"
    symbol: str = Field(
        default="BTCUSDT", min_length=5, max_length=24, pattern=r"^[A-Z0-9]+USDT$"
    )
    interval: Literal["1", "5", "15", "30", "60", "240", "D"] = "15"


class StrategyCreate(MarketParameters):
    rules: StrategyRules = Field(default_factory=StrategyRules)
    name: str = Field(min_length=1, max_length=80)
    quantity: Decimal = Field(default=Decimal("0.001"), ge=Decimal("0.000001"), le=1000)
    initial_equity: Decimal = Field(default=10000, ge=1, le=100000000)
    max_notional: Decimal = Field(default=1000, ge=1, le=100000000)
    fee_rate: Decimal = Field(default=Decimal("0.00055"), ge=0, le=Decimal("0.1"))
    slippage_bps: Decimal = Field(default=Decimal(2), ge=0, le=1000)


class StrategyCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["start", "pause", "flatten"]


class WatchlistCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exchange: Literal["bybit", "binance"] = "bybit"
    symbol: str = Field(min_length=5, max_length=24, pattern=r"^[A-Z0-9]+USDT$")


class BacktestCreate(MarketParameters):
    evaluation_phase: Literal["research", "validation"] = "research"
    rules: StrategyRules = Field(default_factory=StrategyRules)
    max_notional: float = Field(default=1000, ge=1, le=100000000)
    start_at: datetime | None = None
    end_at: datetime | None = None
    limit: int = Field(default=500, ge=100, le=1000)
    initial_equity: float = Field(default=10000, ge=1, le=100000000)
    quantity: float = Field(default=0.001, ge=0.000001, le=1000)
    fee_rate: float = Field(default=0.00055, ge=0, le=0.1)
    slippage_bps: float = Field(default=2, ge=0, le=1000)

    @model_validator(mode="after")
    def validate_dates(self):
        if (self.start_at is None) != (self.end_at is None):
            raise ValueError("开始和结束时间必须同时填写")
        if self.start_at is not None:
            for field in ("start_at", "end_at"):
                stamp = getattr(self, field)
                if stamp.tzinfo is None:
                    raise ValueError("时间必须包含时区")
                setattr(self, field, stamp.astimezone(timezone.utc))
            if self.start_at >= self.end_at or self.end_at > datetime.now(timezone.utc):
                raise ValueError("开始必须早于结束，结束不能晚于现在")
            from .market import INTERVAL_MINUTES

            count = (self.end_at - self.start_at).total_seconds() / (
                INTERVAL_MINUTES[self.interval] * 60
            )
            if count > 100000 or (self.end_at - self.start_at).days > 730:
                raise ValueError("单次区间最多 100000 根 K 线、730 天")
        return self


class BacktestBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base: BacktestCreate
    variants: list[StrategyRules] = Field(min_length=1, max_length=9)


class PromoteBacktest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)


class SearchRange(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    low: float
    high: float
    step: float = Field(default=1.0, gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.low > self.high or not isclose(
            (self.high - self.low) / self.step,
            round((self.high - self.low) / self.step),
            abs_tol=1e-8,
        ):
            raise ValueError("参数上限不能低于下限，区间长度必须是步长的整数倍")
        return self


class OptimizationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    base: BacktestCreate
    split_at: datetime
    ranges: dict[str, SearchRange] = Field(min_length=1, max_length=10)
    trials: int = Field(default=40, ge=5, le=200)
    top_k: int = Field(default=5, ge=1, le=5)
    seed: int = Field(default=42, ge=0, le=2147483647)
    objective: Literal["return_drawdown", "return"] = "return_drawdown"
    drawdown_penalty: float = Field(default=1, ge=0, le=10)
    max_drawdown_pct: float = Field(default=10, gt=0, le=100)
    min_trades: int = Field(default=5, ge=1, le=10000)
    validation_windows: int = Field(default=1, ge=1, le=3)
    require_positive_validation: bool = True

    @model_validator(mode="after")
    def independent_ranges(self):
        from .market import INTERVAL_MINUTES
        from .search_space import COMMON_KEYS, SPACE, TEMPLATE_KEYS

        if (
            not self.base.start_at
            or not self.base.end_at
            or self.split_at.tzinfo is None
        ):
            raise ValueError("自动优化需要起止日期和包含时区的研究／验证切分时间")
        self.split_at = self.split_at.astimezone(timezone.utc)
        step_seconds = INTERVAL_MINUTES[self.base.interval] * 60
        from datetime import timedelta

        remainder = self.split_at.timestamp() % step_seconds
        if remainder:
            self.split_at += timedelta(seconds=step_seconds - remainder)
        train_bars = (self.split_at - self.base.start_at).total_seconds() / step_seconds
        validation_bars = (
            self.base.end_at - self.split_at
        ).total_seconds() / step_seconds
        if train_bars < 100 or validation_bars < 100 * self.validation_windows:
            raise ValueError(
                "研究区间和每个验证窗口至少需要 100 根 K 线，且验证必须在研究之后"
            )
        allowed = TEMPLATE_KEYS[self.base.rules.strategy_type] + COMMON_KEYS
        for key, bounds in self.ranges.items():
            if key not in allowed:
                raise ValueError("搜索参数不适用于当前策略模板")
            _, lower, upper, kind, *_ = SPACE[key]
            if bounds.low < lower or bounds.high > upper:
                raise ValueError(f"{key} 搜索范围超出允许边界")
            if kind == "int" and any(
                not value.is_integer()
                for value in (bounds.low, bounds.high, bounds.step)
            ):
                raise ValueError("周期参数的上下限和步长必须为整数")
        return self
