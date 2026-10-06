"""Hourly direction from complete UTC quarter-hour candles, without lookahead."""

import numpy as np
import pandas as pd


def hourly_direction(bars, fast=20, slow=60, minimum_gap_pct=0.05):
    """Index by input row; an hourly close is available at its closing time."""
    if "timestamp" not in bars:
        raise ValueError("多周期策略需要带时间戳的15分钟K线")
    stamps = pd.to_datetime(bars.timestamp, utc=True).dt.as_unit("ns")
    if (
        stamps.isna().any()
        or stamps.duplicated().any()
        or not stamps.is_monotonic_increasing
    ):
        raise ValueError("多周期K线时间戳异常")
    if ((stamps.astype("int64") // 10**9) % 900).any() or (
        len(stamps) > 1 and (stamps.diff().dropna() < pd.Timedelta(minutes=15)).any()
    ):
        raise ValueError("多周期策略仅支持15分钟入场")
    frame = pd.DataFrame(
        {"close": bars.close.to_numpy()}, index=pd.DatetimeIndex(stamps)
    )
    groups = frame.resample("1h", label="right", closed="left")
    hourly = groups.last().close
    complete = groups.count().close.eq(4)
    # Use the same finite hourly history in long research and short live reads.
    # Normalized exponential weights avoid dependence on a remote EMA seed.
    window = slow + 1

    def bounded_ema(period):
        decay = 1 - 2 / (period + 1)
        weights = decay ** np.arange(window - 1, -1, -1)
        weights /= weights.sum()
        return (
            hourly.where(complete)
            .rolling(window, min_periods=window)
            .apply(lambda values: np.dot(values, weights), raw=True)
        )

    quick, long = bounded_ema(fast), bounded_ema(slow)
    gap = (quick - long) / long * 100
    direction = pd.Series("HOLD", index=hourly.index, dtype=object)
    ready = complete & long.gt(0)
    direction.loc[ready & gap.gt(minimum_gap_pct)] = "LONG"
    direction.loc[ready & gap.lt(-minimum_gap_pct)] = "SHORT"
    decisions = pd.DatetimeIndex(stamps + pd.Timedelta(minutes=15))
    aligned = direction.reindex(decisions, method="ffill").fillna("HOLD")
    return pd.Series(aligned.to_numpy(), index=bars.index, dtype=object)
