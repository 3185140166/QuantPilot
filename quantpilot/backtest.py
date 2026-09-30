"""Small cost-aware backtest engine for research, not execution."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

import numpy as np
import pandas as pd

from .strategy import generate_signals


@dataclass(frozen=True)
class Trade:
    side: str
    entry_time: object
    exit_time: object
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float


@dataclass(frozen=True)
class BacktestResult:
    final_equity: float
    equity_curve: pd.Series
    trades: list[Trade]

    @property
    def total_return(self) -> float:
        if self.equity_curve.empty or self.equity_curve.iloc[0] == 0:
            return 0.0
        return self.final_equity / float(self.equity_curve.iloc[0]) - 1


def run_backtest(
    bars: pd.DataFrame,
    initial_equity: float = 10_000.0,
    quantity: float = 0.001,
    fee_rate: float = 0.00055,
    slippage_bps: float = 2.0,
) -> BacktestResult:
    required = {"timestamp", "open", "close"}
    if not required.issubset(bars.columns):
        raise ValueError(f"bars must contain {sorted(required)}")
    for name, value in {"initial_equity": initial_equity, "quantity": quantity}.items():
        if not isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive")
    if not isfinite(fee_rate) or not 0 <= fee_rate < 1:
        raise ValueError("fee_rate must be in [0, 1)")
    if not isfinite(slippage_bps) or not 0 <= slippage_bps < 10000:
        raise ValueError("slippage_bps must be in [0, 10000)")
    if (
        bars["timestamp"].isna().any()
        or not bars["timestamp"].is_monotonic_increasing
        or bars["timestamp"].duplicated().any()
    ):
        raise ValueError("timestamps must be present, unique and increasing")
    prices = bars[["open", "close"]].to_numpy(dtype=float)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("prices must be finite and positive")
    if len(bars) < 63:
        return BacktestResult(initial_equity, pd.Series(dtype=float), [])
    equity = float(initial_equity)
    position: tuple[str, float, object] | None = None
    trades: list[Trade] = []
    curve: list[tuple[object, float]] = [(bars.iloc[61]["timestamp"], equity)]

    def execution_price(price: float, side: str) -> float:
        impact = slippage_bps / 10_000
        return price * (1 + impact if side == "BUY" else 1 - impact)

    def close_position(price: float, timestamp: object) -> None:
        nonlocal equity, position
        if position is None:
            return
        old_side, entry, entry_time = position
        exit_price = execution_price(
            float(price), "SELL" if old_side == "LONG" else "BUY"
        )
        gross = (
            (exit_price - entry) * quantity
            if old_side == "LONG"
            else (entry - exit_price) * quantity
        )
        fees = (entry + exit_price) * quantity * fee_rate
        equity += gross - fees
        trades.append(
            Trade(
                old_side,
                entry_time,
                timestamp,
                entry,
                exit_price,
                quantity,
                gross - fees,
            )
        )
        position = None

    signals = generate_signals(bars).to_numpy()
    for index in range(61, len(bars) - 1):
        signal = signals[index]
        next_bar = bars.iloc[index + 1]
        if position is not None and (
            (position[0] == "LONG" and signal == "SHORT")
            or (position[0] == "SHORT" and signal == "LONG")
        ):
            close_position(float(next_bar["open"]), next_bar["timestamp"])
        if position is None and signal in {"LONG", "SHORT"}:
            side = "LONG" if signal == "LONG" else "SHORT"
            entry = execution_price(
                float(next_bar["open"]), "BUY" if side == "LONG" else "SELL"
            )
            position = (side, entry, next_bar["timestamp"])
        curve.append((next_bar["timestamp"], equity))
    if position is not None:
        last = bars.iloc[-1]
        close_position(float(last["close"]), last["timestamp"])
    if curve:
        curve[-1] = (curve[-1][0], equity)
    equity_curve = pd.Series(
        {timestamp: value for timestamp, value in curve}, dtype=float
    )
    return BacktestResult(equity, equity_curve, trades)
