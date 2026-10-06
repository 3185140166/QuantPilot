"""Preview validated-label research rules before copying them to a paused assistant."""

import hashlib
import json

from .archive import load_result
from .rules import StrategyRules
from .schemas import TestnetSetup


def preview(job, current, symbols_mode="replace", db=None):
    if symbols_mode not in {"replace", "keep"}:
        raise ValueError("Invalid symbol selection mode")
    if job.status != "completed" or not job.result:
        raise ValueError("仅已完成的回测可以应用参数")
    params = job.parameters
    tested_symbols = (
        params["portfolio_config"]["symbols"]
        if params.get("job_type") == "portfolio"
        else [params["symbol"]]
    )
    if params.get("evaluation_phase") != "validation":
        raise ValueError("请先创建验证标签的回测，核对结果后再应用")
    if params.get("optimization_eligible") is False:
        raise ValueError("该优化候选未通过研究与独立验证约束")
    if params.get("source") != "exchange" or params.get("exchange") != "bybit":
        raise ValueError("只可应用 Bybit 交易所行情回测；演示数据不能作为交易参数来源")
    rules = StrategyRules.model_validate(params["rules"])
    if rules.stop_loss_pct <= 0:
        raise ValueError("交易参数必须设置正数止损")
    result = load_result(job.result)
    quality = result.get("data_quality") or {}
    quality_warnings = (
        quality.get("warnings", [])
        if "bars" in quality
        else [
            f"{symbol}：{warning}"
            for symbol, value in quality.items()
            if isinstance(value, dict)
            for warning in value.get("warnings", [])
        ]
    )
    before = TestnetSetup.model_validate(current.configuration).model_dump(mode="json")
    data = before.copy()
    data.update(
        rules=rules.model_dump(mode="json"),
        symbols=tested_symbols if symbols_mode == "replace" else before["symbols"],
        interval=params["interval"],
        fee_rate=params["fee_rate"],
        slippage_bps=params["slippage_bps"],
    )
    if params.get("job_type") == "portfolio":
        data["selection"] = params["portfolio_config"].get("selection", {})
    candidate = TestnetSetup.model_validate(data)
    from .strategy_review import assess

    if db is not None and candidate.research_guard.min_validation_windows > 1:
        from .rolling_validation import report as rolling_report

        result = {**result, "rolling_validation": rolling_report(db, job, candidate)}
    qualification = assess(result, params, candidate)
    proposed = candidate.model_dump(mode="json")
    unvalidated = [
        symbol for symbol in candidate.symbols if symbol not in tested_symbols
    ]
    source_risk = params.get("portfolio_config") or params.get("risk_sizing") or {}
    differences = []
    for field, value in before.items():
        if field == "rules":
            for key, rule in value.items():
                differences.append(
                    {
                        "field": f"rules.{key}",
                        "before": rule,
                        "after": proposed["rules"][key],
                        "research": rules.model_dump()[key],
                        "changed": rule != proposed["rules"][key],
                    }
                )
        else:
            research = (
                tested_symbols
                if field == "symbols"
                else source_risk.get(field, params.get(field))
            )
            differences.append(
                {
                    "field": field,
                    "before": value,
                    "after": proposed[field],
                    "research": research,
                    "changed": value != proposed[field],
                }
            )
    identity = {
        "qualification": qualification,
        "research_evidence": result.get("strategy_review"),
        "rolling_validation": result.get("rolling_validation"),
        "data_quality": result.get("data_quality"),
        "job_id": job.id,
        "parameters": params,
        "metrics": result.get("metrics"),
        "risk_summary": result.get("risk_summary"),
        "tested_symbols": tested_symbols,
        "execution": params.get("execution"),
        "dataset": result.get("dataset"),
        "configuration": candidate.model_dump(mode="json"),
        "configuration_before": before,
        "symbols_mode": symbols_mode,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            identity, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()
    return {
        "job_id": job.id,
        "fingerprint": fingerprint,
        "qualification": qualification,
        "configuration": candidate.model_dump(mode="json"),
        "rolling_validation": result.get("rolling_validation"),
        "configuration_before": before,
        "symbols_mode": symbols_mode,
        "differences": differences,
        "unvalidated_symbols": unvalidated,
        "metrics": result.get("metrics", {}),
        "dataset": result.get("dataset", {}),
        "market_environment": params.get("market_environment", "未记录"),
        "risk_sizing": params.get("risk_sizing"),
        "risk_summary": result.get("risk_summary"),
        "warnings": [
            "此回测与执行共用止损风险仓位算法；仍需核对预算、风险设置和合约限制快照是否与当前配置一致"
            if params.get("risk_sizing") or params.get("job_type") == "portfolio"
            else "此回测使用固定数量；执行数量仍按共享预算与止损风险重新计算",
            "验证标签不代表盈利证明。组合回测包含资金费、逐仓强平和部分成交的研究模型，详细限制见回测结果，不还原真实订单簿"
            if params.get("job_type") == "portfolio"
            else "验证标签只记录区间用途，不代表策略已被证明盈利；尚未模拟历史资金费、保证金强平与订单簿成交，周期对比为单币结果",
            "应用后保持暂停，需另行启动测试网自动交易",
        ]
        + (result.get("robustness") or {}).get("warnings", [])
        + quality_warnings
        + (
            [
                "组合回测的成交量参与率、限价挂单及强平近似只用于研究，不会改变当前交易助手的市价执行方式；组合与资金设置不同会改变结果"
            ]
            if params.get("job_type") == "portfolio"
            else []
        )
        + (
            [
                f"保留币种中尚未通过此回测验证的币种：{'、'.join(unvalidated)}；相同规则会应用于全部保留币种，需要单独确认"
            ]
            if unvalidated
            else []
        )
        + (
            [
                "这项历史回测未记录统一风控版本，日亏损／回撤可能按旧口径计算；建议按当前设置重新回测"
            ]
            if (params.get("risk_sizing") or params.get("job_type") == "portfolio")
            and (result.get("risk_summary") or {}).get("policy") != "fixed_budget_v1"
            else []
        ),
    }
