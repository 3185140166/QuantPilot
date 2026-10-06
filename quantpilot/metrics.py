"""Backtest report metrics."""

from __future__ import annotations

from .backtest import BacktestResult


def report(result: BacktestResult) -> dict[str, float]:
    curve = result.equity_curve.dropna()
    if curve.empty:
        return {
            "final_equity": result.final_equity,
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "trades": float(len(result.trades)),
            "win_rate": 0.0,
            "average_trade_pnl": 0.0,
        }
    peak = curve.cummax()
    drawdown = (curve - peak) / peak
    wins = sum(trade.pnl > 0 for trade in result.trades)
    return {
        "final_equity": result.final_equity,
        "total_return": result.total_return,
        "max_drawdown": float(drawdown.min()),
        "trades": float(len(result.trades)),
        "win_rate": wins / len(result.trades) if result.trades else 0.0,
        "average_trade_pnl": sum(t.pnl for t in result.trades) / len(result.trades)
        if result.trades
        else 0.0,
    }
