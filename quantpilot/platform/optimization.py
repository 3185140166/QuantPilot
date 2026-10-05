"""Checkpointed TPE search. Holdout prices never enter the search objective."""

import copy
import hashlib
import json
import uuid
from dataclasses import asdict
from pathlib import Path

import pandas as pd
from pydantic import ValidationError
from sqlalchemy import select

from quantpilot.backtest import run_backtest
from quantpilot.metrics import report
from quantpilot.strategy import signal_warmup

from .engine import guard_lease
from .market import INTERVAL_MINUTES, HistoryError
from .models import BacktestJob, OptimizationJob, utcnow
from .rules import StrategyRules
from .schemas import OptimizationCreate
from .search_space import SPACE


class OptimizationCancelled(Exception):
    pass


def check_active(factory, owner, job_id):
    with factory.begin() as db:
        guard_lease(db, "research", owner)
        job = db.get(OptimizationJob, job_id)
        if job.status in {"cancelling", "cancelled"}:
            raise OptimizationCancelled()


def checkpoint(factory, owner, job_id, state):
    with factory.begin() as db:
        guard_lease(db, "research", owner)
        job = db.get(OptimizationJob, job_id)
        if job.status in {"cancelling", "cancelled"}:
            raise OptimizationCancelled()
        job.result = copy.deepcopy(state)
        job.completed_trials = len(state.get("trials", []))


def constraint_reasons(metrics, config, positive=False):
    reasons = []
    if metrics["trades"] < config.min_trades:
        reasons.append(f"成交次数少于 {config.min_trades}")
    if -metrics["max_drawdown"] * 100 > config.max_drawdown_pct:
        reasons.append(f"最大回撤超过 {config.max_drawdown_pct:g}%")
    if positive and metrics["total_return"] <= 0:
        reasons.append("验证收益未达到正值")
    return reasons


def evaluate(frame, config, rules, start, end):
    step = pd.Timedelta(minutes=INTERVAL_MINUTES[config.base.interval])
    first = pd.Timestamp(start) - step * (config._history_warmup + 1)
    selected = frame.loc[
        (frame.timestamp >= first) & (frame.timestamp + step <= pd.Timestamp(end))
    ].reset_index(drop=True)
    result = run_backtest(
        selected,
        initial_equity=config.base.initial_equity,
        quantity=config.base.quantity,
        fee_rate=config.base.fee_rate,
        slippage_bps=config.base.slippage_bps,
        rules=rules,
        max_notional=config.base.max_notional,
        trade_start=start,
    )
    return result, selected


def result_payload(result, bars, params, dataset):
    trades = []
    for trade in result.trades:
        row = asdict(trade)
        row["entry_time"], row["exit_time"] = (
            str(row["entry_time"]),
            str(row["exit_time"]),
        )
        trades.append(row)
    return {
        "metrics": report(result),
        "trades": trades,
        "source": params["source"],
        "equity_kind": "marked",
        "rules": params["rules"],
        "risk_halted": result.risk_halted,
        "rejected_entries": result.rejected_entries,
        "dataset": {
            **dataset,
            "start_at": str(bars.timestamp.iloc[0]),
            "end_at": str(bars.timestamp.iloc[-1]),
        },
        "bars": len(bars),
        "curve": [
            {"timestamp": str(stamp), "equity": float(value)}
            for stamp, value in result.equity_curve.items()
        ],
    }


def run_optimization(factory, market, owner, job_id):
    import optuna
    from optuna.distributions import FloatDistribution, IntDistribution
    from optuna.trial import create_trial

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    with factory() as db:
        job = db.get(OptimizationJob, job_id)
        params = copy.deepcopy(job.parameters)
        state = copy.deepcopy(job.result or {"trials": []})
    config = OptimizationCreate.model_validate(
        {key: value for key, value in params.items() if key != "source"}
    )
    if params["source"] != market.source:
        raise HistoryError("优化任务的行情来源与当前回测进程不一致")
    maximum = config.base.rules.model_dump()
    maximum.update({key: bounds.high for key, bounds in config.ranges.items()})
    # Include filters even when their base value is disabled but the range enables them.
    config._history_warmup = int(signal_warmup(maximum))
    check_active(factory, owner, job_id)
    if state.get("dataset"):
        if state.get("optuna_version", optuna.__version__) != optuna.__version__:
            raise HistoryError("优化库版本已改变，请保留旧结果并新建优化任务")
        dataset = state["dataset"]
        path = Path(dataset["path"])
        if (
            not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != dataset["sha256"]
        ):
            raise HistoryError("已保存的优化行情样本缺失或指纹不一致，不能继续该任务")
        frame = pd.read_parquet(path)
    else:
        frame = market.history(
            config.base.exchange,
            config.base.symbol,
            config.base.interval,
            config.base.start_at,
            config.base.end_at,
            config._history_warmup,
        )
        target = Path("storage/market")
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"optimization-{job_id}-{uuid.uuid4().hex}.parquet"
        frame.to_parquet(path, index=False)
        dataset = {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "warmup_bars": config._history_warmup,
        }
        state.update(
            dataset=dataset,
            split_at=config.split_at.isoformat(),
            ranking="研究评分排序；验证数据不参与参数搜索",
            candidates=[],
            algorithm="Optuna TPE",
            optuna_version=optuna.__version__,
            config_version=config.base.rules.version,
        )
        checkpoint(factory, owner, job_id, state)
    distributions = {}
    for key, bounds in config.ranges.items():
        distributions[key] = (
            IntDistribution(int(bounds.low), int(bounds.high), step=int(bounds.step))
            if SPACE[key][3] == "int"
            else FloatDistribution(bounds.low, bounds.high, step=bounds.step)
        )
    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(
            seed=config.seed + len(state["trials"]), multivariate=False
        ),
    )
    for record in state["trials"]:
        study.add_trial(
            create_trial(
                params=record["sampled"],
                distributions=distributions,
                value=record["score"],
            )
        )
    while len(state["trials"]) < config.trials:
        check_active(factory, owner, job_id)
        trial = study.ask()
        sampled = {}
        for key, bounds in config.ranges.items():
            sampled[key] = (
                trial.suggest_int(
                    key, int(bounds.low), int(bounds.high), step=int(bounds.step)
                )
                if SPACE[key][3] == "int"
                else trial.suggest_float(key, bounds.low, bounds.high, step=bounds.step)
            )
        rules = {**config.base.rules.model_dump(), **sampled}
        try:
            rules = StrategyRules.model_validate(rules).model_dump()
            training, _ = evaluate(
                frame, config, rules, config.base.start_at, config.split_at
            )
            metrics = report(training)
            reasons = constraint_reasons(metrics, config)
            objective = metrics["total_return"] - (
                config.drawdown_penalty * abs(metrics["max_drawdown"])
                if config.objective == "return_drawdown"
                else 0
            )
            score = (
                objective
                if not reasons
                else -1000 - len(reasons) + max(-1, min(1, objective)) / 1000
            )
        except ValidationError:
            metrics, reasons, score = None, ["参数组合不合法"], -1000000.0
        study.tell(trial, score)
        state["trials"].append(
            {
                "number": trial.number,
                "sampled": sampled,
                "rules": rules,
                "train_metrics": metrics,
                "train_pass": not reasons,
                "reasons": reasons,
                "score": score,
            }
        )
        checkpoint(factory, owner, job_id, state)
    candidates, seen = [], set()
    # Select and rank using training only; no repeated search against the holdout.
    for record in sorted(
        state["trials"],
        key=lambda item: (item["train_pass"], item["score"]),
        reverse=True,
    ):
        key = json.dumps(record["rules"], sort_keys=True)
        if record["train_metrics"] is None or key in seen:
            continue
        seen.add(key)
        candidates.append(copy.deepcopy(record))
        if len(candidates) == config.top_k:
            break
    pending_backtests = []
    step = pd.Timedelta(minutes=INTERVAL_MINUTES[config.base.interval])
    validation_count = int(
        (pd.Timestamp(config.base.end_at) - pd.Timestamp(config.split_at)) / step
    )
    for candidate in candidates:
        check_active(factory, owner, job_id)
        validation, bars = evaluate(
            frame, config, candidate["rules"], config.split_at, config.base.end_at
        )
        metrics = report(validation)
        reasons = constraint_reasons(
            metrics, config, config.require_positive_validation
        )
        windows = []
        for index in range(config.validation_windows):
            start = pd.Timestamp(config.split_at) + step * (
                validation_count * index // config.validation_windows
            )
            end = pd.Timestamp(config.split_at) + step * (
                validation_count * (index + 1) // config.validation_windows
            )
            window = (
                validation
                if config.validation_windows == 1
                else evaluate(frame, config, candidate["rules"], start, end)[0]
            )
            window_metrics = report(window)
            window_reasons = constraint_reasons(
                window_metrics, config, config.require_positive_validation
            )
            windows.append(
                {
                    "start_at": start.isoformat(),
                    "end_at": end.isoformat(),
                    "metrics": window_metrics,
                    "passed": not window_reasons,
                    "reasons": window_reasons,
                }
            )
        if any(not window["passed"] for window in windows):
            reasons.append("部分独立验证窗口未通过约束")
        eligible = candidate["train_pass"] and not reasons
        candidate.update(
            validation_metrics=metrics,
            validation_pass=not reasons,
            validation_reasons=reasons,
            windows=windows,
            eligible=eligible,
        )
        backtest_params = {
            **config.base.model_dump(mode="json"),
            "rules": candidate["rules"],
            "start_at": config.split_at.isoformat(),
            "evaluation_phase": "validation",
            "source": market.source,
            "optimization_id": job_id,
            "optimization_trial": candidate["number"],
            "optimization_eligible": eligible,
        }
        pending_backtests.append(
            (
                candidate,
                backtest_params,
                result_payload(validation, bars, backtest_params, dataset),
            )
        )
    with factory.begin() as db:
        guard_lease(db, "research", owner)
        job = db.scalar(
            select(OptimizationJob)
            .where(OptimizationJob.id == job_id)
            .with_for_update()
        )
        if job.status in {"cancelling", "cancelled"}:
            raise OptimizationCancelled()
        for candidate, backtest_params, payload in pending_backtests:
            backtest = BacktestJob(
                status="completed",
                parameters=backtest_params,
                result=payload,
                completed_at=utcnow(),
            )
            db.add(backtest)
            db.flush()
            candidate["backtest_id"] = backtest.id
        state["candidates"] = candidates
        job.result = state
        job.status, job.completed_at = "completed", utcnow()
