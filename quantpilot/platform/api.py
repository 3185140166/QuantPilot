"""Personal API with optional authentication and paper-only execution."""

import hmac
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta, timezone
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from quantpilot.exchange.binance import BinanceFuturesClient
from quantpilot.exchange.bybit import BybitClient
from quantpilot.strategy import generate_signal

from .database import make_database
from .market import MarketService, UnsupportedSymbol, serialize_bars
from .models import (
    BacktestJob,
    EquityPoint,
    Event,
    OptimizationJob,
    PaperOrder,
    Position,
    Strategy,
    WatchlistEntry,
    WorkerLease,
    utcnow,
)
from .rules import TEMPLATES, StrategyRules
from .schemas import (
    BacktestBatch,
    BacktestCreate,
    MarketParameters,
    OptimizationCreate,
    PromoteBacktest,
    StrategyCommand,
    StrategyCreate,
    WatchlistCreate,
)
from .search_space import space_for
from .settings import PlatformSettings


def row_dict(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def create_app(
    settings: PlatformSettings | None = None, market: MarketService | None = None
):
    settings = settings or PlatformSettings.from_env()
    engine, factory = make_database(settings.database_url)
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
        return {
            "status": "ok",
            "service": "quantpilot",
            "auth_required": settings.auth_enabled,
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
        for lease in db.scalars(select(WorkerLease)):
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
            points = list(
                db.scalars(
                    select(EquityPoint)
                    .where(EquityPoint.strategy_id == strategy.id)
                    .order_by(EquityPoint.id.desc())
                    .limit(1000)
                )
            )
            value.update(
                rules=StrategyRules.model_validate(strategy.rules or {}).model_dump(),
                equity=equity,
                total_return=equity / strategy.initial_equity - 1,
                position=row_dict(position) if position else None,
                curve=[
                    {"timestamp": p.bar_time, "equity": p.equity}
                    for p in reversed(points)
                ],
            )
            result.append(value)
        return result

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
        return row_dict(job)

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
        return row_dict(job)

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
