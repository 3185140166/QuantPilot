"""Pure technical indicators."""

from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    if period <= 0:
        raise ValueError("period must be positive")
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    if period <= 0:
        raise ValueError("period must be positive")
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.where(avg_loss != 0)
    result = 100 - (100 / (1 + rs))
    result = result.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    # Preserve warm-up NaNs; only a mature flat series is neutral.
    return result.mask((avg_loss == 0) & (avg_gain == 0), 50.0)


def atr(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    previous = bars.close.shift()
    ranges = pd.concat(
        [
            bars.high - bars.low,
            (bars.high - previous).abs(),
            (bars.low - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return ranges.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def adx(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    up, down = bars.high.diff(), -bars.low.diff()
    positive = up.where((up > down) & (up > 0), 0.0)
    negative = down.where((down > up) & (down > 0), 0.0)
    volatility = atr(bars, period).where(lambda values: values > 0)
    plus = (
        positive.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        / volatility
        * 100
    )
    minus = (
        negative.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        / volatility
        * 100
    )
    total = plus + minus
    dx = (plus - minus).abs() / total.where(total > 0) * 100
    dx = dx.mask(total == 0, 0.0)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
