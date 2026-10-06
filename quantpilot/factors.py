"""Causal market features. No forward returns or fitted thresholds live here."""

import math

import numpy as np
import pandas as pd

from .indicators import adx, atr, ema, rsi

DEFINITIONS = {
    "trend": ("均线趋势差", "快慢EMA差除以当前价格，描述趋势方向", "%"),
    "momentum": ("价格动量", "相对过去20根收盘价的涨跌幅", "%"),
    "rsi": ("RSI强弱", "价格涨跌强弱，既可过滤趋势也可观察回归", ""),
    "volume": ("成交量倍数", "当前成交量除以前期平均量，前期均值不含当前K线", "倍"),
    "volatility": ("ATR波动率", "ATR除以价格，衡量波动幅度，不决定多空方向", "%"),
    "strength": ("ADX趋势强度", "描述趋势强弱，不决定做多还是做空", ""),
}


def features(bars, rules):
    close = bars.close
    result = pd.DataFrame(index=bars.index)
    result["trend"] = (
        (ema(close, rules.get("fast", 20)) - ema(close, rules.get("slow", 60)))
        / close
        * 100
    )
    result["momentum"] = (close / close.shift(20) - 1) * 100
    result["rsi"] = rsi(close, rules.get("rsi_period", 14))
    result["volume"] = np.nan
    if "volume" in bars:
        previous = (
            bars.volume.rolling(
                rules.get("volume_period", 20),
                min_periods=rules.get("volume_period", 20),
            )
            .mean()
            .shift()
        )
        result["volume"] = bars.volume / previous.where(previous > 0)
    result["volatility"] = result["strength"] = np.nan
    if {"high", "low"}.issubset(bars.columns):
        result["volatility"] = atr(bars, rules.get("atr_period", 14)) / close * 100
        result["strength"] = adx(bars, rules.get("adx_period", 14))
    return result.replace([np.inf, -np.inf], np.nan)


def observations(bars, rules):
    if bars.empty:
        return []
    latest = features(bars, rules).iloc[-1]
    filters = {
        "volume": (rules.get("volume_ratio", 0), "min"),
        "volatility": (rules.get("atr_pct_max", 0), "max"),
        "strength": (rules.get("adx_min", 0), "min"),
    }
    rows = []
    for key, (label, description, unit) in DEFINITIONS.items():
        value = float(latest[key]) if pd.notna(latest[key]) else None
        if value is not None and not math.isfinite(value):
            value = None
        threshold, comparison = filters.get(key, (0, None))
        enabled = bool(threshold)
        passed = (
            (
                value is not None
                and (value >= threshold if comparison == "min" else value <= threshold)
            )
            if enabled
            else None
        )
        rows.append(
            {
                "key": key,
                "label": label,
                "description": description,
                "value": value,
                "unit": unit,
                "enabled": enabled,
                "threshold": threshold if enabled else None,
                "comparison": comparison,
                "passed": passed,
            }
        )
    return rows
