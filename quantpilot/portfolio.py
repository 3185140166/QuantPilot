"""Causal multi-symbol replay with shared cash and explicit execution assumptions."""

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from math import isfinite

import pandas as pd

from .account_risk import evaluate_account_risk
from .backtest import BacktestResult, Trade
from .metrics import report
from .selection import budget_fit, observation, selection_features
from .selection import select as choose
from .sizing import allocation, number
from .strategy import generate_signals


@dataclass
class Holding:
    symbol: str
    side: str
    entry_time: object
    entry: Decimal
    qty: Decimal
    stop: Decimal
    take: Decimal | None
    margin: Decimal
    total_qty: Decimal
    entry_value: Decimal
    entry_fees: Decimal
    exit_value: Decimal = Decimal(0)
    exit_fees: Decimal = Decimal(0)
    gross: Decimal = Decimal(0)
    funding: Decimal = Decimal(0)
    closing: str | None = None
    insurance_adjustment: Decimal = Decimal(0)


@dataclass
class Pending:
    symbol: str
    side: str
    qty: Decimal
    price: Decimal
    stop: Decimal
    take: Decimal | None
    created_index: int
    planned_loss: Decimal


def run_portfolio(
    frames, marks, funding, contracts, config, execution, start_at, guard=lambda: None
):
    """Signals use candle i; fills and outcomes use candle i+1, never its signal."""
    from .platform.market import validate_market
    from .platform.schemas import ContractSnapshot, ExecutionAssumptions, TestnetSetup

    config = TestnetSetup.model_validate(config)
    execution = ExecutionAssumptions.model_validate(execution)
    sizing_config = (
        config.model_copy(
            update={"fee_rate": max(config.fee_rate, execution.maker_fee_rate)}
        )
        if execution.order_type == "Limit"
        else config
    )
    symbols = config.symbols
    if (
        set(frames) != set(symbols)
        or set(marks) != set(symbols)
        or set(contracts) != set(symbols)
    ):
        raise ValueError(
            "Every portfolio symbol needs prices, marks and contract constraints"
        )
    reference = pd.DatetimeIndex(frames[symbols[0]].timestamp)
    if reference.tz is None:
        raise ValueError("Portfolio timestamps must include timezone")
    if len(reference) < config.rules.warmup + 2 or not reference.is_unique:
        raise ValueError("Insufficient portfolio data")
    contracts = {s: ContractSnapshot.model_validate(contracts[s]) for s in symbols}
    for symbol in symbols:
        validate_market(frames[symbol])
        validate_market(marks[symbol])
        if not pd.DatetimeIndex(frames[symbol].timestamp).equals(
            reference
        ) or not pd.DatetimeIndex(marks[symbol].timestamp).equals(reference):
            raise ValueError("Portfolio candles must be fully aligned")
    if len(set(reference.to_series().diff().iloc[1:])) != 1:
        raise ValueError("Portfolio candles contain missing intervals")
    first = reference.searchsorted(pd.Timestamp(start_at))
    first = max(first, config.rules.warmup, 30 if config.max_correlation < 1 else 0)
    if first >= len(reference) - 1:
        raise ValueError("No portfolio trading range")
    signals = {
        s: generate_signals(frames[s], **config.rules.signal_options).to_numpy()
        for s in symbols
    }
    returns = {s: frames[s].close.pct_change().to_numpy() for s in symbols}
    selection_frames = (
        {s: selection_features(frames[s], config.rules.model_dump()) for s in symbols}
        if config.selection.enabled
        else {}
    )
    liquidity_values = {
        s: (frames[s].close * frames[s].volume)
        .rolling(24, min_periods=1)
        .median()
        .to_numpy()
        for s in symbols
    }
    selection_log, selection_rejections = [], 0
    schedules = {}
    for symbol in symbols:
        rows = funding.get(symbol, []) if execution.include_funding else []
        if execution.include_funding and not rows:
            raise ValueError("Funding enabled but records missing")
        previous = None
        for row in rows:
            stamp, rate = pd.Timestamp(row["timestamp"]), number(row["rate"])
            if stamp.tzinfo is None:
                raise ValueError("Funding timestamps must include timezone")
            if previous is not None and stamp <= previous:
                raise ValueError("Funding records must be unique and increasing")
            if abs(rate) >= 1:
                raise ValueError("Invalid funding rate")
            previous = stamp
        schedules[symbol] = {
            pd.Timestamp(row["timestamp"]): number(row["rate"]) for row in rows
        }
    interior_funding = {}
    for stamp in sorted({t for rows in schedules.values() for t in rows}):
        index = reference.searchsorted(stamp, side="right") - 1
        if 0 <= index < len(reference) and reference[index] < stamp:
            interior_funding.setdefault(index, []).append(stamp)
    cash = number(config.budget)
    held, pending = {}, {}
    trades, events, curve = (
        [],
        [],
        [{"timestamp": str(reference[first]), "equity": float(cash)}],
    )
    rejected = partials = expired = liquidations = 0
    funding_total = fees_total = Decimal(0)
    peak, day, day_equity, halted = cash, None, cash, False
    maximum_dd = maximum_daily = Decimal(0)
    latest_risk = None
    event_limit = 10000

    def event(kind, symbol, stamp, **values):
        if len(events) < event_limit:
            events.append(
                {
                    "kind": kind,
                    "symbol": symbol,
                    "timestamp": str(stamp),
                    **{
                        key: str(value) if isinstance(value, Decimal) else value
                        for key, value in values.items()
                    },
                }
            )

    def mark_equity(prices):
        return cash + sum(
            (
                p.qty * (number(prices[s]) - p.entry) * (1 if p.side == "LONG" else -1)
                for s, p in held.items()
            ),
            Decimal(0),
        )

    def assess(equity, stamp):
        nonlocal peak, day, day_equity, halted, latest_risk, maximum_dd, maximum_daily
        peak = max(peak, equity)
        current_day = stamp.tz_convert("Asia/Shanghai").date()
        if day != current_day:
            day, day_equity = current_day, equity
        latest_risk = evaluate_account_risk(
            budget=config.budget,
            equity=equity,
            peak_equity=peak,
            day_equity=day_equity,
            daily_loss_pct=config.daily_loss_pct,
            max_drawdown_pct=config.max_drawdown_pct,
        )
        maximum_dd = max(
            maximum_dd, latest_risk.drawdown_amount / number(config.budget) * 100
        )
        maximum_daily = max(
            maximum_daily, latest_risk.daily_loss_amount / number(config.budget) * 100
        )
        if latest_risk.halted and not halted:
            event("risk_halt", "ALL", stamp, reason="共享预算日亏损或回撤达到限额")
        halted |= latest_risk.halted

    def capacity(symbol, index, used):
        step = number(contracts[symbol].constraints.qty_step)
        raw = max(
            Decimal(0),
            number(frames[symbol].iloc[index].volume)
            * number(execution.participation_rate)
            - used.get(symbol, Decimal(0)),
        )
        return (raw / step).to_integral_value(rounding=ROUND_DOWN) * step

    def close(symbol, price, stamp, reason, qty=None, liquidated=False):
        nonlocal cash, fees_total, liquidations
        p = held[symbol]
        qty = p.qty if qty is None else min(qty, p.qty)
        if qty <= 0:
            p.closing = reason
            return
        price = number(price) * (
            1 - number(config.slippage_bps) / 10000
            if p.side == "LONG"
            else 1 + number(config.slippage_bps) / 10000
        )
        fee = qty * price * number(config.fee_rate)
        gross = qty * (price - p.entry) * (1 if p.side == "LONG" else -1)
        raw_gross = gross
        if liquidated:
            # Stress approximation: isolated losses do not consume unrelated margin.
            gross = max(gross - fee, -p.margin) + fee
            liquidations += 1
        p.insurance_adjustment += gross - raw_gross
        cash += gross - fee
        fees_total += fee
        p.gross += gross
        p.exit_fees += fee
        p.exit_value += qty * price
        p.margin *= 1 - qty / p.qty
        p.qty -= qty
        p.closing = reason
        event(
            "close_fill",
            symbol,
            stamp,
            side=p.side,
            quantity=qty,
            price=price,
            fee=fee,
            reason=reason,
            remaining=p.qty,
        )
        if p.qty == 0:
            pnl = p.gross - p.entry_fees - p.exit_fees - p.funding
            trades.append(
                {
                    "symbol": symbol,
                    "side": p.side,
                    "entry_time": str(p.entry_time),
                    "exit_time": str(stamp),
                    "entry_price": float(p.entry_value / p.total_qty),
                    "exit_price": float(p.exit_value / p.total_qty),
                    "quantity": float(p.total_qty),
                    "pnl": float(pnl),
                    "fees": float(p.entry_fees + p.exit_fees),
                    "funding": float(p.funding),
                    "exit_reason": reason,
                    "insurance_adjustment": float(p.insurance_adjustment),
                }
            )
            del held[symbol]

    def opening(order, qty, stamp, fee_rate):
        nonlocal cash, fees_total
        if qty <= 0:
            return
        fee = qty * order.price * number(fee_rate)
        cash -= fee
        fees_total += fee
        if order.symbol not in held:
            held[order.symbol] = Holding(
                order.symbol,
                order.side,
                stamp,
                order.price,
                qty,
                order.stop,
                order.take,
                qty * order.price / config.leverage,
                qty,
                qty * order.price,
                fee,
            )
        else:
            p = held[order.symbol]
            p.entry = (p.entry * p.qty + order.price * qty) / (p.qty + qty)
            p.qty += qty
            p.total_qty += qty
            p.entry_value += qty * order.price
            p.entry_fees += fee
            p.margin += qty * order.price / config.leverage
        event(
            "open_fill",
            order.symbol,
            stamp,
            side=order.side,
            quantity=qty,
            price=order.price,
            fee=fee,
        )

    def cancel(symbol, stamp, reason):
        nonlocal expired
        if symbol in pending:
            order = pending.pop(symbol)
            event("cancel", symbol, stamp, quantity=order.qty, reason=reason)
            if reason == "挂单到期":
                expired += 1

    def charge_funding(stamp, prices):
        nonlocal cash, funding_total
        for symbol, p in list(held.items()):
            if stamp not in schedules[symbol] or p.entry_time >= stamp:
                continue
            amount = (
                p.qty
                * number(prices[symbol])
                * schedules[symbol][stamp]
                * (1 if p.side == "LONG" else -1)
            )
            reserved = sum(
                (o.qty * o.price / config.leverage for o in pending.values()),
                Decimal(0),
            )
            available = max(
                Decimal(0),
                cash - sum((v.margin for v in held.values()), Decimal(0)) - reserved,
            )
            if amount > available:
                p.margin = max(Decimal(0), p.margin - (amount - available))
            p.funding += amount
            cash -= amount
            funding_total += amount
            event(
                "funding",
                symbol,
                stamp,
                quantity=p.qty,
                rate=schedules[symbol][stamp],
                amount=amount,
            )

    def liquidation_price(p, mark):
        tiers = contracts[p.symbol].maintenance_tiers
        candidates = [
            tier
            for tier in tiers
            if p.qty * number(mark) <= number(tier.notional_limit)
        ]
        if not candidates:
            raise ValueError("Position exceeds captured risk tiers")
        tier = candidates[0]
        rate = number(tier.maintenance_rate) + number(config.fee_rate)
        equity = p.margin + p.qty * (mark - p.entry) * (1 if p.side == "LONG" else -1)
        maintenance = max(
            Decimal(0),
            p.qty * mark * number(tier.maintenance_rate) - number(tier.deduction),
        ) + p.qty * mark * number(config.fee_rate)
        if p.side == "LONG" and rate >= 1:
            return mark if equity <= maintenance else Decimal(0)
        if p.side == "LONG":
            return max(
                Decimal(0),
                (p.qty * p.entry - p.margin - number(tier.deduction))
                / (p.qty * (1 - rate)),
            )
        return (p.qty * p.entry + p.margin + number(tier.deduction)) / (
            p.qty * (1 + rate)
        )

    for index in range(first, len(reference) - 1):
        if index % 100 == 0:
            guard()
        nxt, stamp = index + 1, reference[index + 1]
        used = {}
        blocked = set()
        trade_open = {s: number(frames[s].iloc[nxt].open) for s in symbols}
        mark_open = {s: number(marks[s].iloc[nxt].open) for s in symbols}
        assess(
            mark_equity({s: marks[s].iloc[index].close for s in symbols}),
            reference[index],
        )
        # At settlement boundaries, only holdings opened strictly before that time pay.
        charge_funding(stamp, mark_open)
        assess(mark_equity(mark_open), stamp)
        for symbol, p in list(held.items()):
            mark = mark_open[symbol]
            liq = liquidation_price(p, mark)
            gap_liq = mark <= liq if p.side == "LONG" else mark >= liq
            gap_stop = mark <= p.stop if p.side == "LONG" else mark >= p.stop
            reverse = signals[symbol][index] == (
                "SHORT" if p.side == "LONG" else "LONG"
            )
            if halted or reverse or p.closing or gap_stop or gap_liq:
                if gap_stop or gap_liq or p.closing:
                    blocked.add(symbol)
                reason = (
                    "强平近似"
                    if gap_liq
                    else "止损"
                    if gap_stop
                    else "账户风控"
                    if halted
                    else p.closing or "反向信号"
                )
                cancel(symbol, stamp, reason)
                qty = p.qty if gap_liq else min(p.qty, capacity(symbol, nxt, used))
                if qty < p.qty:
                    partials += 1
                close(
                    symbol, trade_open[symbol], stamp, reason, qty, liquidated=gap_liq
                )
                used[symbol] = used.get(symbol, Decimal(0)) + qty
        for symbol, order in list(pending.items()):
            if (
                halted
                or signals[symbol][index]
                == ("SHORT" if order.side == "LONG" else "LONG")
                or index - order.created_index >= execution.limit_expiry_bars
            ):
                cancel(
                    symbol,
                    stamp,
                    "账户风控"
                    if halted
                    else "反向信号"
                    if signals[symbol][index]
                    == ("SHORT" if order.side == "LONG" else "LONG")
                    else "挂单到期",
                )
        selection = choose(
            [
                {
                    "symbol": s,
                    **(
                        observation(selection_frames[s], index)
                        if config.selection.enabled
                        else {"liquidity": float(liquidity_values[s][index])}
                    ),
                    **(
                        budget_fit(
                            config,
                            frames[s].iloc[index].close,
                            contracts[s].constraints.instrument(),
                        )
                        if config.selection.enabled
                        else {}
                    ),
                }
                for s in symbols
            ],
            config.selection,
        )
        ranked = [row["symbol"] for row in selection if row["selected"]]
        selection_rejections += sum(
            not row["selected"] and signals[row["symbol"]][index] != "HOLD"
            for row in selection
        )
        if config.selection.enabled and len(selection_log) < 500:
            selection_log.append(
                {
                    "timestamp": str(reference[index]),
                    "selected": ranked,
                    "rows": selection,
                }
            )
        for symbol in ranked:
            signal = signals[symbol][index]
            if (
                halted
                or signal == "HOLD"
                or symbol in held
                or symbol in pending
                or symbol in blocked
            ):
                continue
            reason = None
            if len(set(held) | set(pending)) >= config.max_positions:
                reason = "持仓及挂单币种数达到上限"
            for other in set(held) | set(pending):
                if config.max_correlation >= 1:
                    break
                pair = pd.DataFrame(
                    {
                        "a": returns[symbol][max(0, index - 99) : index + 1],
                        "b": returns[other][max(0, index - 99) : index + 1],
                    }
                ).dropna()
                corr = pair.a.corr(pair.b) if len(pair) >= 30 else float("nan")
                if not isfinite(corr) or abs(corr) > config.max_correlation:
                    reason = "持仓相关性过高或样本不足"
            risk = sum(
                (
                    p.qty
                    * (
                        max(
                            Decimal(0),
                            (p.entry - p.stop) * (1 if p.side == "LONG" else -1),
                        )
                        + p.entry
                        * (
                            2 * number(sizing_config.fee_rate)
                            + 2 * number(config.slippage_bps) / 10000
                        )
                    )
                    for p in held.values()
                ),
                Decimal(0),
            )
            risk += sum((o.planned_loss for o in pending.values()), Decimal(0))
            notional = sum(
                (p.qty * max(p.entry, mark_open[s]) for s, p in held.items()),
                Decimal(0),
            ) + sum((o.qty * o.price for o in pending.values()), Decimal(0))
            available = max(
                Decimal(0),
                cash
                - sum((p.margin for p in held.values()), Decimal(0))
                - sum(
                    (o.qty * o.price / config.leverage for o in pending.values()),
                    Decimal(0),
                ),
            )
            raw = (
                number(frames[symbol].iloc[index].close)
                if execution.order_type == "Limit"
                else trade_open[symbol]
            )
            quote = (
                raw
                * (
                    1 - number(execution.limit_offset_bps) / 10000
                    if signal == "LONG"
                    else 1 + number(execution.limit_offset_bps) / 10000
                )
                if execution.order_type == "Limit"
                else raw
            )
            step = number(contracts[symbol].constraints.tick_size)
            quote = (quote / step).to_integral_value(rounding=ROUND_DOWN) * step
            if execution.order_type == "Limit" and (
                quote >= trade_open[symbol]
                if signal == "LONG"
                else quote <= trade_open[symbol]
            ):
                reason = "PostOnly限价触及开盘价格代理，视为拒绝"
            planned = allocation(
                sizing_config,
                mark_equity(mark_open),
                quote,
                signal,
                contracts[symbol].constraints.instrument(),
                risk,
                notional,
                available,
            )
            if reason or not planned["accepted"]:
                rejected += 1
                event(
                    "rejected",
                    symbol,
                    stamp,
                    reason=reason or planned["reason"],
                    side=signal,
                )
                continue
            qty = number(planned["quantity"])
            price = (
                quote
                if execution.order_type == "Limit"
                else quote
                * (
                    1 + number(config.slippage_bps) / 10000
                    if signal == "LONG"
                    else 1 - number(config.slippage_bps) / 10000
                )
            )
            order = Pending(
                symbol,
                signal,
                qty,
                price,
                number(planned["stop_loss"]),
                number(planned["take_profit"]) if planned["take_profit"] else None,
                index,
                number(planned["planned_loss"]),
            )
            event(
                "open_intent",
                symbol,
                stamp,
                side=signal,
                quantity=qty,
                price=price,
                planned_loss=order.planned_loss,
                reason="已收盘策略信号通过共享预算检查",
            )
            if execution.order_type == "Limit":
                pending[symbol] = order
            else:
                filled = min(qty, capacity(symbol, nxt, used))
                step = number(contracts[symbol].constraints.qty_step)
                cash_qty = available / (
                    price / config.leverage + price * number(config.fee_rate)
                )
                notional_qty = max(Decimal(0), number(config.budget) - notional) / price
                filled = min(
                    filled,
                    (cash_qty / step).to_integral_value(rounding=ROUND_DOWN) * step,
                    (notional_qty / step).to_integral_value(rounding=ROUND_DOWN) * step,
                )
                if filled < qty:
                    partials += 1
                    event(
                        "ioc_cancel",
                        symbol,
                        stamp,
                        quantity=qty - filled,
                        reason="成交量参与率不足，撤销市价剩余数量",
                    )
                opening(order, filled, stamp, config.fee_rate)
                used[symbol] = used.get(symbol, Decimal(0)) + filled
        for symbol, order in list(pending.items()):
            row = frames[symbol].iloc[nxt]
            touched = (
                row.low <= order.price
                if order.side == "LONG"
                else row.high >= order.price
            )
            if not touched:
                continue
            filled = min(order.qty, capacity(symbol, nxt, used))
            free = max(
                Decimal(0),
                cash
                - sum((p.margin for p in held.values()), Decimal(0))
                - sum(
                    (
                        o.qty * o.price / config.leverage
                        for s, o in pending.items()
                        if s != symbol
                    ),
                    Decimal(0),
                ),
            )
            step = number(contracts[symbol].constraints.qty_step)
            affordable = free / (
                order.price / config.leverage
                + order.price * number(execution.maker_fee_rate)
            )
            other_notional = sum(
                (p.qty * max(p.entry, mark_open[s]) for s, p in held.items()),
                Decimal(0),
            ) + sum(
                (o.qty * o.price for s, o in pending.items() if s != symbol), Decimal(0)
            )
            notional_qty = (
                max(Decimal(0), number(config.budget) - other_notional) / order.price
            )
            filled = min(
                filled,
                (affordable / step).to_integral_value(rounding=ROUND_DOWN) * step,
                (notional_qty / step).to_integral_value(rounding=ROUND_DOWN) * step,
            )
            opening(order, filled, stamp, execution.maker_fee_rate)
            used[symbol] = used.get(symbol, Decimal(0)) + filled
            order.qty -= filled
            order.planned_loss *= order.qty / (order.qty + filled) if filled else 1
            if order.qty <= 0:
                del pending[symbol]
            elif filled:
                partials += 1
        # Interior settlement mark prices are approximated by this candle's mark open.
        interior = interior_funding.get(nxt, [])
        for funding_stamp in interior:
            charge_funding(funding_stamp, mark_open)
        for symbol, p in list(held.items()):
            row = marks[symbol].iloc[nxt]
            worst = number(row.low if p.side == "LONG" else row.high)
            liq = liquidation_price(p, worst)
            liquidated = worst <= liq if p.side == "LONG" else worst >= liq
            stopped = worst <= p.stop if p.side == "LONG" else worst >= p.stop
            taken = p.take and (
                number(row.high) >= p.take
                if p.side == "LONG"
                else number(row.low) <= p.take
            )
            if liquidated or stopped or taken:
                # The closer protective threshold wins; adverse exits precede profit.
                threshold = (
                    (max(p.stop, liq) if p.side == "LONG" else min(p.stop, liq))
                    if stopped and liquidated
                    else liq
                    if liquidated
                    else p.stop
                    if stopped
                    else p.take
                )
                is_liq = liquidated and (not stopped or threshold == liq)
                reason = "强平近似" if is_liq else "止损" if stopped else "止盈"
                cancel(symbol, stamp, reason)
                last_proxy = trade_open[symbol] * threshold / mark_open[symbol]
                qty = p.qty if is_liq else min(p.qty, capacity(symbol, nxt, used))
                if qty < p.qty:
                    partials += 1
                close(symbol, last_proxy, stamp, reason, qty, liquidated=is_liq)
                used[symbol] = used.get(symbol, Decimal(0)) + qty
        marked = mark_equity({s: marks[s].iloc[nxt].close for s in symbols})
        assess(marked, stamp)
        curve.append({"timestamp": str(stamp), "equity": float(marked)})
    for symbol in list(pending):
        cancel(symbol, reference[-1], "回测结束")
    for symbol in list(held):
        close(symbol, frames[symbol].iloc[-1].close, reference[-1], "回测结束结算")
    curve[-1]["equity"] = float(cash)
    assess(cash, reference[-1])
    metric_trades = [
        Trade(
            t["side"],
            t["entry_time"],
            t["exit_time"],
            t["entry_price"],
            t["exit_price"],
            t["quantity"],
            t["pnl"],
        )
        for t in trades
    ]
    result = BacktestResult(
        float(cash),
        pd.Series({r["timestamp"]: r["equity"] for r in curve}),
        metric_trades,
    )
    return {
        "metrics": report(result),
        "curve": curve,
        "trades": trades,
        "events": events,
        "rejected_entries": rejected,
        "partial_fills": partials,
        "expired_orders": expired,
        "liquidations": liquidations,
        "funding_total": float(funding_total),
        "fees_total": float(fees_total),
        "risk_halted": halted,
        "risk_summary": {
            **latest_risk.as_dict(),
            "max_drawdown_pct": float(maximum_dd),
            "max_daily_loss_pct": float(maximum_daily),
            "halted": halted,
        },
        "limitations": [
            "资金费只使用接口实际返回的历史记录，不能证明整个区间无缺失；关闭资金费时该成本不计入结果",
            "单根K线内路径未知，保护退出按不利顺序处理；不还原真实订单簿",
            "当前合约限制与维持保证金档位为快照，并非历史档位；逐仓强平采用含平仓费的近似模型",
            "成交量参与率为假设；PostOnly以开盘价代理盘口，部分退出的剩余仓位在后续K线处理",
            "K线内部资金费使用所在标记K线开盘价近似；开盘保护退出前处理当时资金费",
            "回测结束强制结算假设可以成交；强平压力场景亏损封顶剩余逐仓保证金，不复现保险与ADL过程",
        ],
        "events_truncated": len(events) >= event_limit,
        "selection": {
            "policy": config.selection.model_dump(),
            "history": selection_log,
            "history_truncated": config.selection.enabled and len(selection_log) >= 500,
            "rejected_signals": selection_rejections,
            "semantics": "仅筛选新开仓，使用已收盘数据；排名退出不平仓",
        },
    }
