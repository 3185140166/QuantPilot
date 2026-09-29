"""Market-data normalization and closed-bar handling."""

from __future__ import annotations

from typing import Any

import pandas as pd

KLINE_COLUMNS = [
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
]


def klines_to_frame(rows: list[list[Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=KLINE_COLUMNS)
    if frame.empty:
        return frame
    numeric = ["open", "high", "low", "close", "volume", "turnover"]
    frame[numeric] = frame[numeric].astype(float)
    frame["timestamp"] = pd.to_datetime(
        frame["timestamp"].astype("int64"), unit="ms", utc=True
    )
    return frame.sort_values("timestamp").reset_index(drop=True)


def closed_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """Drop the newest bar, which can still be forming on REST responses."""
    if len(frame) < 2:
        return frame.iloc[0:0].copy()
    return frame.iloc[:-1].copy().reset_index(drop=True)
