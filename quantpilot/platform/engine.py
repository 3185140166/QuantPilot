"""Durable paper-only trading engine. Run independently of the HTTP server."""

import asyncio
import logging
import uuid
from decimal import Decimal

from sqlalchemy import select, update

from quantpilot.strategy import generate_signal

from .database import make_database
from .leases import release_lease, renew_lease
from .market import MarketService
from .models import (
    EquityPoint,
    Event,
    PaperOrder,
    Position,
    Strategy,
    WorkerLease,
    utcnow,
)
from .rules import StrategyRules, position_exit
from .settings import PlatformSettings

logger = logging.getLogger(__name__)


def guard_lease(db, name, owner):
    now = utcnow()
    result = db.execute(
        update(WorkerLease)
        .where(
            WorkerLease.name == name,
            WorkerLease.owner == owner,
            WorkerLease.expires_at > now,
        )
        .values(heartbeat_at=now)
    )
    if result.rowcount != 1:
        raise RuntimeError("Worker lease expired")


def record_fill(db, strategy, position, action, side, price, bar_time, source):
    impact = strategy.slippage_bps / Decimal(10000)
    fill = price * (1 + impact if side == "BUY" else 1 - impact)
    quantity = position.quantity if action == "close" else strategy.quantity
    fee = fill * quantity * strategy.fee_rate
    gross = Decimal(0)
    if action == "close":
        gross = (
            (fill - position.entry_price)
            * quantity
            * (1 if position.side == "LONG" else -1)
        )
        db.delete(position)
    else:
        db.add(
            Position(
                strategy_id=strategy.id,
                side="LONG" if side == "BUY" else "SHORT",
                quantity=quantity,
                entry_price=fill,
                mark_price=price,
            )
        )
    strategy.cash += gross - fee
    db.add(
        PaperOrder(
            strategy_id=strategy.id,
            symbol=strategy.symbol,
            side=side,
            action=action,
            quantity=quantity,
            price=fill,
            fee=fee,
            realized_pnl=gross - fee,
            bar_time=bar_time,
            source=source,
        )
    )


def process_strategy(factory, strategy_id, market, owner=None):
    # Network IO is outside the transaction. Re-read desired status before executing.
    with factory() as db:
        config = db.get(Strategy, strategy_id)
        if config is None:
            return
        if config.source != market.source:
            raise ValueError("Strategy market source differs from worker configuration")
        if config.status == "paused" and db.get(Position, strategy_id) is None:
            return
        rules = StrategyRules.model_validate(config.rules or {})
        frame, closed = market.bars(
            config.exchange,
            config.symbol,
            config.interval,
            limit=max(200, rules.warmup + 3),
        )
    if len(closed) < rules.warmup + 2:
        raise ValueError("Not enough closed bars for configured indicators")
    bar_time = closed.iloc[-1].timestamp.isoformat()
    price = Decimal(str(frame.iloc[-1].close))
    signal = generate_signal(closed, **rules.signal_options)
    with factory.begin() as db:
        if owner:
            guard_lease(db, "engine", owner)
        strategy = db.scalar(
            select(Strategy).where(Strategy.id == strategy_id).with_for_update()
        )
        position = db.get(Position, strategy_id)
        if position:
            position.mark_price = price
        strategy.last_error = None

        def save_equity():
            db.flush()
            current = db.get(Position, strategy_id)
            equity = strategy.cash + (
                (current.mark_price - current.entry_price)
                * current.quantity
                * (1 if current.side == "LONG" else -1)
                if current
                else Decimal(0)
            )
            strategy.peak_equity = max(
                strategy.peak_equity or strategy.initial_equity, equity
            )
            drawdown = (
                (equity / strategy.peak_equity - 1)
                if strategy.peak_equity > 0
                else Decimal(0)
            )
            strategy.max_drawdown = min(strategy.max_drawdown or Decimal(0), drawdown)
            point = db.scalar(
                select(EquityPoint).where(
                    EquityPoint.strategy_id == strategy_id,
                    EquityPoint.bar_time == bar_time,
                )
            )
            if point is None:
                point = EquityPoint(strategy_id=strategy_id, bar_time=bar_time)
                db.add(point)
            point.equity, point.drawdown = equity, drawdown
            return drawdown

        drawdown = save_equity()
        if strategy.status == "flattening":
            if position:
                # Flatten is a separate command identity, even within the same signal bar.
                record_fill(
                    db,
                    strategy,
                    position,
                    "close",
                    "SELL" if position.side == "LONG" else "BUY",
                    price,
                    "flatten:" + utcnow().isoformat(),
                    market.source,
                )
            strategy.status = "paused"
            save_equity()
            db.add(Event(strategy_id=strategy.id, message="模拟持仓已平仓，策略已暂停"))
            return
        reason = (
            position_exit(position.side, position.entry_price, price, rules)
            if position
            else None
        )
        risk_halt = bool(
            rules.max_drawdown_pct and float(-drawdown * 100) >= rules.max_drawdown_pct
        )
        if risk_halt or reason:
            if position:
                record_fill(
                    db,
                    strategy,
                    position,
                    "close",
                    "SELL" if position.side == "LONG" else "BUY",
                    price,
                    "risk:" + utcnow().isoformat(),
                    market.source,
                )
            strategy.last_bar = bar_time
            if risk_halt:
                strategy.status = "paused"
                strategy.risk_halted = True
            db.add(
                Event(
                    strategy_id=strategy.id,
                    level="warning",
                    message="最大回撤触发，模拟平仓并暂停"
                    if risk_halt
                    else f"{reason}触发，模拟持仓已平仓",
                )
            )
            save_equity()
            return
        if strategy.status == "paused":
            # Pause stops entries/reversals, but existing exposure stays protected.
            return
        if strategy.last_bar == bar_time:
            return
        # Never replay missed bars after restart: evaluate the latest closed bar once.
        strategy.last_bar = bar_time
        strategy.signal = signal
        if position and signal in {"LONG", "SHORT"} and position.side != signal:
            record_fill(
                db,
                strategy,
                position,
                "close",
                "SELL" if position.side == "LONG" else "BUY",
                price,
                bar_time,
                market.source,
            )
            db.flush()
            position = None
        if position is None and signal in {"LONG", "SHORT"}:
            notional = (
                price * strategy.quantity * (1 + strategy.slippage_bps / Decimal(10000))
            )
            # No leverage in the paper engine; reject an exposure beyond available cash.
            if (
                notional > strategy.max_notional
                or notional * (1 + strategy.fee_rate) > strategy.cash
            ):
                db.add(
                    Event(
                        strategy_id=strategy.id,
                        level="warning",
                        message="模拟开仓被风控拒绝：超过名义金额或资金上限",
                    )
                )
                return
            record_fill(
                db,
                strategy,
                None,
                "open",
                "BUY" if signal == "LONG" else "SELL",
                price,
                bar_time,
                market.source,
            )
            db.add(
                Event(
                    strategy_id=strategy.id,
                    message=f"模拟开仓：{signal} {strategy.symbol}",
                )
            )
        save_equity()


def tick(factory, market, owner):
    if not renew_lease(factory, "engine", owner):
        return
    with factory() as db:
        ids = list(
            db.scalars(
                select(Strategy.id).where(
                    (Strategy.status.in_(["running", "flattening"]))
                    | Strategy.id.in_(select(Position.strategy_id))
                )
            )
        )
    for strategy_id in ids:
        if not renew_lease(factory, "engine", owner):
            return
        try:
            process_strategy(factory, strategy_id, market, owner)
        except Exception:  # noqa: BLE001 - isolate strategy faults and redact SDK errors
            # Exchange SDK exceptions may contain request metadata: keep secrets out of logs.
            logger.warning("Strategy %s market processing failed", strategy_id)
            with factory.begin() as db:
                guard_lease(db, "engine", owner)
                strategy = db.get(Strategy, strategy_id)
                if strategy.last_error is None:
                    db.add(
                        Event(
                            strategy_id=strategy_id,
                            level="error",
                            message="行情或策略处理失败；引擎将自动重试",
                        )
                    )
                strategy.last_error = "行情或策略处理失败，请检查服务端配置与网络"


async def run():
    settings = PlatformSettings.from_env()
    engine, factory = make_database(settings.database_url)
    owner = uuid.uuid4().hex
    market = MarketService(settings.market_source)
    logger.info("Trading engine started; source=%s", settings.market_source)
    try:
        while True:
            try:
                await asyncio.to_thread(tick, factory, market, owner)
            except Exception:  # noqa: BLE001 - retry transient database/SDK failures
                logger.warning("Engine tick failed; retrying")
            await asyncio.sleep(settings.poll_seconds)
    finally:
        release_lease(factory, "engine", owner)
        engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
