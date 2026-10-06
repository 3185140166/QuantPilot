"""Descriptive same-time multi-contract diagnostics; never fits trading weights."""

import pandas as pd

from quantpilot.factors import DEFINITIONS, features
from quantpilot.strategy import signal_warmup


def analyze(frames, rules, start_at, end_at, horizon=4):
    pieces = []
    for symbol, bars in frames.items():
        values = features(bars, rules).copy()
        values["forward"] = bars.open.shift(-(horizon + 1)) / bars.open.shift(-1) - 1
        stamps = pd.to_datetime(bars.timestamp, utc=True)
        exit_stamps = stamps.shift(-(horizon + 1))
        valid = (
            (stamps >= pd.Timestamp(start_at))
            & (stamps < pd.Timestamp(end_at))
            & (exit_stamps < pd.Timestamp(end_at))
        )
        valid.iloc[: max(signal_warmup(rules), 30)] = False
        values["timestamp"], values["symbol"] = stamps, symbol
        pieces.append(values.loc[valid])
    panel = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()
    if not panel.empty:
        stamps = sorted(panel.timestamp.unique())[::horizon]
        panel = panel.loc[panel.timestamp.isin(stamps)].sort_values(
            ["timestamp", "symbol"]
        )
    rows = []
    for key, (label, _description, _unit) in DEFINITIONS.items():
        ics, spreads = pd.Series(dtype=float), pd.Series(dtype=float)
        if not panel.empty:
            group = panel.dropna(subset=[key, "forward"]).copy()
            grouped = group.groupby("timestamp")
            valid = (
                (grouped.symbol.transform("count") >= 3)
                & (grouped[key].transform("nunique") >= 3)
                & (grouped.forward.transform("nunique") >= 2)
            )
            group = group.loc[valid].copy()
            if not group.empty:
                grouped = group.groupby("timestamp")
                ranks = grouped[[key, "forward"]].rank()
                centered = ranks - ranks.groupby(group.timestamp).transform("mean")
                group["product"] = centered[key] * centered.forward
                group["factor_square"] = centered[key] ** 2
                group["return_square"] = centered.forward**2
                totals = group.groupby("timestamp")[
                    ["product", "factor_square", "return_square"]
                ].sum()
                ics = (
                    totals["product"]
                    / (totals.factor_square * totals.return_square) ** 0.5
                ).dropna()
                order = grouped[key].rank(method="first")
                size = grouped.symbol.transform("count")
                tail = (size // 3).clip(lower=1)
                high = (
                    group.loc[order > size - tail].groupby("timestamp").forward.mean()
                )
                low = group.loc[order <= tail].groupby("timestamp").forward.mean()
                spreads = (high - low).reindex(ics.index)
        rows.append(
            {
                "key": key,
                "label": label,
                "periods": len(ics),
                "mean_rank_ic": float(ics.mean()) if len(ics) >= 30 else None,
                "positive_fraction": float((ics > 0).mean())
                if len(ics) >= 30
                else None,
                "high_minus_low_gross": float(spreads.mean())
                if len(spreads) >= 30
                else None,
            }
        )
    return {
        "rows": rows,
        "symbols": list(frames),
        "horizon": horizon,
        "affects_execution": False,
        "limitations": [
            "同一时刻至少三个合约，至少三十个不重叠观察期才显示汇总；小候选池统计能力有限",
            "高减低为方向未翻转的毛收益，不计成本与仓位，不是可交易组合收益",
            "仅分析固定候选池，不使用验证结果调权重；不替代动态筛选组合回测",
            "当前合约池存在幸存者偏差；尚未还原历史上市和退市合约池",
        ],
    }
