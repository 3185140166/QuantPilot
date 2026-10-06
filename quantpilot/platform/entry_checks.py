"""Fail closed before opening; closing orders do not depend on market quotes."""

import time
from decimal import Decimal

from quantpilot.account_risk import evaluate_account_risk
from quantpilot.sizing import allocation, number


class EntryBlocked(ValueError):
    """A public, credential-free explanation suitable for the trading panel."""


def fingerprint(snapshot):
    positions = sorted(
        tuple(
            str(row.get(key, ""))
            for key in ("symbol", "side", "size", "avgPrice", "stopLoss", "positionIdx")
        )
        for row in snapshot["positions"]
    )
    orders = sorted(
        tuple(
            str(row.get(key, ""))
            for key in (
                "orderId",
                "symbol",
                "orderLinkId",
                "orderStatus",
                "qty",
                "cumExecQty",
                "reduceOnly",
                "price",
                "triggerPrice",
                "leavesQty",
            )
        )
        for row in snapshot["open_orders"]
    )
    return (
        snapshot["wallet_balance"],
        snapshot["margin_mode"],
        positions,
        orders,
    )


def preflight(
    factory, broker, market, config, card, baseline, entry_guard=None, fixed=False
):
    from quantpilot.sizing import spendable

    from .schemas import TestnetSetup
    from .testnet import account, generate_signal, position_risk

    started = time.monotonic()
    if entry_guard and not entry_guard():
        raise EntryBlocked("订单推送状态变化，等待重新对账")
    current = account(factory)
    if (
        not current.enabled
        or current.risk_halted
        or current.flatten_requested
        or TestnetSetup.model_validate(current.configuration) != config
    ):
        raise EntryBlocked("交易已暂停或配置变化，等待重新评估")
    _, closed = market.bars(
        "bybit", card["symbol"], config.interval, max(200, config.rules.warmup + 3)
    )
    if (
        closed.iloc[-1].timestamp.isoformat() != card["bar_time"]
        or generate_signal(closed, **config.rules.signal_options) != card["signal"]
    ):
        raise EntryBlocked("最新已收盘信号变化，等待重新评估")
    fresh = broker.snapshot()
    if fingerprint(fresh) != fingerprint(baseline):
        raise EntryBlocked("账户持仓、挂单或资金变化，等待重新对账")
    if fresh["margin_mode"] != "ISOLATED_MARGIN":
        raise EntryBlocked("账户未处于逐仓模式")
    from .ownership import classify

    ownership = classify(factory, fresh)
    if ownership["uncertain"] or card["symbol"] in ownership["blocked_symbols"]:
        raise EntryBlocked("该币种存在外部订单／持仓或订单来源未确认，等待核查")
    equity = (
        current.equity
        + number(fresh["wallet_equity"])
        - number(baseline["wallet_equity"])
    )
    risk_state = evaluate_account_risk(
        budget=config.budget,
        equity=equity,
        peak_equity=current.peak_equity,
        day_equity=current.day_equity,
        daily_loss_pct=config.daily_loss_pct,
        max_drawdown_pct=config.max_drawdown_pct,
    )
    if risk_state.halted:
        raise EntryBlocked("最新账户权益触发风控，暂停开仓")
    risk, notional = position_risk(fresh, config)
    if notional is not None:
        notional += number(ownership["reserved_notional"])
    occupied = {p["symbol"] for p in fresh["positions"]} | set(
        ownership["reserved_symbols"]
    )
    if risk is None or len(occupied) >= config.max_positions:
        raise EntryBlocked("持仓保护或持仓数量不满足开仓条件")
    instrument = broker.instrument(card["symbol"])
    if (
        number(instrument.get("leverageFilter", {}).get("maxLeverage", config.leverage))
        < config.leverage
    ):
        raise EntryBlocked("合约允许杠杆已变化")
    quote = broker.quote(card["symbol"])
    bid, ask, mark = (number(quote[key]) for key in ("bid", "ask", "mark"))
    if not 0 < bid <= ask or mark <= 0:
        raise EntryBlocked("报价异常，暂停开仓")
    if number(quote["age_seconds"]) > 5 or number(quote["age_seconds"]) < 0:
        raise EntryBlocked("报价超过 5 秒有效期，暂停开仓")
    spread = (ask - bid) / ((ask + bid) / 2) * 10000
    if spread > 20:
        raise EntryBlocked("买卖价差超过 20 个基点，暂停开仓")
    price = ask if card["signal"] == "LONG" else bid
    if abs(price / number(card["entry"]) - 1) * 10000 > 50:
        raise EntryBlocked("价格偏离研究信号超过 0.5%，等待重新评估")
    worst = price * (
        1
        + (1 if card["signal"] == "LONG" else -1) * number(config.slippage_bps) / 10000
    )
    plan = allocation(
        config,
        equity,
        worst,
        card["signal"],
        instrument,
        risk,
        notional,
        fresh["available_balance"],
    )
    if not plan["accepted"]:
        raise EntryBlocked(plan["reason"])
    result = {**card, **plan} if not fixed else dict(card)
    quantity, stop = number(result["quantity"]), number(result["stop_loss"])
    lot = instrument["lotSizeFilter"]
    if (
        quantity <= 0
        or quantity % number(lot["qtyStep"])
        or quantity < number(lot["minOrderQty"])
        or quantity
        > number(lot.get("maxMktOrderQty") or lot.get("maxOrderQty") or quantity)
        or quantity * price < number(lot.get("minNotionalValue"))
        or stop % number(instrument["priceFilter"]["tickSize"])
    ):
        raise EntryBlocked("最新合约数量、金额或价格精度限制不满足")
    if (card["signal"] == "LONG" and stop >= min(mark, price)) or (
        card["signal"] == "SHORT" and stop <= max(mark, price)
    ):
        raise EntryBlocked("标记价格已越过保护止损，暂停开仓")
    take = number(result.get("take_profit"))
    if take and (
        (card["signal"] == "LONG" and take <= mark)
        or (card["signal"] == "SHORT" and take >= mark)
    ):
        raise EntryBlocked("标记价格已越过止盈，暂停开仓")
    per_unit = abs(worst - stop) + worst * (
        2 * number(config.fee_rate) + 2 * number(config.slippage_bps) / 10000
    )
    allowance = min(number(config.budget), equity)
    if (
        quantity * per_unit > allowance * number(config.risk_per_trade_pct) / 100
        or risk + quantity * per_unit
        > allowance * number(config.portfolio_risk_pct) / 100
        or notional + quantity * max(ask, worst) > number(config.budget)
        or quantity
        * max(ask, worst)
        * (Decimal(1) / config.leverage + number(config.fee_rate))
        > spendable(config, fresh["available_balance"])
    ):
        raise EntryBlocked("最新报价或账户余额不满足风险预算")
    depth = number(quote["ask_size"] if card["signal"] == "LONG" else quote["bid_size"])
    if depth < quantity:
        raise EntryBlocked("最优报价深度不足，暂停开仓")
    if time.monotonic() - started > 5 or (entry_guard and not entry_guard()):
        raise EntryBlocked("下单检查耗时过长或账户事件变化，等待重新核查")
    result["preflight"] = {
        "spread_bps": str(spread),
        "quote_age_seconds": quote["age_seconds"],
        "mark": str(mark),
    }
    return result
