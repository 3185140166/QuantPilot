"""Explicit small Testnet smoke run, with a separate durable database and report.

Run with --execute only when test orders are authorized. Never enables the user's
continuous assistant or changes account margin mode, credentials, or fund balances.
"""

import argparse
import json
import logging
import os
import time
import uuid
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from quantpilot.platform.acceptance import summarize
from quantpilot.platform.database import make_database
from quantpilot.platform.leases import release_lease, renew_lease
from quantpilot.platform.models import TestnetAccount, TestnetOrder
from quantpilot.platform.settings import PlatformSettings
from quantpilot.platform.testnet import (
    PENDING,
    TestnetBroker,
    number,
    process_testnet,
    submit_intent,
)
from quantpilot.platform.testnet_journal import trade_cycles


class NoAutomaticSignals:
    source = "exchange"

    def bars(self, *_args):
        raise ValueError("Automatic signal execution disabled in smoke run")


def readiness():
    """Read-only preflight. No strategy toggle, migration or exchange write."""
    settings = PlatformSettings.from_env()
    engine, factory = make_database(settings.database_url)
    try:
        with factory() as db:
            current = db.execute(
                select(TestnetAccount.enabled, TestnetAccount.flatten_requested).where(
                    TestnetAccount.id == 1
                )
            ).first()
            pending = db.scalar(
                select(func.count())
                .select_from(TestnetOrder)
                .where(TestnetOrder.status.in_(PENDING))
            )
        local_paused = not current or not (current.enabled or current.flatten_requested)
        if not local_paused or pending:
            return {
                "ready": False,
                "assistant_paused": local_paused,
                "pending_orders": pending,
                "reason": "先暂停助手并处理未确认订单；本次检查未改变账户",
            }
        broker = TestnetBroker()
        state = broker.snapshot()
        flat = not state["positions"] and not state["open_orders"]
        balance = number(state["available_balance"]) >= 200
        isolated = state["margin_mode"] == "ISOLATED_MARGIN"
        return {
            "ready": flat and balance and isolated,
            "environment": "bybit_testnet",
            "assistant_paused": True,
            "pending_orders": pending,
            "exchange_idle": flat,
            "available_at_least_200": balance,
            "isolated": isolated,
        }
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Read-only readiness check; never submits orders",
    )
    args = parser.parse_args()
    logging.getLogger("pybit").setLevel(logging.CRITICAL)
    if args.check:
        print(json.dumps(readiness(), ensure_ascii=False))
        return
    if not args.execute:
        raise SystemExit("Use --execute only after authorization for Testnet orders")
    settings = PlatformSettings.from_env()
    # A running or unresolved assistant must never share this account with a test.
    prod_engine, prod_factory = make_database(settings.database_url)
    with prod_factory() as db:
        current = db.execute(
            select(TestnetAccount.enabled, TestnetAccount.flatten_requested).where(
                TestnetAccount.id == 1
            )
        ).first()
        if current and (current.enabled or current.flatten_requested):
            raise SystemExit(
                "Pause the continuous assistant before running smoke tests"
            )
        pending = list(
            db.scalars(
                select(TestnetOrder.id).where(
                    TestnetOrder.status.in_(
                        [
                            "intent",
                            "submitted",
                            "unknown",
                            "New",
                            "PartiallyFilled",
                            "Untriggered",
                        ]
                    )
                )
            )
        )
        if pending:
            raise SystemExit("Resolve existing pending orders before smoke tests")
    broker = TestnetBroker()
    initial = broker.snapshot()
    if initial["positions"] or initial["open_orders"]:
        raise SystemExit(
            "Test requires an empty account; existing activity was preserved"
        )
    if (
        number(initial["available_balance"]) < 200
        or initial["margin_mode"] != "ISOLATED_MARGIN"
    ):
        raise SystemExit("Test requires at least 200 test USDT and isolated margin")
    root = Path("artifacts/testnet") / ("smoke-" + uuid.uuid4().hex[:12])
    root.mkdir(parents=True)
    original_url = os.environ.get("DATABASE_URL")
    test_url = "sqlite:///" + (root / "state.db").resolve().as_posix()
    os.environ["DATABASE_URL"] = test_url
    try:
        command.upgrade(Config("alembic.ini"), "head")
    finally:
        if original_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original_url
    engine, factory = make_database(test_url)
    owner = uuid.uuid4().hex
    market = NoAutomaticSignals()
    report = {
        "environment": "bybit_testnet",
        "checks": [],
        "errors": [],
        "initial_wallet": initial["wallet_equity"],
    }
    created = []
    selected = None

    def production_paused():
        with prod_factory() as db:
            current = db.execute(
                select(TestnetAccount.enabled, TestnetAccount.flatten_requested).where(
                    TestnetAccount.id == 1
                )
            ).first()
            if current and (current.enabled or current.flatten_requested):
                raise RuntimeError("Continuous assistant was enabled during smoke test")

    def sync():
        process_testnet(factory, market, owner, broker)
        return broker.snapshot()

    def opening(symbol, quantity, stamp, stop, kind="Market", price=None):
        production_paused()
        with factory.begin() as db:
            db.get(TestnetAccount, 1).enabled = True
        renew_lease(factory, "testnet", owner)
        try:
            submit_intent(
                factory,
                broker,
                owner,
                symbol,
                "Buy",
                "open",
                quantity,
                stamp,
                stop=stop,
                order_type=kind,
                limit_price=price,
            )
        finally:
            with factory.begin() as db:
                db.get(TestnetAccount, 1).enabled = False
        with factory() as db:
            row = db.scalar(select(TestnetOrder).where(TestnetOrder.bar_time == stamp))
        created.append(row.link_id)
        (root / "created-orders.json").write_text(json.dumps(created), encoding="utf-8")
        return row

    try:
        sync()
        tickers = {row["symbol"]: row for row in broker.client.get_tickers()}
        for symbol in ("XRPUSDT", "DOGEUSDT", "SOLUSDT", "ETHUSDT"):
            instrument = broker.instrument(symbol)
            lot = instrument["lotSizeFilter"]
            price = number(tickers[symbol]["lastPrice"])
            step = number(lot["qtyStep"])
            qty = max(
                number(lot["minOrderQty"]),
                number(lot.get("minNotionalValue"))
                * Decimal("1.1")
                / (price * Decimal("0.95")),
            )
            qty = (qty / step).to_integral_value(rounding=ROUND_UP) * step
            if 0 < qty * price <= 12:
                selected = (
                    symbol,
                    price,
                    qty,
                    number(instrument["priceFilter"]["tickSize"]),
                )
                break
        if selected is None:
            raise RuntimeError("No contract fits the smoke test notional cap")
        symbol, price, quantity, tick = selected
        report.update(symbol=symbol, quantity=str(quantity), notional_cap="12 USDT")
        book = broker.client._check(
            broker.session.get_orderbook(category="linear", symbol=symbol, limit=1)
        )["result"]
        bid = number(book["b"][0][0])
        passive = (min(price, bid) * Decimal("0.95") / tick).to_integral_value(
            rounding=ROUND_DOWN
        ) * tick
        stop = (passive * Decimal("0.98") / tick).to_integral_value(
            rounding=ROUND_DOWN
        ) * tick
        order = opening(symbol, quantity, "smoke-limit", stop, "Limit", passive)
        for _ in range(12):
            live = broker.lookup(symbol, order.link_id)
            status = live.get("orderStatus") if live else "unknown"
            if status in {"New", "PartiallyFilled", "Filled", "Rejected", "Cancelled"}:
                break
            time.sleep(1)
        report["checks"].append({"limit_order": status})
        if status in {"New", "PartiallyFilled"}:
            broker.cancel(symbol, order.link_id)
        with factory.begin() as db:
            db.get(TestnetOrder, order.id).cancel_requested = True
        for _ in range(12):
            state = sync()
            with factory() as db:
                status = db.get(TestnetOrder, order.id).status
            if status in {"Cancelled", "Rejected", "Filled"}:
                break
            time.sleep(1)
        report["checks"].append({"limit_cancellation": status})
        if status != "Cancelled" or state["positions"] or state["open_orders"]:
            raise RuntimeError("Limit order was not confirmed cancelled and flat")
        ticker = broker.client._check(
            broker.session.get_tickers(category="linear", symbol=symbol)
        )["result"]["list"][0]
        price = number(ticker["lastPrice"])
        if quantity * price > 12:
            raise RuntimeError("Updated price exceeds test notional cap")
        stop = (price * Decimal("0.98") / tick).to_integral_value(
            rounding=ROUND_DOWN
        ) * tick
        original_submit = broker.submit

        def interrupted_submit(*values):
            original_submit(*values)
            raise TimeoutError("Simulated discarded submission response")

        broker.submit = interrupted_submit
        try:
            order = opening(symbol, quantity, "smoke-market", stop)
        finally:
            broker.submit = original_submit
        response_lost = order.status == "unknown"
        for _ in range(15):
            state = sync()
            with factory() as db:
                status = db.get(TestnetOrder, order.id).status
            if status == "Filled" and state["positions"]:
                break
            time.sleep(1)
        report["checks"].append({"market_order": status})
        report["checks"].append(
            {"submission_response_recovered": response_lost and status == "Filled"}
        )
        position = next((p for p in state["positions"] if p["symbol"] == symbol), None)
        if status != "Filled" or position is None:
            raise RuntimeError("Market fill was not confirmed")
        report["checks"].append(
            {"exchange_stop_present": number(position.get("stopLoss")) > 0}
        )
        if number(position.get("stopLoss")) <= 0:
            raise RuntimeError("Native stop could not be verified")
        release_lease(factory, "testnet", owner)
        engine.dispose()
        engine, factory = make_database(test_url)
        owner = uuid.uuid4().hex
        broker = TestnetBroker()
        state = sync()
        from quantpilot.platform.models import TestnetPosition

        with factory() as db:
            restored = db.get(TestnetPosition, symbol)
        recovered = bool(restored and restored.opening_link_id == order.link_id)
        report["checks"].append({"fresh_client_recovery": recovered})
        if not recovered:
            raise RuntimeError("Fresh client could not prove position ownership")
        with factory.begin() as db:
            db.get(TestnetAccount, 1).flatten_requested = True
        for _ in range(15):
            state = sync()
            if not state["positions"] and not state["open_orders"]:
                break
            time.sleep(1)
        for _ in range(3):
            sync()
            with factory() as db:
                cycles = trade_cycles(db)
            if cycles and cycles[0]["complete"]:
                break
            time.sleep(1)
        report["cycles"] = cycles
        if not cycles or not cycles[0]["complete"]:
            raise RuntimeError("Trade journal did not confirm the complete cycle")
        report["checks"].append(
            {"closed_and_flat": not state["positions"] and not state["open_orders"]}
        )
    except Exception as exc:  # noqa: BLE001 - SDK request details can contain credentials
        report["errors"].append(type(exc).__name__)
    finally:
        # Clean up only the symbol and unique orders created by this run.
        with factory.begin() as db:
            db.get(TestnetAccount, 1).enabled = False
            db.get(TestnetAccount, 1).flatten_requested = True
            for row in db.scalars(
                select(TestnetOrder).where(TestnetOrder.action == "open")
            ):
                row.cancel_requested = True
        for _ in range(15):
            try:
                state = sync()
                ours = [
                    p
                    for p in state["positions"]
                    if selected and p["symbol"] == selected[0]
                ]
                orders = [
                    r for r in state["open_orders"] if r.get("orderLinkId") in created
                ]
                if not ours and not orders:
                    break
            except Exception:  # noqa: BLE001 - preserve durable intents for recovery
                report["errors"].append("CleanupQueryFailed")
            time.sleep(1)
        try:
            final = broker.snapshot()
            report["final_wallet"] = final["wallet_equity"]
            report["final_positions"] = len(final["positions"])
            report["final_open_orders"] = len(final["open_orders"])
        except Exception:  # noqa: BLE001 - always preserve a recovery report
            report["errors"].append("FinalStateUnconfirmed")
            report["final_positions"] = report["final_open_orders"] = None
        report["acceptance"] = summarize(report)
        (root / "report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(json.dumps(report))
        print("Report: " + str((root / "report.json").resolve()))
        release_lease(factory, "testnet", owner)
        engine.dispose()
        prod_engine.dispose()
    if not report["acceptance"]["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - never print credential-bearing SDK errors
        raise SystemExit(
            "Testnet acceptance unavailable: " + type(exc).__name__
        ) from None
