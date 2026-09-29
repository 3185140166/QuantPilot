"""A deliberately small, pure EMA/RSI strategy."""

from __future__ import annotations

from typing import Literal

import pandas as pd

from .indicators import ema, rsi

Signal = Literal["LONG", "SHORT", "HOLD"]


def generate_signal(
    bars: pd.DataFrame,
    fast: int = 20,
    slow: int = 60,
    rsi_period: int = 14,
) -> Signal:
    required = {"close"}
    if not required.issubset(bars.columns):
        raise ValueError("bars must contain a close column")
    if len(bars) < slow + 2:
        return "HOLD"
    data = bars.copy()
    data["ema_fast"] = ema(data["close"], fast)
    data["ema_slow"] = ema(data["close"], slow)
    data["rsi"] = rsi(data["close"], rsi_period)
    previous, current = data.iloc[-2], data.iloc[-1]
    if pd.isna(previous["ema_slow"]) or pd.isna(current["rsi"]):
        return "HOLD"
    if (
        previous["ema_fast"] <= previous["ema_slow"]
        and current["ema_fast"] > current["ema_slow"]
        and current["rsi"] < 70
    ):
        return "LONG"
    if (
        previous["ema_fast"] >= previous["ema_slow"]
        and current["ema_fast"] < current["ema_slow"]
        and current["rsi"] > 30
    ):
        return "SHORT"
    return "HOLD"
