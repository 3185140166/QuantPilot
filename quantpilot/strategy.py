"""Causal EMA/RSI signals shared by live research and backtesting."""

from __future__ import annotations

from typing import Literal, cast

import pandas as pd

from .indicators import ema, rsi

Signal = Literal["LONG", "SHORT", "HOLD"]


def generate_signals(
    bars: pd.DataFrame, fast: int = 20, slow: int = 60, rsi_period: int = 14
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
    signals.loc[(quick.shift() <= long.shift()) & (quick > long) & (momentum < 70)] = (
        "LONG"
    )
    signals.loc[(quick.shift() >= long.shift()) & (quick < long) & (momentum > 30)] = (
        "SHORT"
    )
    signals.iloc[: slow + 1] = "HOLD"
    return signals


def generate_signal(
    bars: pd.DataFrame, fast: int = 20, slow: int = 60, rsi_period: int = 14
) -> Signal:
    signals = generate_signals(bars, fast, slow, rsi_period)
    return cast(Signal, signals.iloc[-1]) if not signals.empty else "HOLD"
