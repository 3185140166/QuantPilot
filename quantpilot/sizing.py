"""Shared risk sizing for research and Testnet execution."""

from decimal import ROUND_DOWN, ROUND_UP, Decimal


def number(value):
    result = Decimal(str(value or 0))
    if not result.is_finite():
        raise ValueError("Invalid numeric value")
    return result


def spendable(config, available):
    """Reserve is a fixed amount of strategy budget, not wallet equity."""
    reserve = (
        number(config.budget) * number(getattr(config, "cash_reserve_pct", 0)) / 100
    )
    return max(Decimal(0), number(available) - reserve)


def allocation(
    config,
    equity,
    price,
    side,
    instrument,
    existing_risk=Decimal(0),
    existing_notional=Decimal(0),
    available=None,
):
    price, equity = number(price), min(number(equity), number(config.budget))
    lot = instrument["lotSizeFilter"]
    step, minimum = number(lot["qtyStep"]), number(lot["minOrderQty"])
    tick = number(instrument["priceFilter"]["tickSize"])
    if price <= 0 or equity <= 0 or min(step, tick) <= 0:
        raise ValueError("Invalid allocation inputs")
    direction = 1 if side == "LONG" else -1
    stop = price * (1 - direction * number(config.rules.stop_loss_pct) / 100)
    stop = (stop / tick).to_integral_value(
        rounding=ROUND_DOWN if direction == 1 else ROUND_UP
    ) * tick
    costs = price * (
        2 * number(config.fee_rate) + 2 * number(config.slippage_bps) / 10000
    )
    per_unit = abs(price - stop) + costs
    if stop <= 0 or per_unit <= 0:
        raise ValueError("Invalid protective stop")
    budget = min(
        equity * number(config.risk_per_trade_pct) / 100,
        max(
            Decimal(0), equity * number(config.portfolio_risk_pct) / 100 - existing_risk
        ),
    )
    notional_cap = max(Decimal(0), number(config.budget) - existing_notional)
    cash = spendable(config, equity if available is None else available)
    quantity = min(
        budget / per_unit,
        notional_cap / price,
        cash / (price / config.leverage + price * number(config.fee_rate)),
    )
    quantity = (quantity / step).to_integral_value(rounding=ROUND_DOWN) * step
    quantity = min(
        quantity,
        number(lot.get("maxMktOrderQty") or lot.get("maxOrderQty") or quantity),
    )
    quantity = (quantity / step).to_integral_value(rounding=ROUND_DOWN) * step
    take = None
    if config.rules.take_profit_pct:
        take = price * (1 + direction * number(config.rules.take_profit_pct) / 100)
        take = (take / tick).to_integral_value(rounding=ROUND_DOWN) * tick
    accepted = (
        quantity >= minimum
        and quantity * price >= number(lot.get("minNotionalValue"))
        and quantity > 0
    )
    return {
        "quantity": str(quantity if accepted else 0),
        "entry": str(price),
        "stop_loss": str(stop),
        "take_profit": str(take) if take else None,
        "notional": str(quantity * price if accepted else 0),
        "margin": str(quantity * price / config.leverage if accepted else 0),
        "planned_loss": str(quantity * per_unit if accepted else 0),
        "accepted": accepted,
        "reason": "符合当前风险预算"
        if accepted
        else "风险预算内无法满足最小订单／数量步长，跳过交易",
    }
