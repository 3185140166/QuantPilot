"""Bounded diagnostics. Holdout results never rerank the original search."""

import copy
import json

import pandas as pd

from quantpilot.metrics import report
from quantpilot.strategy import signal_warmup

from .market import INTERVAL_MINUTES
from .rules import StrategyRules


def neighbors(rules):
    rules = StrategyRules.model_validate(rules).model_dump()
    keys = {
        "ema_cross": ["fast", "slow"],
        "ema_rsi": ["fast", "slow"],
        "rsi_reversion": ["rsi_period"],
        "breakout": ["breakout_period"],
        "bollinger_reversion": ["bollinger_period"],
    }[rules["strategy_type"]]
    found = [rules]
    for key in keys:
        for multiplier in (0.9, 1.1):
            candidate = {**rules, key: max(2, round(rules[key] * multiplier))}
            try:
                candidate = StrategyRules.model_validate(candidate).model_dump()
            except ValueError:
                continue
            if candidate not in found:
                found.append(candidate)
    return found


def diagnose(frame, config, rules, evaluate, guard=lambda: None):
    """Local sensitivity on research only; rolling train/select/test after split."""
    candidates = [
        r for r in neighbors(rules) if signal_warmup(r) <= config._history_warmup
    ]
    if not candidates:
        raise ValueError("稳健性检查缺少参数预热数据")
    sensitivity = []
    step = pd.Timedelta(minutes=INTERVAL_MINUTES[config.base.interval])
    research_frame = frame.loc[frame.timestamp + step <= pd.Timestamp(config.split_at)]
    for candidate in candidates:
        guard()
        result, _ = evaluate(
            research_frame, config, candidate, config.base.start_at, config.split_at
        )
        sensitivity.append({"rules": candidate, "metrics": report(result)})
    stress = []
    for multiplier in (1, 2, 3):
        guard()
        costs = copy.deepcopy(config)
        costs.base.fee_rate = min(0.1, config.base.fee_rate * multiplier)
        costs.base.slippage_bps = min(1000, config.base.slippage_bps * multiplier)
        result, _ = evaluate(frame, costs, rules, config.split_at, config.base.end_at)
        stress.append(
            {
                "multiplier": multiplier,
                "fee_rate": costs.base.fee_rate,
                "slippage_bps": costs.base.slippage_bps,
                "metrics": report(result),
            }
        )
    count = int(
        (pd.Timestamp(config.base.end_at) - pd.Timestamp(config.split_at)) / step
    )
    folds = min(3, count // 100)
    duration = pd.Timestamp(config.split_at) - pd.Timestamp(config.base.start_at)
    rolling = []
    for index in range(folds):
        start = pd.Timestamp(config.split_at) + step * (count * index // folds)
        end = pd.Timestamp(config.split_at) + step * (count * (index + 1) // folds)
        train_start = start - duration
        ranked = []
        past = frame.loc[frame.timestamp + step <= start]
        for candidate in candidates:
            guard()
            train, _ = evaluate(past, config, candidate, train_start, start)
            metrics = report(train)
            score = metrics["total_return"] - (
                config.drawdown_penalty * abs(metrics["max_drawdown"])
                if config.objective == "return_drawdown"
                else 0
            )
            passes = (
                metrics["trades"] >= config.min_trades
                and -metrics["max_drawdown"] * 100 <= config.max_drawdown_pct
            )
            ranked.append(
                (
                    passes,
                    score,
                    json.dumps(candidate, sort_keys=True),
                    candidate,
                    metrics,
                )
            )
        chosen = max(ranked, key=lambda row: row[:3])
        guard()
        tested, _ = evaluate(frame, config, chosen[3], start, end)
        rolling.append(
            {
                "train_start": train_start.isoformat(),
                "train_end": start.isoformat(),
                "test_start": start.isoformat(),
                "test_end": end.isoformat(),
                "rules": chosen[3],
                "train_metrics": chosen[4],
                "train_pass": chosen[0],
                "test_metrics": report(tested),
            }
        )
    baseline = sensitivity[0]["metrics"]["total_return"]
    warnings = []
    if config.base.fee_rate == 0 and config.base.slippage_bps == 0:
        warnings.append(
            "基础手续费和滑点均为零，倍数测试没有增加实际成本，请使用合理成本重新研究"
        )
    if baseline > 0 and any(r["metrics"]["total_return"] <= 0 for r in sensitivity[1:]):
        warnings.append("邻近参数在研究区间转为亏损，结果可能对参数较敏感")
    if stress[-1]["metrics"]["total_return"] <= 0:
        warnings.append("三倍手续费与滑点压力下验证收益非正")
    if any(
        not r["train_pass"]
        or r["test_metrics"]["trades"] < config.min_trades
        or r["test_metrics"]["total_return"] <= 0
        for r in rolling
    ):
        warnings.append("部分滚动窗口交易不足、研究约束未通过或样本外收益非正")
    return {
        "version": 1,
        "sensitivity": sensitivity,
        "cost_stress": stress,
        "rolling": rolling,
        "warnings": warnings,
        "affects_search_ranking": False,
        "limitations": [
            "邻域仅检查主要周期参数上下约10%，不覆盖完整参数空间",
            "滚动窗口每次从相同初始资金重放，不把分段收益直接累加为组合收益",
            "滚动诊断不包含跨窗口持仓延续；费用压力未补充单币回测所缺少的资金费与强平模型",
            "诊断不改变原候选排序和应用资格，验证结果不用于再次搜索原任务",
            "成本倍数在平台费率及滑点上限处截断，表格显示实际参数",
        ],
    }
