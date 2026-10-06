"""Portable SQLAlchemy schema; amounts are fixed precision decimals."""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    exchange: Mapped[str] = mapped_column(String(16), default="bybit")
    source: Mapped[str] = mapped_column(String(16), default="demo")
    symbol: Mapped[str] = mapped_column(String(24), default="BTCUSDT")
    interval: Mapped[str] = mapped_column(String(8), default="15")
    rules: Mapped[dict] = mapped_column(JSON, default=dict)
    origin_job_id: Mapped[int | None] = mapped_column(ForeignKey("backtest_jobs.id"))
    peak_equity: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    max_drawdown: Mapped[Decimal] = mapped_column(Numeric(18, 10), default=0)
    risk_halted: Mapped[bool] = mapped_column(default=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 10), default=Decimal("0.001"))
    initial_equity: Mapped[Decimal] = mapped_column(Numeric(28, 10), default=10000)
    cash: Mapped[Decimal] = mapped_column(Numeric(28, 10), default=10000)
    max_notional: Mapped[Decimal] = mapped_column(Numeric(28, 10), default=1000)
    fee_rate: Mapped[Decimal] = mapped_column(
        Numeric(18, 10), default=Decimal("0.00055")
    )
    slippage_bps: Mapped[Decimal] = mapped_column(Numeric(18, 10), default=2)
    status: Mapped[str] = mapped_column(String(16), default="paused")
    signal: Mapped[str] = mapped_column(String(8), default="HOLD")
    last_bar: Mapped[str | None] = mapped_column(String(40))
    last_error: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Position(Base):
    __tablename__ = "positions"

    strategy_id: Mapped[int] = mapped_column(
        ForeignKey("strategies.id"), primary_key=True
    )
    side: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    entry_price: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    mark_price: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaperOrder(Base):
    __tablename__ = "paper_orders"
    __table_args__ = (UniqueConstraint("strategy_id", "bar_time", "action"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(24))
    side: Mapped[str] = mapped_column(String(8))
    action: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    price: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    fee: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(28, 10), default=0)
    bar_time: Mapped[str] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id"))
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class WorkerLease(Base):
    __tablename__ = "worker_leases"

    name: Mapped[str] = mapped_column(String(24), primary_key=True)
    owner: Mapped[str] = mapped_column(String(40), default="")
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class BacktestJob(Base):
    __tablename__ = "backtest_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    parameters: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WatchlistEntry(Base):
    __tablename__ = "watchlist"

    exchange: Mapped[str] = mapped_column(String(16), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(24), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class EquityPoint(Base):
    __tablename__ = "strategy_equity"
    __table_args__ = (UniqueConstraint("strategy_id", "bar_time"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), index=True)
    bar_time: Mapped[str] = mapped_column(String(40))
    equity: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    drawdown: Mapped[Decimal] = mapped_column(Numeric(18, 10))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class OptimizationJob(Base):
    __tablename__ = "optimization_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    parameters: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON)
    completed_trials: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TestnetAccount(Base):
    __tablename__ = "testnet_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    configuration: Mapped[dict] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(default=False)
    flatten_requested: Mapped[bool] = mapped_column(default=False)
    baseline_equity: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    equity: Mapped[Decimal] = mapped_column(Numeric(28, 10), default=200)
    peak_equity: Mapped[Decimal] = mapped_column(Numeric(28, 10), default=200)
    day: Mapped[str | None] = mapped_column(String(10))
    day_equity: Mapped[Decimal] = mapped_column(Numeric(28, 10), default=200)
    risk_halted: Mapped[bool] = mapped_column(default=False)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    last_bars: Mapped[dict] = mapped_column(JSON, default=dict)
    last_error: Mapped[str | None] = mapped_column(String(240))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    journal_start_ms: Mapped[int | None] = mapped_column(BigInteger)
    journal_cursor_ms: Mapped[int | None] = mapped_column(BigInteger)
    net_flows: Mapped[Decimal] = mapped_column(Numeric(28, 10), default=0)
    ledger_warning: Mapped[str | None] = mapped_column(String(240))
    baseline_cash: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    research_origin: Mapped[dict | None] = mapped_column(JSON)


class TestnetOrder(Base):
    __tablename__ = "testnet_orders"
    __table_args__ = (UniqueConstraint("symbol", "bar_time", "action"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    link_id: Mapped[str] = mapped_column(String(36), unique=True)
    symbol: Mapped[str] = mapped_column(String(24))
    side: Mapped[str] = mapped_column(String(8))
    action: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    take_profit: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    bar_time: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24), default="intent")
    order_id: Mapped[str | None] = mapped_column(String(80))
    filled_quantity: Mapped[Decimal] = mapped_column(Numeric(28, 10), default=0)
    position_closed: Mapped[bool] = mapped_column(default=False)
    average_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    fee: Mapped[Decimal] = mapped_column(Numeric(28, 10), default=0)
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    order_type: Mapped[str] = mapped_column(String(12), default="Market")
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    opening_link_id: Mapped[str | None] = mapped_column(String(36))
    closed_at_ms: Mapped[int | None] = mapped_column(BigInteger)
    context: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class TestnetPosition(Base):
    __tablename__ = "testnet_positions"
    symbol: Mapped[str] = mapped_column(String(24), primary_key=True)
    side: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    entry_price: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    mark_price: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    stop_loss: Mapped[Decimal] = mapped_column(Numeric(28, 10), default=0)
    opening_link_id: Mapped[str] = mapped_column(String(36))


class TestnetExecution(Base):
    __tablename__ = "testnet_executions"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(100), index=True)
    link_id: Mapped[str] = mapped_column(String(36))
    symbol: Mapped[str] = mapped_column(String(24))
    side: Mapped[str] = mapped_column(String(8))
    exec_type: Mapped[str] = mapped_column(String(40))
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    price: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    fee: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    closed_size: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    at_ms: Mapped[int] = mapped_column(BigInteger, index=True)
    exit_reason: Mapped[str | None] = mapped_column(String(60))
    owned: Mapped[bool] = mapped_column(default=False)
    opening_link_id: Mapped[str | None] = mapped_column(String(36))
    sequence: Mapped[int | None] = mapped_column(BigInteger)


class TestnetLedger(Base):
    __tablename__ = "testnet_ledger"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(60))
    symbol: Mapped[str] = mapped_column(String(24))
    order_id: Mapped[str] = mapped_column(String(100))
    change: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    cash_flow: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    fee: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    funding: Mapped[Decimal] = mapped_column(Numeric(28, 10))
    at_ms: Mapped[int] = mapped_column(BigInteger, index=True)
    trade_id: Mapped[str | None] = mapped_column(String(100))
