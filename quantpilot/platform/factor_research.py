"""Descriptive factor checks: purged split, frozen tails, no trading promotion."""

import math

import pandas as pd

from quantpilot.factors import DEFINITIONS, features
from quantpilot.strategy import signal_warmup


def finite(value):
    return float(value) if value is not None and math.isfinite(float(value)) else None


def rank_correlation(frame):
    if (
        len(frame) < 30
        or frame.iloc[:, 0].nunique() < 3
        or frame.iloc[:, 1].nunique() < 3
    ):
        return None
    # Ranking then Pearson needs no optional scipy dependency.
    return finite(frame.iloc[:, 0].rank().corr(frame.iloc[:, 1].rank()))


def analyze(
    bars,
    rules,
    *,
    start_at=None,
    end_at=None,
    split_at=None,
    fee_rate=0.00055,
    slippage_bps=2,
):
    frame = bars.reset_index(drop=True)
    stamps = pd.to_datetime(frame.timestamp, utc=True)
    valid = pd.Series(True, index=frame.index)
    if start_at:
        valid &= stamps >= pd.Timestamp(start_at)
    if end_at:
        valid &= stamps < pd.Timestamp(end_at)
    warmup = max(
        signal_warmup(rules),
        21,
        2 * rules.get("adx_period", 14) + 1,
        rules.get("volume_period", 20) + 1,
    )
    valid.iloc[:warmup] = False
    indices = frame.index[valid]
    if len(indices) < 2:
        return {
            "version": 1,
            "rows": [],
            "redundancy": [],
            "warnings": ["因子诊断缺少有效数据，请扩大研究区间"],
            "limitations": [],
            "affects_execution": False,
        }
    boundary = (
        pd.Timestamp(split_at)
        if split_at
        else stamps.iloc[indices[int(len(indices) * 0.7)]]
    )
    values = features(frame, rules)
    rows, redundancy = [], []
    cost = 2 * fee_rate + 2 * slippage_bps / 10000
    for horizon in (1, 4, 12):
        entry = frame.open.shift(-1)
        # Closed signal at t enters t+1 open, exits t+1+h open.
        forward = frame.open.shift(-(horizon + 1)) / entry - 1
        exit_stamp = stamps.shift(-(horizon + 1))
        in_range = valid & forward.notna() & entry.gt(0)
        if end_at:
            in_range &= exit_stamp < pd.Timestamp(end_at)
        train = in_range & (stamps < boundary) & (exit_stamp < boundary)
        test = in_range & (stamps >= boundary)
        # Every horizon bars prevents overlapping holding intervals per phase.
        train_indices = frame.index[train][::horizon]
        test_indices = frame.index[test][::horizon]
        for key, (label, _description, _unit) in DEFINITIONS.items():
            pairs = pd.DataFrame({"factor": values[key], "forward": forward}).dropna()
            training = pairs.loc[pairs.index.intersection(train_indices)]
            testing = pairs.loc[pairs.index.intersection(test_indices)]
            train_ic, test_ic = rank_correlation(training), rank_correlation(testing)
            direction = (
                1
                if train_ic is not None and train_ic >= 0
                else -1
                if train_ic is not None
                else None
            )
            lower = (
                finite(training.factor.quantile(0.2)) if len(training) >= 30 else None
            )
            upper = (
                finite(training.factor.quantile(0.8)) if len(training) >= 30 else None
            )
            tail_mean = edge = None
            tail_count = 0
            if (
                direction is not None
                and lower is not None
                and upper is not None
                and upper > lower
            ):
                high = testing.loc[testing.factor >= upper, "forward"]
                low = testing.loc[testing.factor <= lower, "forward"]
                if len(high) >= 5 and len(low) >= 5:
                    long_returns, short_returns = (
                        (high, low) if direction == 1 else (low, high)
                    )
                    samples = pd.concat([long_returns, -short_returns])
                    tail_count = len(samples)
                    tail_mean = finite(samples.mean())
                    # Per-trade arithmetic diagnostic, never a portfolio return.
                    edge = finite((samples - cost).mean())
            rows.append(
                {
                    "key": key,
                    "label": label,
                    "horizon": horizon,
                    "train_samples": len(training),
                    "test_samples": len(testing),
                    "train_ic": train_ic,
                    "test_ic": test_ic,
                    "direction": direction,
                    "lower": lower,
                    "upper": upper,
                    "tail_samples": tail_count,
                    "tail_gross_mean": tail_mean,
                    "tail_net_mean": edge,
                }
            )
    training_values = values.loc[valid & (stamps < boundary)]
    for index, key in enumerate(DEFINITIONS):
        for other in list(DEFINITIONS)[index + 1 :]:
            correlation = rank_correlation(training_values[[key, other]].dropna())
            if correlation is not None and abs(correlation) >= 0.8:
                redundancy.append(
                    {
                        "left": DEFINITIONS[key][0],
                        "right": DEFINITIONS[other][0],
                        "correlation": correlation,
                    }
                )
    return {
        "version": 1,
        "split_at": boundary.isoformat(),
        "split_kind": "specified" if split_at else "internal_70_30",
        "rows": rows,
        "redundancy": redundancy,
        "cost_per_trade": cost,
        "affects_execution": False,
        "warnings": ["部分因子或观察期限样本不足，空值不代表因子无效"]
        if any(row["test_ic"] is None for row in rows)
        else [],
        "limitations": [
            "这是单币时间序列的秩相关诊断，不是多币横截面IC；相关不等于因果或盈利证明",
            "方向及20%／80%阈值仅在研究段确定；验证段不用于选择或排序因子",
            "内部70%／30%切分是诊断留出段，不替代独立后续验证；反复查看后调参会消耗样本外独立性",
            "尾部均值仅为不重叠样本的每次方向收益，扣双向手续费及滑点；未计资金费、强平、仓位及组合限制",
            "没有自动搜索因子权重，也没有根据这些诊断启用交易",
        ],
    }


def ablation(rules, evaluate):
    """Remove one configured filter at a time; no threshold tuning or reranking."""
    candidates = [("当前完整规则", dict(rules))]
    for key, label in (
        ("volume_ratio", "成交量过滤"),
        ("atr_pct_max", "ATR波动过滤"),
        ("adx_min", "ADX趋势过滤"),
    ):
        if rules.get(key, 0) > 0:
            candidates.append((f"仅关闭{label}", {**rules, key: 0}))
    if len(candidates) == 1:
        return []
    return [
        {"label": label, "rules": candidate, "metrics": evaluate(candidate)}
        for label, candidate in candidates
    ]
