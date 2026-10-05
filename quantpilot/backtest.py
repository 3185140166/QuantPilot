"""Small cost-aware backtest engine for research, not execution."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

import numpy as np
import pandas as pd

from .strategy import SIGNAL_KEYS, generate_signals, signal_warmup


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
    realized_curve: pd.Series | None = None
    risk_halted: bool = False
    rejected_entries: int = 0

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
    rules: dict | None = None,
    max_notional: float | None = None,
    trade_start: object | None = None,
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
    config = rules or {}
    warmup = signal_warmup(config)
    if max_notional is not None and (not isfinite(max_notional) or max_notional <= 0):
        raise ValueError("max_notional must be positive")
    for key in ("stop_loss_pct", "take_profit_pct", "max_drawdown_pct"):
        if not isfinite(config.get(key, 0)) or config.get(key, 0) < 0:
            raise ValueError("risk percentages must be finite and nonnegative")
    if len(bars) < warmup + 2:
        return BacktestResult(initial_equity, pd.Series(dtype=float), [])
    equity = float(initial_equity)
    position: tuple[str, float, object] | None = None
    trades: list[Trade] = []
    start_index = warmup
    if trade_start is not None:
        indices = np.flatnonzero(
            (bars.timestamp >= pd.Timestamp(trade_start)).to_numpy()
        )
        if not len(indices):
            raise ValueError("No bars in trading range")
        start_index = max(warmup, int(indices[0]))
    curve: list[tuple[object, float]] = [(bars.iloc[start_index]["timestamp"], equity)]
    realized = list(curve)
    peak, halted, rejected = equity, False, 0

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
        equity += gross - exit_price * quantity * fee_rate
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

    options = {key: config[key] for key in SIGNAL_KEYS if key in config}
    signals = (
        generate_signals(bars, **options)
        if rules is not None
        else generate_signals(bars)
    ).to_numpy()
    timestamps = bars.timestamp.to_numpy()
    for index in range(start_index, len(bars) - 1):
        signal = signals[index]
        next_open, next_close = prices[index + 1]
        next_time = timestamps[index + 1]
        mark = float(prices[index, 1])
        marked_equity = equity + (
            (mark - position[1]) * quantity * (1 if position[0] == "LONG" else -1)
            if position
            else 0
        )
        peak = max(peak, marked_equity)
        risk_close = False
        if (
            config.get("max_drawdown_pct")
            and (peak - marked_equity) / peak * 100 >= config["max_drawdown_pct"]
        ):
            halted = True
            risk_close = True
        if position:
            change = (
                (mark / position[1] - 1) * (1 if position[0] == "LONG" else -1) * 100
            )
            risk_close |= bool(
                config.get("stop_loss_pct") and change <= -config["stop_loss_pct"]
            )
            risk_close |= bool(
                config.get("take_profit_pct") and change >= config["take_profit_pct"]
            )
        if position is not None and (
            risk_close
            or (
                (position[0] == "LONG" and signal == "SHORT")
                or (position[0] == "SHORT" and signal == "LONG")
            )
        ):
            close_position(float(next_open), next_time)
        if (
            position is None
            and signal in {"LONG", "SHORT"}
            and not risk_close
            and not halted
        ):
            side = "LONG" if signal == "LONG" else "SHORT"
            entry = execution_price(
                float(next_open), "BUY" if side == "LONG" else "SELL"
            )
            notional = entry * quantity
            if max_notional is not None and (
                notional > max_notional or notional * (1 + fee_rate) > equity
            ):
                rejected += 1
            else:
                position = (side, entry, next_time)
                equity -= notional * fee_rate
        marked = equity + (
            (float(next_close) - position[1])
            * quantity
            * (1 if position[0] == "LONG" else -1)
            if position
            else 0
        )
        peak = max(peak, marked)
        curve.append((next_time, marked))
        realized.append((next_time, equity))
    if position is not None:
        last = bars.iloc[-1]
        close_position(float(last["close"]), last["timestamp"])
    if curve:
        curve[-1] = (curve[-1][0], equity)
        realized[-1] = (realized[-1][0], equity)
    equity_curve = pd.Series(
        {timestamp: value for timestamp, value in curve}, dtype=float
    )
    return BacktestResult(
        equity,
        equity_curve,
        trades,
        pd.Series(dict(realized), dtype=float),
        halted,
        rejected,
    )
