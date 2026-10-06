"""Explicit evidence checks; missing or untested evidence cannot pass a gate."""

import math


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def assess(result, params, config):
    metrics = result.get("metrics", {})
    review = result.get("strategy_review", {})
    checks = []

    def add(name, passed, explanation):
        checks.append(
            {"name": name, "passed": bool(passed), "explanation": explanation}
        )

    add(
        "后续独立验证",
        params.get("evaluation_phase") == "validation"
        and bool(params.get("validation_parent_job_id")),
        "需要关联研究任务的后续区间验证",
    )
    from .schemas import TestnetSetup

    source = (
        TestnetSetup.model_validate(params["portfolio_config"])
        if params.get("portfolio_config")
        else None
    )
    fields = (
        "budget",
        "risk_per_trade_pct",
        "portfolio_risk_pct",
        "daily_loss_pct",
        "max_drawdown_pct",
        "max_positions",
        "leverage",
        "max_correlation",
        "fee_rate",
        "slippage_bps",
        "rules",
        "interval",
        "selection",
        "cash_reserve_pct",
    )
    add(
        "组合与执行假设一致",
        source is not None
        and set(source.symbols) == set(config.symbols)
        and all(getattr(source, key) == getattr(config, key) for key in fields)
        and params.get("execution", {}).get("order_type") == "Market",
        "币种池、筛选、预算、风险及市价执行须与验证一致",
    )
    add(
        "样本交易数",
        (number(metrics.get("trades")) or 0) >= config.research_guard.min_trades,
        f"至少 {config.research_guard.min_trades} 笔已结算交易",
    )
    net = number(metrics.get("total_return"))
    add(
        "成本后收益",
        net is not None and net > 0,
        "费用后的历史收益需要为正，不代表未来盈利",
    )
    dd = number((result.get("risk_summary") or {}).get("max_drawdown_pct"))
    add(
        "风险上限",
        dd is not None and dd <= config.max_drawdown_pct,
        "预算口径回撤不得超过当前风险上限",
    )
    add(
        "资金费与强平",
        params.get("job_type") == "portfolio"
        and params.get("execution", {}).get("include_funding") is True
        and result.get("liquidations") == 0,
        "需要包含历史资金费且没有强平近似事件",
    )
    quality = result.get("data_quality") or {}
    warnings = (
        quality.get("warnings", [])
        if "bars" in quality
        else [
            warning
            for item in quality.values()
            if isinstance(item, dict)
            for warning in item.get("warnings", [])
        ]
    )
    informational = (
        "历史资金费覆盖依据接口已返回记录；结算频率可能变化，不将未返回记录补为零"
    )
    warnings = [warning for warning in warnings if warning != informational]
    audited = set(config.symbols).issubset(quality) and all(
        isinstance(quality[symbol], dict)
        and all(
            quality[symbol].get(kind, {}).get("fingerprint")
            and (quality[symbol].get(kind, {}).get("bars") or 0) > 0
            for kind in ("price", "mark")
        )
        for symbol in config.symbols
    )
    add(
        "数据完整性",
        audited and not warnings,
        "每个合约需有价格与标记价格审计，且没有缺失或异常警告；历史结算频率仍有研究限制",
    )
    stress = review.get("stress", [])
    add(
        "双倍成本压力",
        bool(stress)
        and all(
            (number(item.get("metrics", {}).get("total_return")) or 0) > 0
            and item.get("liquidations") == 0
            and (
                number(item.get("risk_summary", {}).get("max_drawdown_pct")) is not None
                and number(item["risk_summary"]["max_drawdown_pct"])
                <= config.max_drawdown_pct
            )
            for item in stress
        ),
        "手续费与滑点提高后收益、回撤均需通过，不自动改参数",
    )
    neighbors = review.get("neighbors", [])
    add(
        "研究参数稳定性",
        bool(neighbors)
        and all(
            (number(item.get("metrics", {}).get("total_return")) or 0) > 0
            for item in neighbors
        ),
        "研究段邻近参数结果应稳定；缺失检查视为未通过",
    )
    if config.research_guard.min_validation_windows > 1:
        rolling = result.get("rolling_validation") or {}
        add(
            "多窗口独立验证",
            rolling.get("complete") is True
            and rolling.get("passed") is True
            and (rolling.get("expected") or 0)
            >= config.research_guard.min_validation_windows,
            f"至少{config.research_guard.min_validation_windows}个后续窗口完成，且每个窗口均通过证据检查",
        )
    return {
        "eligible": all(row["passed"] for row in checks),
        "enforced": config.research_guard.enabled,
        "checks": checks,
        "failed": [row["name"] for row in checks if not row["passed"]],
        "limits": "这是历史证据准入规则，不是收益保证",
    }


def current_review(db, current):
    from .archive import load_result
    from .execution_status import configuration_fingerprint
    from .models import BacktestJob
    from .schemas import TestnetSetup

    config = TestnetSetup.model_validate(current.configuration)
    origin = current.research_origin or {}
    job = db.get(BacktestJob, origin.get("job_id")) if origin.get("job_id") else None
    if not job or job.status != "completed" or not job.result:
        return {
            "eligible": False,
            "enforced": config.research_guard.enabled,
            "checks": [],
            "failed": ["尚未关联完成的验证回测"],
        }
    try:
        result = dict(load_result(job.result))
        if config.research_guard.min_validation_windows > 1:
            from .rolling_validation import report as rolling_report

            result["rolling_validation"] = rolling_report(db, job, config)
        report = assess(result, job.parameters, config)
        applied = origin.get("applied_configuration")
        unchanged = (
            bool(applied)
            and not origin.get("modified")
            and configuration_fingerprint(applied)
            == configuration_fingerprint(current.configuration)
        )
        report["checks"].append(
            {
                "name": "参数与验证来源一致",
                "passed": unchanged,
                "explanation": "修改筛选、币种或风险参数后需重新研究验证",
            }
        )
        report["eligible"] &= unchanged
        if not unchanged:
            report["failed"].append("参数已修改或来源未记录")
        return report
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {
            "eligible": False,
            "enforced": config.research_guard.enabled,
            "checks": [],
            "failed": ["验证结果不可读取，需要重新验证"],
        }
