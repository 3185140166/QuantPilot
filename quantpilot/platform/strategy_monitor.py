"""Confirmed system fills only; monitoring never closes or adopts positions."""

from collections import defaultdict

from sqlalchemy import select

from .execution_status import configuration_fingerprint
from .models import TestnetExecution, TestnetOrder
from .schemas import TestnetSetup
from .strategy_review import number
from .testnet_journal import trade_cycles


def assess(db, current, cycles=None):
    config = TestnetSetup.model_validate(current.configuration)
    fingerprint = configuration_fingerprint(current.configuration)
    openings = db.scalars(
        select(TestnetOrder)
        .where(TestnetOrder.action == "open")
        .order_by(TestnetOrder.id.desc())
        .limit(100)
    ).all()
    matching, slippage = set(), []
    eligible = []
    entry_fees, entry_notional = 0.0, 0.0
    for order in openings:
        context = order.context or {}
        saved = context.get("configuration")
        try:
            same = saved and configuration_fingerprint(saved) == fingerprint
        except ValueError:
            same = False
        if not same:
            continue
        matching.add(order.link_id)
        reference = number((context.get("decision") or {}).get("entry"))
        if not reference or reference <= 0:
            continue
        if order.order_id:
            eligible.append((order, reference))
    fills_by_order = defaultdict(list)
    if eligible:
        for fill in db.scalars(
            select(TestnetExecution).where(
                TestnetExecution.order_id.in_(
                    [order.order_id for order, _ in eligible]
                ),
                TestnetExecution.exec_type == "Trade",
                TestnetExecution.owned.is_(True),
            )
        ):
            fills_by_order[fill.order_id].append(fill)
    for order, reference in eligible:
        fills = fills_by_order[order.order_id]
        quantity = sum(float(row.quantity) for row in fills)
        if not quantity:
            continue
        entry_fees += sum(float(row.fee) for row in fills)
        entry_notional += sum(float(row.price * row.quantity) for row in fills)
        actual = sum(float(row.price * row.quantity) for row in fills) / quantity
        slippage.append(
            (actual / reference - 1) * 10000 * (1 if order.side == "Buy" else -1)
        )
    completed = [
        row
        for row in (cycles if cycles is not None else trade_cycles(db))
        if row["opening_link_id"] in matching
        and row["complete"]
        and row["reconciled"]
        and number(row["net_pnl"]) is not None
    ]
    completed.sort(key=lambda row: row.get("closed_at_ms") or 0, reverse=True)
    streak = 0
    for row in completed:
        if number(row["net_pnl"]) >= 0:
            break
        streak += 1
    adverse = (
        sum(max(0, value) for value in slippage) / len(slippage) if slippage else None
    )
    reasons = []
    actual_fee_rate = entry_fees / entry_notional if entry_notional else None
    if streak >= config.monitoring.consecutive_losses:
        reasons.append(f"当前参数连续 {streak} 笔已对账交易亏损")
    if (
        len(slippage) >= config.monitoring.min_fills
        and adverse > config.monitoring.max_adverse_slippage_bps
    ):
        reasons.append("平均不利成交偏差超过运行监测上限")
    if (
        len(slippage) >= config.monitoring.min_fills
        and actual_fee_rate is not None
        and actual_fee_rate > config.fee_rate * 2
    ):
        reasons.append("实际开仓费率超过配置假设的两倍")
    return {
        "enabled": config.monitoring.enabled,
        "pause_new_entries": config.monitoring.enabled and bool(reasons),
        "reasons": reasons,
        "completed_trades": len(completed),
        "consecutive_losses": streak,
        "observed_openings": len(slippage),
        "adverse_slippage_bps": adverse,
        "assumed_slippage_bps": config.slippage_bps,
        "actual_entry_fee_rate": actual_fee_rate,
        "assumed_fee_rate": config.fee_rate,
        "validation_return": number(
            (current.research_origin or {}).get("metrics", {}).get("total_return")
        ),
        "net_pnl": sum(number(row["net_pnl"]) for row in completed),
        "fees": sum(number(row["fees"]) or 0 for row in completed),
        "funding": sum(number(row["funding"]) or 0 for row in completed),
        "limitations": [
            "窗口为最近100次开仓，连续亏损按已对账交易的平仓时间排序；不是账户全历史收益",
            "仅统计与当前完整配置匹配、已确认且已对账的本系统交易；手工持仓不计入策略成绩",
            "成交偏差相对下单前计划价格，不等同于订单簿真实滑点；缺少参考价的历史订单不纳入",
            "触发后只暂停本系统新开仓，不平仓、不撤单、不接管外部仓位",
        ],
    }
