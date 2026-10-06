"""Queued portfolio research; no private account reads or order submission."""

import hashlib
from pathlib import Path

import pandas as pd

from quantpilot.portfolio import run_portfolio

from .contract_history import load_inputs, snapshot
from .cross_sectional import analyze as cross_analysis
from .data_quality import contract_audit
from .engine import guard_lease
from .factor_research import analyze
from .market import HistoryError
from .models import BacktestJob
from .schemas import TestnetSetup


def execute(factory, market, owner, job_id, params):
    config = TestnetSetup.model_validate(params["portfolio_config"])
    from quantpilot.strategy import signal_warmup

    from .robustness import neighbors

    history_warmup = max(
        100, *(signal_warmup(rules) for rules in neighbors(config.rules.model_dump()))
    )
    profile = market.exchange_settings("bybit", config.symbols[0], config.interval)
    active = "testnet" if profile.testnet else "mainnet"
    if params.get("market_environment") != active:
        raise HistoryError("组合任务的环境与研究进程不一致，请重启后重新创建")
    contracts = params.get("contracts")
    if not contracts:
        contracts = {
            symbol: snapshot(market, symbol, config.interval)
            for symbol in config.symbols
        }
        params["contracts"] = contracts
        with factory.begin() as db:
            guard_lease(db, "research", owner)
            db.get(BacktestJob, job_id).parameters = params.copy()
    frames, marks, funding, saved = {}, {}, {}, []
    coverage, quality = {}, {}
    for symbol in config.symbols:
        frames[symbol], marks[symbol], funding[symbol] = load_inputs(
            market,
            symbol,
            config.interval,
            params["start_at"],
            params["end_at"],
            history_warmup,
            params["execution"]["include_funding"],
        )
        for kind, frame in (("price", frames[symbol]), ("mark", marks[symbol])):
            saved.append(frame.assign(symbol=symbol, kind=kind))
        if funding[symbol]:
            saved.append(
                pd.DataFrame(funding[symbol]).assign(symbol=symbol, kind="funding")
            )
        coverage[symbol] = {
            "price_bars": len(frames[symbol]),
            "mark_bars": len(marks[symbol]),
            "funding_records": len(funding[symbol]),
            "funding_first": str(funding[symbol][0]["timestamp"])
            if funding[symbol]
            else None,
            "funding_last": str(funding[symbol][-1]["timestamp"])
            if funding[symbol]
            else None,
        }
        quality[symbol] = contract_audit(
            frames[symbol],
            marks[symbol],
            funding[symbol],
            contracts[symbol],
            config.interval,
            params["start_at"],
            params["end_at"],
            params["execution"]["include_funding"],
        )
        quality[symbol]["market_environment"] = active
    if sum(len(frame) for frame in frames.values()) > 500000:
        raise HistoryError("组合数据超过500000根K线，请缩短区间或减少币种")
    target = Path("storage/market")
    target.mkdir(parents=True, exist_ok=True)
    dataset = target / f"portfolio-{job_id}-{owner}.parquet"
    pd.concat(saved, ignore_index=True).to_parquet(dataset, index=False)
    from .leases import renew_lease

    def keep_alive():
        if not renew_lease(factory, "research", owner):
            raise ValueError("研究任务租约已变化")

    result = run_portfolio(
        frames,
        marks,
        funding,
        contracts,
        config,
        params["execution"],
        params["start_at"],
        keep_alive,
    )
    result.update(
        job_type="portfolio",
        source=market.source,
        equity_kind="marked",
        symbols=config.symbols,
        rules=config.rules.model_dump(),
        configuration=config.model_dump(mode="json"),
        execution=params["execution"],
        contracts=contracts,
        coverage=coverage,
        data_quality=quality,
        factor_analysis={
            symbol: analyze(
                frame,
                config.rules.model_dump(),
                start_at=params["start_at"],
                end_at=params["end_at"],
                fee_rate=config.fee_rate,
                slippage_bps=config.slippage_bps,
            )
            for symbol, frame in frames.items()
        },
        bars=sum(len(frame) for frame in frames.values()),
        dataset={
            "path": str(dataset),
            "sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
            "start_at": params["start_at"],
            "end_at": params["end_at"],
            "warmup_bars": history_warmup,
        },
    )
    result["cross_sectional"] = cross_analysis(
        frames, config.rules.model_dump(), params["start_at"], params["end_at"]
    )
    review = {"stress": [], "neighbors": []}
    stressed = config.model_copy(deep=True)
    stressed.fee_rate = min(0.01, config.fee_rate * 2)
    stressed.slippage_bps = min(100, config.slippage_bps * 2)
    stress_execution = {
        **params["execution"],
        "maker_fee_rate": min(
            0.01, params["execution"].get("maker_fee_rate", 0.0002) * 2
        ),
    }
    stressed_result = run_portfolio(
        frames,
        marks,
        funding,
        contracts,
        stressed,
        stress_execution,
        params["start_at"],
        keep_alive,
    )
    review["stress"].append(
        {
            "multiplier": 2,
            "metrics": stressed_result["metrics"],
            "liquidations": stressed_result["liquidations"],
            "risk_summary": stressed_result["risk_summary"],
        }
    )
    if params.get("evaluation_phase") == "research":
        from .robustness import neighbors

        for rules in neighbors(config.rules.model_dump())[1:]:
            adjacent = config.model_copy(deep=True)
            adjacent.rules = config.rules.model_validate(rules)
            adjacent_result = run_portfolio(
                frames,
                marks,
                funding,
                contracts,
                adjacent,
                params["execution"],
                params["start_at"],
                keep_alive,
            )
            review["neighbors"].append(
                {"rules": rules, "metrics": adjacent_result["metrics"]}
            )
    elif params.get("validation_parent_job_id"):
        from .archive import load_result

        with factory() as db:
            parent = db.get(BacktestJob, params["validation_parent_job_id"])
            if parent and parent.status == "completed" and parent.result:
                review["neighbors"] = (
                    load_result(parent.result).get("strategy_review") or {}
                ).get("neighbors", [])
    result["strategy_review"] = review
    from .strategy_review import assess

    result["qualification"] = assess(result, params, config)
    return result
