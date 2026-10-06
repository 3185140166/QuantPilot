"""Personal API with paper research and explicitly enabled Testnet execution."""

import hmac
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer

from quantpilot.account_risk import evaluate_account_risk
from quantpilot.exchange.binance import BinanceFuturesClient
from quantpilot.exchange.bybit import BybitClient
from quantpilot.strategy import generate_signal

from .archive import ArchiveError, load_result
from .archive import execute as execute_archive
from .archive import preview as preview_archive
from .database import make_database
from .environment import BybitEnvironment, EnvironmentError
from .market import MarketService, UnsupportedSymbol, serialize_bars
from .models import (
    BacktestJob,
    EquityPoint,
    Event,
    OptimizationJob,
    PaperOrder,
    Position,
    Strategy,
    TestnetAccount,
    TestnetExecution,
    TestnetLedger,
    TestnetOrder,
    TestnetPosition,
    WatchlistEntry,
    WorkerLease,
    utcnow,
)
from .research_promotion import preview as research_preview
from .rules import TEMPLATES, StrategyRules
from .schemas import (
    ArchiveExecute,
    ArchivePreview,
    BacktestBatch,
    BacktestCreate,
    BybitEnvironmentSelect,
    FollowupValidation,
    MarketParameters,
    OptimizationCreate,
    PeriodComparison,
    PortfolioCreate,
    PromoteBacktest,
    RollingValidation,
    StrategyCommand,
    StrategyCreate,
    TestnetCommand,
    TestnetResearchApply,
    TestnetSetup,
    WatchlistCreate,
)
from .search_space import space_for
from .settings import PlatformSettings
from .testnet import PENDING, TestnetBroker, candidates, number, position_risk
from .testnet import account as testnet_account
from .testnet_journal import trade_cycles


def row_dict(row, exclude=()):
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name not in exclude
    }


def create_app(
    settings: PlatformSettings | None = None,
    market: MarketService | None = None,
    environment: BybitEnvironment | None = None,
):
    settings = settings or PlatformSettings.from_env()
    engine, factory = make_database(settings.database_url)
    environment = environment or BybitEnvironment(settings)
    market = market or MarketService(settings.market_source)

    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.dispose()

    app = FastAPI(
        title="QuantPilot Private Platform",
        version="0.2.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.session_factory = factory
    app.state.engine = engine

    @app.middleware("http")
    async def freeze_environment_mutations(request, call_next):
        from starlette.responses import JSONResponse

        runtime = (
            environment.runtime_status()
            if hasattr(environment, "runtime_status")
            else {}
        )
        if request.method not in {"GET", "HEAD", "OPTIONS"} and runtime.get(
            "switch_state"
        ) in {"pending", "switching"}:
            pause = False
            if request.url.path == "/api/trading/command":
                try:
                    pause = (await request.json()).get("action") == "pause"
                except (ValueError, AttributeError):
                    pass
            if not pause:
                return JSONResponse(
                    {"detail": "环境正在切换，暂不接受配置或交易操作"}, status_code=409
                )
        return await call_next(request)

    bearer = HTTPBearer(auto_error=False)

    def authenticate(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        if not settings.auth_enabled:
            return
        if credentials is None or not hmac.compare_digest(
            credentials.credentials.encode(), settings.access_token.encode()
        ):
            raise HTTPException(
                401, "访问凭证无效", headers={"WWW-Authenticate": "Bearer"}
            )

    def session():
        with factory() as db:
            yield db

    db_dep = Depends(session)
    protected = [Depends(authenticate)]

    @app.get("/api/system/status", dependencies=protected)
    def system_status():
        from .health import status

        return status(factory, settings, environment.active)

    @app.post("/api/system/backups/verify", dependencies=protected)
    def check_backup():
        from pathlib import Path

        from .backups import verify_latest
        from .health import profile_id, read_status

        workspace = Path.cwd()
        backup = read_status(workspace / "storage/run/backup.json")
        if backup.get("profile") != profile_id(settings.database_url, workspace):
            raise HTTPException(409, "当前数据库没有可验证的定期备份")
        try:
            return verify_latest(workspace)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                409, "备份文件不完整或校验失败，请检查本地备份"
            ) from exc

    def execution_available():
        return (
            settings.testnet_execution_enabled
            and market.source == "exchange"
            and not environment.status()["restart_required"]
        )

    def require_execution():
        if not execution_available() or environment.status()["restart_required"]:
            raise HTTPException(
                409, "请使用交易所行情、启用执行并重启到已选择的 Bybit 环境"
            )

        profile = market.exchange_settings("bybit")
        if ("testnet" if profile.testnet else "mainnet") != environment.active:
            raise HTTPException(409, "行情与执行环境不一致，请重启平台")
        if (
            not profile.testnet
            and profile.live_trading_confirm != "I_UNDERSTAND_LIVE_TRADING"
        ):
            raise HTTPException(409, "主网执行确认尚未配置")

    @app.post("/api/trading/portfolio", status_code=202, dependencies=protected)
    def queue_portfolio(payload: PortfolioCreate, db=db_dep):
        require_execution()
        current = TestnetSetup.model_validate(testnet_account(factory).configuration)
        if len(current.symbols) < 2:
            raise HTTPException(422, "组合回测需要已保存至少两个扫描币种")
        try:
            base = BacktestCreate(
                symbol=current.symbols[0],
                interval=current.interval,
                rules=current.rules,
                start_at=payload.start_at,
                end_at=payload.end_at,
                initial_equity=current.budget,
                max_notional=current.budget,
                fee_rate=current.fee_rate,
                slippage_bps=current.slippage_bps,
                evaluation_phase=payload.evaluation_phase,
            )
        except ValueError as exc:
            raise HTTPException(
                422, "日期范围无效，需要时区，结束不能晚于现在"
            ) from exc
        if (base.end_at - base.start_at).total_seconds() / (
            int(current.interval) * 60 if current.interval != "D" else 86400
        ) < 100:
            raise HTTPException(422, "组合回测交易区间至少需要100根K线")
        from .market import INTERVAL_MINUTES

        if (base.end_at - base.start_at).total_seconds() / (
            INTERVAL_MINUTES[current.interval] * 60
        ) * len(current.symbols) > 500000:
            raise HTTPException(422, "组合数据超过500000根K线，请缩短区间")
        count = db.scalar(
            select(func.count())
            .select_from(BacktestJob)
            .where(BacktestJob.status.in_(["pending", "running"]))
        )
        if count >= 10:
            raise HTTPException(409, "回测队列已满")
        job = BacktestJob(
            parameters={
                **base.model_dump(mode="json"),
                "source": "exchange",
                "market_environment": environment.active,
                "job_type": "portfolio",
                "portfolio_config": current.model_dump(mode="json"),
                "execution": payload.execution.model_dump(mode="json"),
            }
        )
        db.add(job)
        db.commit()
        return row_dict(job)

    @app.get("/api/trading/replay/{opening_link_id}", dependencies=protected)
    def trade_replay(opening_link_id: str, db=db_dep):
        opening = db.scalar(
            select(TestnetOrder).where(
                TestnetOrder.link_id == opening_link_id, TestnetOrder.action == "open"
            )
        )
        if opening is None:
            raise HTTPException(404, "开仓记录不存在")
        orders = db.scalars(
            select(TestnetOrder)
            .where(
                or_(
                    TestnetOrder.link_id == opening_link_id,
                    TestnetOrder.opening_link_id == opening_link_id,
                )
            )
            .order_by(TestnetOrder.id)
        ).all()
        fills = db.scalars(
            select(TestnetExecution)
            .where(TestnetExecution.opening_link_id == opening_link_id)
            .order_by(
                TestnetExecution.at_ms, TestnetExecution.sequence, TestnetExecution.id
            )
        ).all()
        return {
            "context": opening.context,
            "orders": [row_dict(r) for r in orders],
            "fills": [row_dict(r) for r in fills],
        }

    @app.get("/api/trading/portfolio", dependencies=protected)
    def portfolio_results(db=db_dep):
        rows = db.execute(
            select(
                BacktestJob.id,
                BacktestJob.status,
                BacktestJob.error,
                BacktestJob.parameters["evaluation_phase"].label("phase"),
                BacktestJob.parameters["start_at"].label("start"),
                BacktestJob.parameters["end_at"].label("end"),
                BacktestJob.parameters["rolling_group_id"].label("rolling_group_id"),
                BacktestJob.parameters["rolling_index"].label("rolling_index"),
                BacktestJob.parameters["rolling_windows"].label("rolling_windows"),
                BacktestJob.parameters["portfolio_config"]["symbols"].label("symbols"),
                BacktestJob.parameters["portfolio_config"]["interval"].label(
                    "interval"
                ),
                BacktestJob.result["metrics"].label("metrics"),
                BacktestJob.result["funding_total"].label("funding_total"),
                BacktestJob.result["partial_fills"].label("partial_fills"),
                BacktestJob.result["liquidations"].label("liquidations"),
            )
            .where(BacktestJob.parameters["job_type"].as_string() == "portfolio")
            .order_by(BacktestJob.id.desc())
            .limit(30)
        )
        return [
            {
                "id": row.id,
                "status": row.status,
                "error": row.error,
                "parameters": {
                    "evaluation_phase": row.phase,
                    "start_at": row.start,
                    "end_at": row.end,
                    "rolling_group_id": row.rolling_group_id,
                    "rolling_index": row.rolling_index,
                    "rolling_windows": row.rolling_windows,
                    "portfolio_config": {
                        "symbols": row.symbols,
                        "interval": row.interval,
                    },
                },
                "metrics": row.metrics,
                "funding_total": row.funding_total,
                "partial_fills": row.partial_fills,
                "liquidations": row.liquidations,
            }
            for row in rows
        ]

    @app.post(
        "/api/trading/portfolio/{job_id}/validation",
        status_code=202,
        dependencies=protected,
    )
    def validate_portfolio(job_id: int, payload: FollowupValidation, db=db_dep):
        require_execution()
        source = db.get(BacktestJob, job_id)
        if source is None:
            raise HTTPException(404, "组合研究不存在")
        if (
            source.status != "completed"
            or source.parameters.get("job_type") != "portfolio"
            or source.parameters.get("evaluation_phase") != "research"
        ):
            raise HTTPException(409, "请选择已完成的组合研究")
        if payload.start_at.tzinfo is None or payload.start_at < datetime.fromisoformat(
            source.parameters["end_at"]
        ):
            raise HTTPException(422, "验证区间必须在研究结束之后且包含时区")
        params = source.parameters.copy()
        try:
            base = BacktestCreate.model_validate(
                {
                    **{
                        k: v
                        for k, v in params.items()
                        if k in BacktestCreate.model_fields
                    },
                    "start_at": payload.start_at,
                    "end_at": payload.end_at,
                    "evaluation_phase": "validation",
                }
            )
        except ValueError as exc:
            raise HTTPException(422, "验证日期范围无效") from exc
        from .market import INTERVAL_MINUTES

        if (base.end_at - base.start_at).total_seconds() / (
            INTERVAL_MINUTES[base.interval] * 60
        ) * len(params["portfolio_config"]["symbols"]) > 500000:
            raise HTTPException(422, "组合数据超过500000根K线，请缩短区间")
        if (base.end_at - base.start_at).total_seconds() / (
            INTERVAL_MINUTES[base.interval] * 60
        ) < 100:
            raise HTTPException(422, "验证区间至少100根K线")
        if (
            db.scalar(
                select(func.count())
                .select_from(BacktestJob)
                .where(BacktestJob.status.in_(["pending", "running"]))
            )
            >= 10
        ):
            raise HTTPException(409, "回测队列已满")
        job = BacktestJob(
            parameters={
                **params,
                **base.model_dump(mode="json"),
                "validation_parent_job_id": job_id,
            }
        )
        db.add(job)
        db.commit()
        return row_dict(job)

    @app.post(
        "/api/trading/portfolio/{job_id}/rolling-validation",
        status_code=202,
        dependencies=protected,
    )
    def create_rolling_validation(job_id: int, payload: RollingValidation, db=db_dep):
        require_execution()
        from .rolling_validation import plan

        source = db.get(BacktestJob, job_id)
        if source is None:
            raise HTTPException(404, "研究任务不存在")
        if source.parameters.get("market_environment") != environment.active:
            raise HTTPException(409, "研究任务与当前环境不一致")
        try:
            parameters = plan(source, payload)
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(422, str(error)) from None
        pending = db.scalar(
            select(func.count())
            .select_from(BacktestJob)
            .where(BacktestJob.status.in_(["pending", "running"]))
        )
        if pending + len(parameters) > 10:
            raise HTTPException(409, "队列容量不足，所有窗口均未提交，请等待任务完成")
        jobs = [BacktestJob(parameters=value) for value in parameters]
        db.add_all(jobs)
        db.commit()
        return {
            "group_id": parameters[0]["rolling_group_id"],
            "jobs": [row_dict(job) for job in jobs],
        }

    @app.get(
        "/api/trading/portfolio/{job_id}/rolling-validation", dependencies=protected
    )
    def rolling_validation_report(job_id: int, db=db_dep):
        from .rolling_validation import report

        job = db.get(BacktestJob, job_id)
        if job is None:
            raise HTTPException(404, "回测不存在")
        return report(
            db,
            job,
            TestnetSetup.model_validate(job.parameters.get("portfolio_config", {})),
        )

    @app.post("/api/trading/comparisons", status_code=202, dependencies=protected)
    def compare_periods(payload: PeriodComparison, db=db_dep):
        require_execution()
        validate_symbol("bybit", payload.symbol)
        current = TestnetSetup.model_validate(testnet_account(factory).configuration)
        source_strategy = None
        if payload.source_strategy_id is not None:
            source_strategy = db.get(Strategy, payload.source_strategy_id)
            if source_strategy is None:
                raise HTTPException(404, "来源模拟策略不存在")
            if (
                source_strategy.exchange != "bybit"
                or source_strategy.symbol != payload.symbol
            ):
                raise HTTPException(422, "请选择来源策略的 Bybit 币种")
            if payload.evaluation_phase != "research":
                raise HTTPException(422, "来源策略需先研究，再创建后续独立验证")
            current = current.model_copy(
                update={"rules": StrategyRules.model_validate(source_strategy.rules)}
            )
            if current.rules.stop_loss_pct <= 0:
                raise HTTPException(
                    422, "来源模拟策略需设置正数止损，才能按助手预算研究交易仓位"
                )
        if source_strategy is None and payload.symbol not in current.symbols:
            raise HTTPException(422, "请选择已保存配置中的币种")
        if (
            payload.start_at.tzinfo is None
            or payload.end_at.tzinfo is None
            or payload.start_at.timestamp() % 14400
            or payload.end_at.timestamp() % 14400
        ):
            raise HTTPException(422, "起止时间需含时区并对齐 UTC 的四小时边界")
        pending = db.scalar(
            select(func.count())
            .select_from(BacktestJob)
            .where(BacktestJob.status.in_(["pending", "running"]))
        )
        intervals = (
            ("15",) if current.rules.hourly_trend_filter else ("15", "60", "240")
        )
        if pending + len(intervals) > 10:
            raise HTTPException(409, "回测队列容量不足，请等待任务完成")
        bases = []
        for interval in intervals:
            try:
                base = BacktestCreate(
                    symbol=payload.symbol,
                    interval=interval,
                    rules=current.rules,
                    start_at=payload.start_at,
                    end_at=payload.end_at,
                    initial_equity=current.budget,
                    max_notional=current.budget,
                    fee_rate=current.fee_rate,
                    slippage_bps=current.slippage_bps,
                    evaluation_phase=payload.evaluation_phase,
                )
            except ValueError as exc:
                raise HTTPException(
                    422, "日期范围无效，最多730天及100000根K线"
                ) from exc
            if (base.end_at - base.start_at).total_seconds() / (
                int(interval) * 60
            ) < 100:
                raise HTTPException(422, "每个周期的研究区间至少需要100根K线")
            bases.append(base)
        try:
            raw = TestnetBroker().instrument(payload.symbol)
            lot, price = raw["lotSizeFilter"], raw["priceFilter"]
            from .schemas import RiskSizing

            sizing = RiskSizing(
                budget=current.budget,
                cash_reserve_pct=current.cash_reserve_pct,
                risk_per_trade_pct=current.risk_per_trade_pct,
                portfolio_risk_pct=current.portfolio_risk_pct,
                leverage=current.leverage,
                daily_loss_pct=current.daily_loss_pct,
                max_drawdown_pct=current.max_drawdown_pct,
                constraints={
                    "qty_step": lot["qtyStep"],
                    "min_qty": lot["minOrderQty"],
                    "max_qty": lot.get("maxMktOrderQty") or lot["maxOrderQty"],
                    "min_notional": lot.get("minNotionalValue", 0),
                    "tick_size": price["tickSize"],
                },
            )
        except Exception as exc:
            raise HTTPException(
                503, "无法核实当前环境合约下单限制，请稍后重试"
            ) from exc
        batch_id = uuid.uuid4().hex
        jobs = []
        for base in bases:
            job = BacktestJob(
                parameters={
                    **base.model_dump(mode="json"),
                    "risk_sizing": sizing.model_dump(mode="json"),
                    "source": "exchange",
                    "market_environment": environment.active,
                    "comparison_id": batch_id,
                    "constraints_at": utcnow().isoformat(),
                    "history_warmup": current.rules.warmup,
                    "source_strategy": {
                        "id": source_strategy.id,
                        "name": source_strategy.name,
                        "symbol": source_strategy.symbol,
                        "interval": source_strategy.interval,
                        "rules": current.rules.model_dump(mode="json"),
                    }
                    if source_strategy is not None
                    else None,
                }
            )
            db.add(job)
            jobs.append(job)
        db.commit()
        return {"comparison_id": batch_id, "jobs": [row_dict(job) for job in jobs]}

    @app.post(
        "/api/trading/comparisons/{job_id}/validation",
        status_code=202,
        dependencies=protected,
    )
    def followup_validation(job_id: int, payload: FollowupValidation, db=db_dep):
        require_execution()
        original = db.get(BacktestJob, job_id)
        if original is None:
            raise HTTPException(404, "研究回测不存在")
        params = original.parameters
        if (
            original.status != "completed"
            or not params.get("comparison_id")
            or params.get("evaluation_phase") != "research"
            or params.get("source") != "exchange"
            or params.get("exchange") != "bybit"
            or params.get("market_environment") != environment.active
            or not params.get("risk_sizing")
        ):
            raise HTTPException(409, "请选择当前环境已完成的周期研究回测")
        if (
            payload.start_at.tzinfo is None
            or payload.end_at.tzinfo is None
            or payload.start_at < datetime.fromisoformat(params["end_at"])
            or payload.start_at.timestamp() % 14400
            or payload.end_at.timestamp() % 14400
        ):
            raise HTTPException(422, "验证时间必须在研究结束之后，且对齐四小时边界")
        data = {
            key: value
            for key, value in params.items()
            if key in BacktestCreate.model_fields
        }
        data.update(
            start_at=payload.start_at,
            end_at=payload.end_at,
            evaluation_phase="validation",
        )
        try:
            checked = BacktestCreate.model_validate(data)
        except ValueError as exc:
            raise HTTPException(422, "验证日期无效，结束不能晚于现在") from exc
        from .market import INTERVAL_MINUTES

        if (checked.end_at - checked.start_at).total_seconds() / (
            INTERVAL_MINUTES[checked.interval] * 60
        ) < 100:
            raise HTTPException(422, "验证区间至少需要100根K线，请预留更长时间")
        pending = db.scalar(
            select(func.count())
            .select_from(BacktestJob)
            .where(BacktestJob.status.in_(["pending", "running"]))
        )
        if pending >= 10:
            raise HTTPException(409, "回测队列已满，请等待任务完成")
        batch_id = uuid.uuid4().hex
        job = BacktestJob(
            parameters={
                **params,
                **checked.model_dump(mode="json"),
                "comparison_id": batch_id,
                "validation_parent_job_id": job_id,
            }
        )
        db.add(job)
        db.commit()
        return {"comparison_id": batch_id, "jobs": [row_dict(job)]}

    @app.get("/api/trading/comparisons", dependencies=protected)
    def comparison_results(db=db_dep):
        worker = db.get(WorkerLease, "research")
        online = bool(
            worker and worker.expires_at.replace(tzinfo=timezone.utc) > utcnow()
        )
        jobs = db.execute(
            select(
                BacktestJob.id,
                BacktestJob.status,
                BacktestJob.error,
                BacktestJob.parameters,
                BacktestJob.result["metrics"].label("metrics"),
                BacktestJob.result["rejected_entries"].label("rejected_entries"),
                BacktestJob.result["risk_summary"].label("risk_summary"),
            )
            .where(BacktestJob.parameters["comparison_id"].as_string().is_not(None))
            .order_by(BacktestJob.id.desc())
            .limit(30)
        )
        return [
            {
                "id": job.id,
                "status": job.status,
                "error": job.error,
                "parameters": job.parameters,
                "metrics": job.metrics,
                "rejected_entries": job.rejected_entries or 0,
                "research_worker_online": online,
                "risk_summary": job.risk_summary,
            }
            for job in jobs
        ]

    @app.get("/api/trading/status", dependencies=protected)
    def trading_status(db=db_dep):
        from .execution_status import strategy_status

        value = row_dict(testnet_account(factory))
        value["configuration"] = TestnetSetup.model_validate(
            value["configuration"]
        ).model_dump(mode="json")
        value.update(
            trades=trade_cycles(db),
            available=execution_available(),
            environment=environment.active,
            positions=[row_dict(row) for row in db.scalars(select(TestnetPosition))],
            orders=[
                row_dict(row, exclude={"context"})
                for row in db.scalars(
                    select(TestnetOrder)
                    .options(defer(TestnetOrder.context))
                    .order_by(TestnetOrder.id.desc())
                    .limit(100)
                )
            ],
            executions=[
                row_dict(row)
                for row in db.scalars(
                    select(TestnetExecution)
                    .order_by(TestnetExecution.at_ms.desc())
                    .limit(100)
                )
            ],
            ledger=[
                row_dict(row)
                for row in db.scalars(
                    select(TestnetLedger)
                    .order_by(TestnetLedger.at_ms.desc())
                    .limit(100)
                )
            ],
        )
        worker = db.get(WorkerLease, "testnet")
        worker_active = (
            worker is not None
            and worker.expires_at.replace(tzinfo=timezone.utc) > utcnow()
        )
        pending = (
            db.scalar(
                select(TestnetOrder.id).where(TestnetOrder.status.in_(PENDING)).limit(1)
            )
            is not None
        )
        command_at = db.scalar(
            select(Event.created_at)
            .where(
                Event.message.in_(
                    [
                        f"{'测试网' if environment.active == 'testnet' else '主网'}交易助手指令：{action}"
                        for action in ("start", "pause", "flatten", "reset")
                    ]
                )
            )
            .order_by(Event.id.desc())
            .limit(1)
        )
        from .ownership import classify

        value["snapshot"]["ownership"] = classify(factory, value["snapshot"])
        from .account_exposure import summarize

        value["account_exposure"] = summarize(
            value["snapshot"],
            value["snapshot"]["ownership"],
            TestnetSetup.model_validate(value["configuration"]),
        )
        value["strategy_status"] = strategy_status(value, worker_active, command_at)
        from .strategy_monitor import assess as monitor_assess
        from .strategy_review import current_review

        value["research_qualification"] = current_review(db, testnet_account(factory))
        value["strategy_monitor"] = monitor_assess(
            db, testnet_account(factory), value["trades"]
        )
        value["execution_state"] = (
            "unavailable"
            if not value["available"]
            else "offline"
            if not worker_active
            else "risk_halted"
            if value["risk_halted"]
            else "flattening"
            if value["flatten_requested"]
            else "confirming"
            if pending
            else "blocked"
            if value["last_error"] or value["ledger_warning"]
            else "paused"
            if not value["enabled"]
            else "recovering"
            if value["snapshot"].get("execution_ready") is False
            or not value["strategy_status"]["acknowledged"]
            else "entry_blocked"
            if value["snapshot"].get("entry_check")
            else "managing_positions"
            if value["positions"]
            else "waiting_signal"
        )
        age = None
        try:
            stamp = datetime.fromisoformat(value["snapshot"]["at"])
            if stamp.tzinfo is not None:
                age = (utcnow() - stamp).total_seconds()
        except (KeyError, TypeError, ValueError):
            pass
        value["health"] = {
            "worker_online": worker_active,
            "snapshot_age_seconds": age,
            "snapshot_fresh": age is not None and 0 <= age <= 60,
            "unprotected_symbols": [
                p["symbol"]
                for p in value["snapshot"].get("positions", [])
                if number(p.get("stopLoss")) <= 0
            ],
            "pending_orders": pending,
        }
        config = TestnetSetup.model_validate(value["configuration"])
        protective = (
            value["snapshot"].get("protective_equity", value["equity"])
            if value["ledger_warning"]
            else value["equity"]
        )
        value["account_risk"] = evaluate_account_risk(
            budget=config.budget,
            equity=protective,
            peak_equity=value["peak_equity"],
            day_equity=value["day_equity"],
            daily_loss_pct=config.daily_loss_pct,
            max_drawdown_pct=config.max_drawdown_pct,
        ).as_dict()
        return value

    @app.get("/api/trading/research", dependencies=protected)
    def trading_research(db=db_dep):
        rows = db.execute(
            select(BacktestJob.id, BacktestJob.parameters)
            .where(
                BacktestJob.status == "completed",
                BacktestJob.parameters["evaluation_phase"].as_string() == "validation",
                BacktestJob.parameters["exchange"].as_string() == "bybit",
                BacktestJob.parameters["source"].as_string() == "exchange",
            )
            .order_by(BacktestJob.id.desc())
            .limit(100)
        )
        return [
            {
                "id": row.id,
                "symbol": "、".join(row.parameters["portfolio_config"]["symbols"])
                if row.parameters.get("job_type") == "portfolio"
                else row.parameters.get("symbol"),
                "job_type": row.parameters.get("job_type", "single"),
                "interval": row.parameters.get("interval"),
                "strategy_type": row.parameters.get("rules", {}).get("strategy_type"),
                "market_environment": row.parameters.get(
                    "market_environment", "未记录"
                ),
                "source_strategy": row.parameters.get("source_strategy"),
            }
            for row in rows
            if row.parameters.get("evaluation_phase") == "validation"
            and row.parameters.get("exchange") == "bybit"
            and row.parameters.get("source") == "exchange"
            and row.parameters.get("market_environment") == environment.active
            and row.parameters.get("optimization_eligible") is not False
            and row.parameters.get("rules", {}).get("stop_loss_pct", 0) > 0
            and row.parameters.get("interval") in {"15", "30", "60", "240", "D"}
        ]

    def get_research_preview(job_id, db, symbols_mode="replace"):
        job = db.get(BacktestJob, job_id)
        if job is None:
            raise HTTPException(404, "回测不存在")
        if job.parameters.get("market_environment") != environment.active:
            raise HTTPException(409, "验证回测与当前执行环境不一致")
        try:
            return research_preview(job, testnet_account(factory), symbols_mode, db=db)
        except (ValueError, ArchiveError, KeyError, TypeError):
            raise HTTPException(
                409,
                "回测参数不可应用：需要已完成的 Bybit 交易所行情验证回测、有效止损及合规成本参数；优化候选需通过约束",
            ) from None

    @app.get("/api/trading/research/{job_id}/preview", dependencies=protected)
    def preview_testnet_research(
        job_id: int,
        symbols_mode: Literal["replace", "keep"] = Query("replace"),
        db=db_dep,
    ):
        return get_research_preview(job_id, db, symbols_mode)

    @app.post("/api/trading/research/{job_id}/apply", dependencies=protected)
    def apply_testnet_research(job_id: int, payload: TestnetResearchApply, db=db_dep):
        require_execution()
        testnet_account(factory)
        db.execute(update(TestnetAccount).where(TestnetAccount.id == 1).values(id=1))
        current = db.get(TestnetAccount, 1)
        if (
            current.enabled
            or current.flatten_requested
            or current.snapshot.get("positions")
            or db.scalar(select(TestnetPosition.symbol).limit(1))
            or db.scalar(
                select(TestnetOrder.id).where(TestnetOrder.status.in_(PENDING)).limit(1)
            )
        ):
            raise HTTPException(409, "应用前必须暂停、空仓并确认所有订单")
        candidate = get_research_preview(job_id, db, payload.symbols_mode)
        if (
            TestnetSetup.model_validate(
                candidate["configuration"]
            ).research_guard.enabled
            and not candidate["qualification"]["eligible"]
        ):
            raise HTTPException(
                409,
                "研究准入未通过：" + "、".join(candidate["qualification"]["failed"]),
            )
        if not payload.acknowledge_model_difference or not hmac.compare_digest(
            candidate["fingerprint"], payload.fingerprint
        ):
            raise HTTPException(409, "请重新预览参数并确认回测与执行仓位模型的差异")
        if (
            candidate["unvalidated_symbols"]
            and not payload.acknowledge_unvalidated_symbols
        ):
            raise HTTPException(
                409, "保留的其他币种尚未通过此回测验证，请核对并单独确认"
            )
        current.configuration = candidate["configuration"]
        current.enabled = False
        current.research_origin = {
            "source_strategy": db.get(BacktestJob, job_id).parameters.get(
                "source_strategy"
            ),
            "job_id": job_id,
            "fingerprint": candidate["fingerprint"],
            "rules": candidate["configuration"]["rules"],
            "dataset": candidate["dataset"],
            "metrics": candidate["metrics"],
            "market_environment": candidate["market_environment"],
            "risk_sizing": candidate.get("risk_sizing"),
            "applied_configuration": candidate["configuration"],
            "configuration_before": candidate["configuration_before"],
            "symbols_mode": candidate["symbols_mode"],
            "unvalidated_symbols": candidate["unvalidated_symbols"],
            "risk_summary": candidate["risk_summary"],
            "job_type": db.get(BacktestJob, job_id).parameters.get(
                "job_type", "single"
            ),
            "applied_at": utcnow().isoformat(),
            "modified": False,
        }
        db.add(Event(message=f"交易助手应用验证回测 {job_id} 的规则，保持暂停"))
        db.commit()
        return row_dict(current)

    @app.post("/api/trading/configuration", dependencies=protected)
    def trading_configuration(payload: TestnetSetup, db=db_dep):
        testnet_account(factory)
        db.execute(update(TestnetAccount).where(TestnetAccount.id == 1).values(id=1))
        current = db.scalar(
            select(TestnetAccount).where(TestnetAccount.id == 1).with_for_update()
        )
        if (
            current.enabled
            or current.flatten_requested
            or current.snapshot.get("positions")
            or db.scalar(
                select(func.count())
                .select_from(TestnetOrder)
                .where(TestnetOrder.status.in_(PENDING))
            )
        ):
            raise HTTPException(
                409,
                "请先暂停自动交易、账户空仓并确认本系统待处理订单，再修改预算；外部挂单可保留",
            )
        budget_changed = float(current.configuration["budget"]) != payload.budget
        configuration_changed = TestnetSetup.model_validate(
            current.configuration
        ).model_dump(mode="json") != payload.model_dump(mode="json")
        current.configuration = payload.model_dump(mode="json")
        if current.research_origin and configuration_changed:
            current.research_origin = {**current.research_origin, "modified": True}
        if budget_changed:
            current.baseline_equity = None
            current.journal_start_ms, current.baseline_cash = None, None
            current.net_flows = 0
            current.equity = current.peak_equity = current.day_equity = payload.budget
            current.day = None
        db.commit()
        return row_dict(current)

    @app.post("/api/trading/orders/{order_id}/cancel", dependencies=protected)
    def cancel_testnet_order(
        order_id: int,
        db=db_dep,
        expected_environment: Literal["testnet", "mainnet"] | None = Query(
            None, alias="environment"
        ),
    ):
        require_execution()
        if (
            expected_environment is not None
            and expected_environment != environment.active
        ) or (environment.active == "mainnet" and expected_environment != "mainnet"):
            raise HTTPException(409, "撤单环境已变化，请刷新页面")
        db.execute(
            update(TestnetAccount).where(TestnetAccount.id == 1).values(enabled=False)
        )
        row = db.get(TestnetOrder, order_id)
        if row is None:
            raise HTTPException(404, "订单不存在")
        if row.status not in PENDING:
            raise HTTPException(409, "订单已经结束，无需撤单")
        row.cancel_requested = True
        db.add(Event(message=f"交易助手撤单请求：{row.symbol}；已暂停新开仓"))
        db.commit()
        return {"message": "已请求撤单并暂停新开仓；执行进程会查询并确认原订单"}

    @app.post("/api/trading/command", dependencies=protected)
    def trading_command(payload: TestnetCommand, db=db_dep):
        from .execution_status import configuration_fingerprint

        if payload.action != "pause":
            require_execution()
        if (
            payload.environment is not None
            and payload.environment != environment.active
        ):
            raise HTTPException(409, "操作环境已变化，请刷新页面")
        if (
            environment.active == "mainnet"
            and payload.action != "pause"
            and payload.environment != "mainnet"
        ):
            raise HTTPException(409, "主网操作必须明确指定当前环境")
        if (
            environment.active == "mainnet"
            and payload.action == "start"
            and payload.configuration_fingerprint is None
        ):
            raise HTTPException(409, "请核对已保存参数后启动主网交易")
        testnet_account(factory)
        db.execute(update(TestnetAccount).where(TestnetAccount.id == 1).values(id=1))
        current = db.scalar(
            select(TestnetAccount).where(TestnetAccount.id == 1).with_for_update()
        )
        if payload.action != "pause":
            require_execution()
        if (
            payload.action == "start"
            and payload.configuration_fingerprint is not None
            and not hmac.compare_digest(
                payload.configuration_fingerprint,
                configuration_fingerprint(current.configuration),
            )
        ):
            raise HTTPException(409, "已保存参数已经变化，请刷新并核对当前配置后再启动")
        pending = db.scalar(
            select(func.count())
            .select_from(TestnetOrder)
            .where(TestnetOrder.status.in_(PENDING))
        )
        if payload.action == "start":
            if current.risk_halted or current.flatten_requested or pending:
                raise HTTPException(409, "风险已暂停、正在平仓或有未确认订单，不能启动")
            if not current.updated_at:
                raise HTTPException(409, "请先等待交易账户同步成功")
            stamp = (
                current.updated_at.replace(tzinfo=timezone.utc)
                if current.updated_at.tzinfo is None
                else current.updated_at
            )
            if (
                utcnow() - stamp > timedelta(seconds=60)
                or current.last_error
                or current.ledger_warning
            ):
                raise HTTPException(409, "交易账户数据不新鲜或同步异常，请先检查连接")
            if current.snapshot.get("margin_mode") != "ISOLATED_MARGIN":
                raise HTTPException(
                    409, "请先把空仓交易账户设置为逐仓，并使用单向持仓模式"
                )
            config = TestnetSetup.model_validate(current.configuration)
            from .strategy_monitor import assess as monitor_assess
            from .strategy_review import current_review

            qualification = current_review(db, current)
            if config.research_guard.enabled and not qualification["eligible"]:
                raise HTTPException(
                    409, "研究准入未通过：" + "、".join(qualification["failed"])
                )
            monitoring = monitor_assess(db, current)
            if monitoring["pause_new_entries"]:
                raise HTTPException(
                    409, "策略运行监测暂停新开仓：" + "；".join(monitoring["reasons"])
                )
            if number(current.snapshot.get("available_balance")) <= 0:
                raise HTTPException(409, "扣除挂单与持仓占用后，没有可用于新开仓的余额")
            risk, _ = position_risk(current.snapshot, config)
            if number(current.equity) <= 0 or risk is None:
                raise HTTPException(409, "账户权益无效或已有持仓缺少止损，请先核查持仓")
            if (
                risk
                > min(number(config.budget), number(current.equity))
                * number(config.portfolio_risk_pct)
                / 100
            ):
                raise HTTPException(409, "已有持仓超过组合风险预算，不能恢复自动开仓")
            from .ownership import classify

            ownership = classify(factory, current.snapshot)
            if ownership["uncertain"]:
                raise HTTPException(409, "外部订单来源或占用金额未确认，不能启动")
            current.enabled = True
        elif payload.action == "pause":
            current.enabled = False
        elif payload.action == "flatten":
            current.enabled, current.flatten_requested = False, True
        else:
            if current.snapshot.get("positions") or pending or current.enabled:
                raise HTTPException(409, "重置风险基线前必须停止、空仓并确认所有订单")
            current.risk_halted = False
            current.baseline_equity = None
            current.journal_start_ms, current.baseline_cash = None, None
            current.net_flows, current.ledger_warning = 0, None
            current.peak_equity = current.equity = current.configuration["budget"]
            current.day, current.last_bars = None, {}
        db.add(
            Event(
                message=f"{'测试网' if environment.active == 'testnet' else '主网'}交易助手指令：{payload.action}"
            )
        )
        db.commit()
        return row_dict(current)

    @app.post("/api/trading/prepare-isolated", dependencies=protected)
    def prepare_testnet(
        expected_environment: Literal["testnet", "mainnet"] | None = Query(
            None, alias="environment"
        ),
    ):
        require_execution()
        if (
            expected_environment is not None
            and expected_environment != environment.active
        ) or (environment.active == "mainnet" and expected_environment != "mainnet"):
            raise HTTPException(409, "账户初始化环境已变化，请刷新页面")
        current = testnet_account(factory)
        if current.enabled:
            raise HTTPException(409, "请先暂停自动交易")
        try:
            TestnetBroker().prepare_isolated()
            return {"message": "测试账户逐仓／单向设置已确认，请等待下一次账户同步"}
        except Exception:  # noqa: BLE001 - redact private SDK metadata
            raise HTTPException(
                409, "逐仓设置失败：请确认测试账户没有持仓或挂单，且 API 具有交易权限"
            ) from None

    @app.post("/api/trading/scan", dependencies=protected)
    def scan_testnet():
        require_execution()
        try:
            return {
                "candidates": candidates(factory, market, TestnetBroker()),
                "execution": "bybit_" + environment.active,
            }
        except Exception:  # noqa: BLE001 - redact private SDK metadata
            raise HTTPException(502, "交易信号扫描失败，请检查行情和密钥配置") from None

    @app.get("/api/bybit-environment", dependencies=protected)
    def bybit_environment():
        return environment.status()

    @app.post("/api/bybit-environment", dependencies=protected)
    def select_bybit_environment(payload: BybitEnvironmentSelect):
        try:
            return environment.save(factory, payload.environment)
        except EnvironmentError as exc:
            raise HTTPException(409, str(exc)) from None

    def validate_symbol(exchange, symbol):
        try:
            market.require_symbol(exchange, symbol)
        except UnsupportedSymbol:
            raise HTTPException(422, "该交易对不是当前可用的 USDT 永续合约") from None
        except Exception:  # noqa: BLE001 - SDK boundary, do not expose request metadata
            raise HTTPException(
                502, "交易对列表获取失败，请检查交易所配置与网络"
            ) from None

    @app.get("/api/health")
    def health():
        import os

        from .health import profile_id

        return {
            "status": "ok",
            "service": "quantpilot",
            "auth_required": settings.auth_enabled,
            "environment": environment.active,
            "profile": profile_id(
                settings.database_url, getattr(environment, "directory", None)
            ),
            "switch_id": os.getenv("PLATFORM_ENVIRONMENT_SWITCH_ID"),
        }

    @app.get("/api/overview", dependencies=protected)
    def overview(db=db_dep):
        strategies = list(db.scalars(select(Strategy).order_by(Strategy.id)))
        positions = list(db.scalars(select(Position)))
        unrealized = sum(
            (p.mark_price - p.entry_price)
            * p.quantity
            * (1 if p.side == "LONG" else -1)
            for p in positions
        )
        workers = []
        for lease in db.scalars(
            select(WorkerLease).where(
                WorkerLease.name.in_(["engine", "research", "testnet"])
            )
        ):
            stamp = (
                lease.expires_at.replace(tzinfo=timezone.utc)
                if lease.expires_at.tzinfo is None
                else lease.expires_at
            )
            workers.append(
                {
                    "name": lease.name,
                    "online": bool(lease.owner) and stamp > utcnow(),
                    "heartbeat_at": lease.heartbeat_at,
                }
            )
        return {
            "source": settings.market_source,
            "mode": "paper",
            "strategies": len(strategies),
            "running": sum(s.status == "running" for s in strategies),
            "positions": len(positions),
            "cash": sum(s.cash for s in strategies),
            "unrealized_pnl": unrealized,
            "equity": sum(s.cash for s in strategies) + unrealized,
            "initial_equity": sum(s.initial_equity for s in strategies),
            "orders": db.scalar(select(func.count()).select_from(PaperOrder)),
            "workers": workers,
        }

    @app.get("/api/market", dependencies=protected)
    def get_market(
        params: Annotated[MarketParameters, Depends()],
        limit: int = Query(200, ge=100, le=1000),
    ):
        validate_symbol(params.exchange, params.symbol)
        try:
            _, bars = market.bars(
                params.exchange, params.symbol, params.interval, limit
            )
            return {
                "source": settings.market_source,
                "signal": generate_signal(bars),
                "bars": serialize_bars(bars),
            }
        except Exception:  # noqa: BLE001 - SDK boundary; never expose request metadata
            raise HTTPException(
                502, "行情获取失败，请检查服务端交易所配置与网络"
            ) from None

    @app.get("/api/instruments", dependencies=protected)
    def instruments(exchange: Literal["bybit", "binance"] = "bybit"):
        try:
            return {
                "source": settings.market_source,
                "exchange": exchange,
                "instruments": market.instruments(exchange),
            }
        except Exception:  # noqa: BLE001 - public SDK boundary
            raise HTTPException(
                502, "交易对列表获取失败，请检查交易所配置与网络"
            ) from None

    @app.get("/api/tickers", dependencies=protected)
    def tickers(exchange: Literal["bybit", "binance"] = "bybit"):
        try:
            return market.tickers(exchange)
        except Exception:  # noqa: BLE001 - public SDK boundary
            raise HTTPException(
                502, "多币种行情获取失败，请检查交易所配置与网络"
            ) from None

    @app.get("/api/watchlist", dependencies=protected)
    def watchlist(exchange: Literal["bybit", "binance"] = "bybit", db=db_dep):
        return [
            row_dict(entry)
            for entry in db.scalars(
                select(WatchlistEntry)
                .where(WatchlistEntry.exchange == exchange)
                .order_by(WatchlistEntry.created_at)
            )
        ]

    @app.post("/api/watchlist", dependencies=protected)
    def add_watchlist(payload: WatchlistCreate, db=db_dep):
        validate_symbol(payload.exchange, payload.symbol)
        entry = db.get(WatchlistEntry, (payload.exchange, payload.symbol))
        if entry is None:
            entry = WatchlistEntry(**payload.model_dump())
            db.add(entry)
            try:
                db.commit()
            except IntegrityError:
                # Repeated clicks in two tabs are idempotent.
                db.rollback()
                entry = db.get(WatchlistEntry, (payload.exchange, payload.symbol))
                if entry is None:
                    raise
        return row_dict(entry)

    @app.delete(
        "/api/watchlist/{exchange}/{symbol}", status_code=204, dependencies=protected
    )
    def remove_watchlist(
        exchange: Literal["bybit", "binance"],
        symbol: Annotated[
            str, Path(min_length=5, max_length=24, pattern=r"^[A-Z0-9]+USDT$")
        ],
        db=db_dep,
    ):
        entry = db.get(WatchlistEntry, (exchange, symbol))
        if entry:
            db.delete(entry)
            db.commit()
        return Response(status_code=204)

    @app.get("/api/strategies", dependencies=protected)
    def list_strategies(db=db_dep):
        result = []
        for strategy in db.scalars(select(Strategy).order_by(Strategy.id)):
            value = row_dict(strategy)
            position = db.get(Position, strategy.id)
            equity = strategy.cash + (
                (position.mark_price - position.entry_price)
                * position.quantity
                * (1 if position.side == "LONG" else -1)
                if position
                else 0
            )
            value.update(
                rules=StrategyRules.model_validate(strategy.rules or {}).model_dump(),
                equity=equity,
                total_return=equity / strategy.initial_equity - 1,
                position=row_dict(position) if position else None,
            )
            result.append(value)
        return result

    @app.get("/api/strategies/{strategy_id}/curve", dependencies=protected)
    def strategy_curve(
        strategy_id: int,
        limit: int = Query(1000, ge=1, le=1000),
        since_id: int = Query(0, ge=0),
        db=db_dep,
    ):
        if db.get(Strategy, strategy_id) is None:
            raise HTTPException(404, "策略不存在")
        query = select(EquityPoint).where(EquityPoint.strategy_id == strategy_id)
        if since_id:
            query = query.where(EquityPoint.id >= since_id).order_by(EquityPoint.id)
        else:
            query = query.order_by(EquityPoint.id.desc())
        points = list(db.scalars(query.limit(limit)))
        if not since_id:
            points.reverse()
        return {
            "curve": [
                {"id": p.id, "timestamp": p.bar_time, "equity": p.equity}
                for p in points
            ],
            "sampling": "bar",
            "drawdown_sampling": "poll",
        }

    def archived_row(row):
        value = row_dict(row)
        try:
            value["result"] = load_result(row.result)
        except ArchiveError as exc:
            raise HTTPException(409, str(exc)) from None
        return value

    @app.post("/api/maintenance/archive/preview", dependencies=protected)
    def archive_preview(payload: ArchivePreview):
        try:
            return preview_archive(factory, payload.retention_days)
        except (OSError, ArchiveError):
            raise HTTPException(409, "归档预览失败，请检查存储空间和权限") from None

    @app.post("/api/maintenance/archive/execute", dependencies=protected)
    def archive_execute(payload: ArchiveExecute):
        try:
            return execute_archive(factory, payload.id)
        except ArchiveError as exc:
            raise HTTPException(409, str(exc)) from None
        except OSError:
            raise HTTPException(
                409, "归档文件写入失败，原始数据未移除，请检查存储空间和权限"
            ) from None

    @app.get("/api/strategy-templates", dependencies=protected)
    def strategy_templates():
        return TEMPLATES

    def save_strategy(payload: StrategyCreate, db, origin_job_id=None):
        validate_symbol(payload.exchange, payload.symbol)
        if db.scalar(select(func.count()).select_from(Strategy)) >= 20:
            raise HTTPException(409, "第一版最多创建 20 个策略实例")
        strategy = Strategy(
            **payload.model_dump(),
            cash=payload.initial_equity,
            source=settings.market_source,
            peak_equity=payload.initial_equity,
            origin_job_id=origin_job_id,
        )
        db.add(strategy)
        db.flush()
        db.add(Event(strategy_id=strategy.id, message=f"创建模拟策略：{strategy.name}"))
        if origin_job_id is not None:
            db.add(
                Event(
                    strategy_id=strategy.id,
                    message=f"从回测 #{origin_job_id} 创建配置 v{payload.rules.version}，等待模拟运行",
                )
            )
        db.commit()
        return row_dict(strategy)

    @app.post("/api/strategies", status_code=201, dependencies=protected)
    def create_strategy(payload: StrategyCreate, db=db_dep):
        return save_strategy(payload, db)

    @app.post("/api/strategies/{strategy_id}/command", dependencies=protected)
    def command(strategy_id: int, payload: StrategyCommand, db=db_dep):
        current = db.get(Strategy, strategy_id)
        if current and current.risk_halted and payload.action == "start":
            raise HTTPException(
                409, "策略已触发最大回撤限制，请研究新配置并创建新的模拟实例"
            )
        filters = [
            Strategy.id == strategy_id,
            Strategy.source == settings.market_source,
        ]
        if payload.action != "flatten":
            filters.append(Strategy.status != "flattening")
        if payload.action == "start":
            filters.append(Strategy.risk_halted.is_(False))
        changed = db.execute(
            update(Strategy)
            .where(*filters)
            .values(
                status={"start": "running", "pause": "paused", "flatten": "flattening"}[
                    payload.action
                ]
            )
        )
        if changed.rowcount != 1:
            strategy = db.get(Strategy, strategy_id)
            if strategy is None:
                raise HTTPException(404, "策略不存在")
            raise HTTPException(409, "策略行情来源不匹配或正在等待平仓")
        strategy = db.get(Strategy, strategy_id)
        db.add(Event(strategy_id=strategy_id, message=f"用户指令：{payload.action}"))
        db.commit()
        return row_dict(strategy)

    @app.get("/api/positions", dependencies=protected)
    def positions(db=db_dep):
        result = []
        for position in db.scalars(select(Position)):
            value = row_dict(position)
            strategy = db.get(Strategy, position.strategy_id)
            value.update(
                symbol=strategy.symbol,
                strategy_name=strategy.name,
                unrealized_pnl=(position.mark_price - position.entry_price)
                * position.quantity
                * (1 if position.side == "LONG" else -1),
            )
            result.append(value)
        return result

    @app.get("/api/orders", dependencies=protected)
    def orders(limit: int = Query(100, ge=1, le=500), db=db_dep):
        return [
            row_dict(o)
            for o in db.scalars(
                select(PaperOrder).order_by(PaperOrder.id.desc()).limit(limit)
            )
        ]

    @app.get("/api/events", dependencies=protected)
    def events(limit: int = Query(50, ge=1, le=200), db=db_dep):
        return [
            row_dict(e)
            for e in db.scalars(select(Event).order_by(Event.id.desc()).limit(limit))
        ]

    @app.post("/api/backtests", status_code=202, dependencies=protected)
    def queue_backtest(payload: BacktestCreate, db=db_dep):
        validate_symbol(payload.exchange, payload.symbol)
        pending = db.scalar(
            select(func.count())
            .select_from(BacktestJob)
            .where(BacktestJob.status.in_(["pending", "running"]))
        )
        if pending >= 10:
            raise HTTPException(409, "回测队列已满，请等待当前任务完成")
        job = BacktestJob(
            parameters={
                **payload.model_dump(mode="json"),
                "source": settings.market_source,
                "market_environment": environment.active
                if payload.exchange == "bybit"
                else "未记录",
            }
        )
        db.add(job)
        db.commit()
        return row_dict(job)

    @app.post("/api/backtests/batch", status_code=202, dependencies=protected)
    def batch_backtest(payload: BacktestBatch, db=db_dep):
        validate_symbol(payload.base.exchange, payload.base.symbol)
        pending = db.scalar(
            select(func.count())
            .select_from(BacktestJob)
            .where(BacktestJob.status.in_(["pending", "running"]))
        )
        if pending + len(payload.variants) > 10:
            raise HTTPException(409, "队列容量不足，请等待当前任务完成或减少参数组")
        batch_id = uuid.uuid4().hex
        warmup = max(rules.warmup for rules in payload.variants)
        base = payload.base.model_dump(mode="json")
        if not base.get("start_at"):
            from .market import INTERVAL_MINUTES

            step_seconds = INTERVAL_MINUTES[payload.base.interval] * 60
            if payload.base.limit <= warmup + 2:
                raise HTTPException(422, "样本不足以预热这组参数，请增加样本数")
            stamp = utcnow()
            end = stamp - timedelta(seconds=stamp.timestamp() % step_seconds)
            base.update(
                start_at=(
                    end
                    - timedelta(
                        seconds=step_seconds * (payload.base.limit - warmup - 1)
                    )
                ).isoformat(),
                end_at=end.isoformat(),
            )
        jobs = []
        for rules in payload.variants:
            params = base.copy()
            params.update(
                rules=rules.model_dump(),
                source=settings.market_source,
                batch_id=batch_id,
                market_environment=environment.active
                if payload.base.exchange == "bybit"
                else "未记录",
                history_warmup=warmup,
            )
            job = BacktestJob(parameters=params)
            db.add(job)
            jobs.append(job)
        db.commit()
        return {"batch_id": batch_id, "jobs": [row_dict(job) for job in jobs]}

    @app.post(
        "/api/backtests/{job_id}/promote", status_code=201, dependencies=protected
    )
    def promote_backtest(job_id: int, payload: PromoteBacktest, db=db_dep):
        job = db.get(BacktestJob, job_id)
        if job is None:
            raise HTTPException(404, "回测不存在")
        if job.status != "completed" or not job.result:
            raise HTTPException(409, "仅已完成的回测可以创建模拟策略")
        if job.parameters.get("optimization_eligible") is False:
            raise HTTPException(
                409, "该优化候选未通过研究与独立验证约束，请查看未通过原因"
            )
        if job.parameters["source"] != settings.market_source:
            raise HTTPException(409, "回测行情来源与当前运行环境不匹配")
        if (
            job.parameters.get("risk_sizing")
            or job.parameters.get("job_type") == "portfolio"
        ):
            raise HTTPException(
                409, "风险仓位回测请应用到交易助手，固定数量模拟策略无法复现此资金管理"
            )
        params = {
            key: value
            for key, value in job.parameters.items()
            if key in StrategyCreate.model_fields and key != "name"
        }
        config = StrategyCreate(name=payload.name, **params)
        return save_strategy(config, db, job_id)

    @app.get("/api/backtests", dependencies=protected)
    def backtests(db=db_dep):
        # Polling needs metrics only; avoid loading every large equity curve.
        columns = [
            column
            for column in BacktestJob.__table__.columns
            if column.name != "result"
        ]
        statement = (
            select(*columns, BacktestJob.result["metrics"].label("summary"))
            .order_by(BacktestJob.id.desc())
            .limit(50)
        )
        return [dict(row._mapping) for row in db.execute(statement)]

    @app.get("/api/backtests/{job_id}", dependencies=protected)
    def backtest(job_id: int, db=db_dep):
        job = db.get(BacktestJob, job_id)
        if job is None:
            raise HTTPException(404, "回测不存在")
        return archived_row(job)

    @app.get("/api/account", dependencies=protected)
    def account(exchange: str = Query("bybit", pattern="^(bybit|binance)$")):
        if settings.market_source == "demo":
            return {
                "source": "demo",
                "connected": False,
                "message": "演示模式不访问交易所账户",
            }
        try:
            config = market.exchange_settings(exchange)
            if exchange == "bybit":
                raw = BybitClient(config, require_private=True).get_wallet_balance()[
                    "result"
                ]["list"]
                balances = [
                    {"coin": coin["coin"], "balance": coin.get("walletBalance", "0")}
                    for wallet in raw
                    for coin in wallet.get("coin", [])
                ]
                testnet = config.testnet
            else:
                raw = BinanceFuturesClient(config, require_private=True).get_account()
                balances = [
                    {"coin": item["asset"], "balance": item["walletBalance"]}
                    for item in raw.get("assets", [])
                ]
                testnet = config.binance_testnet
            return {
                "source": "exchange",
                "connected": True,
                "exchange": exchange,
                "testnet": testnet,
                "balances": balances,
            }
        except Exception:  # noqa: BLE001 - private SDK exceptions may contain credentials
            raise HTTPException(
                502, "账户读取失败，请检查密钥、读取权限、主网确认和网络"
            ) from None

    @app.get("/api/optimization-space", dependencies=protected)
    def optimization_space(
        template: Literal[
            "ema_rsi", "ema_cross", "rsi_reversion", "breakout", "bollinger_reversion"
        ] = "ema_rsi",
    ):
        return space_for(template)

    @app.post("/api/optimizations", status_code=202, dependencies=protected)
    def queue_optimization(payload: OptimizationCreate, db=db_dep):
        validate_symbol(payload.base.exchange, payload.base.symbol)
        pending = db.scalar(
            select(func.count())
            .select_from(OptimizationJob)
            .where(OptimizationJob.status.in_(["pending", "running", "cancelling"]))
        )
        if pending >= 2:
            raise HTTPException(
                409, "自动优化最多同时排队两个任务，请等待或取消当前任务"
            )
        job = OptimizationJob(
            parameters={
                **payload.model_dump(mode="json"),
                "source": settings.market_source,
                "market_environment": environment.active
                if settings.market_source == "exchange"
                and payload.base.exchange == "bybit"
                else "未记录",
            }
        )
        db.add(job)
        db.commit()
        return row_dict(job)

    @app.get("/api/optimizations", dependencies=protected)
    def list_optimizations(db=db_dep):
        columns = [
            column
            for column in OptimizationJob.__table__.columns
            if column.name != "result"
        ]
        return [
            dict(row._mapping)
            for row in db.execute(
                select(*columns).order_by(OptimizationJob.id.desc()).limit(30)
            )
        ]

    @app.get("/api/optimizations/{job_id}", dependencies=protected)
    def get_optimization(job_id: int, db=db_dep):
        job = db.get(OptimizationJob, job_id)
        if job is None:
            raise HTTPException(404, "优化任务不存在")
        return archived_row(job)

    @app.post("/api/optimizations/{job_id}/cancel", dependencies=protected)
    def cancel_optimization(job_id: int, db=db_dep):
        job = db.scalar(
            select(OptimizationJob)
            .where(OptimizationJob.id == job_id)
            .with_for_update()
        )
        if job is None:
            raise HTTPException(404, "优化任务不存在")
        if job.status not in {"pending", "running", "cancelling"}:
            raise HTTPException(409, "该任务已结束")
        job.status = "cancelled" if job.status == "pending" else "cancelling"
        if job.status == "cancelled":
            job.completed_at = utcnow()
        db.commit()
        return row_dict(job)

    return app
