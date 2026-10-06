"""Database-backed backtest queue, isolated from the paper engine."""

import asyncio
import hashlib
import logging
import uuid
from dataclasses import asdict
from pathlib import Path
from threading import Event as ThreadEvent
from threading import Thread

from sqlalchemy import select

from quantpilot.backtest import run_backtest
from quantpilot.metrics import report

from .data_quality import candle_audit, environment_tag
from .database import make_database
from .engine import guard_lease
from .factor_research import ablation, analyze
from .leases import release_lease, renew_lease
from .market import HistoryError, MarketService
from .models import BacktestJob, OptimizationJob, utcnow
from .optimization import OptimizationCancelled, run_optimization
from .rules import StrategyRules
from .settings import PlatformSettings

logger = logging.getLogger(__name__)


def process_job(factory, market, owner):
    if not renew_lease(factory, "research", owner):
        return
    with factory.begin() as db:
        guard_lease(db, "research", owner)
        # A new lease owner resumes a job interrupted by a previous process.
        backtest = db.scalar(
            select(BacktestJob)
            .where(BacktestJob.status.in_(["pending", "running"]))
            .order_by(BacktestJob.id)
            .with_for_update()
            .limit(1)
        )
        optimization = db.scalar(
            select(OptimizationJob)
            .where(OptimizationJob.status.in_(["pending", "running", "cancelling"]))
            .order_by(OptimizationJob.id)
            .with_for_update()
            .limit(1)
        )
        job = (
            optimization
            if optimization
            and (not backtest or optimization.created_at <= backtest.created_at)
            else backtest
        )
        if job is None:
            return
        model = OptimizationJob if isinstance(job, OptimizationJob) else BacktestJob
        if job.status == "cancelling":
            job.status, job.completed_at = "cancelled", utcnow()
            return
        job.status = "running"
        job_id, params = job.id, job.parameters.copy()
    try:
        if params["source"] != market.source:
            raise ValueError("Backtest source differs from worker configuration")
        market_environment = (
            environment_tag(
                market,
                params.get("exchange", "bybit"),
                params.get("symbol", "BTCUSDT"),
                params.get("interval", "15"),
                params.get("market_environment"),
            )
            if model is BacktestJob
            else None
        )
        if (
            model is BacktestJob
            and market.source == "exchange"
            and params.get("market_environment") not in {"testnet", "mainnet"}
        ):
            params["market_environment"] = market_environment
            with factory.begin() as db:
                guard_lease(db, "research", owner)
                db.get(BacktestJob, job_id).parameters = params.copy()
        stop_heartbeat = ThreadEvent()

        def heartbeat():
            while not stop_heartbeat.wait(20):
                try:
                    if not renew_lease(factory, "research", owner):
                        return
                except Exception:  # noqa: BLE001 - commit still checks ownership
                    return

        heartbeat_thread = Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()
        if model is OptimizationJob:
            run_optimization(factory, market, owner, job_id)
            return
        if params.get("job_type") == "portfolio":
            from .portfolio_research import execute

            payload = execute(factory, market, owner, job_id, params)
            with factory.begin() as db:
                guard_lease(db, "research", owner)
                job = db.get(BacktestJob, job_id)
                job.result, job.status, job.completed_at = (
                    payload,
                    "completed",
                    utcnow(),
                )
            return
        rules = StrategyRules.model_validate(params.get("rules", {}))
        if params.get("start_at"):
            bars = market.history(
                params["exchange"],
                params["symbol"],
                params["interval"],
                params["start_at"],
                params["end_at"],
                params.get("history_warmup", rules.warmup),
            )
        else:
            _, bars = market.bars(
                params["exchange"],
                params["symbol"],
                params["interval"],
                params["limit"],
            )
        if len(bars) < rules.warmup + 2:
            raise HistoryError("样本不足以预热当前指标，请增加样本数或扩大日期区间")
        target = Path("storage/market")
        target.mkdir(parents=True, exist_ok=True)
        dataset = target / f"backtest-{job_id}-{owner}.parquet"
        bars.to_parquet(dataset, index=False)
        result = run_backtest(
            bars,
            **{
                key: params[key]
                for key in ["initial_equity", "quantity", "fee_rate", "slippage_bps"]
            },
            rules=rules.model_dump(),
            max_notional=params.get("max_notional", 1000),
            trade_start=params.get("start_at"),
            risk_sizing=params.get("risk_sizing"),
        )
        trades = []
        for trade in result.trades:
            item = asdict(trade)
            item["entry_time"], item["exit_time"] = (
                str(item["entry_time"]),
                str(item["exit_time"]),
            )
            trades.append(item)
        factor_analysis = analyze(
            bars,
            rules.model_dump(),
            start_at=params.get("start_at"),
            end_at=params.get("end_at"),
            fee_rate=params["fee_rate"],
            slippage_bps=params["slippage_bps"],
        )

        def compare_filters(candidate_rules):
            return report(
                run_backtest(
                    bars,
                    **{
                        key: params[key]
                        for key in [
                            "initial_equity",
                            "quantity",
                            "fee_rate",
                            "slippage_bps",
                        ]
                    },
                    rules=candidate_rules,
                    max_notional=params.get("max_notional", 1000),
                    trade_start=factor_analysis.get("split_at"),
                    risk_sizing=params.get("risk_sizing"),
                )
            )

        factor_analysis["ablation"] = (
            ablation(rules.model_dump(), compare_filters)
            if factor_analysis.get("split_at")
            else []
        )
        payload = {
            "factor_analysis": factor_analysis,
            "data_quality": {
                **candle_audit(bars, params["interval"]),
                "market_environment": market_environment,
            },
            "metrics": report(result),
            "trades": trades,
            "source": market.source,
            "equity_kind": "marked",
            "rules": rules.model_dump(),
            "risk_halted": result.risk_halted,
            "rejected_entries": result.rejected_entries,
            "risk_summary": result.risk_summary,
            "dataset": {
                "path": str(dataset),
                "sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
                "start_at": str(bars.timestamp.iloc[0]),
                "end_at": str(bars.timestamp.iloc[-1]),
                "warmup_bars": params.get("history_warmup", rules.warmup),
            },
            "bars": len(bars),
            "curve": [
                {"timestamp": str(t), "equity": float(v)}
                for t, v in result.equity_curve.items()
            ],
        }
        with factory.begin() as db:
            guard_lease(db, "research", owner)
            job = db.get(BacktestJob, job_id)
            job.result, job.status, job.completed_at = payload, "completed", utcnow()
    except OptimizationCancelled:
        with factory.begin() as db:
            guard_lease(db, "research", owner)
            job = db.get(OptimizationJob, job_id)
            job.status, job.completed_at = "cancelled", utcnow()
    except Exception as exc:  # noqa: BLE001 - persist failed jobs without exposing SDK metadata
        logger.warning("Backtest %s failed", job_id)
        with factory.begin() as db:
            guard_lease(db, "research", owner)
            job = db.get(model, job_id)
            job.status, job.error, job.completed_at = (
                "failed",
                str(exc)
                if isinstance(exc, HistoryError)
                else "回测失败，请检查行情、配置和服务端依赖",
                utcnow(),
            )
    finally:
        if "stop_heartbeat" in locals():
            stop_heartbeat.set()
            heartbeat_thread.join(timeout=1)


async def run():
    settings = PlatformSettings.from_env()
    engine, factory = make_database(settings.database_url)
    market, owner = MarketService(settings.market_source), uuid.uuid4().hex
    try:
        while True:
            try:
                await asyncio.to_thread(process_job, factory, market, owner)
            except Exception:  # noqa: BLE001 - keep worker alive after database faults
                logger.warning("Research queue unavailable; retrying")
            await asyncio.sleep(3)
    finally:
        release_lease(factory, "research", owner)
        engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
