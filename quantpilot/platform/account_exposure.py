"""Account-wide exposure visibility without adopting external positions."""

from decimal import Decimal

from quantpilot.sizing import number, spendable


def summarize(snapshot, ownership, config):
    groups = {
        source: {
            "positions": 0,
            "long_notional": Decimal(0),
            "short_notional": Decimal(0),
            "stop_risk": Decimal(0),
        }
        for source in ("system", "external", "unknown")
    }
    unprotected = []
    invalid = []
    for row in ownership.get("positions", []):
        source = row.get("source", "unknown")
        group = groups[source if source in groups else "unknown"]
        group["positions"] += 1
        try:
            quantity, entry = number(row.get("size")), number(row.get("avgPrice"))
            mark = number(row.get("markPrice"))
            if (
                quantity <= 0
                or entry <= 0
                or mark <= 0
                or row.get("side") not in {"Buy", "Sell"}
            ):
                raise ValueError("Invalid exposure")
            group["long_notional" if row["side"] == "Buy" else "short_notional"] += (
                quantity * mark
            )
            stop = number(row.get("stopLoss"))
            if stop <= 0:
                unprotected.append(row.get("symbol"))
                continue
            distance = max(
                Decimal(0), entry - stop if row["side"] == "Buy" else stop - entry
            )
            group["stop_risk"] += quantity * (
                distance
                + entry
                * (
                    2 * number(config.fee_rate)
                    + 2 * number(config.slippage_bps) / 10000
                )
            )
        except (ValueError, ArithmeticError, TypeError):
            invalid.append(row.get("symbol"))
    available = number(snapshot.get("available_balance"))
    total_risk = sum((row["stop_risk"] for row in groups.values()), Decimal(0))
    risk_limit = number(config.budget) * number(config.portfolio_risk_pct) / 100
    reasons = []
    if unprotected or invalid:
        reasons.append("存在无止损或数据不完整持仓，整体止损风险无法完整估计")
    if ownership.get("uncertain"):
        reasons.append("外部挂单占用或来源未确认")
    if total_risk >= risk_limit:
        reasons.append("已知持仓止损风险达到组合预算上限")
    if spendable(config, available) <= 0:
        reasons.append("扣除现金保留额度后没有可用于新开仓的余额")
    return {
        "groups": {
            key: {
                field: str(value) if isinstance(value, Decimal) else value
                for field, value in row.items()
            }
            for key, row in groups.items()
        },
        "available_balance": str(available),
        "reserve_amount": str(
            number(config.budget) * number(config.cash_reserve_pct) / 100
        ),
        "spendable_balance": str(spendable(config, available)),
        "reserved_external_notional": ownership.get("reserved_notional", "0"),
        "known_stop_risk": str(total_risk),
        "risk_limit": str(risk_limit),
        "risk_complete": not unprotected and not invalid,
        "unprotected_symbols": sorted(set(unprotected)),
        "invalid_symbols": sorted(set(invalid)),
        "reasons": reasons,
        "semantics": "包含手工与其他程序持仓；止损风险按入场价估算并加入成本，不是最坏损失。挂单名义金额不能等同于保证金；可用余额以交易所返回值为准。外部持仓只查看，不接管。",
    }
