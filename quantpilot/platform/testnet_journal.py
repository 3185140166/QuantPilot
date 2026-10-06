"""Idempotent exchange journal; transfers are never counted as trading profit."""

import hashlib
import json
from decimal import Decimal

from sqlalchemy import func, select

from .engine import guard_lease
from .models import TestnetAccount, TestnetExecution, TestnetLedger, TestnetOrder

FLOWS = {"TRANSFER_IN", "TRANSFER_OUT", "BONUS_TRANSFER_IN", "BONUS_TRANSFER_OUT"}


def amount(value):
    result = Decimal(str(value or 0))
    if not result.is_finite():
        raise ValueError("Invalid journal amount")
    return result


def ingest(factory, broker, transactions, executions, owner):
    with factory() as db:
        orders = list(db.scalars(select(TestnetOrder)))
    links = {row.link_id: row for row in orders}
    ids = {row.order_id: row for row in orders if row.order_id}
    prepared = []
    for raw in executions:
        if not raw.get("execId"):
            raise ValueError("Execution has no durable ID")
        order = links.get(raw.get("orderLinkId")) or ids.get(raw.get("orderId"))
        reason = None
        if order and order.action == "close":
            reason = (
                "账户保护／请求平仓"
                if order.bar_time.startswith("protect:")
                else "信号退出"
            )
        if not order and amount(raw.get("closedSize")) > 0:
            with factory() as db:
                saved = db.get(TestnetExecution, raw["execId"])
            if saved and saved.owned:
                prepared.append((raw, True, saved.exit_reason, saved.opening_link_id))
                continue
            report = broker.lookup_id(raw["symbol"], raw["orderId"])
            parent = (report or {}).get("parentOrderLinkId")
            if parent in links:
                order = links[parent]
                reason = (report or {}).get("stopOrderType") or "交易所保护退出"
        origin = (
            (order.link_id if order.action == "open" else order.opening_link_id)
            if order
            else None
        )
        prepared.append((raw, order is not None, reason, origin))
    with factory.begin() as db:
        guard_lease(db, "testnet", owner)
        for raw in transactions:
            identity = [
                raw.get("id"),
                raw.get("tradeId"),
                raw.get("type"),
                raw.get("transactionTime"),
                raw.get("orderId"),
            ]
            key = hashlib.sha256(
                json.dumps(identity, separators=(",", ":")).encode()
            ).hexdigest()
            saved = db.get(TestnetLedger, key)
            if saved is None:
                db.add(
                    TestnetLedger(
                        id=key,
                        kind=raw["type"],
                        symbol=raw.get("symbol") or "",
                        order_id=raw.get("orderId") or "",
                        change=amount(raw.get("change")),
                        cash_flow=amount(raw.get("cashFlow")),
                        fee=amount(raw.get("fee")),
                        funding=amount(raw.get("funding")),
                        at_ms=int(raw["transactionTime"]),
                        trade_id=raw.get("tradeId") or None,
                    )
                )
            elif saved.trade_id is None and raw.get("tradeId"):
                saved.trade_id = raw["tradeId"]
        for raw, owned, reason, origin in prepared:
            row = db.get(TestnetExecution, raw["execId"])
            if row is None:
                row = TestnetExecution(
                    id=raw["execId"],
                    order_id=raw["orderId"],
                    link_id=raw.get("orderLinkId") or "",
                    symbol=raw["symbol"],
                    side=raw.get("side") or "",
                    exec_type=raw.get("execType") or "Trade",
                    quantity=amount(raw.get("execQty")),
                    price=amount(raw.get("execPrice")),
                    fee=amount(raw.get("execFee")),
                    closed_size=amount(raw.get("closedSize")),
                    at_ms=int(raw["execTime"]),
                    owned=owned,
                    exit_reason=reason,
                    opening_link_id=origin,
                    sequence=int(raw["seq"])
                    if raw.get("seq") not in (None, "")
                    else None,
                )
                db.add(row)
            elif owned:
                row.owned, row.exit_reason, row.opening_link_id = True, reason, origin
                if raw.get("seq") not in (None, ""):
                    row.sequence = int(raw["seq"])
        db.flush()
        touched_links = {row.get("orderLinkId") for row in executions}
        touched_ids = {row.get("orderId") for row in executions}
        for order in db.scalars(select(TestnetOrder)):
            if order.link_id not in touched_links and order.order_id not in touched_ids:
                continue
            fills = list(
                db.scalars(
                    select(TestnetExecution).where(
                        (TestnetExecution.link_id == order.link_id)
                        | (TestnetExecution.order_id == order.order_id)
                        if order.order_id
                        else TestnetExecution.link_id == order.link_id
                    )
                )
            )
            fills = [row for row in fills if row.exec_type == "Trade"]
            qty = sum((row.quantity for row in fills), Decimal(0))
            if qty:
                order.filled_quantity = qty
                order.average_price = (
                    sum((row.quantity * row.price for row in fills), Decimal(0)) / qty
                )
                order.fee = sum((row.fee for row in fills), Decimal(0))
                order.order_id = fills[0].order_id
                if qty >= order.quantity:
                    order.status, order.error = "Filled", None


def synchronize_journal(factory, broker, snapshot, owner):
    with factory() as db:
        current = db.get(TestnetAccount, 1)
        earliest = db.scalar(select(func.min(TestnetOrder.created_at)))
        cursor = current.journal_cursor_ms
    end = int(snapshot["at_ms"])
    start = max(0, (cursor or end) - 86400000)
    if cursor is None and earliest:
        from datetime import timezone

        start = min(
            start, int(earliest.replace(tzinfo=timezone.utc).timestamp() * 1000) - 60000
        )
    if end - start > 365 * 86400000:
        raise ValueError("Journal requires an explicit historical import")
    transactions, executions = broker.journal(start, end)
    ingest(factory, broker, transactions, executions, owner)
    with factory.begin() as db:
        guard_lease(db, "testnet", owner)
        current = db.get(TestnetAccount, 1)
        current.journal_cursor_ms = end


def flow_totals(db, start_ms):
    rows = list(db.scalars(select(TestnetLedger).where(TestnetLedger.at_ms > start_ms)))
    flows = sum((row.change for row in rows if row.kind in FLOWS), Decimal(0))
    change = sum((row.change for row in rows), Decimal(0))
    return flows, change


def cycle_inventory(fills, entry_order_id, side):
    """Replay actual fills in time/sequence order, updating remaining cost basis."""
    rows = sorted(fills, key=lambda row: (row.at_ms, row.sequence or 0, row.id))
    remaining, cost, gross = Decimal(0), Decimal(0), Decimal(0)
    valid = True
    groups = {}
    for row in rows:
        groups.setdefault(row.at_ms, []).append(row)
    for group in groups.values():
        if (
            any(row.order_id == entry_order_id for row in group)
            and any(row.closed_size > 0 for row in group)
            and any(not row.sequence or row.sequence < 0 for row in group)
        ):
            valid = False
    for row in rows:
        if row.order_id == entry_order_id:
            remaining += row.quantity
            cost += row.quantity * row.price
        elif row.closed_size > 0:
            if remaining <= 0 or row.closed_size > remaining:
                valid = False
                continue
            basis = cost / remaining
            gross += (
                (row.price - basis) * row.closed_size * (1 if side == "Buy" else -1)
            )
            remaining -= row.closed_size
            cost -= basis * row.closed_size
    return {
        "remaining": remaining,
        "cost": cost,
        "gross": gross,
        "valid": valid and bool(rows),
    }


def trade_cycles(db):
    """Group confirmed executions by their durable opening, not by wallet changes."""
    result = []
    openings = list(
        db.scalars(
            select(TestnetOrder)
            .where(TestnetOrder.action == "open", TestnetOrder.filled_quantity > 0)
            .order_by(TestnetOrder.id.desc())
            .limit(100)
        )
    )
    for opening in openings:
        fills = list(
            db.scalars(
                select(TestnetExecution)
                .where(
                    TestnetExecution.opening_link_id == opening.link_id,
                    TestnetExecution.exec_type == "Trade",
                )
                .order_by(TestnetExecution.at_ms)
            )
        )
        entries = [row for row in fills if row.order_id == opening.order_id]
        exits = [row for row in fills if row.closed_size > 0]
        entry_qty = sum((row.quantity for row in entries), Decimal(0))
        closed_qty = sum((row.closed_size for row in exits), Decimal(0))
        if not entry_qty:
            continue
        entry = (
            sum((row.quantity * row.price for row in entries), Decimal(0)) / entry_qty
        )
        inventory = cycle_inventory(fills, opening.order_id, opening.side)
        inventory["valid"] = inventory["valid"] and entry_qty == opening.filled_quantity
        gross = inventory["gross"]
        exit_ids = {row.id for row in exits}
        actual_pnl = (
            list(
                db.scalars(
                    select(TestnetLedger).where(TestnetLedger.trade_id.in_(exit_ids))
                )
            )
            if exit_ids
            else []
        )
        exchange_confirmed = bool(exit_ids) and exit_ids.issubset(
            {row.trade_id for row in actual_pnl}
        )
        if exchange_confirmed:
            gross = sum((row.cash_flow for row in actual_pnl), Decimal(0))
        fees = sum((row.fee for row in fills), Decimal(0))
        complete = (
            inventory["valid"]
            and inventory["remaining"] == 0
            and opening.status
            not in {
                "intent",
                "submitted",
                "unknown",
                "New",
                "PartiallyFilled",
                "Untriggered",
            }
        )
        start, end = (
            entries[0].at_ms,
            exits[-1].at_ms
            if complete and exits
            else opening.closed_at_ms or 2**63 - 1,
        )
        funding = sum(
            (
                row.funding
                for row in db.scalars(
                    select(TestnetLedger).where(
                        TestnetLedger.kind == "SETTLEMENT",
                        TestnetLedger.symbol == opening.symbol,
                        TestnetLedger.at_ms >= start,
                        TestnetLedger.at_ms <= end,
                    )
                )
            ),
            Decimal(0),
        )
        result.append(
            {
                "opening_link_id": opening.link_id,
                "entry_at_ms": start,
                "closed_at_ms": end if complete else None,
                "symbol": opening.symbol,
                "side": opening.side,
                "entry_price": str(entry),
                "entry_quantity": str(entry_qty),
                "closed_quantity": str(closed_qty),
                "exit_price": str(
                    sum((row.price * row.closed_size for row in exits), Decimal(0))
                    / closed_qty
                )
                if closed_qty
                else None,
                "gross_pnl": str(gross)
                if inventory["valid"] or exchange_confirmed
                else None,
                "fees": str(fees),
                "funding": str(funding),
                "net_pnl": str(gross - fees + funding)
                if inventory["valid"] or exchange_confirmed
                else None,
                "complete": complete,
                "reconciled": inventory["valid"]
                and not (opening.position_closed and inventory["remaining"] > 0),
                "pnl_source": "exchange" if exchange_confirmed else "reconstructed",
                "exit_reason": exits[-1].exit_reason if exits else None,
            }
        )
    return result
