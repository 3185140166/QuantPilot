"""Causal EMA/RSI signals shared by live research and backtesting."""

from __future__ import annotations

from typing import Literal, cast

import pandas as pd

from .indicators import adx, atr, ema, rsi

Signal = Literal["LONG", "SHORT", "HOLD"]

SIGNAL_KEYS = (
    "fast",
    "slow",
    "rsi_period",
    "strategy_type",
    "rsi_lower",
    "rsi_upper",
    "breakout_period",
    "bollinger_period",
    "bollinger_std",
    "volume_period",
    "volume_ratio",
    "atr_period",
    "atr_pct_max",
    "adx_period",
    "adx_min",
)


def signal_warmup(config):
    periods = [config.get("slow", 60) + 1, config.get("rsi_period", 14) + 1]
    if config.get("strategy_type") == "breakout":
        periods.append(config.get("breakout_period", 20) + 1)
    if config.get("strategy_type") == "bollinger_reversion":
        periods.append(config.get("bollinger_period", 20) + 1)
    if config.get("volume_ratio", 0):
        periods.append(config.get("volume_period", 20) + 1)
    if config.get("atr_pct_max", 0):
        periods.append(config.get("atr_period", 14) + 1)
    if config.get("adx_min", 0):
        periods.append(config.get("adx_period", 14) * 2 + 1)
    return max(periods)


def generate_signals(
    bars: pd.DataFrame,
    fast: int = 20,
    slow: int = 60,
    rsi_period: int = 14,
    strategy_type: str = "ema_rsi",
    rsi_lower: float = 30,
    rsi_upper: float = 70,
    breakout_period: int = 20,
    bollinger_period: int = 20,
    bollinger_std: float = 2,
    volume_period: int = 20,
    volume_ratio: float = 0,
    atr_period: int = 14,
    atr_pct_max: float = 0,
    adx_period: int = 14,
    adx_min: float = 0,
) -> pd.Series:
    """Calculate all signals once, using only present and previous observations."""
    if "close" not in bars:
        raise ValueError("bars must contain a close column")
    if not 0 < fast < slow or rsi_period <= 0:
        raise ValueError("require 0 < fast < slow and rsi_period > 0")
    close = bars["close"]
    quick, long = ema(close, fast), ema(close, slow)
    momentum = rsi(close, rsi_period)
    signals = pd.Series("HOLD", index=bars.index, dtype=object)
    upward = (quick.shift() <= long.shift()) & (quick > long)
    downward = (quick.shift() >= long.shift()) & (quick < long)
    if strategy_type == "ema_rsi":
        upward &= momentum < rsi_upper
        downward &= momentum > rsi_lower
    elif strategy_type == "rsi_reversion":
        upward = (momentum.shift() <= rsi_lower) & (momentum > rsi_lower)
        downward = (momentum.shift() >= rsi_upper) & (momentum < rsi_upper)
    elif strategy_type == "breakout":
        upper = (
            bars.high.rolling(breakout_period, min_periods=breakout_period)
            .max()
            .shift()
        )
        lower = (
            bars.low.rolling(breakout_period, min_periods=breakout_period).min().shift()
        )
        upward = (close > upper) & (close.shift() <= upper.shift())
        downward = (close < lower) & (close.shift() >= lower.shift())
    elif strategy_type == "bollinger_reversion":
        center = close.rolling(bollinger_period, min_periods=bollinger_period).mean()
        deviation = (
            close.rolling(bollinger_period, min_periods=bollinger_period).std(ddof=0)
            * bollinger_std
        )
        upper, lower = center + deviation, center - deviation
        upward = (close.shift() < lower.shift()) & (close >= lower)
        downward = (close.shift() > upper.shift()) & (close <= upper)
    elif strategy_type != "ema_cross":
        raise ValueError("Unknown strategy template")
    allowed = pd.Series(True, index=bars.index)
    if volume_ratio > 0:
        average = (
            bars.volume.rolling(volume_period, min_periods=volume_period).mean().shift()
        )
        allowed &= bars.volume / average.where(average > 0) >= volume_ratio
    if atr_pct_max > 0:
        allowed &= atr(bars, atr_period) / close * 100 <= atr_pct_max
    if adx_min > 0:
        allowed &= adx(bars, adx_period) >= adx_min
    upward &= allowed
    downward &= allowed
    signals.loc[upward] = "LONG"
    signals.loc[downward] = "SHORT"
    signals.iloc[: signal_warmup(locals())] = "HOLD"
    return signals


def generate_signal(
    bars: pd.DataFrame,
    fast: int = 20,
    slow: int = 60,
    rsi_period: int = 14,
    **kwargs,
) -> Signal:
    signals = generate_signals(bars, fast, slow, rsi_period, **kwargs)
    return cast(Signal, signals.iloc[-1]) if not signals.empty else "HOLD"
