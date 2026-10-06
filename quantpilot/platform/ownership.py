"""Classify exchange orders without adopting or mutating external orders."""

from decimal import Decimal

from sqlalchemy import or_, select

from quantpilot.sizing import number

from .models import TestnetOrder, TestnetPosition


def classify(factory, snapshot):
    raw_orders = snapshot.get("open_orders", [])
    raw_links = {
        value
        for row in raw_orders
        for value in (row.get("orderLinkId"), row.get("parentOrderLinkId"))
        if value
    }
    raw_ids = {row["orderId"] for row in raw_orders if row.get("orderId")}
    with factory() as db:
        orders = list(
            db.scalars(
                select(TestnetOrder).where(
                    or_(
                        TestnetOrder.link_id.in_(raw_links),
                        TestnetOrder.order_id.in_(raw_ids),
                    )
                )
            )
        )
        owned_positions = {
            row.symbol: row for row in db.scalars(select(TestnetPosition))
        }
        owned = set(owned_positions)
        active_openings = set(
            db.scalars(
                select(TestnetOrder.symbol).where(
                    TestnetOrder.symbol.in_(
                        {row["symbol"] for row in snapshot.get("positions", [])}
                    ),
                    TestnetOrder.action == "open",
                    TestnetOrder.filled_quantity > 0,
                    TestnetOrder.position_closed.is_(False),
                )
            )
        )
    links = {row.link_id for row in orders}
    ids = {row.order_id for row in orders if row.order_id}
    blocked, reserved_symbols, rows, positions = set(), set(), [], []
    reserved = Decimal(0)
    uncertain = False
    for raw in raw_orders:
        link = raw.get("orderLinkId") or ""
        system = (
            link in links
            or raw.get("orderId") in ids
            or raw.get("parentOrderLinkId") in links
        )
        protected_position = owned_positions.get(raw.get("symbol"))
        system |= bool(
            protected_position
            and raw.get("reduceOnly") is True
            and int(raw.get("positionIdx", 0)) == 0
            and raw.get("side") != protected_position.side
            and raw.get("stopOrderType")
            in {"StopLoss", "TakeProfit", "PartialStopLoss", "PartialTakeProfit"}
        )
        source = (
            "system" if system else "unknown" if link.startswith("qp-") else "external"
        )
        row = {**raw, "source": source, "managed": system}
        rows.append(row)
        if system:
            continue
        symbol = raw.get("symbol")
        if symbol:
            blocked.add(symbol)
        uncertain |= source == "unknown" or not symbol
        if raw.get("reduceOnly") is True:
            continue
        if symbol:
            reserved_symbols.add(symbol)
        remaining = max(
            Decimal(0),
            number(
                raw.get("leavesQty")
                if raw.get("leavesQty") not in (None, "")
                else number(raw.get("qty")) - number(raw.get("cumExecQty"))
            ),
        )
        price = max(number(raw.get("price")), number(raw.get("triggerPrice")))
        if remaining <= 0 or price <= 0:
            uncertain = True
        reserved += remaining * price
    for raw in snapshot.get("positions", []):
        system = raw["symbol"] in owned and int(raw.get("positionIdx", 0)) == 0
        positions.append(
            {
                **raw,
                "source": "system"
                if system
                else "unknown"
                if raw["symbol"] in active_openings
                else "external",
                "managed": system,
                "unrealisedPnl": raw.get("unrealisedPnl")
                if raw.get("unrealisedPnl") not in (None, "")
                else None
                if any(
                    raw.get(key) in (None, "")
                    for key in ("size", "markPrice", "avgPrice", "side")
                )
                else str(
                    number(raw["size"])
                    * (number(raw.get("markPrice")) - number(raw.get("avgPrice")))
                    * (1 if raw.get("side") == "Buy" else -1)
                ),
            }
        )
        if not system:
            blocked.add(raw["symbol"])
        uncertain |= int(raw.get("positionIdx", 0)) != 0
    return {
        "orders": rows,
        "positions": positions,
        "blocked_symbols": sorted(blocked),
        "reserved_symbols": sorted(reserved_symbols),
        "reserved_notional": str(reserved),
        "uncertain": uncertain,
    }
